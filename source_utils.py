import re
from urllib.parse import urlparse

SPOTIFY_PLAYLIST_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]+)"
)


def is_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def spotify_playlist_id(value: str) -> str | None:
    match = SPOTIFY_PLAYLIST_RE.match(value.strip())
    return match.group(1) if match else None


def format_duration(milliseconds: int | None) -> str:
    if not milliseconds or milliseconds <= 0:
        return ""

    total_seconds = milliseconds // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

