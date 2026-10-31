import os

DEFAULT_VOLUME = 50
LAVALINK_URI = "http://lavalink:2333"
LAVALINK_PASSWORD = "adacord-internal-lavalink"
PLAYBACK_STATE_FILE = "data/playback_state.json"
LAVALINK_VOICE_READY_TIMEOUT = 10.0
LAVALINK_VOICE_READY_INTERVAL = 0.25


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


def default_volume() -> int:
    return env_int("DEFAULT_VOLUME", DEFAULT_VOLUME, minimum=0, maximum=200)


def player_idle_timeout() -> int:
    return env_int("PLAYER_IDLE_TIMEOUT", 30, minimum=0)


def voice_connect_timeout() -> float:
    return env_float("VOICE_CONNECT_TIMEOUT", 30.0, minimum=0.0)


def playback_state_file() -> str:
    return PLAYBACK_STATE_FILE
