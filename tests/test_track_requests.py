from adacord import track_requests
from adacord.sources import LoadSummary
from conftest import FakePlayer, FakeQueue, FakeTrack


async def test_queue_track_request_can_insert_tracks_first_preserving_order(monkeypatch) -> None:
    queued = FakeTrack("Queued")
    first = FakeTrack("First")
    second = FakeTrack("Second")
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([queued]))
    saved = []
    cleared = []

    async def fake_load_tracks(query, requester):
        return [first, second], LoadSummary("Playlist", 2, "youtube")

    async def fake_save(seen_player):
        saved.append(seen_player)

    monkeypatch.setattr(track_requests, "load_tracks", fake_load_tracks)
    monkeypatch.setattr(track_requests, "save_player_state", fake_save)
    monkeypatch.setattr(track_requests, "clear_guild_recommendation_cache", lambda guild_id: cleared.append(guild_id))

    result = await track_requests.queue_track_request(player, "playlist", "tester", play_first=True)

    assert result.tracks == [first, second]
    assert result.was_idle is False
    assert list(player.queue) == [first, second, queued]
    assert player.current.title == "Current"
    assert saved == [player]
    assert cleared == [player.guild.id]


async def test_queue_track_request_play_first_starts_front_track_when_not_playing(monkeypatch) -> None:
    queued = FakeTrack("Queued")
    first = FakeTrack("First")
    player = FakePlayer(queue=FakeQueue([queued]), playing=False)

    async def fake_load_tracks(query, requester):
        return [first], LoadSummary("First", 1, "youtube")

    async def fake_save(player):
        return None

    monkeypatch.setattr(track_requests, "load_tracks", fake_load_tracks)
    monkeypatch.setattr(track_requests, "save_player_state", fake_save)
    monkeypatch.setattr(track_requests, "clear_guild_recommendation_cache", lambda guild_id: None)

    result = await track_requests.queue_track_request(player, "first", "tester", play_first=True)

    assert result.tracks == [first]
    assert player.current is first
    assert list(player.queue) == [queued]
