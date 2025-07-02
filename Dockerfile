FROM python:3.11-slim

# Install FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade yt-dlp

# Copy bot code
COPY . .

# Set environment variables (optional)
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "bot.py"]
