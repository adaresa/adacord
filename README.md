# Adacord Discord Bot

A simple Discord music bot that plays YouTube audio in voice channels.

## Features
- Play YouTube audio in voice channels
- Queue system for multiple songs
- Skip currently playing songs
- View and clear the queue
- Automatic cleanup and disconnection

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/play <url>` | `/p` | Add a YouTube video to the queue |
| `/skip` | `/s` | Skip the currently playing song |
| `/queue` | `/q` | View the current queue |
| `/clear` | `/c` | Clear the entire queue |

## Setup

### Prerequisites
- Python 3.11+
- FFmpeg installed on your system
- Discord Bot Token

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/adacord.git
   cd adacord
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a Discord application and bot:
   * Go to https://discord.com/developers/applications
   * Create a new application
   * Go to the Bot section
   * Create a bot and copy the token

4. Create a `.env` file:
   ```bash
   DISCORD_TOKEN=your-bot-token-here
   LOG_LEVEL=ERROR # Optional: DEBUG, INFO, WARNING, ERROR
   ```

5. Invite the bot to your server:
   * In the Discord Developer Portal, go to OAuth2 > URL Generator
   * Select bot and applications.commands scopes
   * Select permissions: Send Messages, Connect, Speak, Use Slash Commands
   * Use the generated URL to invite the bot

6. Run the bot:
   ```bash
   python bot.py
   ```

## Docker Usage

### Build and Run

   ```bash
   # Build the image
   docker build -t adacord-bot .
   
   # Run with .env file
   docker run --env-file .env adacord-bot

   # Or run with environment variables
   docker run -e DISCORD_TOKEN=your-token-here adacord-bot
   ```

### Docker Compose (optional)

   Create `docker-compose.yml`:
   ```yaml
   version: '3.8'

   services:
      bot:
         build: .
         env_file: .env
         restart: unless-stopped
   ```

   Then run:
   ```bash
   docker-compose up -d
   ```
