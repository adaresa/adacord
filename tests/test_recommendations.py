from __future__ import annotations

import time

import pytest

from adacord import recommendations
from conftest import FakePlayer, FakeQueue, FakeTrack


@pytest.fixture(autouse=True)
def clear_recommendation_cache():
    recommendations.clear_recommendation_cache()
    yield
    recommendations.clear_recommendation_cache()


def test_recommendation_queries_use_spotify_isrc_and_youtube_fallbacks() -> None:
    current = FakeTrack("One More Time", author="Daft Punk")
    current.isrc = "GBDUW0000053"
    current.uri = "https://open.spotify.com/track/abc123?si=token"
    current.source = "spotify"
    player = FakePlayer(current=current, queue=FakeQueue([FakeTrack("Digital Love", author="Daft Punk")]))

    queries = recommendations.recommendation_queries(player)

    assert "sprec:mix:track:abc123" in queries
    assert "sprec:mix:isrc:GBDUW0000053" in queries
    assert "ytmsearch:Daft Punk radio" in queries
    assert "ytmsearch:Daft Punk - Digital Love radio" in queries
    assert "ytmsearch:Daft Punk similar artists songs" in queries
    assert "ytmsearch:Daft Punk - One More Time similar songs" in queries
    assert any("Digital Love" in query for query in queries)


def test_rank_recommendations_deduplicates_current_queue_and_candidates() -> None:
    current = FakeTrack("Current", author="Artist")
    queued = FakeTrack("Queued", author="Artist")
    duplicate_current = FakeTrack("Current", author="Artist")
    duplicate_queued = FakeTrack("Queued", author="Artist")
    suggestion = FakeTrack("Fresh Song", author="Artist")
    duplicate_suggestion = FakeTrack("Fresh Song", author="Artist")
    player = FakePlayer(current=current, queue=FakeQueue([queued]))

    ranked = recommendations.rank_recommendations(
        [duplicate_current, duplicate_queued, suggestion, duplicate_suggestion],
        player,
        5,
    )

    assert [item.track for item in ranked] == [suggestion]
    assert ranked[0].label == "Artist - Fresh Song"


def test_rank_recommendations_filters_same_song_versions_and_keeps_queue_context_candidates() -> None:
    current = FakeTrack("Golden Hour", author="JVKE")
    queued = [FakeTrack("Kids", author="MGMT"), FakeTrack("Time to Pretend", author="MGMT")]
    variants = [
        FakeTrack("Golden Hour orchestral version", author="JVKE"),
        FakeTrack("Golden Hour acapella", author="JVKE"),
        FakeTrack("Golden Hour Fuji Kaze remix", author="JVKE"),
    ]
    candidate = FakeTrack("Little Dark Age", author="MGMT")
    player = FakePlayer(current=current, queue=FakeQueue(queued))

    ranked = recommendations.rank_recommendations([*variants, candidate], player, 5)

    assert [item.track for item in ranked] == [candidate]


def test_rank_recommendations_diversifies_current_artist() -> None:
    current = FakeTrack("Kids", author="MGMT")
    same_artist = [FakeTrack(f"MGMT Song {index}", author="MGMT") for index in range(3)]
    other_artist = [FakeTrack("Always Forever", author="Cults"), FakeTrack("505", author="Arctic Monkeys")]
    player = FakePlayer(current=current)

    ranked = recommendations.rank_recommendations([*same_artist, *other_artist], player, 5)

    assert sum(1 for item in ranked if item.track.author == "MGMT") == 1
    assert any(item.track.author == "Cults" for item in ranked)
    assert any(item.track.author == "Arctic Monkeys" for item in ranked)


async def test_recommendations_cache_by_current_track(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = []

    async def fake_load(seen_player):
        calls.append(seen_player)
        return [FakeTrack("Suggestion", author="Artist")]

    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)

    first = await recommendations.recommendations_for_player(player)
    second = await recommendations.recommendations_for_player(player)

    assert len(calls) == 1
    assert first == second
    assert first[0].track.title == "Suggestion"


async def test_recommendations_skip_load_when_refresh_not_allowed(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))

    async def fail_load(seen_player):
        raise AssertionError("progress refresh should not load recommendations")

    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fail_load)

    assert await recommendations.recommendations_for_player(player, allow_refresh=False) == ()


async def test_recommendations_return_stale_cache_when_refresh_not_allowed(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    now = time.monotonic()

    async def fake_load(seen_player):
        return [FakeTrack("Suggestion", author="Artist")]

    monkeypatch.setattr(recommendations.time, "monotonic", lambda: now)
    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)
    first = await recommendations.recommendations_for_player(player)

    async def fail_load(seen_player):
        raise AssertionError("stale cache should be reused for progress refresh")

    monkeypatch.setattr(recommendations.time, "monotonic", lambda: now + recommendations.RECOMMENDATION_CACHE_TTL + 1)
    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fail_load)

    assert await recommendations.recommendations_for_player(player, allow_refresh=False) == first


