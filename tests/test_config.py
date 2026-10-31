from __future__ import annotations

import re
from pathlib import Path

import pytest

from adacord.config import default_volume, player_idle_timeout, voice_connect_timeout


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_lavalink_playlist_limits_cover_large_playlists() -> None:
    config = (REPO_ROOT / "lavalink/application.yml").read_text(encoding="utf-8")
    youtube_limit = re.search(r"^\s+youtubePlaylistLoadLimit:\s+(\d+)\s*$", config, flags=re.MULTILINE)
    lavasrc_playlist_limit = re.search(r"^\s+playlistLoadLimit:\s+(\d+)\s*$", config, flags=re.MULTILINE)

    assert youtube_limit is not None
    assert int(youtube_limit.group(1)) >= 50
    assert lavasrc_playlist_limit is not None
    assert int(lavasrc_playlist_limit.group(1)) >= 50


@pytest.mark.parametrize(
    ("env_name", "values"),
    [
        ("DEFAULT_VOLUME", [(None, 50), ("not-a-number", 50), ("300", 200), ("-10", 0), ("75", 75)]),
        ("VOICE_CONNECT_TIMEOUT", [(None, 30), ("not-a-number", 30), ("-10", 0), ("45.5", 45.5)]),
        ("PLAYER_IDLE_TIMEOUT", [(None, 30), ("not-a-number", 30), ("-10", 0), ("45", 45)]),
    ],
)
def test_env_defaults_and_clamping(monkeypatch, env_name: str, values) -> None:
    readers = {
        "DEFAULT_VOLUME": default_volume,
        "VOICE_CONNECT_TIMEOUT": voice_connect_timeout,
        "PLAYER_IDLE_TIMEOUT": player_idle_timeout,
    }
    for raw_value, expected in values:
        if raw_value is None:
            monkeypatch.delenv(env_name, raising=False)
        else:
            monkeypatch.setenv(env_name, raw_value)
        assert readers[env_name]() == expected
