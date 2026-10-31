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


def label(source: str) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ""
    return _claude(
        "Nadaj temu postowi jedna etykiete tematyczna: 1-3 slowa po polsku, malymi literami "
        "(np. 'przepis', 'trening', 'narzedzia ai'). Odpowiedz samą etykieta.\n\n" + source[:6000], 300
    ).strip(" .\n\"'").lower()


def write_note(notes: Path, code: str, url: str, info: dict, tag: str,
               caption: str, transcript: str, slides: str, recipe_md: str) -> None:
    notes.mkdir(parents=True, exist_ok=True)
    parts = [f"---\nurl: {url}\nautor: {info.get('uploader') or info.get('channel') or ''}\n"
             f"etykieta: {tag}\npobrano: {date.today()}\n---\n", f"# {code}\n"]
    for title, body in (("Opis", caption), ("Transkrypcja", transcript),
                        ("Tekst ze slajdow / z ekranu", slides)):
        if body.strip():
            parts.append(f"## {title}\n\n{body.strip()}\n")
    if recipe_md and not recipe_md.lower().startswith("to nie przepis"):
        parts.append(f"## Przepis\n\n{recipe_md}\n")
    (notes / f"{code}.md").write_text("\n".join(parts), encoding="utf-8")


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
    write_note(notes, dest.name, url, info, label(source), caption, text, slides, rec)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--urls-file")
    ap.add_argument("--browser", default="chrome")
    ap.add_argument("--cookies", help="plik cookies.txt (Netscape); zamiast --browser")
    ap.add_argument("--out", default="ig_out")
    ap.add_argument("--notes", default="notes", help="folder na notatki .md (po jednej na post)")
    a = ap.parse_args()
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
