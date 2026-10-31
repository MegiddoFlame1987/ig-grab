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
                    "tytul": title.group(1) if title else f.stem,
                    "autor": meta.get("autor", ""), "url": meta.get("url", ""), "text": t})
    return out


INDEX = r"""<!doctype html><html lang=pl><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ig-grab</title>
<style>
:root{--bg:#fff;--bg2:#f5f5f7;--fg:#1d1d1f;--mut:#6e6e73;--line:#e2e2e6;--acc:#7c3aed;--accbg:#ede9fe}
@media(prefers-color-scheme:dark){:root{--bg:#1e1e1e;--bg2:#262626;--fg:#dcddde;--mut:#8b8d90;--line:#363636;--acc:#a78bfa;--accbg:#2e2748}}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,sans-serif;background:var(--bg);color:var(--fg);height:100vh;display:flex}
a{color:var(--acc);text-decoration:none}
#side{width:340px;min-width:260px;background:var(--bg2);border-right:1px solid var(--line);display:flex;flex-direction:column}
#side header{padding:14px;border-bottom:1px solid var(--line)}
#side h1{font-size:17px;margin:0 0 8px}
input,textarea,button{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input{width:100%}textarea{width:100%;height:84px;resize:vertical}
button{cursor:pointer;background:var(--acc);color:#fff;border:0}button:hover{opacity:.9}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{padding:2px 10px;border-radius:99px;background:var(--bg);border:1px solid var(--line);cursor:pointer;font-size:13px;color:var(--mut)}
.chip.on{background:var(--accbg);color:var(--acc);border-color:var(--acc)}
#list{overflow:auto;flex:1}
.item{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer}
.item:hover{background:var(--bg)}.item.on{background:var(--accbg)}
.item b{display:block;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item small{color:var(--mut)}
#main{flex:1;overflow:auto;padding:28px 40px}
#note{max-width:760px;margin:0 auto}
#note h1{font-size:24px}#note h2{font-size:18px;margin-top:1.6em;border-bottom:1px solid var(--line);padding-bottom:4px}
#note h3{font-size:15px;margin-top:1.2em;color:var(--mut)}
#note p{white-space:pre-wrap;margin:.5em 0}#note li{margin:.2em 0}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px;font-size:13px;color:var(--mut)}
.rel{margin-top:2em;padding-top:1em;border-top:1px solid var(--line);font-size:14px}.rel a{display:block;margin:3px 0}
details{margin-top:10px;font-size:13px}details ul{margin:6px 0;padding-left:18px}
.empty{color:var(--mut);margin-top:30vh;text-align:center}
@media(max-width:760px){body{flex-direction:column;height:auto}#side{width:100%}#main{padding:16px}}
</style>
<aside id=side><header>
 <h1>ig-grab <small id=quota style="font-weight:400;color:var(--mut)"></small></h1>
 <details><summary>Dodaj linki do kolejki</summary>
  <textarea id=urls placeholder="wklej linki, jeden pod drugim"></textarea>
  <button onclick=add()>Dodaj do kolejki</button>
  <div id=queue></div></details>
 <input id=q placeholder="szukaj w treści notatek" oninput=render() style="margin-top:10px">
 <div class=chips id=chips></div>
</header><div id=list></div></aside>
<main id=main><div id=note><p class=empty>Wybierz notatkę z listy.</p></div></main>
<script>
let S={notes:[],queue:[],log:[],used:0,limit:0},sel=null,tag="";
const esc=t=>t.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const inl=t=>esc(t).replace(/\*\*(.+?)\*\*/g,"<b>$1</b>").replace(/(https?:\/\/[^\s<]+)/g,'<a href="$1" target=_blank>$1</a>');
function md(t){let h="",ul=false,para=[];
 const fl=()=>{if(para.length){h+="<p>"+para.map(inl).join("\n")+"</p>";para=[]}};
 for(const l of t.split("\n")){
  let m;
  if(m=l.match(/^(#{1,3}) (.*)/)){fl();if(ul){h+="</ul>";ul=false}h+=`<h${m[1].length}>${inl(m[2])}</h${m[1].length}>`}
  else if(m=l.match(/^[-*] (.*)/)){fl();if(!ul){h+="<ul>";ul=true}h+="<li>"+inl(m[1])+"</li>"}
  else if(!l.trim()){fl();if(ul){h+="</ul>";ul=false}}
  else{if(ul){h+="</ul>";ul=false}para.push(l)}}
 fl();if(ul)h+="</ul>";return h}
function body(n){return n.text.replace(/^---[\s\S]*?---\s*/,"").split(/\n## Moje/)[0]}
function moje(n){const p=n.text.split(/\n## Moje/);return p.length>1?p[1].trim():""}
async function saveMy(id){const t=document.getElementById("my");
 await fetch("/api/my",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,text:t.value})});
 document.getElementById("saved").textContent="zapisano";await load();sel=id}
function render(){
 const q=document.getElementById("q").value.toLowerCase();
 const tags=[...new Set(S.notes.map(n=>n.tag).filter(Boolean))].sort();
 document.getElementById("chips").innerHTML=tags.map(t=>`<span class="chip ${t==tag?"on":""}" onclick="setTag('${esc(t)}')">${esc(t)}</span>`).join("");
 const shown=S.notes.filter(n=>(!tag||n.tag==tag)&&n.text.toLowerCase().includes(q));
 document.getElementById("list").innerHTML=shown.map(n=>{
  return `<div class="item ${n.id==sel?"on":""}" onclick="pick('${esc(n.id)}')"><b>${esc(n.tytul.slice(0,80))}</b><small>${esc([n.tag,n.ocena,n.autor].filter(Boolean).join(" · "))}</small></div>`}).join("")||'<p class=empty style="margin:20px">Brak notatek.</p>';
 document.getElementById("quota").textContent=`dziś ${S.used}/${S.limit} · kolejka ${S.queue.length}`;
 document.getElementById("queue").innerHTML="<ul>"+S.queue.map(u=>"<li>"+esc(u)+"</li>").join("")+"</ul><ul>"+S.log.slice(-6).reverse().map(x=>"<li>"+esc(x.ts+" "+x.status)+"</li>").join("")+"</ul>";
}
function setTag(t){tag=tag==t?"":t;render()}
function pick(id){sel=id;const n=S.notes.find(x=>x.id==id);if(!n)return;
 const rel=S.notes.filter(x=>x.tag==n.tag&&x.id!=id).slice(0,8);
 document.getElementById("note").innerHTML=
  `<div class=meta><span class="chip on" onclick="setTag('${esc(n.tag)}')">${esc(n.tag)}</span><span>${esc(n.autor)}</span><span>${esc(n.ocena)}</span><span>${esc(n.data)}</span><a href="${esc(n.url)}" target=_blank>oryginał ↗</a></div>`+md(body(n))+
  `<h2>Moje</h2><textarea id=my style="height:110px" placeholder="twoja notatka: co z tym zrobić, gdzie wykorzystać">${esc(moje(n))}</textarea><button onclick="saveMy('${esc(n.id)}')">Zapisz</button> <small id=saved style="color:var(--mut)"></small>`+
  (rel.length?`<div class=rel><b>Podobne (${esc(n.tag)})</b>`+rel.map(r=>`<a href=# onclick="pick('${esc(r.id)}');return false">${esc(r.tytul)}</a>`).join("")+"</div>":"");
 render();document.getElementById("main").scrollTop=0}
async function load(){try{S=await (await fetch("/api/state")).json();render()}catch(e){}}
async function add(){const t=document.getElementById("urls");
 await fetch("/add",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"urls="+encodeURIComponent(t.value)});t.value="";load()}
load();setInterval(load,10000);
</script></html>"""


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

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/state":
            st = load()
            data = {"notes": [{k: n[k] for k in ("id", "tag", "ocena", "data", "tytul", "autor", "url", "text")} for n in read_notes()],
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
