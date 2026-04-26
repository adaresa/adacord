# Deploying Adacord

This repo is safe to keep public as long as secrets stay out of git. The bot's real environment belongs in a server-local `.env` file, and the deploy key belongs in GitHub Actions secrets.

## VPS Setup

Install Docker and the Docker Compose plugin on the VPS, then clone the public repo:

```bash
sudo mkdir -p /opt/adacord
sudo chown "$USER:$USER" /opt/adacord
git clone https://github.com/<your-user>/adacord.git /opt/adacord
cd /opt/adacord
git checkout main
```

Create `/opt/adacord/.env` manually:

```bash
DISCORD_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=your-server-id
LOG_LEVEL=INFO
MESSAGE_DELETE_AFTER=5
DEFAULT_VOLUME=50
LAVALINK_PASSWORD=choose-a-password
LAVALINK_URI=http://lavalink:2333
PLAYER_IDLE_TIMEOUT=30
VOICE_CONNECT_TIMEOUT=30
PLAYBACK_STATE_FILE=/app/data/playback_state.json
```

If YouTube playback fails on the VPS with `This video requires login`, add OAuth settings to the same `.env` file using a burner Google/YouTube account:

```bash
YOUTUBE_OAUTH_ENABLED=true
YOUTUBE_OAUTH_REFRESH_TOKEN=your-refresh-token
YOUTUBE_OAUTH_SKIP_INITIALIZATION=true
```

Start the stack once:

```bash
docker compose up -d --build
```

## GitHub Actions Secrets

Create a dedicated SSH key for GitHub Actions. Put the public key in the VPS user's `~/.ssh/authorized_keys`, then add these repository secrets in GitHub:

| Secret | Value |
| --- | --- |
| `VPS_HOST` | VPS hostname or IP address |
| `VPS_USER` | SSH user on the VPS |
| `VPS_PORT` | SSH port, usually `22` |
| `VPS_SSH_KEY` | Private deploy key |
| `VPS_KNOWN_HOSTS` | Output from `ssh-keyscan -p <port> <host>` |
| `APP_DIR` | Optional app path, defaults to `/opt/adacord` |

Do not put Discord tokens, Lavalink passwords, or `.env` values in workflow files.

## YouTube OAuth

VPS/datacenter IPs are more likely to trigger YouTube bot checks than a home connection. If Lavalink logs `This video requires login` for normal public videos, use the Lavalink YouTube plugin's OAuth flow.

Temporarily enable initialization on the VPS:

```bash
cd /opt/adacord
nano .env
```

Set:

```bash
YOUTUBE_OAUTH_ENABLED=true
YOUTUBE_OAUTH_SKIP_INITIALIZATION=false
```

Restart Lavalink and watch its logs:

```bash
docker compose up -d
docker compose logs -f lavalink
```

Follow the device login instructions printed by Lavalink. After login, copy the refresh token from the logs into `/opt/adacord/.env`:

```bash
YOUTUBE_OAUTH_ENABLED=true
YOUTUBE_OAUTH_REFRESH_TOKEN=the-token-from-the-logs
YOUTUBE_OAUTH_SKIP_INITIALIZATION=true
```

Restart once more:

```bash
docker compose up -d
```

Use a burner account, not your primary Google account. The youtube-source docs warn that OAuth is not guaranteed and can carry account risk.

## YouTube Remote Cipher

The Compose stack includes a private `yt-cipher` service for YouTube signature deciphering. This is used by Lavalink through the internal Docker network only; do not publish port `8001` publicly. Lavalink and `yt-cipher` share an internal Compose token, so no `.env` setting is required for this service.

If Lavalink logs `Must find sig function`, confirm the cipher service is running:

```bash
docker compose ps yt-cipher
docker compose logs --tail=80 yt-cipher
```

## Deploy Flow

Merging to `main` runs CI first. If CI succeeds for that push, `.github/workflows/deploy.yml` SSHes into the VPS and runs:

```bash
cd /opt/adacord
git fetch origin main
git reset --hard origin/main
docker compose up -d --build
docker image prune -f
```

The `.env` file is untracked, so it remains on the server across deploys.
The Compose stack also mounts `./data` into the bot container for playback recovery state, so bot restarts can rebuild
the active queue and player display.

## Branch Flow

Use `dev` for day-to-day changes and `main` for the deployed bot.

```bash
git switch dev
# make changes, test locally, push dev
git push origin dev
```

When a batch of changes is ready, open a pull request from `dev` to `main`. Merging to `main` is what triggers the VPS deploy.

## Local Bot Testing

Do not run a local bot with the production `DISCORD_TOKEN` while the VPS bot is running. Discord allows only one active gateway session per bot token, so the local process and VPS process will fight each other.

Recommended setup:

- Create a second Discord application/bot for development.
- Invite the dev bot to the same server, ideally with a distinct name/avatar.
- Keep a local-only `.env.dev` or alternate `.env` containing the dev bot token.
- Use the same `DISCORD_GUILD_ID` so slash commands sync instantly to your server.
- Stop the local dev bot after testing; production keeps running on the VPS.

For local Docker testing, copy `.env.example` to `.env` and use the dev bot token:

```bash
docker compose up -d --build
docker compose logs -f bot lavalink yt-cipher
```

If you only need static validation, run the checks without starting a Discord session:

```bash
python -m py_compile bot.py adacord/*.py tests/*.py
python -m pytest
docker compose config --no-interpolate
```

## Checks

Pull requests and pushes to `main` run `.github/workflows/ci.yml`:

```bash
python -m py_compile bot.py adacord/*.py tests/*.py
python -m pytest
docker compose config --no-interpolate
```

After the first deploy, confirm the VPS state with:

```bash
docker compose ps
docker compose logs -f bot lavalink
```
