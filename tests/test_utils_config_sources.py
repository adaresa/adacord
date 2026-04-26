from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import wavelink

from adacord.config import (
    default_volume,
    lavalink_connect_delay,
    lavalink_connect_retries,
    lavalink_voice_ready_interval,
    lavalink_voice_ready_timeout,
    playback_state_file,
    player_idle_timeout,
    voice_connect_timeout,
)
from adacord.persistence import (
    clear_guild_state_now as clear_saved_guild_state,
    load_state,
    save_player_state_now as save_player_state,
    track_from_payload,
    track_payload,
)
from adacord.sources import (
    LoadSummary,
    choose_best_song_candidate,
    load_tracks,
    search_youtube,
    spotify_public_playlist_queries,
)
from adacord.utils import (
    display_track_title,
    format_duration,
    is_url,
    spotify_playlist_id,
    track_display_title,
    track_requester,
)
from conftest import FakePlayer, FakeQueue, FakeTrack


def test_url_detection() -> None:
    assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_url("http://example.com/audio")
    assert not is_url("daft punk one more time")


def test_spotify_playlist_id() -> None:
    assert (
        spotify_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    assert spotify_playlist_id("https://open.spotify.com/intl-ee/playlist/abc123?si=token") == "abc123"
    assert spotify_playlist_id("https://open.spotify.com/track/abc123") is None


def test_duration_formatting() -> None:
    assert format_duration(None) == ""
    assert format_duration(0) == ""
    assert format_duration(65_000) == "1:05"
    assert format_duration(3_665_000) == "1:01:05"


def test_song_candidate_prefers_song_like_result(fake_track_factory) -> None:
    generic = fake_track_factory("Daft Punk One More Time random upload", author="Some Channel")
    official = fake_track_factory("One More Time", author="Daft Punk - Topic")

    assert choose_best_song_candidate([generic, official], "daft punk one more time") is official


def test_song_candidate_avoids_extended_result(fake_track_factory) -> None:
    extended = fake_track_factory("One More Time extended loop 1 hour", length=3_600_000)
    normal = fake_track_factory("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([extended, normal], "daft punk one more time") is normal


def test_song_candidate_allows_requested_variant_terms(fake_track_factory) -> None:
    remix = fake_track_factory("One More Time remix", author="Daft Punk")
    original = fake_track_factory("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([remix, original], "daft punk one more time remix") is remix


def test_display_title_keeps_requested_variant_visible(fake_track_factory) -> None:
    track = fake_track_factory("Rockefeller Street")

    assert display_track_title(track, "rockefeller street nightcore") == "Rockefeller Street (nightcore)"
    assert display_track_title(track, "rockefeller street") == "Rockefeller Street"


def test_track_extras_support_dict_and_attribute_access(fake_track_factory) -> None:
    dict_track = fake_track_factory("Fallback")
    dict_track.extras = {"requester": "ada", "display_title": "Custom dict title"}

    attr_track = fake_track_factory("Fallback")
    attr_track.extras = SimpleNamespace(requester="kassu", display_title="Custom attr title")

    assert track_requester(dict_track) == "ada"
    assert track_display_title(dict_track) == "Custom dict title"
    assert track_requester(attr_track) == "kassu"
    assert track_display_title(attr_track) == "Custom attr title"


@pytest.mark.parametrize(
    ("env_name", "values"),
    [
        ("DEFAULT_VOLUME", [(None, 50), ("not-a-number", 50), ("300", 200), ("-10", 0), ("75", 75)]),
        ("VOICE_CONNECT_TIMEOUT", [(None, 30), ("not-a-number", 30), ("-10", 0), ("45.5", 45.5)]),
        ("LAVALINK_CONNECT_RETRIES", [(None, 30), ("not-a-number", 30), ("0", 1), ("5", 5)]),
        ("LAVALINK_CONNECT_DELAY", [(None, 2), ("not-a-number", 2), ("-1", 0), ("0.5", 0.5)]),
        ("LAVALINK_VOICE_READY_TIMEOUT", [(None, 10), ("not-a-number", 10), ("-1", 0), ("12.5", 12.5)]),
        ("LAVALINK_VOICE_READY_INTERVAL", [(None, 0.25), ("not-a-number", 0.25), ("0", 0.01), ("1.5", 1.5)]),
        ("PLAYER_IDLE_TIMEOUT", [(None, 30), ("not-a-number", 30), ("-10", 0), ("45", 45)]),
    ],
)
def test_env_defaults_and_clamping(monkeypatch, env_name: str, values) -> None:
    readers = {
        "DEFAULT_VOLUME": default_volume,
        "VOICE_CONNECT_TIMEOUT": voice_connect_timeout,
        "LAVALINK_CONNECT_RETRIES": lavalink_connect_retries,
        "LAVALINK_CONNECT_DELAY": lavalink_connect_delay,
        "LAVALINK_VOICE_READY_TIMEOUT": lavalink_voice_ready_timeout,
        "LAVALINK_VOICE_READY_INTERVAL": lavalink_voice_ready_interval,
        "PLAYER_IDLE_TIMEOUT": player_idle_timeout,
    }
    for raw_value, expected in values:
        if raw_value is None:
            monkeypatch.delenv(env_name, raising=False)
        else:
            monkeypatch.setenv(env_name, raw_value)
        assert readers[env_name]() == expected


def test_playback_state_file_uses_internal_data_path(monkeypatch) -> None:
    monkeypatch.setattr("adacord.config.PLAYBACK_STATE_FILE", "data/playback_state.json")
    monkeypatch.setenv("PLAYBACK_STATE_FILE", "custom/state.json")
    assert playback_state_file() == "data/playback_state.json"


def test_track_payload_round_trips_to_playable() -> None:
    track = FakeTrack("Round Trip", author="Tester")
    track.extras = {"requester": "ada", "display_title": "Custom title"}

    restored = track_from_payload(track_payload(track))

    assert restored is not None
    assert restored.title == "Round Trip"
    assert restored.author == "Tester"
    assert dict(restored.extras) == {"requester": "ada", "display_title": "Custom title"}


def test_save_and_clear_player_state() -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Next")]))

    save_player_state(player)
    data = load_state()

    saved = data["guilds"][str(player.guild.id)]
    assert saved["current"]["title"] == "Current"
    assert saved["queue"][0]["title"] == "Next"

    clear_saved_guild_state(player.guild.id)
    assert load_state()["guilds"] == {}


async def test_url_search_keeps_first_result(monkeypatch, fake_track_factory) -> None:
    first = fake_track_factory("Specific video URL")
    better = fake_track_factory("Specific video URL lyrics")

    async def fake_search(query: str, *, source=None):
        assert query == "https://youtu.be/example"
        assert source is None
        return [first, better]

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    assert await search_youtube("https://youtu.be/example", "tester") == [first]
    assert first.extras["requester"] == "tester"


async def test_search_youtube_uses_youtube_music_for_terms(monkeypatch, fake_track_factory) -> None:
    first = fake_track_factory("Generic upload", author="Some Channel")
    best = fake_track_factory("One More Time", author="Daft Punk - Topic")
    calls = []

    async def fake_search(query: str, *, source=None):
        calls.append((query, source))
        return [first, best]

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    tracks = await search_youtube("daft punk one more time", "tester")

    assert tracks == [best]
    assert calls == [("daft punk one more time", wavelink.TrackSource.YouTubeMusic)]


async def test_search_youtube_handles_empty_results(monkeypatch) -> None:
    async def fake_search(query: str, *, source=None):
        return []

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    assert await search_youtube("missing song", "tester") == []


async def test_load_tracks_uses_public_spotify_metadata(monkeypatch, fake_track_factory) -> None:
    resolved = [fake_track_factory("Song A"), fake_track_factory("Song B")]
    searches = []

    async def fake_playlist_queries(playlist_id: str):
        assert playlist_id == "abc123"
        return ["artist a - song a", "artist b - song b"]

    async def fake_search_youtube(query: str, requester: str):
        searches.append((query, requester))
        return [resolved[len(searches) - 1]]

    monkeypatch.setattr("adacord.sources.spotify_playlist_queries", fake_playlist_queries)
    monkeypatch.setattr("adacord.sources.search_youtube", fake_search_youtube)

    tracks, summary = await load_tracks("https://open.spotify.com/playlist/abc123", "tester")

    assert tracks == resolved
    assert searches == [("artist a - song a", "tester"), ("artist b - song b", "tester")]
    assert summary == LoadSummary("Spotify playlist", 2, "spotify-public")


async def test_load_tracks_raises_when_spotify_sources_fail(monkeypatch) -> None:
    async def fake_playlist_queries(playlist_id: str):
        raise RuntimeError("no metadata")

    async def fake_search(query: str, *, source=None):
        return []

    monkeypatch.setattr("adacord.sources.spotify_playlist_queries", fake_playlist_queries)
    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    with pytest.raises(RuntimeError, match="Could not load that Spotify playlist"):
        await load_tracks("https://open.spotify.com/playlist/abc123", "tester")


async def test_public_spotify_metadata_fetch_is_mockable(monkeypatch) -> None:
    payload = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "trackList": [
                                {"entityType": "track", "title": "Song A", "subtitle": "Artist A"},
                                {"entityType": "track", "title": "Episode", "subtitle": "Podcast", "isPlayable": False},
                            ]
                        }
                    }
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return html.encode()

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr("adacord.sources.urlopen", fake_urlopen)

    assert await spotify_public_playlist_queries("abc123") == ["Artist A - Song A"]
