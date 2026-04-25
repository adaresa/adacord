import asyncio
import os

import wavelink

from types import SimpleNamespace

from adacord.config import default_volume, message_delete_after, voice_connect_timeout
from adacord.sources import choose_best_song_candidate, search_youtube
from adacord.utils import (
    display_track_title,
    format_duration,
    is_url,
    spotify_playlist_id,
    track_display_title,
    track_requester,
)


class FakeTrack:
    def __init__(
        self,
        title: str,
        *,
        author: str = "",
        length: int = 210_000,
        source: str = "youtube music",
    ):
        self.title = title
        self.author = author
        self.length = length
        self.source = source


def test_url_detection() -> None:
    assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_url("http://example.com/audio")
    assert not is_url("daft punk one more time")


def test_spotify_playlist_id() -> None:
    assert (
        spotify_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    assert (
        spotify_playlist_id("https://open.spotify.com/intl-ee/playlist/abc123?si=token")
        == "abc123"
    )
    assert spotify_playlist_id("https://open.spotify.com/track/abc123") is None


def test_duration_formatting() -> None:
    assert format_duration(None) == ""
    assert format_duration(0) == ""
    assert format_duration(65_000) == "1:05"
    assert format_duration(3_665_000) == "1:01:05"


def test_song_candidate_prefers_song_like_result() -> None:
    generic = FakeTrack("Daft Punk One More Time random upload", author="Some Channel")
    official = FakeTrack("One More Time", author="Daft Punk - Topic")

    assert choose_best_song_candidate([generic, official], "daft punk one more time") is official


def test_song_candidate_avoids_extended_result() -> None:
    extended = FakeTrack("One More Time extended loop 1 hour", length=3_600_000)
    normal = FakeTrack("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([extended, normal], "daft punk one more time") is normal


def test_song_candidate_allows_requested_variant_terms() -> None:
    remix = FakeTrack("One More Time remix", author="Daft Punk")
    original = FakeTrack("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([remix, original], "daft punk one more time remix") is remix


def test_display_title_keeps_requested_variant_visible() -> None:
    track = FakeTrack("Rockefeller Street")

    assert display_track_title(track, "rockefeller street nightcore") == "Rockefeller Street (nightcore)"
    assert display_track_title(track, "rockefeller street") == "Rockefeller Street"


def test_track_extras_support_dict_and_attribute_access() -> None:
    dict_track = FakeTrack("Fallback")
    dict_track.extras = {"requester": "ada", "display_title": "Custom dict title"}

    attr_track = FakeTrack("Fallback")
    attr_track.extras = SimpleNamespace(requester="kassu", display_title="Custom attr title")

    assert track_requester(dict_track) == "ada"
    assert track_display_title(dict_track) == "Custom dict title"
    assert track_requester(attr_track) == "kassu"
    assert track_display_title(attr_track) == "Custom attr title"


async def test_url_search_keeps_first_result() -> None:
    first = FakeTrack("Specific video URL")
    better = FakeTrack("Specific video URL lyrics")
    original_search = wavelink.Playable.search

    async def fake_search(query: str, *, source=None):
        assert query == "https://youtu.be/example"
        assert source is None
        return [first, better]

    try:
        wavelink.Playable.search = staticmethod(fake_search)
        assert await search_youtube("https://youtu.be/example", "tester") == [first]
    finally:
        wavelink.Playable.search = original_search


def test_message_delete_after_default_and_invalid_values() -> None:
    previous = os.environ.get("MESSAGE_DELETE_AFTER")
    try:
        os.environ.pop("MESSAGE_DELETE_AFTER", None)
        assert message_delete_after() == 5
        os.environ["MESSAGE_DELETE_AFTER"] = "not-a-number"
        assert message_delete_after() == 5
        os.environ["MESSAGE_DELETE_AFTER"] = "0"
        assert message_delete_after() == 0
        os.environ["MESSAGE_DELETE_AFTER"] = "-10"
        assert message_delete_after() == 0
        os.environ["MESSAGE_DELETE_AFTER"] = "2.5"
        assert message_delete_after() == 2.5
    finally:
        if previous is None:
            os.environ.pop("MESSAGE_DELETE_AFTER", None)
        else:
            os.environ["MESSAGE_DELETE_AFTER"] = previous


def test_default_volume_values() -> None:
    previous = os.environ.get("DEFAULT_VOLUME")
    try:
        os.environ.pop("DEFAULT_VOLUME", None)
        assert default_volume() == 50
        os.environ["DEFAULT_VOLUME"] = "not-a-number"
        assert default_volume() == 50
        os.environ["DEFAULT_VOLUME"] = "300"
        assert default_volume() == 200
        os.environ["DEFAULT_VOLUME"] = "-10"
        assert default_volume() == 0
        os.environ["DEFAULT_VOLUME"] = "75"
        assert default_volume() == 75
    finally:
        if previous is None:
            os.environ.pop("DEFAULT_VOLUME", None)
        else:
            os.environ["DEFAULT_VOLUME"] = previous


def test_voice_connect_timeout_values() -> None:
    previous = os.environ.get("VOICE_CONNECT_TIMEOUT")
    try:
        os.environ.pop("VOICE_CONNECT_TIMEOUT", None)
        assert voice_connect_timeout() == 30
        os.environ["VOICE_CONNECT_TIMEOUT"] = "not-a-number"
        assert voice_connect_timeout() == 30
        os.environ["VOICE_CONNECT_TIMEOUT"] = "-10"
        assert voice_connect_timeout() == 0
        os.environ["VOICE_CONNECT_TIMEOUT"] = "45.5"
        assert voice_connect_timeout() == 45.5
    finally:
        if previous is None:
            os.environ.pop("VOICE_CONNECT_TIMEOUT", None)
        else:
            os.environ["VOICE_CONNECT_TIMEOUT"] = previous


if __name__ == "__main__":
    test_url_detection()
    test_spotify_playlist_id()
    test_duration_formatting()
    test_song_candidate_prefers_song_like_result()
    test_song_candidate_avoids_extended_result()
    test_song_candidate_allows_requested_variant_terms()
    test_display_title_keeps_requested_variant_visible()
    test_track_extras_support_dict_and_attribute_access()
    asyncio.run(test_url_search_keeps_first_result())
    test_message_delete_after_default_and_invalid_values()
    test_default_volume_values()
    test_voice_connect_timeout_values()
    print("OK")
