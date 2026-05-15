from __future__ import annotations

from adacord import events
from adacord.state import get_guild_state
from conftest import FakeGuild, FakePlayer, FakeTrack


class FakeBot:
    def __init__(self, guild: FakeGuild):
        self.guild = guild

    def get_guild(self, guild_id: int):
        return self.guild if guild_id == self.guild.id else None


def saved_track(track: FakeTrack) -> dict:
    return {"raw_data": track.raw_data, "extras": track.extras, "title": track.title, "uri": track.uri}


async def test_reconnect_saved_voice_playback_restores_connected_player(monkeypatch) -> None:
    guild = FakeGuild()
    player = FakePlayer(guild=guild)
    guild.voice_client = None
    calls = []

    async def fake_restore(bot, guild_id, saved):
        calls.append((bot, guild_id, saved))
        player.connected = True
        guild.voice_client = player

    monkeypatch.setattr(events, "restore_guild_playback_state", fake_restore)
    monkeypatch.setattr(events, "get_player", lambda seen_guild: player if seen_guild is guild else None)

    saved = {"voice_channel_id": 456, "current": saved_track(FakeTrack("Current")), "queue": []}

    await events.reconnect_saved_voice_playback(FakeBot(guild), guild.id, saved)

    assert calls == [(calls[0][0], guild.id, saved)]


async def test_reconnect_saved_voice_playback_retries_and_preserves_state(monkeypatch) -> None:
    guild = FakeGuild()
    calls = []
    sleeps = []

    async def fake_restore(bot, guild_id, saved):
        calls.append((guild_id, saved))
        raise RuntimeError("voice unavailable")

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(events, "restore_guild_playback_state", fake_restore)
    monkeypatch.setattr(events.asyncio, "sleep", fake_sleep)

    saved = {"voice_channel_id": 456, "current": saved_track(FakeTrack("Current")), "queue": []}

    await events.reconnect_saved_voice_playback(FakeBot(guild), guild.id, saved)

    assert len(calls) == len(events.VOICE_RECONNECT_ATTEMPT_DELAYS)
    assert sleeps == [1.0, 3.0]


async def test_bot_voice_disconnect_without_saved_music_does_not_reconnect(monkeypatch) -> None:
    guild = FakeGuild()
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(events, "load_state", lambda: {"version": 1, "guilds": {}})
    monkeypatch.setattr(events.asyncio, "create_task", fake_create_task)

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id)

    assert scheduled == []
    assert get_guild_state(guild.id).voice_reconnect_task is None


async def test_bot_voice_disconnect_schedules_reconnect_when_saved_music_exists(monkeypatch) -> None:
    guild = FakeGuild()
    saved = {"voice_channel_id": 456, "current": saved_track(FakeTrack("Current")), "queue": []}
    scheduled = []

    class FakeTask:
        def done(self):
            return False

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return FakeTask()

    monkeypatch.setattr(events, "load_state", lambda: {"version": 1, "guilds": {str(guild.id): saved}})
    monkeypatch.setattr(events.asyncio, "create_task", fake_create_task)

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id)

    assert len(scheduled) == 1
    assert get_guild_state(guild.id).voice_reconnect_task is not None
