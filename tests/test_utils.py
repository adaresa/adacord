from __future__ import annotations

from types import SimpleNamespace

import pytest

from adacord.utils import (
    display_track_title,
    format_duration,
    is_url,
    spotify_playlist_id,
    track_display_title,
    track_requester,
    youtube_start_position,
    youtube_watch_url_without_playlist,
)


def test_url_detection() -> None:
    assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_url("http://example.com/audio")
    assert not is_url("daft punk one more time")


def test_youtube_watch_url_without_playlist() -> None:
    assert (
        youtube_watch_url_without_playlist(
            "https://www.youtube.com/watch?v=uwTw63tTNeI&list=PLDnafrUBAs3mUL4oQt4oaQGByc1OFXj0Y&index=7"
        )
        == "https://www.youtube.com/watch?v=uwTw63tTNeI"
    )
    assert youtube_watch_url_without_playlist("https://youtu.be/uwTw63tTNeI?list=abc") is None
    assert youtube_watch_url_without_playlist("https://www.youtube.com/watch?v=uwTw63tTNeI") is None


def test_spotify_playlist_id() -> None:
    assert (
        spotify_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    assert spotify_playlist_id("https://open.spotify.com/intl-ee/playlist/abc123?si=token") == "abc123"
    assert spotify_playlist_id("https://open.spotify.com/track/abc123") is None


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/watch?v=8bCDdWSdMDM&t=241s", 241000),
    ("https://youtu.be/8bCDdWSdMDM?t=4m1s", 241000),
    ("https://www.youtube.com/watch?v=x&list=y&start=241", 241000),
    ("https://youtu.be/x#t=1h2m3s", 3723000),
    ("https://youtu.be/x?t=-1", 0),
    ("https://youtu.be/x?t=oops", 0),
    ("https://example.com/watch?t=241", 0),
    ("song title", 0),
])
def test_youtube_start_position(url, expected):
    assert youtube_start_position(url) == expected


def test_duration_formatting() -> None:
    assert format_duration(None) == ""
    assert format_duration(0) == ""
    assert format_duration(65_000) == "1:05"
    assert format_duration(3_665_000) == "1:01:05"


def test_display_title_keeps_requested_variant_visible(fake_track_factory) -> None:
    track = fake_track_factory("Rockefeller Street")

    assert display_track_title(track, "rockefeller street nightcore") == "Rockefeller Street (nightcore)"
    assert display_track_title(track, "rockefeller street") == "Rockefeller Street"


def test_display_title_removes_youtube_channel_prefixes(fake_track_factory) -> None:
    uploads = [
        (
            fake_track_factory("Martin Garrix & Bebe Rexha - In The Name Of Love (Lyrics)", author="7clouds"),
            "Martin Garrix & Bebe Rexha - In The Name Of Love",
        ),
        (
            fake_track_factory("Halsey - Without Me (Lyrics)", author="Creative Chaos and 2 more"),
            "Halsey - Without Me",
        ),
        (
            fake_track_factory(
                """The Chainsmokers - Don't Let Me Down (Lyrics) | "I need you right now" """,
                author="ChillTracks",
            ),
            "The Chainsmokers - Don't Let Me Down",
        ),
        (
            fake_track_factory("Daft Punk - One More Time [Official Audio]", author="Uploader"),
            "Daft Punk - One More Time",
        ),
    ]

    for track, expected in uploads:
        assert display_track_title(track, "search terms") == expected


def test_display_title_still_prepends_official_artist_metadata(fake_track_factory) -> None:
    track = fake_track_factory("One More Time", author="Daft Punk - Topic")

    assert display_track_title(track, "daft punk one more time") == "Daft Punk - One More Time"


def test_track_extras_support_dict_and_attribute_access(fake_track_factory) -> None:
    dict_track = fake_track_factory("Fallback")
    dict_track.extras = {"requester": "ada", "display_title": "Custom dict title"}

    attr_track = fake_track_factory("Fallback")
    attr_track.extras = SimpleNamespace(requester="alice", display_title="Custom attr title")

    assert track_requester(dict_track) == "ada"
    assert track_display_title(dict_track) == "Custom dict title"
    assert track_requester(attr_track) == "alice"
    assert track_display_title(attr_track) == "Custom attr title"
