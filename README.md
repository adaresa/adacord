# Adacord

Adacord is a self-hosted Discord music bot built around a persistent player panel. Instead of flooding your server with playback messages, Adacord keeps one Discord message updated with the current track, queue preview, volume, loop mode, and clickable controls.

It is packaged for Docker Compose and runs with Lavalink, YouTube search/playback, Spotify playlist metadata resolution, queue management, and full core playback controls without relying on a hosted bot or premium paywall.

## Why Adacord?

Most Discord music bots are either hosted services with feature limits behind paywalls or command-heavy bots that clutter the music channel. Adacord is designed for small private servers that want a clean, self-hosted music experience:

- One persistent player panel that updates in place
- Clickable controls for playback, queue, loop, shuffle, mute, and volume
- Short-lived command responses so the music channel stays clean
- Docker-first setup for VPS or home-server installs
- Local playback state under `./data` so sessions can recover across restarts
- No hosted-bot dependency, premium tier, or external dashboard required

## Features

- Persistent Discord player panel with clickable controls
- YouTube URL and search playback
- Spotify playlist links resolved through public metadata and YouTube Music search
- Queue, skip, pause, resume, clear, shuffle, remove, move, loop, volume, and disconnect commands
- Playback session state stored locally under `./data`
- Docker Compose stack with bot, Lavalink, and YouTube cipher services

## Quick Start

### 1. Install Docker

Install Docker Engine and the Docker Compose plugin on the machine that will run the bot.

### 2. Create a Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create an application, then create a bot for it.
3. Copy the bot token.
4. Invite the bot to your server with these scopes:
   - `bot`
   - `applications.commands`
5. Give it these practical permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Read Message History
   - Connect
   - Speak
   - Use Voice Activity

### 3. Configure Adacord

Copy the example environment file and set your Discord values:

```bash
cp .env.example .env
```

```env
DISCORD_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=your-server-id
```

`DISCORD_GUILD_ID` is recommended for self-hosted single-server installs because slash commands update quickly in that server.

### 4. Start the bot

```bash
docker compose up -d
```

Check status and logs:

```bash
docker compose ps
docker compose logs --tail=120 bot lavalink yt-cipher
```

Follow logs while testing playback:

```bash
docker compose logs -f bot lavalink yt-cipher
```

## Updating

Pull the latest Compose file from the repo, then update the containers:

```bash
docker compose pull
docker compose up -d
docker image prune -f
```

## Configuration

Required:

```env
DISCORD_TOKEN=your-discord-bot-token
```

Recommended:

```env
DISCORD_GUILD_ID=your-server-id
```

Optional playback settings:

```env
DEFAULT_VOLUME=50
PLAYER_IDLE_TIMEOUT=30
VOICE_CONNECT_TIMEOUT=30
```

If YouTube playback fails on a VPS or datacenter IP with a login-required error, enable YouTube OAuth using a dedicated Google/YouTube account:

```env
YOUTUBE_OAUTH_ENABLED=true
YOUTUBE_OAUTH_REFRESH_TOKEN=your-refresh-token
YOUTUBE_OAUTH_SKIP_INITIALIZATION=true
```

## Commands

| Command | Description |
| --- | --- |
| `/play <query>` or `/p <query>` | Play a YouTube URL/search or Spotify playlist link |
| `/skip` or `/s` | Skip the current track |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/queue` or `/q` | Show the queue |
| `/clear` or `/c` | Clear queue and stop playback |
| `/disconnect` or `/dc` | Disconnect from voice |
| `/volume <0-200>` | Set player volume |
| `/shuffle` | Shuffle the queue |
| `/remove <position>` | Remove a queued track |
| `/move <from_pos> <to_pos>` | Move a queued track |
| `/loop <none\|track\|queue>` | Set loop mode |
| `/nowplaying` or `/np` | Show the player panel |

## Local Development

Install dependencies for tests:

```bash
python -m pip install -r requirements-dev.txt
```

Run checks:

```bash
python -m py_compile bot.py adacord/*.py tests/*.py
python -m pytest
docker compose config --no-interpolate
docker compose -f docker-compose.yml -f docker-compose.override.example.yml config --no-interpolate
```

To build the bot image locally instead of pulling the published image:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up -d --build
```

Rebuild only the bot after Python changes:

```bash
docker compose up -d --build bot
```

Stop the stack:

```bash
docker compose down
```

## Notes

- Playback session state is stored under `./data` and survives container restarts.
- Spotify links are metadata inputs only. Adacord does not download or stream Spotify audio.
- For live local testing, use a separate development Discord bot token if production is already running elsewhere.
