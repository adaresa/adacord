import os

DEFAULT_VOLUME = 50


def discord_token() -> str | None:
    return os.getenv("DISCORD_TOKEN")


def discord_guild_id() -> str | None:
    return os.getenv("DISCORD_GUILD_ID")


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").upper()


def message_delete_after() -> float:
    raw_value = os.getenv("MESSAGE_DELETE_AFTER", "5").strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 5.0


def default_volume() -> int:
    raw_value = os.getenv("DEFAULT_VOLUME", str(DEFAULT_VOLUME)).strip()
    try:
        return max(0, min(200, int(raw_value)))
    except ValueError:
        return DEFAULT_VOLUME


def lavalink_uri() -> str:
    return os.getenv("LAVALINK_URI", "http://lavalink:2333")


def lavalink_password() -> str:
    return os.getenv("LAVALINK_PASSWORD", "youshallnotpass")


def lavalink_connect_retries() -> int:
    return int(os.getenv("LAVALINK_CONNECT_RETRIES", "30"))


def lavalink_connect_delay() -> float:
    return float(os.getenv("LAVALINK_CONNECT_DELAY", "2"))


def lavalink_voice_ready_timeout() -> float:
    return float(os.getenv("LAVALINK_VOICE_READY_TIMEOUT", "10"))


def lavalink_voice_ready_interval() -> float:
    return float(os.getenv("LAVALINK_VOICE_READY_INTERVAL", "0.25"))


def player_idle_timeout() -> int:
    return int(os.getenv("PLAYER_IDLE_TIMEOUT", "30"))


def voice_connect_timeout() -> float:
    raw_value = os.getenv("VOICE_CONNECT_TIMEOUT", "30").strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 30.0
