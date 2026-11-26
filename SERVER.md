# Wdrozenie na serwerze w domu (Docker) - instrukcja dla sesji "Server"

Zlecenie od Norberta: dodaj Dockera na serwerze i uruchom kontener ig-grab ("brain": strona z notatkami, upload zrzutow,
linki IG/YouTube/WWW). Repo: MegiddoFlame1987/ig-grab (prywatne), branch main. Pliki gotowe: `Dockerfile`, `compose.yaml`,
`.dockerignore`. Obraz: python:3.12-slim + ffmpeg + yt-dlp, faster-whisper, anthropic, av<17, trafilatura, pypdf, pillow.

## Czego nie wiemy
Obrazu nie zbudowano jeszcze nigdzie (sesja w chmurze nie ma demona Dockera). Zbuduj i **zglos bledy** z
`docker compose up -d --build`, szczegolnie instalacje `av` i `faster-whisper` oraz uprawnienia wolumenu.

## Kroki
1. Sprawdz, czy Docker Engine i plugin compose sa zainstalowane. Jesli nie, zainstaluj oficjalna metoda (Docker docs).
   Przy sudo lub zmianach systemowych zapytaj Norberta, nie rob po cichu.
2. `git clone` repo (prywatne: uzyj istniejacego logowania do GitHuba), `cd ig-grab`.
3. `mkdir data && sudo chown 1000 data` (kontener dziala jako uid 1000).
4. Plik `.env` z linia `ANTHROPIC_API_KEY=...`. Klucz poda Norbert. Nie wklejaj go do czatu i nie commituj (`.env` jest w `.gitignore`).
   Opcjonalnie `data/cookies.txt` (cookies Instagrama z osobnego konta, format Netscape).
5. `docker compose up -d --build`
6. Dostep tylko przez Tailscale: `tailscale serve --bg 8765`. **Nie** `tailscale funnel`.
   Port w `compose.yaml` jest publikowany wylacznie na `127.0.0.1:8765`; nie zmieniaj tego na `8765:8765`.
7. Test: z telefonu przez Tailscale otworz strone, dodaj jeden link (YouTube lub artykul), sprawdz, ze powstaje notatka.
   Zrzuty ekranu dzialaja dopiero, gdy konto API ma srodki (strona pokaze czerwony pasek, zadania czekaja w kolejce).

## Zasady
- Nie ruszaj innych uslug na serwerze.
- Limit dzienny Instagrama (`--limit 10` w CMD Dockerfile) nie zwiekszaj bez pytania.
- Instagram pobieraj tylko z domowego adresu, nie z VPS.
- Wynik (co dziala, co nie, komunikaty bledow) odeslij Norbertowi w odpowiedzi.
