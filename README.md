# Adacord

A personal Discord music bot backed by Lavalink and Wavelink.

The bot handles Discord commands and queue controls. Lavalink handles track loading, streaming, reconnects, and audio delivery.

## Features

- YouTube URL and search playback
- Balanced YouTube Music search ranking to prefer song-like results over long or generic videos
- Spotify playlist links via public playlist metadata and YouTube Music resolution
- Queue, skip, pause, resume, clear, shuffle, remove, move, loop, volume, and disconnect commands
- Persistent Discord control panel
- Docker Compose setup with separate bot and Lavalink services

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
| `/loop <none|track|queue>` | Set loop mode |
| `/nowplaying` or `/np` | Show the player panel |

## Setup

1. Copy `.env.example` to `.env`.
2. Set `DISCORD_TOKEN`.
3. Optionally set `DISCORD_GUILD_ID` for instant slash-command syncing to one server.
4. Optionally set `MESSAGE_DELETE_AFTER` to control transient bot message cleanup in seconds. Use `0` to keep confirmations.
5. Optionally set `DEFAULT_VOLUME` from `0` to `200`. The default is `50`.
6. Optionally set `VOICE_CONNECT_TIMEOUT` if Discord voice joins need longer than the default `30` seconds.
7. Start everything:

```bash
docker compose up -d --build
```

The bot connects to Lavalink at `http://lavalink:2333` inside Docker.

## Local Development

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the Docker stack locally:

```bash
docker compose up -d --build
```

Rebuild only the bot after Python code changes:

```bash
docker compose up -d --build bot
```

Check container status and logs:

```bash
docker compose ps
docker compose logs --tail=120 bot lavalink
docker compose logs -f bot lavalink
```

Stop the local stack:

```bash
docker compose down
```

Run the lightweight local checks in PowerShell:

```powershell
python -m py_compile bot.py (Get-ChildItem adacord -Filter *.py).FullName test_extractor.py
python test_extractor.py
```

Or, in shells that expand globs:

```bash
python -m py_compile bot.py adacord/*.py test_extractor.py
python test_extractor.py
```

To run the bot outside Docker, start a Lavalink node and set:

```bash
LAVALINK_URI=http://localhost:2333
```

Then run:

```bash
python -m adacord
```

## Notes

- The built-in Lavalink YouTube source is disabled. The maintained YouTube plugin is configured in `lavalink/application.yml`.
- Spotify links are treated as metadata/playlist inputs. The bot does not download or stream Spotify audio directly.
- For a private bot, global slash commands can take time to update. Set `DISCORD_GUILD_ID` while iterating.