async def test_recommendations_cache_refreshes_when_queue_context_changes(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Queued A")]))
    calls = []

    async def fake_load(seen_player):
        calls.append(list(seen_player.queue))
        return [FakeTrack(f"Suggestion {len(calls)}", author="Artist")]

    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)

    first = await recommendations.recommendations_for_player(player)
    player.queue.clear()
    player.queue.put(FakeTrack("Queued B"))
    second = await recommendations.recommendations_for_player(player)

    assert len(calls) == 2
    assert first[0].track.title == "Suggestion 1"
    assert second[0].track.title == "Suggestion 2"


async def test_clear_guild_recommendation_cache_only_removes_that_guild(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    other_player = FakePlayer(current=FakeTrack("Other"))
    other_player.guild.id = 999

    async def fake_load(seen_player):
        return [FakeTrack(f"Suggestion {seen_player.guild.id}", author="Artist")]

    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)

    await recommendations.recommendations_for_player(player)
    await recommendations.recommendations_for_player(other_player)

    recommendations.clear_guild_recommendation_cache(player.guild.id)

    assert all(key[0] == other_player.guild.id for key in recommendations.recommendation_cache)


def test_prune_recommendation_cache_removes_expired_and_bounds_per_guild() -> None:
    now = time.monotonic()
    for index in range(recommendations.MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD + 5):
        recommendations.recommendation_cache[(123, f"fresh-{index}")] = recommendations.RecommendationCacheEntry(
            now + 60,
            (),
        )
    recommendations.recommendation_cache[(123, "expired")] = recommendations.RecommendationCacheEntry(now - 1, ())
    recommendations.recommendation_cache[(999, "other")] = recommendations.RecommendationCacheEntry(now + 60, ())

    recommendations.prune_recommendation_cache(now, 123)

    guild_keys = [key for key in recommendations.recommendation_cache if key[0] == 123]
    assert len(guild_keys) == recommendations.MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD
    assert (123, "expired") not in recommendations.recommendation_cache
    assert (999, "other") in recommendations.recommendation_cache


async def test_recommendations_refresh_after_cache_expiry(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    now = time.monotonic()
    calls = []

    async def fake_load(seen_player):
        calls.append(seen_player)
        return [FakeTrack(f"Suggestion {len(calls)}", author="Artist")]

    monkeypatch.setattr(recommendations.time, "monotonic", lambda: now)
    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)

    first = await recommendations.recommendations_for_player(player)
    monkeypatch.setattr(recommendations.time, "monotonic", lambda: now + recommendations.RECOMMENDATION_CACHE_TTL + 1)
    second = await recommendations.recommendations_for_player(player)

    assert len(calls) == 2
    assert first[0].track.title == "Suggestion 1"
    assert second[0].track.title == "Suggestion 2"


async def test_load_recommendation_candidates_continues_after_query_failure(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current", author="Artist"))
    found = FakeTrack("Found", author="Artist")
    calls = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("source unavailable")
        return [found]

    monkeypatch.setattr(recommendations, "search_lavalink", fake_search)

    candidates = await recommendations.load_recommendation_candidates(player)

    assert found in candidates
    assert len(calls) > 1


async def test_load_recommendation_candidates_uses_spotify_seed_recommendations(monkeypatch) -> None:
    current = FakeTrack("Kids", author="MGMT")
    spotify_seed = FakeTrack("Kids", author="MGMT", source="spotify")
    spotify_seed.identifier = "spotify-kids"
    suggestion = FakeTrack("Electric Feel", author="MGMT")
    player = FakePlayer(current=current)
    calls = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(query)
        if query.startswith("spsearch:"):
            return [spotify_seed]
        if query == "sprec:mix:track:spotify-kids":
            return [suggestion]
        return []

    monkeypatch.setattr(recommendations, "search_lavalink", fake_search)

    candidates = await recommendations.load_recommendation_candidates(player)

    assert "spsearch:MGMT - Kids" in calls
    assert "sprec:mix:track:spotify-kids" in calls
    assert suggestion in candidates


async def test_resolve_recommendation_value_uses_lavalink_search(monkeypatch) -> None:
    resolved = FakeTrack("Resolved", author="Artist")

    async def fake_search(query, requester, *, limit=None):
        assert query == resolved.uri
        assert requester == "tester"
        assert limit == 1
        return [resolved]

    monkeypatch.setattr(recommendations, "search_lavalink", fake_search)

    assert await recommendations.resolve_recommendation_value(resolved.uri, "tester") is resolved
