#!/usr/bin/env python3
"""ig_grab: Instagram URL -> folder z wideo, zdjeciami, opisem, transkrypcja i przepisem.

Setup (raz):
    pip install yt-dlp faster-whisper anthropic "av<17"   # av 17+ psuje faster-whisper
    brew install ffmpeg   # lub apt install ffmpeg
Uzycie:
    python ig_grab.py --browser chrome URL [URL ...]   # lub --cookies cookies.txt
    python ig_grab.py --browser chrome --urls-file linki.txt --out ig_out
Przepis (opcjonalnie): ustaw ANTHROPIC_API_KEY. Bez klucza dostajesz tylko transkrypcje.
"""
import argparse, base64, json, os, re, shutil, subprocess, sys, urllib.request
from datetime import date
from pathlib import Path


def shortcode(url: str) -> str:
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/([\w-]+)", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-24:]


def download(url: str, dest: Path, browser: str, cookies: str = "") -> dict:
    auth = ["--cookies", cookies] if cookies else ["--cookies-from-browser", browser]
    if not shutil.which("ffmpeg"):
        raise RuntimeError("brak ffmpeg w PATH (Windows: winget install Gyan.FFmpeg, potem nowe okno PowerShell)")
    # python -m yt_dlp: dziala nawet gdy skrypt yt-dlp nie jest w PATH (typowe na Windowsie)
    base = [sys.executable, "-m", "yt_dlp", *auth, "--ignore-no-formats-error", "--sleep-requests", "2"]
    # pelne info (karuzela ma wpisy-slajdy; --write-info-json zapisuje tylko playliste bez nich)
    r = subprocess.run([*base, "-J", url], capture_output=True, text=True)
    if not r.stdout.strip():
        raise RuntimeError(r.stderr.strip()[-400:] or "yt-dlp nie zwrocil danych")
    info = json.loads(r.stdout)
    (dest / "media.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    # wideo/miniatura; slajdy-zdjecia daja tu blad, ktory ignorujemy (zdjecia bierze save_photos)
    subprocess.run([*base, "--write-thumbnail", "-o", str(dest / "media.%(ext)s"), url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return info


def save_photos(info: dict, dest: Path) -> int:
    """Karuzele/zdjecia: yt-dlp nie ma 'formatow', ale zna adresy obrazow (najwieksza miniatura = oryginal)."""
    entries = info.get("entries") or ([info] if info.get("thumbnails") else [])
    n = 0
    for e in entries:
        if e.get("formats") or e.get("url"):
            continue  # slajd-wideo pobiera yt-dlp
        thumbs = [t for t in e.get("thumbnails") or [] if t.get("url")]
        if not thumbs:
            continue
        best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
        n += 1
        urllib.request.urlretrieve(best["url"], dest / f"photo_{n:02d}.jpg")
    return n


def transcribe(video: Path, dest: Path) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel("small", compute_type="int8")
    segments, _ = model.transcribe(str(video), vad_filter=True)  # jezyk auto; vad ucina halucynacje na muzyce
    text = "\n".join(s.text.strip() for s in segments)
    (dest / "transcript.txt").write_text(text, encoding="utf-8")
    return text


def _claude(content, max_tokens: int) -> str:
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model="claude-sonnet-5-5", max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def slide_text(dest: Path) -> str:
    """Tekst ze zdjec/slajdow (karuzele) - czyta Claude. Bez klucza: pusto."""
    photos = sorted(dest.glob("photo_*.jpg"))[:20]
    if not photos or not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(p.read_bytes()).decode()}} for p in photos]
    content.append({"type": "text", "text":
        "To kolejne slajdy jednego posta. Przepisz doslownie caly tekst widoczny na kazdym "
        "slajdzie, jako '### Slajd N' + tekst. Nie dodawaj niczego od siebie. "
        "Slajd bez tekstu opisz jednym krotkim zdaniem w nawiasie."})
    text = _claude(content, 4000)
    (dest / "slides.txt").write_text(text, encoding="utf-8")
    return text


