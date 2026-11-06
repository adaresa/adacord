from __future__ import annotations

import time
import asyncio

import pytest

from adacord import recommendations
from conftest import FakePlayer, FakeQueue, FakeTrack


@pytest.fixture(autouse=True)
def clear_recommendation_cache():
    recommendations.clear_recommendation_cache()
    yield
    recommendations.clear_recommendation_cache()



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


async def test_recommendations_share_concurrent_refresh(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = 0

    async def fake_load(seen_player):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return [FakeTrack("Suggestion", author="Artist")]

    monkeypatch.setattr(recommendations, "load_recommendation_candidates", fake_load)

    first, second = await asyncio.gather(
        recommendations.recommendations_for_player(player),
        recommendations.recommendations_for_player(player),
    )

    assert calls == 1
    assert first == second


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
        key = (123, f"fresh-{index}")
        recommendations.recommendation_cache[key] = recommendations.RecommendationCacheEntry(
            now + 60,
            (),
        )
        recommendations.recommendation_load_locks[key] = asyncio.Lock()
    expired_key = (123, "expired")
    recommendations.recommendation_cache[expired_key] = recommendations.RecommendationCacheEntry(now - 1, ())
    recommendations.recommendation_load_locks[expired_key] = asyncio.Lock()
    other_key = (999, "other")
    recommendations.recommendation_cache[other_key] = recommendations.RecommendationCacheEntry(now + 60, ())
    recommendations.recommendation_load_locks[other_key] = asyncio.Lock()

    recommendations.prune_recommendation_cache(now, 123)

    guild_keys = [key for key in recommendations.recommendation_cache if key[0] == 123]
    assert len(guild_keys) == recommendations.MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD
    assert expired_key not in recommendations.recommendation_cache
    assert expired_key not in recommendations.recommendation_load_locks
    assert other_key in recommendations.recommendation_cache
    assert other_key in recommendations.recommendation_load_locks
    assert set(recommendations.recommendation_load_locks) == set(recommendations.recommendation_cache)


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



async def test_resolve_recommendation_value_uses_lavalink_search(monkeypatch) -> None:
    resolved = FakeTrack("Resolved", author="Artist")

    async def fake_search(query, requester, *, limit=None):
        assert query == resolved.uri
        assert requester == "tester"
        assert limit == 1
        return [resolved]

    monkeypatch.setattr(recommendations, "search_lavalink", fake_search)

    assert await recommendations.resolve_recommendation_value(resolved.uri, "tester") is resolved


def yt_track(title: str, author: str, video_id: str, **kwargs) -> FakeTrack:
    track = FakeTrack(title, author=author, source="youtube", **kwargs)
    track.identifier = video_id
    track.uri = f"https://www.youtube.com/watch?v={video_id}"
    track.raw_data["info"].update(identifier=video_id, uri=track.uri, sourceName="youtube")
    return track


def fake_radios(monkeypatch, radios: dict[str, list[FakeTrack]], searches: dict[str, list[FakeTrack]] | None = None):
    calls: list[str] = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(query)
        for video_id, tracks in radios.items():
            if query == recommendations.radio_url(video_id):
                return list(tracks)
        if searches and query in searches:
            return list(searches[query])[:limit]
        raise RuntimeError(f"unexpected query {query}")

    monkeypatch.setattr(recommendations, "search_lavalink", fake_search)
    return calls


def test_radio_url_uses_youtube_music_radio() -> None:
    assert recommendations.radio_url("FGBhQbmPwH8") == (
        "https://music.youtube.com/watch?v=FGBhQbmPwH8&list=RDAMVMFGBhQbmPwH8"
    )


async def test_suggestions_come_from_the_current_songs_radio(monkeypatch) -> None:
    current = yt_track("Daft Punk - One More Time (Official Video)", "Daft Punk", "FGBhQbmPwH8")
    radio = [
        current,
        yt_track("Daft Punk - Around The World", "Daft Punk", "K0HSD_i2DvA"),
        yt_track("Stardust - Music Sounds Better With You", "Stardust", "FQlAEiCb8m0"),
        yt_track("Kylie Minogue - Can't Get You Out Of My Head", "Kylie Minogue", "c18441Eh_WE"),
    ]
    fake_radios(monkeypatch, {"FGBhQbmPwH8": radio})
    player = FakePlayer(current=current)

    suggestions = await recommendations.recommendations_for_player(player)

    # The current artist only fills a slot once other artists run out.
    assert [item.track.author for item in suggestions] == ["Stardust", "Kylie Minogue", "Daft Punk"]
    assert suggestions[0].label == "Stardust - Music Sounds Better With You"


async def test_suggestions_skip_played_queued_and_reuploaded_songs(monkeypatch) -> None:
    current = yt_track("One More Time", "Daft Punk", "FGBhQbmPwH8")
    played = yt_track("Bob Sinclar - World Hold On (Official HD Video)", "Bob Sinclar", "XpdpW0z9xnQ")
    queued = yt_track("Kylie Minogue - Can't Get You Out Of My Head", "Kylie Minogue", "c18441Eh_WE")
    radio = [
        yt_track("Daft Punk - One More Time (Lyrics)", "PitchPerfect Channel", "hVrVPBLqGfQ"),
        yt_track("Bob Sinclar - World Hold On (Lyrics)", "Some Lyrics Channel", "aaaaaaaaaaa"),
        yt_track("Kylie Minogue - Can't Get You Out Of My Head (Official Video)", "Kylie Minogue", "bbbbbbbbbbb"),
        yt_track("Stardust - Music Sounds Better With You", "Stardust", "FQlAEiCb8m0"),
    ]
    fake_radios(monkeypatch, {"FGBhQbmPwH8": radio, "c18441Eh_WE": []})
    player = FakePlayer(current=current, queue=FakeQueue([queued]))
    player.queue.history.append(played)

    suggestions = await recommendations.recommendations_for_player(player)

    assert [item.track.identifier for item in suggestions] == ["FQlAEiCb8m0"]


async def test_suggestions_put_remixes_and_live_versions_last(monkeypatch) -> None:
    current = yt_track("One More Time", "Daft Punk", "FGBhQbmPwH8")
    remix = yt_track("Gala - Freed From Desire (Progressive House Remix)", "H2Beats", "u-D_zYjm2U8")
    original = yt_track("Alice Deejay - Better Off Alone (Official Video)", "Alice Deejay", "Lgs9QUtWc3M")
    fake_radios(monkeypatch, {"FGBhQbmPwH8": [remix, original]})

    suggestions = await recommendations.recommendations_for_player(FakePlayer(current=current))

    assert [item.track for item in suggestions] == [original, remix]


async def test_suggestions_blend_queue_radios_behind_the_current_song(monkeypatch) -> None:
    current = yt_track("Current", "Artist A", "AAAAAAAAAAA")
    queued = yt_track("Queued", "Artist B", "BBBBBBBBBBB")
    current_radio = [yt_track(f"Current pick {i}", f"Current Artist {i}", f"c{i:010d}") for i in range(4)]
    queue_radio = [yt_track(f"Queue pick {i}", f"Queue Artist {i}", f"q{i:010d}") for i in range(4)]
    fake_radios(monkeypatch, {"AAAAAAAAAAA": current_radio, "BBBBBBBBBBB": queue_radio})

    suggestions = await recommendations.recommendations_for_player(
        FakePlayer(current=current, queue=FakeQueue([queued]))
    )

    assert [item.track.title for item in suggestions][:6] == [
        "Current pick 0",
        "Queue pick 0",
        "Current pick 1",
        "Current pick 2",
        "Queue pick 1",
        "Current pick 3",
    ]


async def test_failed_radio_seed_does_not_hide_other_suggestions(monkeypatch) -> None:
    current = yt_track("Current", "Artist A", "AAAAAAAAAAA")
    queued = yt_track("Queued", "Artist B", "BBBBBBBBBBB")
    pick = yt_track("Pick", "Artist C", "CCCCCCCCCCC")
    fake_radios(monkeypatch, {"BBBBBBBBBBB": [pick]})

    suggestions = await recommendations.recommendations_for_player(
        FakePlayer(current=current, queue=FakeQueue([queued]))
    )

    assert [item.track for item in suggestions] == [pick]


async def test_non_youtube_seed_finds_a_youtube_video_first(monkeypatch) -> None:
    current = FakeTrack("Song", author="SoundCloud Artist", source="soundcloud")
    found = yt_track("SoundCloud Artist - Song", "SoundCloud Artist", "SSSSSSSSSSS")
    pick = yt_track("Pick", "Artist C", "CCCCCCCCCCC")
    calls = fake_radios(
        monkeypatch,
        {"SSSSSSSSSSS": [pick]},
        searches={"ytsearch:SoundCloud Artist - Song": [found]},
    )

    suggestions = await recommendations.recommendations_for_player(FakePlayer(current=current))

    assert calls[0] == "ytsearch:SoundCloud Artist - Song"
    assert [item.track for item in suggestions] == [pick]


async def test_radio_is_cached_per_video(monkeypatch) -> None:
    calls = fake_radios(monkeypatch, {"AAAAAAAAAAA": [yt_track("Pick", "Artist", "CCCCCCCCCCC")]})

    first = await recommendations.load_radio("AAAAAAAAAAA")
    second = await recommendations.load_radio("AAAAAAAAAAA")

    assert first == second
    assert calls == [recommendations.radio_url("AAAAAAAAAAA")]


async def test_autoplay_picks_a_fresh_song_by_a_different_artist(monkeypatch) -> None:
    last = yt_track("Last", "Artist A", "AAAAAAAAAAA")
    earlier = yt_track("Earlier", "Artist B", "BBBBBBBBBBB")
    radio = [
        last,
        earlier,
        yt_track("Another by A", "Artist A", "DDDDDDDDDDD"),
        yt_track("Fresh", "Artist C", "CCCCCCCCCCC"),
    ]
    fake_radios(monkeypatch, {"AAAAAAAAAAA": radio})
    player = FakePlayer()
    player.queue.history.extend([earlier, last])

    pick = await recommendations.autoplay_track(player, last)

    assert pick is radio[3]


async def test_autoplay_falls_back_to_the_same_artist_when_nothing_else_is_left(monkeypatch) -> None:
    last = yt_track("Last", "Artist A", "AAAAAAAAAAA")
    same_artist = yt_track("Another by A", "Artist A", "DDDDDDDDDDD")
    fake_radios(monkeypatch, {"AAAAAAAAAAA": [last, same_artist]})

    assert await recommendations.autoplay_track(FakePlayer(), last) is same_artist


async def test_autoplay_vibe_seeds_from_a_search(monkeypatch) -> None:
    vibe_seed = yt_track("French house classic", "Vibe Artist", "VVVVVVVVVVV")
    pick = yt_track("Vibe pick", "Artist C", "CCCCCCCCCCC")
    calls = fake_radios(
        monkeypatch,
        {"VVVVVVVVVVV": [vibe_seed, pick]},
        searches={"ytsearch:90s french house": [vibe_seed]},
    )

    player = FakePlayer()
    assert await recommendations.autoplay_track(player, None, vibe="90s french house") is vibe_seed
    assert "ytsearch:90s french house" in calls

    player.queue.history.append(vibe_seed)
    assert await recommendations.autoplay_track(player, vibe_seed, vibe="90s french house") is pick


async def test_autoplay_returns_none_without_seeds() -> None:
    assert await recommendations.autoplay_track(FakePlayer(), None) is None


async def test_fan_reuploads_count_toward_the_current_artist(monkeypatch) -> None:
    current = yt_track("Arctic Monkeys - Do I Wanna Know? (Official Video)", "Official Arctic Monkeys", "bpOSxM0rNPM")
    reupload = yt_track("Arctic Monkeys - Knee Socks", "Miguel Bellan", "kkkkkkkkkkk")
    credited_after = yt_track("Why'd you only call me when you're high - Arctic Monkeys", "Carlos", "wwwwwwwwwww")
    other = yt_track("The Neighbourhood - Sweater Weather", "Dan Music", "nnnnnnnnnnn")
    fake_radios(monkeypatch, {"bpOSxM0rNPM": [reupload, credited_after, other]})

    suggestions = await recommendations.recommendations_for_player(FakePlayer(current=current))

    assert suggestions[0].track is other


async def test_radio_falls_back_to_another_upload_when_a_video_has_none(monkeypatch) -> None:
    current = yt_track("MGMT - Kids (Official HD Video)", "MGMT", "fe4EK4HSPkI")
    other_upload = yt_track("MGMT - Kids (Lyrics)", "7clouds", "rdKfuwy6LUM")
    pick = yt_track("Franz Ferdinand - Take Me Out", "Franz Ferdinand", "fffffffffff")
    fake_radios(
        monkeypatch,
        {"rdKfuwy6LUM": [pick]},
        searches={"ytsearch:MGMT - Kids": [current, other_upload]},
    )

    suggestions = await recommendations.recommendations_for_player(FakePlayer(current=current))

    assert [item.track for item in suggestions] == [pick]


def test_suggestion_labels_drop_upload_decorations() -> None:
    track = yt_track("Major Lazer & DJ Snake - Lean On (feat. MØ) [Official 4K Music Video]", "Major Lazer", "YqeW9_5kURI")

    assert recommendations.format_recommendation_label(track) == "Major Lazer & DJ Snake - Lean On (feat. MØ)"
