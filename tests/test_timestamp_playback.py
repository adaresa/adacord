import pytest

from adacord import track_requests
from adacord.player import play_next
from adacord.sources import LoadSummary
from adacord.utils import youtube_start_position
from conftest import FakePlayer, FakeQueue, FakeTrack


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


@pytest.mark.parametrize("queued", [False, True])
async def test_timestamp_reaches_play_once(monkeypatch, queued):
    track = FakeTrack("Volume 1", length=600000)
    track.extras = {"requester": "tester"}
    player = FakePlayer(current=FakeTrack("Current") if queued else None, playing=queued)

    async def load(query, requester):
        return [track], LoadSummary("Volume 1", 1, "youtube")

    async def save(player):
        pass

    monkeypatch.setattr(track_requests, "load_tracks", load)
    monkeypatch.setattr(track_requests, "save_player_state", save)
    await track_requests.queue_track_request(player, "https://youtu.be/8bCDdWSdMDM?t=241s", "tester")
    if queued:
        assert not player.play_calls
        await play_next(player)
    assert player.play_kwargs[-1]["start"] == 241000
    assert track.extras == {"requester": "tester"}
    player.queue = FakeQueue([track])
    await play_next(player)
    assert player.play_kwargs[-1]["start"] == 0


async def test_timestamp_past_end_does_not_queue(monkeypatch):
    async def load(query, requester):
        return [FakeTrack("Short", length=1000)], LoadSummary("Short", 1, "youtube")

    monkeypatch.setattr(track_requests, "load_tracks", load)
    player = FakePlayer(playing=False)
    with pytest.raises(track_requests.TrackRequestLoadError, match="outside"):
        await track_requests.queue_track_request(player, "https://youtu.be/x?t=241s", "tester")
    assert player.queue.is_empty
    assert not player.play_calls