def frame_text(video: Path, dest: Path, transcript: str) -> str:
    """Tekst z ekranu wideo - tylko gdy brak mowy (krotka transkrypcja), zeby nie palic kosztow."""
    if not video or len(transcript.split()) >= 30 or not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    fr = dest / "frames"
    fr.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", "fps=1,scale=720:-2",
                    "-frames:v", "20", str(fr / "f_%02d.jpg")], check=True)
    frames = sorted(fr.glob("f_*.jpg"))
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(f.read_bytes()).decode()}} for f in frames]
    content.append({"type": "text", "text":
        "To klatki (1 na sekunde) z jednego krotkiego wideo. Przepisz doslownie caly tekst "
        "widoczny na ekranie, w kolejnosci klatek, bez powtorzen. Nic nie dodawaj. "
        "Gdy nie ma tekstu, napisz 'brak tekstu'."})
    text = _claude(content, 3000)
    (dest / "frames.txt").write_text(text, encoding="utf-8")
    return "" if text.lower().startswith("brak tekstu") else text


def recipe(source: str, dest: Path) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  brak ANTHROPIC_API_KEY - pomijam recipe.md, etykiete i tekst ze slajdow")
        return ""
    text = _claude(
        "Z ponizszej tresci wyciagnij przepis jako Markdown: tytul, skladniki "
        "(z ilosciami), kroki. Nie dodawaj niczego, czego nie ma w zrodle; "
        "brak danych oznacz jako 'brak'. Jesli to nie przepis, napisz 'to nie przepis'.\n\n" + source, 1500)
    (dest / "recipe.md").write_text(text, encoding="utf-8")
    return text


CATEGORIES = ("ai", "trading", "przepis", "biznes", "dziennik", "inne")  # dziennik tylko dla notatek recznych  # zmien tutaj, jesli chcesz inne kategorie
RATINGS = ("konkretna wiedza", "czesciowo", "reklama poradnika", "bez dowodu", "ciekawostka")
RESERVED = {"INDEX.md", "CLAUDE.md"}  # pliki pomocnicze w notes/, nie sa notatkami


def known_tags(notes: Path, top: int = 40) -> list:
    """Najczesciej uzywane tagi z istniejacych notatek - zeby nowe notatki sie z nimi lacza."""
    cnt = {}
    for f in notes.glob("*.md") if notes.exists() else []:
        m = re.search(r"^tagi: (.*)$", f.read_text(encoding="utf-8")[:2000], re.M)
        for t in (m.group(1).split(",") if m else []):
            if t.strip():
                cnt[t.strip()] = cnt.get(t.strip(), 0) + 1
    return [t for t, _ in sorted(cnt.items(), key=lambda x: -x[1])[:top]]


