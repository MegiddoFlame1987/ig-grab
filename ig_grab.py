#!/usr/bin/env python3
"""ig_grab: link (Instagram, YouTube, strona WWW, PDF) -> folder ze zrodlem + notatka (tytul, kategoria, tagi, odcisk SHA-256).

Setup (raz):
    pip install yt-dlp faster-whisper anthropic "av<17" trafilatura pypdf pillow   # av 17+ psuje faster-whisper
    brew install ffmpeg   # lub apt install ffmpeg
Uzycie:
    python ig_grab.py --browser chrome URL [URL ...]   # lub --cookies cookies.txt
    python ig_grab.py --browser chrome --urls-file linki.txt --out ig_out
Przepis (opcjonalnie): ustaw ANTHROPIC_API_KEY. Bez klucza dostajesz tylko transkrypcje.
"""
import argparse, base64, hashlib, io, ipaddress, json, os, re, shutil, socket, subprocess, sys, urllib.request
from urllib.parse import parse_qs, urlparse
from datetime import date
from pathlib import Path


ALLOW_PRIVATE = False  # tylko dla testow: pozwala pobierac z localhost


def kind(url: str) -> str:
    if url.startswith("file:"):
        return "plik"
    h = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    if h.endswith("instagram.com"):
        return "instagram"
    if h.endswith("youtube.com") or h == "youtu.be" or h.endswith("youtube-nocookie.com"):
        return "youtube"
    return "web"


def shortcode(url: str) -> str:
    k = kind(url)
    if k == "plik":  # "file:<sha256>.<ext>"
        return "img-" + url[5:17]
    if k == "youtube":
        u = urlparse(url)
        vid = (parse_qs(u.query).get("v") or [u.path.rstrip("/").split("/")[-1]])[0]
        return "yt-" + re.sub(r"[^\w-]", "", vid)
    if k == "web":
        return "web-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reels?|p|tv)/([\w-]+)", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-24:]


def write_source(dest: Path, source: str) -> str:
    """Zapisuje tresc zrodlowa (to, co czytal Claude) i zwraca jej SHA-256. Bajty, nie tekst: bez zmian konca linii."""
    b = source.encode("utf-8")
    (dest / "source.txt").write_bytes(b)
    return hashlib.sha256(b).hexdigest()


def check_public(url: str) -> None:
    """Odrzuca adresy lokalne/prywatne (serwer nie ma pobierac wnetrza Twojej sieci)."""
    if ALLOW_PRIVATE:
        return
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RuntimeError("tylko adresy http/https")
    for info in socket.getaddrinfo(u.hostname, None):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise RuntimeError(f"adres lokalny lub prywatny odrzucony: {u.hostname}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


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


USAGE_FILE = Path("usage.jsonl")


def _claude(content, max_tokens: int, label: str = "") -> str:
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model="claude-sonnet-5-5", max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}])
    try:  # co zuzywa API: kazde wywolanie zapisane z etykieta i liczba tokenow
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"day": str(date.today()), "label": label, "in": msg.usage.input_tokens,
                                "out": msg.usage.output_tokens}) + "\n")
    except Exception:
        pass
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
    text = _claude(content, 4000, "slajdy (obrazy)")
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
    text = _claude(content, 3000, "klatki wideo")
    (dest / "frames.txt").write_text(text, encoding="utf-8")
    return "" if text.lower().startswith("brak tekstu") else text


def recipe(source: str, dest: Path) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  brak ANTHROPIC_API_KEY - pomijam recipe.md, etykiete i tekst ze slajdow")
        return ""
    text = _claude(
        "Z ponizszej tresci wyciagnij przepis jako Markdown: tytul, skladniki "
        "(z ilosciami), kroki. Nie dodawaj niczego, czego nie ma w zrodle; "
        "brak danych oznacz jako 'brak'. Jesli to nie przepis, napisz 'to nie przepis'.\n\n" + source, 1500, "przepis")
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


