#!/usr/bin/env python3
"""ig_web: lokalna strona do ig_grab - kolejka linkow z limitem dziennym + przegladarka notatek.

Uruchom:  python ig_web.py --cookies cookies.txt     (potem http://127.0.0.1:8765)
Dziala tylko na 127.0.0.1. Limit dzienny i losowe przerwy chronia konto przed blokada.
"""
import argparse, html, json, random, re, threading, time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import ig_grab

CFG = {}
LOCK = threading.Lock()
STATE_FILE = Path("ig_state.json")


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"queue": [], "log": []}


def save(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


LOCAL_ERRORS = ("No module named", "brak ffmpeg", "WinError 2")  # nie dotarly do Instagrama


def today_count(st: dict) -> int:
    """Do limitu licza sie tylko proby, ktore realnie poszly do Instagrama."""
    return sum(1 for e in st["log"] if e["day"] == str(date.today())
               and not any(m in e["status"] for m in LOCAL_ERRORS))


def worker() -> None:
    while True:
        with LOCK:
            st = load()
            ready = st["queue"] and today_count(st) < CFG["limit"]
            url = st["queue"][0] if ready else None
        if not url:
            time.sleep(3)
            continue
        code = ig_grab.shortcode(url)
        status = "ok"
        if (Path(CFG["notes"]) / f"{code}.md").exists():
            status = "juz jest"
        else:
            try:
                (CFG["fake"] or ig_grab.process)(url, Path(CFG["out"]), Path(CFG["notes"]),
                                                  "chrome", CFG["cookies"])
            except Exception as e:
                status = f"blad: {str(e)[:150]}"
        with LOCK:
            st = load()
            st["queue"] = [u for u in st["queue"] if u != url]
            if status != "juz jest":  # tylko realne zapytania do IG liczą się do limitu
                st["log"].append({"day": str(date.today()), "url": url, "status": status,
                                  "ts": time.strftime("%H:%M:%S")})
            save(st)
        if status != "juz jest":
            time.sleep(random.uniform(*CFG["pause"]))


def read_notes() -> list:
    out = []
    for f in sorted(Path(CFG["notes"]).glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name in ig_grab.RESERVED:
            continue
        t = f.read_text(encoding="utf-8")
        meta = dict(re.findall(r"^(\w+): (.*)$", t.split("---")[1], re.M)) if t.startswith("---") else {}
        title = re.search(r"^# (.+)$", t, re.M)
        out.append({"id": f.stem, "tag": meta.get("kategoria") or meta.get("etykieta", ""),
                    "ocena": meta.get("ocena", ""), "data": meta.get("data_posta", ""),
                    "tagi": [t.strip() for t in meta.get("tagi", "").split(",") if t.strip()],
                    "tytul": title.group(1) if title else f.stem,
                    "autor": meta.get("autor", ""), "url": meta.get("url", ""), "text": t})
    return out


INDEX = r"""<!doctype html><html lang=pl><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ig-grab</title>
<style>
:root{--bg:#fff;--bg2:#f5f5f7;--fg:#1d1d1f;--mut:#6e6e73;--line:#e2e2e6;--acc:#7c3aed;--accbg:#ede9fe}
@media(prefers-color-scheme:dark){:root{--bg:#1e1e1e;--bg2:#262626;--fg:#dcddde;--mut:#8b8d90;--line:#363636;--acc:#a78bfa;--accbg:#2e2748}}
*{box-sizing:border-box}body{margin:0;font:15px/1.6 system-ui,sans-serif;background:var(--bg);color:var(--fg);height:100vh;display:flex}
a{color:var(--acc);text-decoration:none}
#side{width:380px;min-width:280px;background:var(--bg2);border-right:1px solid var(--line);display:flex;flex-direction:column}
#side header{padding:14px;border-bottom:1px solid var(--line)}
#side h1{font-size:17px;margin:0 0 8px}
input,textarea,button{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input{width:100%}textarea{width:100%;height:84px;resize:vertical}
button{cursor:pointer;background:var(--acc);color:#fff;border:0}button:hover{opacity:.9}
.seg{display:flex;gap:6px;margin:8px 0}.seg button{flex:1;background:var(--bg);color:var(--mut);border:1px solid var(--line)}
.seg button.on{background:var(--accbg);color:var(--acc);border-color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{padding:2px 10px;border-radius:99px;background:var(--bg);border:1px solid var(--line);cursor:pointer;font-size:13px;color:var(--mut)}
.chip.on{background:var(--accbg);color:var(--acc);border-color:var(--acc)}
#list{overflow:auto;flex:1}
.item{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.item:hover{background:var(--bg)}.item.on{background:var(--accbg)}
.item b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item small{color:var(--mut)}
#main{flex:1;overflow:auto;position:relative}
#noteView{display:flex;gap:48px;padding:28px 48px;max-width:1500px;margin:0 auto;align-items:flex-start}
#note{flex:1;min-width:0;max-width:880px}
#rail{width:320px;flex:none;position:sticky;top:28px;font-size:14px}
#rail h3{margin:18px 0 8px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut)}
#rail h3:first-child{margin-top:0}
#rail .rel{display:block;padding:6px 0;border-bottom:1px solid var(--line);color:var(--fg)}
#rail .rel small{display:block;color:var(--mut)}
#note h1{font-size:26px;line-height:1.25}#note h2{font-size:18px;margin-top:1.6em;border-bottom:1px solid var(--line);padding-bottom:4px}
#note h3{font-size:15px;margin-top:1.2em;color:var(--mut)}
#note p{white-space:pre-wrap;margin:.5em 0}#note li{margin:.2em 0}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px;font-size:13px;color:var(--mut);align-items:center}
details{margin-top:10px;font-size:13px}details ul{margin:6px 0;padding-left:18px}
.empty{color:var(--mut);margin-top:30vh;text-align:center}
#map{position:absolute;inset:0;width:100%;height:100%;cursor:grab}
#legend{position:absolute;left:18px;top:14px;font-size:13px;color:var(--mut);display:flex;gap:14px;flex-wrap:wrap;pointer-events:none}
#legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
#hint{position:absolute;right:18px;bottom:12px;font-size:12px;color:var(--mut);pointer-events:none}
@media(max-width:1500px){#noteView{flex-direction:column;gap:20px}#rail{width:100%;position:static}}
@media(max-width:760px){body{flex-direction:column;height:auto}#side{width:100%}#noteView{padding:16px}}
</style>
<aside id=side><header>
 <h1>ig-grab <small id=quota style="font-weight:400;color:var(--mut)"></small></h1>
 <div class=seg><button id=bNote class=on onclick="setView('note')">Notatka</button><button id=bMap onclick="setView('map')">Mapa</button></div>
 <details><summary>Nowa notatka (wklej tekst)</summary>
  <textarea id=nt placeholder="wklej tekst: dziennik, tweet, pomysł..." style="height:110px"></textarea>
  <input id=nti placeholder="tytuł (puste = Claude nada)" style="margin-top:6px">
  <input id=ntg placeholder="tagi po przecinku (puste = Claude nada)" style="margin-top:6px">
  <select id=ntc style="margin:6px 0;width:100%;padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)"><option value="">kategoria: auto</option><option>ai</option><option>trading</option><option>przepis</option><option>biznes</option><option>inne</option></select>
  <button onclick=newNote()>Zapisz notatkę</button> <small id=ntst style="color:var(--mut)"></small></details>
 <details><summary>Dodaj linki do kolejki</summary>
  <textarea id=urls placeholder="wklej linki, jeden pod drugim"></textarea>
  <button onclick=add()>Dodaj do kolejki</button>
  <div id=queue></div></details>
 <input id=q placeholder="szukaj w treści notatek" oninput=render() style="margin-top:10px">
 <div class=chips id=chips></div>
</header><div id=list></div></aside>
<main id=main>
 <div id=noteView><div id=note><p class=empty>Wybierz notatkę z listy.</p></div><aside id=rail></aside></div>
 <canvas id=map hidden></canvas><div id=legend hidden></div><div id=hint hidden>kółka = notatki · szare punkty = wspólne tagi · kliknij, przeciągnij, kółko myszy = zoom</div>
</main>
<script>
let S={notes:[],queue:[],log:[],used:0,limit:0},sel=null,tag="",view="note",sig="";
const CAT={ai:"#8b5cf6",trading:"#22c55e",przepis:"#f59e0b",biznes:"#3b82f6",inne:"#9ca3af"};
const col=c=>CAT[c]||"#9ca3af";
const esc=t=>String(t).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const $=id=>document.getElementById(id);
const inl=t=>esc(t).replace(/\*\*(.+?)\*\*/g,"<b>$1</b>").replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target=_blank>$1</a>');
function md(t){let h="",ul=false,para=[];
 const fl=()=>{if(para.length){h+="<p>"+para.map(inl).join("\n")+"</p>";para=[]}};
 for(const l of t.split("\n")){let m;
  if(m=l.match(/^(#{1,3}) (.*)/)){fl();if(ul){h+="</ul>";ul=false}h+=`<h${m[1].length}>${inl(m[2])}</h${m[1].length}>`}
  else if(m=l.match(/^[-*] (.*)/)){fl();if(!ul){h+="<ul>";ul=true}h+="<li>"+inl(m[1])+"</li>"}
  else if(!l.trim()){fl();if(ul){h+="</ul>";ul=false}}
  else{if(ul){h+="</ul>";ul=false}para.push(l)}}
 fl();if(ul)h+="</ul>";return h}
function body(n){return n.text.replace(/^---[\s\S]*?---\s*/,"").split(/\n## Moje/)[0]}
function moje(n){const p=n.text.split(/\n## Moje/);return p.length>1?p[1].trim():""}
async function saveMy(id){const t=$("my");
 await fetch("/api/my",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,text:t.value})});
 $("saved").textContent="zapisano";await load();sel=id}
function render(){
 const q=$("q").value.toLowerCase();
 const cats=[...new Set(S.notes.map(n=>n.tag).filter(Boolean))].sort();
 $("chips").innerHTML=cats.map(t=>`<span class="chip ${t==tag?"on":""}" onclick="setTag('${esc(t)}')">${esc(t)}</span>`).join("");
 const shown=S.notes.filter(n=>(!tag||n.tag==tag)&&n.text.toLowerCase().includes(q));
 $("list").innerHTML=shown.map(n=>`<div class="item ${n.id==sel?"on":""}" onclick="pick('${esc(n.id)}')"><b>${esc(n.tytul.slice(0,80))}</b><small>${esc([n.tag,n.ocena,n.autor].filter(Boolean).join(" · "))}</small></div>`).join("")||'<p class=empty style="margin:20px">Brak notatek.</p>';
 $("quota").textContent=`dziś ${S.used}/${S.limit} · kolejka ${S.queue.length}`;
 $("queue").innerHTML="<ul>"+S.queue.map(u=>"<li>"+esc(u)+"</li>").join("")+"</ul><ul>"+S.log.slice(-6).reverse().map(x=>"<li>"+esc(x.ts+" "+x.status)+"</li>").join("")+"</ul>";
}
function setTag(t){tag=tag==t?"":t;render()}
function searchTag(t){$("q").value=t;setView("note");render()}
function pick(id){sel=id;setView("note",true);const n=S.notes.find(x=>x.id==id);if(!n)return;
 const rel=S.notes.filter(x=>x.id!=id).map(x=>({n:x,sh:x.tagi.filter(t=>n.tagi.includes(t))})).filter(r=>r.sh.length).sort((a,b)=>b.sh.length-a.sh.length).slice(0,8);
 $("note").innerHTML=
  `<div class=meta><span class="chip on" onclick="setTag('${esc(n.tag)}')">${esc(n.tag)}</span><span>${esc(n.autor)}</span><span>${esc(n.ocena)}</span><span>${esc(n.data)}</span>${/^https?:/.test(n.url)?`<a href="${esc(n.url)}" target=_blank>oryginał ↗</a>`:""}</div>`+md(body(n))+
  `<h2>Moje</h2><textarea id=my style="height:110px" placeholder="twoja notatka: co z tym zrobić, gdzie wykorzystać">${esc(moje(n))}</textarea><button onclick="saveMy('${esc(n.id)}')">Zapisz</button> <small id=saved style="color:var(--mut)"></small>`;
 $("rail").innerHTML=`<h3>Tagi</h3><div class=chips style="margin:0">${n.tagi.map(t=>`<span class=chip onclick="searchTag('${esc(t)}')">${esc(t)}</span>`).join("")||"<small>brak</small>"}</div>`+
  `<h3>Powiązane (wspólne tagi)</h3>`+(rel.map(r=>`<a class=rel href=# onclick="pick('${esc(r.n.id)}');return false">${esc(r.n.tytul)}<small>${esc(r.sh.join(", "))}</small></a>`).join("")||"<small>brak powiązań</small>");
 render();$("main").scrollTop=0}
async function load(){try{S=await (await fetch("/api/state")).json();render();
 const s2=S.notes.map(n=>n.id+n.tagi.join()).join("|");if(s2!=sig){sig=s2;if(G||view=="map")buildGraph()}}catch(e){}}
async function newNote(){const t=$("nt").value.trim();if(!t)return;$("ntst").textContent="zapisuję…";
 const r=await (await fetch("/api/new",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:t,title:$("nti").value,tagi:$("ntg").value,kategoria:$("ntc").value})})).json();
 $("nt").value=$("nti").value=$("ntg").value="";$("ntc").value="";$("ntst").textContent="zapisano";await load();if(r.id)pick(r.id)}
async function add(){const t=$("urls");
 await fetch("/add",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"urls="+encodeURIComponent(t.value)});t.value="";load()}

/* ---------- mapa (graf notatek i tagów) ---------- */
let G=null,cv=$("map"),cx=cv.getContext("2d"),cam={x:0,y:0,k:1},hov=null,drag=null,pan=null,alpha=1,dirty=true,moved=0,looping=false;
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}
function buildGraph(){
 const cnt={};S.notes.forEach(n=>n.tagi.forEach(t=>cnt[t]=(cnt[t]||0)+1));
 const nodes=[],idx={};
 S.notes.forEach((n,i)=>{const a=i*2.4,r=60+i*8,o={id:"n:"+n.id,type:"note",label:n.tytul,cat:n.tag,r:9,x:Math.cos(a)*r,y:Math.sin(a)*r,vx:0,vy:0,ref:n};idx[o.id]=o;nodes.push(o)});
 Object.keys(cnt).filter(t=>cnt[t]>=2).forEach((t,i)=>{const a=i*1.7+1,o={id:"t:"+t,type:"tag",label:t,r:4+Math.min(cnt[t],8),x:Math.cos(a)*30,y:Math.sin(a)*30,vx:0,vy:0};idx[o.id]=o;nodes.push(o)});
 const edges=[];S.notes.forEach(n=>n.tagi.forEach(t=>{const b=idx["t:"+t];if(b)edges.push([idx["n:"+n.id],b])}));
 const nb=new Map(nodes.map(n=>[n,new Set([n])]));edges.forEach(([a,b])=>{nb.get(a).add(b);nb.get(b).add(a)});
 G={nodes,edges,nb};alpha=1;dirty=true;cam.auto=true;legend();if(view=="map")loop()}
function legend(){const cats=[...new Set(S.notes.map(n=>n.tag).filter(Boolean))];
 $("legend").innerHTML=cats.map(c=>`<span><i style="background:${col(c)}"></i>${esc(c)}</span>`).join("")}
function step(){const N=G.nodes;
 for(let i=0;i<N.length;i++)for(let j=i+1;j<N.length;j++){
  let dx=N[j].x-N[i].x,dy=N[j].y-N[i].y,d2=dx*dx+dy*dy+.01,d=Math.sqrt(d2);
  const f=(N[i].type=="tag"||N[j].type=="tag"?2200:4200)/d2*alpha;dx/=d;dy/=d;
  N[i].vx-=dx*f;N[i].vy-=dy*f;N[j].vx+=dx*f;N[j].vy+=dy*f}
 for(const [a,b] of G.edges){let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-150)*.03*alpha;dx/=d;dy/=d;a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f}
 for(const n of N){n.vx-=n.x*.012*alpha;n.vy-=n.y*.012*alpha;n.vx*=.78;n.vy*=.78;if(n!==drag){n.x+=n.vx;n.y+=n.vy}}
 alpha*=.985}
function size(){const r=cv.getBoundingClientRect(),d=window.devicePixelRatio||1;
 if(cv.width!=Math.round(r.width*d)){cv.width=Math.round(r.width*d);cv.height=Math.round(r.height*d);cx.setTransform(d,0,0,d,0,0);dirty=true}
 return [r.width,r.height]}
function sx(n){return cam.x+n.x*cam.k}function sy(n){return cam.y+n.y*cam.k}
function fit(){const [W,H]=size(),N=G.nodes;
 const x0=Math.min(...N.map(n=>n.x)),x1=Math.max(...N.map(n=>n.x)),y0=Math.min(...N.map(n=>n.y)),y1=Math.max(...N.map(n=>n.y));
 const k=Math.min((W-220)/Math.max(x1-x0,1),(H-180)/Math.max(y1-y0,1),4.5);
 cam.k=k;cam.x=W/2-(x0+x1)/2*k;cam.y=H/2-(y0+y1)/2*k;cam.set=true;dirty=true}
function draw(){const [W,H]=size();if(!cam.set){cam.x=W/2;cam.y=H/2;cam.k=Math.min(W,H)/700;cam.set=true}
 cx.clearRect(0,0,W,H);if(!G)return;
 const fg=css("--fg"),mut=css("--mut"),line=css("--line"),near=hov?G.nb.get(hov):null;
 cx.lineWidth=1;
 for(const [a,b] of G.edges){const on=near&&near.has(a)&&near.has(b);cx.strokeStyle=on?css("--acc"):line;cx.globalAlpha=near&&!on?.25:1;
  cx.beginPath();cx.moveTo(sx(a),sy(a));cx.lineTo(sx(b),sy(b));cx.stroke()}
 cx.globalAlpha=1;cx.textAlign="center";
 for(const n of G.nodes){const dim=near&&!near.has(n),r=Math.max(n.r*cam.k,n.type=="note"?5:3);
  cx.globalAlpha=dim?.25:1;cx.fillStyle=n.type=="note"?col(n.cat):mut;
  cx.beginPath();cx.arc(sx(n),sy(n),r+(n===hov?2:0),0,7);cx.fill();
  if(n.type=="note"||n===hov||cam.k>.9){cx.fillStyle=n.type=="note"?fg:mut;cx.font=(n.type=="note"?"13px":"11px")+" system-ui";
   cx.fillText(n.label.length>34?n.label.slice(0,33)+"…":n.label,sx(n),sy(n)+r+14)}}
 cx.globalAlpha=1;dirty=false}
function loop(){if(looping)return;looping=true;
 (function f(){if(view!="map"){looping=false;return}
  if(alpha>.01&&G){step();dirty=true;if(cam.auto!==false&&alpha<.12){fit();if(alpha<.02)cam.auto=false}}
  if(dirty)draw();requestAnimationFrame(f)})()}
function hit(e){const r=cv.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top;let best=null,bd=1e9;
 for(const n of G.nodes){const d=Math.hypot(sx(n)-px,sy(n)-py);if(d<Math.max(n.r*cam.k,6)+5&&d<bd){best=n;bd=d}}return best}
cv.onmousedown=e=>{if(!G)return;cam.auto=false;const n=hit(e);moved=0;if(n){drag=n;alpha=Math.max(alpha,.25)}else pan={x:e.clientX,y:e.clientY,cx:cam.x,cy:cam.y};cv.style.cursor="grabbing"};
window.onmousemove=e=>{if(view!="map"||!G)return;
 if(drag){const r=cv.getBoundingClientRect();drag.x=(e.clientX-r.left-cam.x)/cam.k;drag.y=(e.clientY-r.top-cam.y)/cam.k;drag.vx=drag.vy=0;moved++;dirty=true}
 else if(pan){cam.x=pan.cx+e.clientX-pan.x;cam.y=pan.cy+e.clientY-pan.y;moved++;dirty=true}
 else{const h=e.target==cv?hit(e):null;if(h!==hov){hov=h;dirty=true;cv.style.cursor=h?"pointer":"grab"}}};
window.onmouseup=()=>{if(drag&&moved<4){if(drag.type=="note")pick(drag.ref.id);else searchTag(drag.label)}drag=null;pan=null;cv.style.cursor=hov?"pointer":"grab"};
cv.onwheel=e=>{e.preventDefault();cam.auto=false;const r=cv.getBoundingClientRect(),px=e.clientX-r.left,py=e.clientY-r.top,k=Math.min(4,Math.max(.25,cam.k*(e.deltaY<0?1.12:.89)));
 cam.x=px-(px-cam.x)*k/cam.k;cam.y=py-(py-cam.y)*k/cam.k;cam.k=k;dirty=true};
window.onresize=()=>{dirty=true};

function setView(v,keep){view=v;$("noteView").style.display=v=="note"?"flex":"none";
 for(const id of ["map","legend","hint"])$(id).hidden=v!="map";
 $("bNote").className=v=="note"?"on":"";$("bMap").className=v=="map"?"on":"";
 if(v=="map"){if(!G)buildGraph();dirty=true;loop()}}
load();setInterval(load,10000);
</script></html>"""


def create_note(d: dict) -> str:
    """Reczna notatka (np. dziennik, tweet). Puste pola uzupelnia Claude, jesli jest klucz API."""
    text = str(d.get("text", "")).strip()
    if not text:
        return ""
    notes = Path(CFG["notes"])
    title, cat = str(d.get("title", "")).strip(), str(d.get("kategoria", "")).strip()
    tags = [t.strip().lower() for t in str(d.get("tagi", "")).split(",") if t.strip()]
    an = {}
    if not (title and cat in ig_grab.CATEGORIES and tags):
        an = ig_grab.analyze(text, ig_grab.known_tags(notes))
    title = title or an.get("tytul") or text.splitlines()[0][:60]
    cat = cat if cat in ig_grab.CATEGORIES else an.get("kategoria", "inne")
    tags = tags or an.get("tagi", [])
    base = re.sub(r"[^\w]+", "-", title.lower())[:40].strip("-") or "notatka"
    slug, i = f"{base}-{date.today()}", 2
    while (notes / f"{slug}.md").exists():
        slug, i = f"{base}-{date.today()}-{i}", i + 1
    fm = {"autor": "ja", "kategoria": cat, "ocena": "moja notatka", "tagi": ", ".join(tags),
          "data_posta": str(date.today()), "pobrano": str(date.today())}
    notes.mkdir(parents=True, exist_ok=True)
    (notes / f"{slug}.md").write_text(ig_grab.note_text(
        fm, {"tytul": title, "streszczenie": an.get("streszczenie", "")}, slug,
        [f"## Tresc\n\n{text}\n"]), encoding="utf-8")
    ig_grab.refresh_index(notes)
    return slug


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body: str, code: int = 200) -> None:
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_json(self, data) -> None:
        b = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/state":
            st = load()
            data = {"notes": [{k: n[k] for k in ("id", "tag", "ocena", "data", "tagi", "tytul", "autor", "url", "text")} for n in read_notes()],
                    "queue": st["queue"], "log": st["log"], "used": today_count(st), "limit": CFG["limit"]}
            b = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self.send(INDEX)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if self.path == "/api/new":
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            return self.send_json({"id": create_note(d)})
        if self.path == "/api/my":
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            f = Path(CFG["notes"]) / (re.sub(r"[^\w-]", "", d.get("id", "")) + ".md")
            if f.exists():
                head = f.read_text(encoding="utf-8").split("\n## Moje")[0].rstrip()
                f.write_text(f"{head}\n\n## Moje\n\n{str(d.get('text', '')).strip()}\n", encoding="utf-8")
            return self.send("ok")
        urls = parse_qs(self.rfile.read(n).decode("utf-8")).get("urls", [""])[0].split()
        with LOCK:
            st = load()
            for u in urls:
                have = (Path(CFG["notes"]) / f"{ig_grab.shortcode(u)}.md").exists()
                if "instagram.com/" in u and u not in st["queue"] and not have:
                    st["queue"].append(u)
            save(st)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookies", default="", help="plik cookies.txt; bez niego yt-dlp uzyje --browser chrome")
    ap.add_argument("--limit", type=int, default=20, help="max postow dziennie")
    ap.add_argument("--pause", type=float, nargs=2, default=[30, 90], metavar=("MIN", "MAX"),
                    help="losowa przerwa miedzy postami (s)")
    ap.add_argument("--out", default="ig_out")
    ap.add_argument("--notes", default="notes")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--fake", action="store_true", help="test bez Instagrama")
    a = ap.parse_args()
    CFG.update(cookies=a.cookies, limit=a.limit, pause=a.pause, out=a.out, notes=a.notes,
               fake=_fake if a.fake else None)
    ig_grab.refresh_index(Path(a.notes))
    threading.Thread(target=worker, daemon=True).start()
    print(f"http://127.0.0.1:{a.port}  (limit {a.limit}/dzien)")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


def _fake(url, out, notes, browser, cookies):
    code = ig_grab.shortcode(url)
    (notes / f"{code}.md").write_text(
        f"---\nurl: {url}\nautor: test\netykieta: test\npobrano: {date.today()}\n---\n\n# {code}\n\n## Opis\n\ntestowa tresc {code}\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