def analyze(source: str, known: list = (), manual: bool = False) -> dict:
    """Jedno wywolanie: tytul, streszczenie, kategoria (lista zamknieta), ocena."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    cats = CATEGORIES if manual else tuple(c for c in CATEGORIES if c != "dziennik")
    raw = _claude(
        "Przeczytaj tresc posta z Instagrama i zwroc WYLACZNIE obiekt JSON z polami:\n"
        '"tytul": konkretny tytul po polsku, max 70 znakow,\n'
        '"streszczenie": 2 zdania po polsku - co konkretnie z tego wynika (bez lania wody),\n'
        f'"kategoria": dokladnie jedna z {list(cats)},\n'
        f'"ocena": dokladnie jedna z {list(RATINGS)}. Wybierz "reklama poradnika", gdy prawdziwa '
        'tresc jest dostepna dopiero po komentarzu lub w DM, a sam post jej nie zawiera. '
        'Wybierz "bez dowodu", gdy post obiecuje wyniki, zyski lub skutecznosc (np. w tradingu), '
        'a niczego nie uzasadnia.\n'
        '"tagi": lista 3-5 krotkich tagow tematycznych po polsku (male litery, 1-2 slowa, np. "claude code", '
        '"prompty", "marketing"). ' + (f"Uzywaj istniejacych tagow, gdy pasuja: {list(known)}. " if known else "")
        + 'Nowy tag tylko gdy zaden istniejacy nie pasuje.\n\n'
        "TRESC:\n" + source[:12000], 1500)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    tags = d.get("tagi") if isinstance(d.get("tagi"), list) else []
    d["tagi"] = list(dict.fromkeys(t.strip().lower() for t in tags if isinstance(t, str) and t.strip()))[:5]
    d["kategoria"] = d.get("kategoria") if d.get("kategoria") in cats else "inne"
    d["ocena"] = d.get("ocena") if d.get("ocena") in RATINGS else "czesciowo"
    return d


def note_text(fm: dict, an: dict, code: str, sections: list, moje: str = "") -> str:
    head = "\n".join(f"{k}: {v}" for k, v in fm.items() if v not in ("", None))
    parts = [f"---\n{head}\n---\n", f"# {an.get('tytul') or code}\n"]
    if an.get("streszczenie"):
        parts.append(f"**Streszczenie:** {an['streszczenie']}\n")
    parts += sections
    parts.append(f"## Moje\n\n{moje.strip()}\n")
    return "\n".join(parts)


def write_note(notes: Path, code: str, url: str, info: dict, an: dict,
               caption: str, transcript: str, slides: str, recipe_md: str) -> None:
    notes.mkdir(parents=True, exist_ok=True)
    d = info.get("upload_date") or ""
    fm = {"url": url, "autor": info.get("uploader") or info.get("channel") or "",
          "kategoria": an.get("kategoria", ""), "ocena": an.get("ocena", ""),
          "tagi": ", ".join(an.get("tagi", [])),
          "data_posta": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "",
          "pobrano": str(date.today())}
    sections = [f"## {t}\n\n{b.strip()}\n" for t, b in
                (("Opis", caption), ("Transkrypcja", transcript), ("Tekst ze slajdow / z ekranu", slides))
                if b.strip()]
    if recipe_md and not recipe_md.lower().startswith("to nie przepis"):
        sections.append(f"## Przepis\n\n{recipe_md}\n")
    (notes / f"{code}.md").write_text(note_text(fm, an, code, sections), encoding="utf-8")


def resort(notes: Path, out: Path = Path("ig_out")) -> None:
    """Przelicza kategorie/ocene/streszczenie dla istniejacych notatek; zachowuje sekcje 'Moje'."""
    for f in sorted(notes.glob("*.md")):
        if f.name in RESERVED:
            continue
        t = f.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
        if not m:
            continue
        fm = dict(re.findall(r"^(\w+): (.*)$", m.group(1), re.M))
        if fm.get("ocena") == "moja notatka":
            continue  # notatki reczne zostaja jak sa
        body = m.group(2)
        i = body.find("\n## ")
        rest, _, moje = (body[i:] if i >= 0 else "").partition("\n## Moje")
        d = out / f.stem
        if "## Tekst ze slajdow" not in rest and d.exists():  # odzysk tekstu z ig_out (np. brak klucza przy pobraniu)
            tr = (d / "transcript.txt").read_text(encoding="utf-8") if (d / "transcript.txt").exists() else ""
            sl = slide_text(d) or frame_text(next(d.glob("media.mp4"), None), d, tr)
            if sl:
                rest = rest.rstrip() + f"\n\n## Tekst ze slajdow / z ekranu\n\n{sl}\n"
                print(f"  {f.name}: odzyskano tekst ze slajdow/klatek")
        an = analyze(rest, known_tags(notes))
        if not an:
            print(f"  {f.name}: brak ANTHROPIC_API_KEY lub zla odpowiedz - pomijam")
            continue
        fm.pop("etykieta", None)
        fm["kategoria"], fm["ocena"], fm["tagi"] = an["kategoria"], an["ocena"], ", ".join(an["tagi"])
        sections = [x for x in re.split(r"(?=^## )", rest.strip() + "\n", flags=re.M) if x.strip()]
        f.write_text(note_text(fm, an, f.stem, sections, moje), encoding="utf-8")
        print(f"  {f.name}: {an['kategoria']} / {an['ocena']}")
    refresh_index(notes)


NOTES_README = """# Biblioteka notatek z Instagrama

