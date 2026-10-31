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


def today_count(st: dict) -> int:
    return sum(1 for e in st["log"] if e["day"] == str(date.today()))


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
        t = f.read_text(encoding="utf-8")
        meta = dict(re.findall(r"^(\w+): (.*)$", t.split("---")[1], re.M)) if t.startswith("---") else {}
        out.append({"id": f.stem, "tag": meta.get("etykieta", ""), "autor": meta.get("autor", ""),
                    "url": meta.get("url", ""), "text": t})
    return out


def page(q: str = "", tag: str = "") -> str:
    st = load()
    notes = read_notes()
    tags = sorted({n["tag"] for n in notes if n["tag"]})
    shown = [n for n in notes if (not tag or n["tag"] == tag) and q.lower() in n["text"].lower()]
    used, lim = today_count(st), CFG["limit"]
    e = html.escape
    rows = "".join(
        f"<li><a href='/note?id={e(n['id'])}'>{e(n['id'])}</a> "
        f"<b>{e(n['tag'])}</b> <small>{e(n['autor'])}</small></li>" for n in shown)
    tag_links = " ".join(f"<a href='/?tag={e(t)}'>{e(t)}</a>" for t in tags)
    queue = "".join(f"<li>{e(u)}</li>" for u in st["queue"])
    log = "".join(f"<li>{e(x['ts'])} {e(x['status'])} - {e(x['url'])}</li>" for x in st["log"][-8:][::-1])
    return f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=15>
<title>ig-grab</title><style>body{{font:16px system-ui;max-width:820px;margin:2em auto;padding:0 1em}}
textarea{{width:100%;height:5em}}li{{margin:.3em 0}}</style>
<h1>ig-grab</h1>
<p><b>Dzisiaj: {used}/{lim}</b> pobranych (limit chroni konto; po limicie kolejka czeka do jutra).
W kolejce: {len(st['queue'])}.</p>
<form method=post action=/add><textarea name=urls placeholder="wklej linki, jeden pod drugim"></textarea>
<button>Dodaj do kolejki</button></form>
<ul>{queue}</ul><details><summary>Ostatnie</summary><ul>{log}</ul></details>
<h2>Notatki ({len(shown)}/{len(notes)})</h2>
<form><input name=q value="{e(q)}" placeholder="szukaj w tresci"><button>Szukaj</button>
<a href=/>wszystko</a></form><p>{tag_links}</p><ul>{rows}</ul>"""


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
        if u.path == "/note":
            f = Path(CFG["notes"]) / (re.sub(r"[^\w-]", "", q.get("id", [""])[0]) + ".md")
            body = html.escape(f.read_text(encoding="utf-8")) if f.exists() else "brak"
            return self.send(f"<meta charset=utf-8><a href=/>wstecz</a><pre style='white-space:pre-wrap;"
                             f"font:15px system-ui;max-width:820px;margin:1em auto'>{body}</pre>")
        self.send(page(q.get("q", [""])[0], q.get("tag", [""])[0]))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
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
    Path(a.notes).mkdir(exist_ok=True)
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
