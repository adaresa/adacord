"""Replay real provider metadata; no network or Discord connection required.

The fixture contains 110 queries captured on 2026-09-07, with manually reviewed
song/version expectations. It tests selection, not the audio inside an upload.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import wavelink

from adacord.sources import choose_best_song_candidate, load_tracks, search_youtube, search_youtube_alternative


CASES = json.loads((Path(__file__).parent / "fixtures/song_search_2026_09_07.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["query"])
async def test_captured_search_selects_reviewed_song_and_version(monkeypatch, case):
    calls = []

    async def search(query, *, source):
        assert query == case["provider_query"]
        calls.append(source)
        records = case["music"] if source is wavelink.TrackSource.YouTubeMusic else case["youtube"]
        return [SimpleNamespace(**record) for record in records]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    tracks = await search_youtube(case["query"], "tester")

    assert len(tracks) == 1
    assert tracks[0].uri == case["expected_uri"]
    assert tracks[0].extras["requester"] == "tester"
    assert tracks[0].extras["query"] == case["query"]
    assert calls == [wavelink.TrackSource.YouTube] + (
        [wavelink.TrackSource.YouTubeMusic] if case["music"] else []
    )


@pytest.mark.parametrize("query", [
    "Drake Hotline Bling not slowed", "Drake Hotline Bling no remix",
    "Drake Hotline Bling without sped-up",
])
async def test_negative_version_requests_do_not_search_or_display_that_version(monkeypatch, fake_track_factory, query):
    track = fake_track_factory("Hotline Bling", author="Drake")

    async def search(provider_query, *, source):
        assert provider_query == "Drake Hotline Bling"
        return [track]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    tracks, summary = await load_tracks(query, "tester")
    assert tracks[0].extras["display_title"] == "Drake - Hotline Bling"
    assert summary.title == "Drake - Hotline Bling"


def test_featured_artist_credit_does_not_make_a_cover_more_exact(fake_track_factory):
    original = fake_track_factory("Somebody That I Used to Know (feat. Kimbra)", author="Gotye")
    cover = fake_track_factory("Somebody That I Used to Know", author="Three Days Grace")
    assert choose_best_song_candidate([original, cover], "somebody that i used to know") is original


def test_movie_context_does_not_displace_the_requested_title(fake_track_factory):
    original = fake_track_factory("Golden", author="HUNTR/X")
    other_song = fake_track_factory("How It's Done (from the Netflix film KPop Demon Hunters)", author="HUNTR/X")
    assert choose_best_song_candidate([original, other_song], "kpop demon golden") is original


def test_acoustic_request_accepts_unplugged(fake_track_factory):
    acoustic = fake_track_factory("Take On Me (MTV Unplugged)", author="a-ha")
    original = fake_track_factory("Take On Me", author="a-ha")
    assert choose_best_song_candidate([original, acoustic], "take on me acoustic") is acoustic


def test_extended_request_accepts_twelve_inch_mix(fake_track_factory):
    original = fake_track_factory("One More Time", author="Daft Punk")
    extended = fake_track_factory("One More Time (12 Mix)", author="Daft Punk", length=481_000)
    assert choose_best_song_candidate([original, extended], "daft punk one more time extended") is extended


def test_unrequested_livestream_does_not_displace_recording(fake_track_factory):
    stream = fake_track_factory("Golden", author="HUNTR/X")
    stream.is_stream = True
    recording = fake_track_factory("Golden", author="HUNTR/X")
    assert choose_best_song_candidate([stream, recording], "golden") is recording


async def test_explicit_multilingual_request_stays_on_youtube(monkeypatch, fake_track_factory):
    multilingual = fake_track_factory('Let It Go (Multi-Language)', author="Disney")

    async def search(query, *, source):
        assert source is wavelink.TrackSource.YouTube
        return [multilingual]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    assert await search_youtube("let it go multi language", "tester") == [multilingual]


async def test_empty_catalogue_fallback_keeps_available_youtube_results(monkeypatch, fake_track_factory):
    multilingual = fake_track_factory('Let It Go (Multi-Language)', author="Disney")

    async def search(query, *, source):
        return [] if source is wavelink.TrackSource.YouTubeMusic else [multilingual]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    assert await search_youtube("let it go frozen", "tester") == [multilingual]


async def test_failed_optional_catalogue_fallback_keeps_youtube_results(monkeypatch, fake_track_factory):
    multilingual = fake_track_factory('Let It Go (Multi-Language)', author="Disney")

    async def search(query, *, source):
        if source is wavelink.TrackSource.YouTubeMusic:
            raise RuntimeError("catalogue unavailable")
        return [multilingual]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    assert await search_youtube("let it go frozen", "tester") == [multilingual]


async def test_playback_recovery_preserves_negative_version_request(monkeypatch, fake_track_factory):
    failed = fake_track_factory("Hotline Bling", author="Drake")
    alternate = fake_track_factory("Drake - Hotline Bling", author="Drake")
    slowed = fake_track_factory("Hotline Bling (Slowed)", author="Drake")

    async def search(query, *, source):
        assert query == "Drake Hotline Bling"
        return [failed, slowed, alternate]

    monkeypatch.setattr(wavelink.Playable, "search", search)
    result = await search_youtube_alternative("Drake Hotline Bling not slowed", "tester", exclude_uris={failed.uri})
    assert result is alternate
    assert "slowed" not in result.extras["display_title"].lower()


@pytest.mark.parametrize("query, version", [
    ("song speed up", "Sped Up"), ("song spedup", "Sped Up"),
    ("song unplugged", "Acoustic"), ("song remix", "REM/X"),
])
def test_version_aliases_keep_song_identity(fake_track_factory, query, version):
    original = fake_track_factory("Song", author="Artist")
    requested = fake_track_factory(f"Song ({version})", author="Artist")
    assert choose_best_song_candidate([original, requested], query) is requested
