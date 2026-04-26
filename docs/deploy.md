# Automated VPS Deployment

This guide describes one way to deploy Adacord to a VPS with GitHub Actions. It is optional; self-hosted users can run the stack manually with the README quick start.

## Server Setup

Install Docker and the Docker Compose plugin on the server, then clone the repository:

```bash
sudo mkdir -p /opt/adacord
sudo chown "$USER:$USER" /opt/adacord
git clone https://github.com/<your-user>/adacord.git /opt/adacord
cd /opt/adacord
git checkout main
```

Create `/opt/adacord/.env`:

```bash
DISCORD_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=your-server-id
MESSAGE_DELETE_AFTER=5
DEFAULT_VOLUME=50
PLAYER_IDLE_TIMEOUT=30
VOICE_CONNECT_TIMEOUT=30
```

Start the stack:

```bash
docker compose pull
docker compose up -d
```

## GitHub Actions Secrets

Create a dedicated SSH key for deployments. Add the public key to the server user's `~/.ssh/authorized_keys`, then add these repository secrets in GitHub:

| Secret | Value |
| --- | --- |
| `VPS_HOST` | Server hostname or IP address |
| `VPS_USER` | SSH user on the server |
| `VPS_PORT` | SSH port, usually `22` |
| `VPS_SSH_KEY` | Private deployment key |
| `VPS_KNOWN_HOSTS` | Output from `ssh-keyscan -p <port> <host>` |
| `APP_DIR` | Optional app path, defaults to `/opt/adacord` |

## Deploy Flow

Merging to `main` runs CI. If the push passes, `.github/workflows/deploy.yml` connects to the server and runs:

```bash
cd /opt/adacord
git fetch origin main
git reset --hard origin/main
docker compose pull
docker compose up -d
docker image prune -f
```

The `.env` file and `./data` directory are untracked, so configuration and playback recovery state remain on the server across deploys.

## YouTube OAuth

VPS and datacenter IPs are more likely to trigger YouTube bot checks than home connections. If Lavalink logs `This video requires login` for normal public videos, use the Lavalink YouTube plugin OAuth flow with a dedicated Google/YouTube account.

Temporarily enable initialization:

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

Restart again:

```bash
docker compose up -d
```

OAuth is not guaranteed to work forever and can carry account risk, so avoid using an important personal account.

## YouTube Remote Cipher

The Compose stack includes a private `yt-cipher` service for YouTube signature deciphering. It is used by Lavalink through the internal Docker network only; do not publish port `8001` publicly.

If Lavalink logs `Must find sig function`, confirm the cipher service is running:

```bash
docker compose ps yt-cipher
docker compose logs --tail=80 yt-cipher
```

## Local Validation

Run static checks without starting a Discord session:

```bash
python -m py_compile bot.py adacord/*.py tests/*.py
python -m pytest
docker compose config --no-interpolate
docker compose -f docker-compose.yml -f docker-compose.override.example.yml config --no-interpolate
```

For local Docker testing with source builds:

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
docker compose up -d --build
docker compose logs -f bot lavalink yt-cipher
```

After deployment, confirm the server state with:

```bash
docker compose ps
docker compose logs -f bot lavalink
```
