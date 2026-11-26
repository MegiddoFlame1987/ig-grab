FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp faster-whisper anthropic "av<17" trafilatura pypdf pillow

WORKDIR /app
COPY ig_grab.py ig_web.py ./

# dane (notes/, ig_out/, ig_state.json, cookies.txt, model Whisper) leza w wolumenie /data
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
USER app
ENV HF_HOME=/data/hf HF_HUB_DISABLE_SYMLINKS_WARNING=1
WORKDIR /data

EXPOSE 8765
CMD ["python", "/app/ig_web.py", "--host", "0.0.0.0", "--port", "8765", "--cookies", "/data/cookies.txt", "--limit", "10"]
