from __future__ import annotations

import json

import pytest
import wavelink

from adacord.sources import (
    LoadSummary,
    choose_best_song_candidate,
    load_tracks,
    search_lavalink,
    search_youtube,
    search_youtube_alternative,
    spotify_public_playlist_queries,
)


def test_song_candidate_preserves_relevance_over_topic_label(fake_track_factory) -> None:
    generic = fake_track_factory("Daft Punk One More Time random upload", author="Some Channel")
    official = fake_track_factory("One More Time", author="Daft Punk - Topic")

    assert choose_best_song_candidate([generic, official], "daft punk one more time") is generic


def test_song_candidate_avoids_extended_result(fake_track_factory) -> None:
    extended = fake_track_factory("One More Time extended loop 1 hour", length=3_600_000)
    normal = fake_track_factory("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([extended, normal], "daft punk one more time") is normal


def test_song_candidate_allows_requested_variant_terms(fake_track_factory) -> None:
    remix = fake_track_factory("One More Time remix", author="Daft Punk")
    original = fake_track_factory("One More Time lyrics", author="Daft Punk")

    assert choose_best_song_candidate([remix, original], "daft punk one more time remix") is remix


def test_janice_stfu_preserves_drake_from_live_search_results(fake_track_factory) -> None:
    # Captured ytmsearch order: an artist name containing "Audio" is not an upload label.
    results = [
        ("Janice STFU", "Drake", 238_000),
        ("Janice STFU", "KAYLA KING", 186_000),
        ("Janice STFU (Instrumental Cover) (feat. RVNI PLUG)", "Rio Marlow", 223_000),
        ("Janice STFU (Chopped & Screwed)", "Tha Audio Unit", 374_000),
        ("Janice STFU (Techno)", "CARA", 103_000),
        ("Janice STFU", "Gina Fiona", 223_000),
        ("Janice STFU", "Strings From Paris", 169_000),
        ("Janice STFU (Extended Mix)", "DAXTX", 156_000),
    ]
    tracks = [fake_track_factory(title, author=artist, length=length, source="youtube")
              for title, artist, length in results]
    assert choose_best_song_candidate(tracks, "Janice STFU") is tracks[0]
    assert choose_best_song_candidate(tracks, "Janice STFU chopped screwed") is tracks[3]


@pytest.mark.parametrize("label", ["Lyrics", "Official Audio", "Official Music Video"])
def test_upload_labels_do_not_displace_first_matching_song(fake_track_factory, label) -> None:
    original = fake_track_factory("Drake - Janice STFU", author="Drake")
    upload = fake_track_factory(f"Drake - Janice STFU ({label})", author="Lyrics Topic")
    assert choose_best_song_candidate([original, upload], "Janice STFU") is original


@pytest.mark.parametrize("variant", [
    "Cover", "Sped-Up", "Chopped & Screwed", "Acoustic", "Clean", "Techno",
    "𝕊𝕝𝕠𝕨𝕖𝕕", "Instrumental", "Remix",
])
def test_song_versions_are_selected_only_when_requested(fake_track_factory, variant) -> None:
    edit = fake_track_factory(f"Janice STFU ({variant})", author="Drake")
    original = fake_track_factory("Janice STFU", author="Drake")
    assert choose_best_song_candidate([edit, original], "Janice STFU") is original
    assert choose_best_song_candidate([original, edit], f"Janice STFU {variant}") is edit


def test_artist_and_title_match_beats_partial_match_with_labels(fake_track_factory) -> None:
    wrong_artist = fake_track_factory("Janice STFU (Official Audio)", author="Other - Topic")
    wrong_title = fake_track_factory("Janice (Lyrics)", author="Drake")
    original = fake_track_factory("Janice STFU", author="Drake")
    assert choose_best_song_candidate([wrong_artist, wrong_title, original], "Drake Janice STFU") is original


def test_artist_name_is_not_treated_as_version(fake_track_factory) -> None:
    original = fake_track_factory("Song", author="Live")
    other = fake_track_factory("Song", author="Other")
    assert choose_best_song_candidate([original, other], "Song") is original


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


async def test_url_search_retries_youtube_watch_url_without_playlist(monkeypatch, fake_track_factory) -> None:
    track = fake_track_factory("Direct video")
    calls = []

    async def fake_search(query: str, *, source=None):
        calls.append((query, source))
        if "list=" in query:
            raise RuntimeError("playlist does not exist")
        return [track]

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    tracks = await search_youtube(
        "https://www.youtube.com/watch?v=uwTw63tTNeI&list=PLDnafrUBAs3mUL4oQt4oaQGByc1OFXj0Y&index=7",
        "tester",
    )

    assert tracks == [track]
    assert calls == [
        (
            "https://www.youtube.com/watch?v=uwTw63tTNeI&list=PLDnafrUBAs3mUL4oQt4oaQGByc1OFXj0Y&index=7",
            None,
        ),
        ("https://www.youtube.com/watch?v=uwTw63tTNeI", None),
    ]
    assert track.extras["query"] == "https://www.youtube.com/watch?v=uwTw63tTNeI"


async def test_search_youtube_uses_regular_youtube_for_terms(monkeypatch, fake_track_factory) -> None:
    first = fake_track_factory("Generic upload", author="Some Channel")
    best = fake_track_factory("One More Time", author="Daft Punk - Topic")
    calls = []

    async def fake_search(query: str, *, source=None):
        calls.append((query, source))
        return [first, best]

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    tracks = await search_youtube("daft punk one more time", "tester")

    assert tracks == [best]
    assert calls == [("daft punk one more time", wavelink.TrackSource.YouTube)]


async def test_search_youtube_handles_empty_results(monkeypatch) -> None:
    async def fake_search(query: str, *, source=None):
        return []

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    assert await search_youtube("missing song", "tester") == []


async def test_search_youtube_alternative_uses_regular_youtube_and_excludes_failed_uri(
    monkeypatch,
    fake_track_factory,
) -> None:
    failed = fake_track_factory("Exact", author="Artist")
    alternate = fake_track_factory("Exact Official Audio", author="Artist")

    async def fake_search(query, *, source):
        assert query == "artist exact"
        assert source is wavelink.TrackSource.YouTube
        return [failed, alternate]

    monkeypatch.setattr(wavelink.Playable, "search", fake_search)

    result = await search_youtube_alternative(
        "artist exact",
        "recovery",
        exclude_uris={failed.uri},
    )

    assert result is alternate
    assert dict(result.extras)["query"] == "artist exact"


async def test_search_lavalink_keeps_custom_prefix_and_limits_results(monkeypatch, fake_track_factory) -> None:
    tracks = [fake_track_factory("First"), fake_track_factory("Second"), fake_track_factory("Third")]
    calls = []

    async def fake_search(query: str, *, source=None):
        calls.append((query, source))
        return tracks

    monkeypatch.setattr(wavelink.Playable, "search", staticmethod(fake_search))

    resolved = await search_lavalink("sprec:mix:isrc:GBDUW0000053", "tester", limit=2)

    assert resolved == tracks[:2]
    assert calls == [("sprec:mix:isrc:GBDUW0000053", None)]
    assert tracks[0].extras["query"] == "sprec:mix:isrc:GBDUW0000053"


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
    async def fake_public(playlist_id: str, requester: str):
        raise RuntimeError("no metadata")

    monkeypatch.setattr("adacord.sources.load_spotify_with_public_metadata", fake_public)

    with pytest.raises(RuntimeError, match="Could not load that Spotify playlist"):
        await load_tracks("https://open.spotify.com/playlist/abc123", "tester")


async def test_spotify_playlist_queries_read_embed_metadata(monkeypatch) -> None:
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