def analyze_content(blocks: list, known: list = (), manual: bool = False, image: bool = False) -> dict:
    """Jedno wywolanie: tytul, streszczenie, kategoria (lista zamknieta), ocena, tagi. Dla obrazu dodatkowo tekst i zrodlo."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    cats = CATEGORIES if manual else tuple(c for c in CATEGORIES if c != "dziennik")
    prompt = {"type": "text", "text": (
        ("Przeczytaj ten zrzut ekranu (np. post z social media, artykul, czat, notatka)" if image
         else "Przeczytaj tresc posta z Instagrama") + " i zwroc WYLACZNIE obiekt JSON z polami:\n"
        + ('"tekst": doslowny zapis calego tekstu widocznego na obrazie, w kolejnosci czytania, bez komentarzy,\n'
           '"zrodlo": autor, konto lub serwis widoczny na zrzucie (pusty napis, gdy nie widac),\n' if image else "")
        + '"tytul": konkretny tytul po polsku, max 70 znakow,\n'
        '"streszczenie": 2 zdania po polsku - co konkretnie z tego wynika (bez lania wody),\n'
        f'"kategoria": dokladnie jedna z {list(cats)},\n'
        f'"ocena": dokladnie jedna z {list(RATINGS)}. Wybierz "reklama poradnika", gdy prawdziwa '
        'tresc jest dostepna dopiero po komentarzu lub w DM, a sam post jej nie zawiera. '
        'Wybierz "bez dowodu", gdy post obiecuje wyniki, zyski lub skutecznosc (np. w tradingu), '
        'a niczego nie uzasadnia.\n'
        '"tagi": lista 3-5 krotkich tagow tematycznych po polsku (male litery, 1-2 slowa, np. "claude code", '
        '"prompty", "marketing"). ' + (f"Uzywaj istniejacych tagow, gdy pasuja: {list(known)}. " if known else "")
        + "Nowy tag tylko gdy zaden istniejacy nie pasuje.\n")}
    content = [*blocks, prompt] if image else [prompt, *blocks]
    d = None
    for _ in range(2):  # dlugi tekst bywa zle ucieczkowany w JSON - jedna ponowna proba
        raw = _claude(content, 4000 if image else 1500, "zrzut ekranu (obraz+analiza)" if image else "analiza notatki")
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group(0)) if m else None
        except ValueError:
            d = None
        if isinstance(d, dict):
            break
    if not isinstance(d, dict):
        return {}
    tags = d.get("tagi") if isinstance(d.get("tagi"), list) else []
    d["tagi"] = list(dict.fromkeys(t.strip().lower() for t in tags if isinstance(t, str) and t.strip()))[:5]
    d["kategoria"] = d.get("kategoria") if d.get("kategoria") in cats else "inne"
    d["ocena"] = d.get("ocena") if d.get("ocena") in RATINGS else "czesciowo"
    return d


def analyze(source: str, known: list = (), manual: bool = False) -> dict:
    return analyze_content([{"type": "text", "text": "TRESC:\n" + source[:12000]}], known, manual)


def note_text(fm: dict, an: dict, code: str, sections: list, moje: str = "") -> str:
    head = "\n".join(f"{k}: {v}" for k, v in fm.items() if v not in ("", None))
    parts = [f"---\n{head}\n---\n", f"# {an.get('tytul') or code}\n"]
    if an.get("streszczenie"):
        parts.append(f"**Streszczenie:** {an['streszczenie']}\n")
    parts += sections
    parts.append(f"## Moje\n\n{moje.strip()}\n")
    return "\n".join(parts)


def write_note(notes: Path, code: str, url: str, info: dict, an: dict,
               caption: str, transcript: str, slides: str, recipe_md: str,
               typ: str = "instagram", sha: str = "", extra: tuple = (), sha_raw: str = "") -> None:
    notes.mkdir(parents=True, exist_ok=True)
    d = info.get("upload_date") or ""
    fm = {"url": url, "autor": info.get("uploader") or info.get("channel") or "",
          "kategoria": an.get("kategoria", ""), "ocena": an.get("ocena", ""),
          "tagi": ", ".join(an.get("tagi", [])), "typ": typ, "sha256": sha, "sha256_pliku": sha_raw, "plik": info.get("plik", ""),
          "data_posta": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else "",
          "pobrano": str(date.today())}
    sections = [f"## {t}\n\n{b.strip()}\n" for t, b in
                (("Opis", caption), ("Transkrypcja", transcript), ("Tekst ze slajdow / z ekranu", slides), *extra)
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
    sha = write_source(dest, source)
    an = analyze(source, known_tags(notes))
    rec = recipe(source, dest) if an.get("kategoria") == "przepis" else ""  # osobne wywolanie tylko dla przepisow
    write_note(notes, dest.name, url, info, an, caption, text, slides, rec, typ="instagram", sha=sha)
    refresh_index(notes)


def vtt_to_text(p: Path) -> str:
    out, last = [], ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line != last:  # napisy automatyczne powtarzaja linie miedzy kadrami
            out.append(line)
            last = line
    return "\n".join(out)


def process_youtube(url: str, out: Path, notes: Path) -> None:
    """YouTube: metadane + napisy (bez pobierania wideo; strona osadza odtwarzacz). Brak napisow -> Whisper (<=30 min)."""
    dest = out / shortcode(url)
    dest.mkdir(parents=True, exist_ok=True)
    base = [sys.executable, "-m", "yt_dlp", "--no-playlist"]
    r = subprocess.run([*base, "-J", url], capture_output=True, text=True)
    if not r.stdout.strip():
        raise RuntimeError(r.stderr.strip()[-400:] or "yt-dlp nie zwrocil danych")
    info = json.loads(r.stdout)
    if info.get("entries"):
        raise RuntimeError("playlisty nieobslugiwane - podaj link do pojedynczego filmu")
    (dest / "media.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    subprocess.run([*base, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs",
                    "pl,pl-orig,en,en-orig", "--sub-format", "vtt", "-o", str(dest / "subs.%(ext)s"), url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subs = {p.name.split(".")[1]: p for p in dest.glob("subs.*.vtt")}
    text = vtt_to_text(next((subs[l] for l in ("pl", "pl-orig", "en", "en-orig") if l in subs), None)) if subs else ""
    if not text and (info.get("duration") or 0) <= 1800:
        if not shutil.which("ffmpeg"):
            raise RuntimeError("brak ffmpeg w PATH")
        subprocess.run([*base, "-f", "bestaudio/best", "-x", "--audio-format", "mp3", "-o",
                        str(dest / "audio.%(ext)s"), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio = next(dest.glob("audio.*"), None)
        text = transcribe(audio, dest) if audio else ""
    if not text:
        raise RuntimeError("brak napisow, a film jest za dlugi na transkrypcje (>30 min) lub niedostepny")
    if info.get("thumbnail"):
        try:
            urllib.request.urlretrieve(info["thumbnail"], dest / "media.jpg")
        except Exception:
            pass
    caption = (info.get("description") or "")[:4000]
    source = (f"TYTUL:\n{info.get('title', '')}\nKANAL:\n{info.get('channel', '')}\n\n"
              f"OPIS:\n{caption}\n\nTRANSKRYPCJA:\n{text}")
    sha = write_source(dest, source)
    an = analyze(source, known_tags(notes))
    an.setdefault("tytul", info.get("title") or dest.name)
    write_note(notes, dest.name, info.get("webpage_url") or url, info, an, caption, text[:60000], "", "",
               typ="youtube", sha=sha)
    refresh_index(notes)


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages[:80])


def process_web(url: str, out: Path, notes: Path) -> None:
    """Strona WWW lub PDF: surowy plik + wyciagniety tekst (trafilatura / pypdf)."""
    check_public(url)
    dest = out / shortcode(url)
    dest.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ig-grab)",
                                               "Accept-Language": "pl,en;q=0.8"})
    with _opener.open(req, timeout=30) as r:
        ctype, charset, raw = r.headers.get("Content-Type", ""), r.headers.get_content_charset() or "utf-8", r.read(8_000_000)
    host = urlparse(url).hostname or ""
    title, meta_date, site = Path(urlparse(url).path).name or host, "", host
    if "pdf" in ctype.lower() or urlparse(url).path.lower().endswith(".pdf"):
        (dest / "source.pdf").write_bytes(raw)
        text = pdf_text(raw)
    else:
        (dest / "source.html").write_bytes(raw)
        import trafilatura
        html = raw.decode(charset, errors="replace")
        text = trafilatura.extract(html, include_tables=True) or ""
        md = trafilatura.extract_metadata(html)
        if md:
            title, meta_date, site = md.title or title, (md.date or "").replace("-", "")[:8], md.sitename or md.author or host
    if len(text.strip()) < 200:
        raise RuntimeError("za malo tresci (strona wymaga JavaScriptu lub logowania?)")
    source = f"TYTUL:\n{title}\nADRES:\n{url}\n\nTRESC:\n{text}"
    sha = write_source(dest, source)
    an = analyze(source, known_tags(notes))
    an.setdefault("tytul", title)
    body = text if len(text) <= 60000 else text[:60000] + "\n\n[obcieto]"
    write_note(notes, dest.name, url, {"uploader": site, "upload_date": meta_date}, an, "", "", "", "",
               typ="web", sha=sha, extra=(("Tresc", body),), sha_raw=hashlib.sha256(raw).hexdigest())
    refresh_index(notes)


UPLOADS = Path("uploads")  # tu laduja wgrane zrzuty (nazwa = sha256 + rozszerzenie)
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


def process_image(name: str, out: Path, notes: Path) -> None:
    """Zrzut ekranu: oryginal + odcisk SHA-256 pliku; Claude czyta tekst z obrazu i od razu analizuje (jedno wywolanie)."""
    if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|webp)", name):
        raise RuntimeError("zla nazwa pliku")
    src = UPLOADS / name
    raw = src.read_bytes()
    sha_raw, ext = hashlib.sha256(raw).hexdigest(), name.rsplit(".", 1)[1]
    code = "img-" + sha_raw[:12]
    dest = out / code
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest / f"image.{ext}")
    api_bytes, api_type = raw, MEDIA[ext]
    try:  # Pillow (opcjonalnie): miniatura na liste i mniejszy obraz do API (taniej)
        from PIL import Image
        im = Image.open(src).convert("RGB")
        th = im.copy()
        th.thumbnail((360, 480))
        th.save(dest / "media.jpg", quality=80)
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            api_bytes, api_type = buf.getvalue(), "image/jpeg"
    except ImportError:
        pass
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("brak ANTHROPIC_API_KEY - zrzut wymaga Claude'a do odczytu tekstu")
    block = {"type": "image", "source": {"type": "base64", "media_type": api_type,
                                         "data": base64.b64encode(api_bytes).decode()}}
    an = analyze_content([block], known_tags(notes), image=True, manual=False)
    if not an:
        raise RuntimeError("Claude nie zwrocil poprawnej odpowiedzi dla tego zrzutu")
    text = str(an.get("tekst") or "")
    sha = write_source(dest, text)
    name_file = UPLOADS / f"{name.rsplit('.', 1)[0]}.name"
    orig = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else ""
    write_note(notes, code, "", {"uploader": str(an.get("zrodlo") or ""), "plik": orig}, an, "", "", "", "",
               typ="zrzut", sha=sha, extra=(("Tekst ze zrzutu", text),), sha_raw=sha_raw)
    refresh_index(notes)


def process_any(url: str, out: Path, notes: Path, browser: str = "chrome", cookies: str = "") -> None:
    k = kind(url)
    if k == "plik":
        return process_image(url[5:], out, notes)
    if k == "instagram":
        return process(url, out, notes, browser, cookies)
    return (process_youtube if k == "youtube" else process_web)(url, out, notes)


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
            process_any(url, Path(a.out), Path(a.notes), a.browser, a.cookies or "")
        except Exception as e:  # jeden zly link nie zatrzymuje reszty
            print(f"  BLAD: {e}", file=sys.stderr)
            failed.append(url)
    print(f"gotowe: {len(urls) - len(failed)}/{len(urls)} OK")
    for u in failed:
        print("  nieudane:", u)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
