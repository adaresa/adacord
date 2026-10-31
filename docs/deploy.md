# Automated VPS Deployment

This guide describes one way to deploy Adacord to a VPS with GitHub Actions. It is optional; self-hosted users can run the stack manually with the [README quick start](../README.md#quick-start).

It assumes you deploy from your own fork. On every push to `main`, CI publishes the bot image to `ghcr.io/<your-user>/adacord`, then the Deploy workflow updates the server over SSH. If your fork changes the bot code, point the `bot` service's `image:` in `docker-compose.yml` at your own image so the server runs your build. Without the secrets below, the Deploy workflow skips itself.

## Server Setup

Install Docker and the Docker Compose plugin on the server, then clone the repository:

```bash
sudo mkdir -p /opt/adacord
sudo chown "$USER:$USER" /opt/adacord
git clone https://github.com/<your-user>/adacord.git /opt/adacord
cd /opt/adacord
git checkout main
```

Create `/opt/adacord/.env` from `.env.example` and set at least `DISCORD_TOKEN` and `DISCORD_GUILD_ID`. The [README](../README.md#configuration) lists every setting.

Start the stack:

```bash
docker compose pull
docker compose up -d
```

Optionally add `COMPOSE_PROFILES=watchdog` to `.env` to enable the [playback watchdog](../README.md#optional-playback-watchdog). Probe failure logs include the returned Lavalink `loadType` or a compact stream error for diagnosis.

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
# Before resetting, check whether lavalink/application.yml changed.
git reset --hard origin/main
docker compose pull bot
# Only when Lavalink configuration changed:
docker compose up -d --no-deps --force-recreate --wait lavalink
docker compose up -d --no-deps bot
```

The `.env` file and `./data` directory are untracked, so configuration and playback recovery state remain on the server across deploys.

Lavalink configuration updates interrupt active audio, so schedule those releases between listening sessions. Ordinary bot updates leave Lavalink and the cipher running. Deployments do not prune shared Docker images.

The YouTube plugin is pinned to upstream commit `f45bbb7aebfcbc1c553769e04af6cd43afa8b7c3`, which fixes premature EOF when YouTube omits the total content length ([upstream #235](https://github.com/lavalink-devs/youtube-source/pull/235)). Version 1.18.2 can stop valid tracks at the first byte-range boundary, including immediately after seeking. Before replacing this pin, verify actual decoding past that boundary and through the end of the track; a metadata lookup or short stream sample does not cover this failure.

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

## Checking the Server

After deployment, confirm the server state with:

```bash
docker compose ps
docker compose logs -f bot lavalink
```
