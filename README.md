# ig-grab

Instagram URL -> folder z wideo, miniaturą, opisem, transkrypcją i przepisem. Narzędzie lokalne, nie jest częścią żadnej gry.

## Setup

```bash
pip install yt-dlp faster-whisper anthropic "av<17"   # av 17+ psuje faster-whisper
```

Potrzebny też `ffmpeg` (`brew install ffmpeg` lub `apt install ffmpeg`).

## Użycie

Bądź zalogowany na Instagramie w przeglądarce (domyślnie Chrome).

```bash
python ig_grab.py --browser chrome URL [URL ...]
```

```bash
python ig_grab.py --urls-file linki.txt --out ig_out
```

Wynik w `ig_out/<kod>/`: `media.*`, `caption.txt`, `transcript.txt`, `recipe.md`.
`recipe.md` powstaje tylko przy ustawionym `ANTHROPIC_API_KEY`.

## Status

Przeszedł tylko sprawdzenie składni. Działanie na Instagramie nieprzetestowane.
Zdjęcia z karuzel: tylko miniatura (ograniczenie yt-dlp).

## Uwaga

Scraping narusza regulamin Instagrama i konto może dostać ban. Używaj osobnego konta i trzymaj limit tempa.
Nie commituj `cookies.txt` ani `ig_out/`.

## Strona lokalna (ig_web.py)
    python ig_web.py --cookies cookies.txt --limit 20
Otworz http://127.0.0.1:8765, wklej linki. Kolejka idzie z limitem dziennym i losowymi przerwami (30-90 s),
posty juz pobrane sa pomijane. Notatki: wyszukiwanie w tresci i filtr po etykiecie. Dziala tylko lokalnie.
