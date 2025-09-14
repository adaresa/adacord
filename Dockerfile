FROM python:3.11-slim

# Install system dependencies (FFmpeg + opus + libsodium)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 libopus-dev libsodium23 libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade yt-dlp

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
