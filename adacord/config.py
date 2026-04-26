import os

DEFAULT_VOLUME = 50


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        value = default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        value = default

    if minimum is not None:
        value = max(minimum, value)
    return value


def discord_token() -> str | None:
    return os.getenv("DISCORD_TOKEN")


def discord_guild_id() -> str | None:
    return os.getenv("DISCORD_GUILD_ID")


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").upper()


def message_delete_after() -> float:
    return env_float("MESSAGE_DELETE_AFTER", 5.0, minimum=0.0)


def default_volume() -> int:
    return env_int("DEFAULT_VOLUME", DEFAULT_VOLUME, minimum=0, maximum=200)


def lavalink_uri() -> str:
    return os.getenv("LAVALINK_URI", "http://lavalink:2333")


def lavalink_password() -> str:
    return os.getenv("LAVALINK_PASSWORD", "youshallnotpass")


def lavalink_connect_retries() -> int:
    return env_int("LAVALINK_CONNECT_RETRIES", 30, minimum=1)


def lavalink_connect_delay() -> float:
    return env_float("LAVALINK_CONNECT_DELAY", 2.0, minimum=0.0)


def lavalink_voice_ready_timeout() -> float:
    return env_float("LAVALINK_VOICE_READY_TIMEOUT", 10.0, minimum=0.0)


def lavalink_voice_ready_interval() -> float:
    return env_float("LAVALINK_VOICE_READY_INTERVAL", 0.25, minimum=0.01)


def player_idle_timeout() -> int:
    return env_int("PLAYER_IDLE_TIMEOUT", 30, minimum=0)


def voice_connect_timeout() -> float:
    return env_float("VOICE_CONNECT_TIMEOUT", 30.0, minimum=0.0)


def playback_state_file() -> str:
    return os.getenv("PLAYBACK_STATE_FILE", "data/playback_state.json")