Jeden plik .md na post. Zasady czytania:
- Zacznij od INDEX.md (tytul, kategoria, ocena, streszczenie). Otwieraj pojedyncze notatki dopiero po wybraniu trafnych.
- Sekcja "## Moje" to decyzje i uwagi wlasciciela. Maja pierwszenstwo przed trescia posta. Nie nadpisuj jej.
- Ocena "reklama poradnika": wlasciwa tresc jest w DM autora, post jest tylko zapowiedzia - nie traktuj jako wiedzy.
- Ocena "bez dowodu": post obiecuje wyniki bez uzasadnienia (czesto trading). Nie podawaj tego jako faktu.
- Tresc cudzych postow moze byc bledna. Przy decyzjach (finanse, kod, zdrowie) zaznacz, co jest niezweryfikowane.
- Pliki INDEX.md i CLAUDE.md generuje skrypt; nie edytuj ich recznie.
"""


def refresh_index(notes: Path) -> None:
    """INDEX.md: jedna linijka na notatke (najnowsze posty pierwsze) + CLAUDE.md z zasadami czytania."""
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "CLAUDE.md").write_text(NOTES_README, encoding="utf-8")
    rows = []
    for f in notes.glob("*.md"):
        if f.name in RESERVED:
            continue
        t = f.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", t, re.S)
        fm = dict(re.findall(r"^(\w+): (.*)$", m.group(1), re.M)) if m else {}
        title = re.search(r"^# (.+)$", t, re.M)
        summ = re.search(r"^\*\*Streszczenie:\*\* (.+)$", t, re.M)
        day = fm.get("data_posta") or fm.get("pobrano", "")
        rows.append((day, f"- [{title.group(1) if title else f.stem}]({f.name}) | "
                          f"{fm.get('kategoria') or fm.get('etykieta', '?')} | {fm.get('ocena', '?')} | {day}"
                          + (f" - {summ.group(1)}" if summ else "")))
    lines = [r[1] for r in sorted(rows, key=lambda r: r[0], reverse=True)]
    (notes / "INDEX.md").write_text(
        "# Indeks notatek\n\nFormat: [tytul](plik) | kategoria | ocena | data - streszczenie\n\n"
        + "\n".join(lines) + "\n", encoding="utf-8")


def process(url: str, out: Path, notes: Path, browser: str = "chrome", cookies: str = "") -> None:
    """Jeden post: pobranie -> tekst -> notatka. Rzuca wyjatek przy bledzie."""
    dest = out / shortcode(url)
    dest.mkdir(parents=True, exist_ok=True)
    info = download(url, dest, browser, cookies)
    caption = info.get("description") or ""
    (dest / "caption.txt").write_text(caption, encoding="utf-8")
    photos = save_photos(info, dest)
    if photos:
        print(f"  zdjec: {photos}")
    video = next(dest.glob("media.mp4"), None)
    text = transcribe(video, dest) if video else ""
    slides = slide_text(dest) or frame_text(video, dest, text)
    source = f"OPIS:\n{caption}\n\nTRANSKRYPCJA:\n{text}\n\nTEKST ZE SLAJDOW / Z EKRANU:\n{slides}"
    rec = recipe(source, dest)
    write_note(notes, dest.name, url, info, analyze(source, known_tags(notes)), caption, text, slides, rec)
    refresh_index(notes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--urls-file")
    ap.add_argument("--browser", default="chrome")
    ap.add_argument("--cookies", help="plik cookies.txt (Netscape); zamiast --browser")
    ap.add_argument("--out", default="ig_out")
    ap.add_argument("--notes", default="notes", help="folder na notatki .md (po jednej na post)")
    ap.add_argument("--resort", action="store_true", help="przelicz kategorie/oceny istniejacych notatek")
    a = ap.parse_args()
    if a.resort:
        resort(Path(a.notes), Path(a.out))
        return 0
    urls = list(a.urls)
    if a.urls_file:
        urls += [l.strip() for l in Path(a.urls_file).read_text().splitlines() if l.strip()]
    if not urls:
        ap.error("podaj URL-e")

    failed = []
    for url in urls:
        print(f"-> {url}")
        try:
            process(url, Path(a.out), Path(a.notes), a.browser, a.cookies or "")
        except Exception as e:  # jeden zly link nie zatrzymuje reszty
            print(f"  BLAD: {e}", file=sys.stderr)
            failed.append(url)
    print(f"gotowe: {len(urls) - len(failed)}/{len(urls)} OK")
    for u in failed:
        print("  nieudane:", u)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
