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
import argparse, json, os, re, subprocess, sys
from pathlib import Path


def shortcode(url: str) -> str:
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/([\w-]+)", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-24:]


def download(url: str, dest: Path, browser: str, cookies: str = "") -> None:
    auth = ["--cookies", cookies] if cookies else ["--cookies-from-browser", browser]
    subprocess.run(
        ["yt-dlp", *auth, "--write-info-json",
         "--write-thumbnail", "--sleep-requests", "2",
         "-o", str(dest / "media.%(ext)s"), url],
        check=True,
    )


def transcribe(video: Path, dest: Path) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel("small", compute_type="int8")
    segments, _ = model.transcribe(str(video))  # jezyk wykrywany automatycznie
    text = "\n".join(s.text.strip() for s in segments)
    (dest / "transcript.txt").write_text(text, encoding="utf-8")
    return text


def recipe(caption: str, transcript: str, dest: Path) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  brak ANTHROPIC_API_KEY - pomijam recipe.md")
        return
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model="claude-sonnet-5-5", max_tokens=1500,
        messages=[{"role": "user", "content":
            "Z opisu i transkrypcji wyciagnij przepis jako Markdown: tytul, skladniki "
            "(z ilosciami), kroki. Nie dodawaj niczego, czego nie ma w zrodle; "
            "brak danych oznacz jako 'brak'. Jesli to nie przepis, napisz 'to nie przepis'.\n\n"
            f"OPIS:\n{caption}\n\nTRANSKRYPCJA:\n{transcript}"}],
    )
    (dest / "recipe.md").write_text(msg.content[0].text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--urls-file")
    ap.add_argument("--browser", default="chrome")
    ap.add_argument("--cookies", help="plik cookies.txt (Netscape); zamiast --browser")
    ap.add_argument("--out", default="ig_out")
    a = ap.parse_args()
    urls = list(a.urls)
    if a.urls_file:
        urls += [l.strip() for l in Path(a.urls_file).read_text().splitlines() if l.strip()]
    if not urls:
        ap.error("podaj URL-e")

    failed = []
    for url in urls:
        dest = Path(a.out) / shortcode(url)
        dest.mkdir(parents=True, exist_ok=True)
        print(f"-> {url}")
        try:
            download(url, dest, a.browser, a.cookies)
            info = json.loads(next(dest.glob("media.info.json")).read_text(encoding="utf-8"))
            caption = info.get("description") or ""
            (dest / "caption.txt").write_text(caption, encoding="utf-8")
            video = next(dest.glob("media.mp4"), None)
            text = transcribe(video, dest) if video else ""
            recipe(caption, text, dest)
        except Exception as e:  # jeden zly link nie zatrzymuje reszty
            print(f"  BLAD: {e}", file=sys.stderr)
            failed.append(url)
    print(f"gotowe: {len(urls) - len(failed)}/{len(urls)} OK")
    for u in failed:
        print("  nieudane:", u)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
