from types import SimpleNamespace

from adacord import events
from conftest import FakePlayer, FakeTrack


async def test_direct_link_recovery_keeps_exact_upload(monkeypatch):
    failed = FakeTrack("Volume 1", length=600000)
    failed.uri = "https://www.youtube.com/watch?v=8bCDdWSdMDM"
    failed.extras = {"query": failed.uri + "&t=242s"}
    replacement = FakeTrack("Volume 1", length=600000)
    replacement.uri = failed.uri
    player = FakePlayer(current=failed, playing=True)
    failed.position = 242000
    calls = []

    async def search(query, requester, *, limit):
        calls.append(query)
        return [replacement]

    async def alternative(*args, **kwargs):
        raise AssertionError("An exact upload must never be substituted")

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(events, "search_lavalink", search)
    monkeypatch.setattr(events, "search_youtube_alternative", alternative)
    monkeypatch.setattr(events, "save_player_state", noop)
    monkeypatch.setattr(events, "update_display_for_guild", noop)
    await events.handle_track_exception(SimpleNamespace(
        player=player, track=failed,
        exception="All clients failed to load the item. This video requires login.",
    ))
    assert calls == [failed.uri]
    assert player.current.uri == failed.uri
    assert player.play_kwargs[-1]["start"] == 242000
