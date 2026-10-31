from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import wavelink

from adacord.persistence import load_state
from adacord.player import (
    MissingVoicePermissions,
    add_tracks,
    clear_player,
    disconnect_player,
    ensure_player,
    has_next_track,
    play_next,
    reconnect_player_voice,
    set_loop_mode,
    set_volume,
)
from adacord.state import get_guild_state
from conftest import FakeGuild, FakeNode, FakePlayer, FakeQueue, FakeTrack, FakeVoiceChannel


async def test_add_tracks_starts_playback_when_idle() -> None:
    player = FakePlayer(volume=75, playing=False)
    tracks = [FakeTrack("First"), FakeTrack("Second")]

    await add_tracks(player, tracks)

    assert player.current is tracks[0]
    assert player.play_calls == [(tracks[0], 75)]
    assert list(player.queue) == [tracks[1]]


async def test_clear_paused_player_then_add_tracks_starts_playback() -> None:
    player = FakePlayer(current=FakeTrack("Old"), paused=True)
    await clear_player(player)
    assert player.paused is False
    assert player.connected is True
    track = FakeTrack("New")
    await add_tracks(player, [track])
    assert player.current is track
    assert player.paused is False


async def test_add_tracks_recovers_orphaned_pause_without_interrupting_paused_track() -> None:
    player = FakePlayer(paused=True, queue=FakeQueue([FakeTrack("Waiting")]))
    await add_tracks(player, [FakeTrack("New")])
    assert player.current.title == "Waiting"
    assert player.play_kwargs[-1]["paused"] is False
    assert player.paused is False

    current = player.current
    await player.pause(True)
    await add_tracks(player, [FakeTrack("Later")])
    assert player.current is current
    assert player.paused is True
    assert len(player.play_calls) == 1


async def test_add_tracks_does_not_interrupt_active_player() -> None:
    current = FakeTrack("Current")
    player = FakePlayer(current=current, playing=True)
    tracks = [FakeTrack("Next")]

    await add_tracks(player, tracks)

    assert player.current is current
    assert player.play_calls == []
    assert list(player.queue) == tracks


async def test_play_next_handles_empty_queue() -> None:
    player = FakePlayer(queue=FakeQueue())

    assert await play_next(player) is None
    assert player.play_calls == []


async def test_play_next_uses_default_volume_when_player_volume_is_missing() -> None:
    track = FakeTrack("First")
    player = FakePlayer(queue=FakeQueue([track]), volume=None, playing=False)

    assert await play_next(player) is track
    assert player.play_calls == [(track, 50)]


async def test_play_next_leaves_an_active_track_playing() -> None:
    current = FakeTrack("Current")
    queued = FakeTrack("Queued")
    player = FakePlayer(current=current, queue=FakeQueue([queued]))

    assert await play_next(player) is None
    assert player.current is current
    assert player.play_calls == []
    assert list(player.queue) == [queued]


async def test_concurrent_play_next_calls_start_only_one_track() -> None:
    first = FakeTrack("First")
    second = FakeTrack("Second")
    player = FakePlayer(queue=FakeQueue([first, second]), playing=False)

    class SlowNode(FakeNode):
        async def fetch_player_info(self, guild_id: int):
            await asyncio.sleep(0)
            return await super().fetch_player_info(guild_id)

    player.node = SlowNode()

    results = await asyncio.gather(play_next(player), play_next(player))

    assert results == [first, None]
    assert player.current is first
    assert player.play_calls == [(first, 50)]
    assert list(player.queue) == [second]


async def test_ensure_player_rejects_missing_voice_permissions_before_connect() -> None:
    guild = FakeGuild()
    channel = FakeVoiceChannel(
        guild=guild,
        permissions=SimpleNamespace(view_channel=True, connect=False, speak=True),
        name="locked voice",
    )

    with pytest.raises(MissingVoicePermissions, match="Connect"):
        await ensure_player(guild, channel)

    assert channel.connect_kwargs is None


async def test_reconnect_player_voice_preserves_playback_state() -> None:
    guild = FakeGuild()
    current = FakeTrack("Current")
    current.position = 45_000
    queued = FakeTrack("Next")
    old_player = FakePlayer(guild=guild, current=current, queue=FakeQueue([queued]), volume=80, paused=True)
    new_player = FakePlayer(guild=guild, volume=50, playing=False)
    channel = FakeVoiceChannel(guild=guild, player=new_player)
    old_player.channel = channel
    guild.voice_client = old_player
    state = get_guild_state(guild.id)
    state.loop_mode = "queue"

    result = await reconnect_player_voice(old_player)

    assert result is new_player
    assert old_player.disconnect_calls == 1
    assert new_player.volume == 80
    assert new_player.current is current
    assert new_player.play_kwargs[-1] == {"start": 45_000, "add_history": False, "paused": False}
    assert list(new_player.queue) == [queued]
    assert new_player.queue.mode == wavelink.QueueMode.loop_all
    assert state.voice_refresh_in_progress is False


@pytest.mark.parametrize("failure", [wavelink.ChannelTimeoutException, TimeoutError])
async def test_ensure_player_retries_timeout_with_clean_voice_client(monkeypatch, failure) -> None:
    guild = FakeGuild()
    channel = FakeVoiceChannel(guild=guild)
    attempts = []

    async def connect(**kwargs):
        assert get_guild_state(guild.id).connect_lock.locked()
        assert guild.voice_client is None
        player = FakePlayer(guild=guild)
        player.channel = channel
        attempts.append(player)
        if len(attempts) == 1:
            raise failure("voice handshake timed out")
        return player

    monkeypatch.setattr(channel, "connect", connect)
    result = await ensure_player(guild, channel)

    assert len(attempts) == 2
    assert attempts[0].disconnect_calls == 1
    assert result is attempts[1]
    assert guild.voice_client is result
    assert not get_guild_state(guild.id).connect_lock.locked()


@pytest.mark.parametrize("failure, expected_attempts", [
    (wavelink.ChannelTimeoutException, 2),
    (RuntimeError, 1),
    (asyncio.CancelledError, 1),
])
async def test_failed_ensure_player_cleans_up_and_releases_lock(monkeypatch, failure, expected_attempts) -> None:
    guild = FakeGuild()
    channel = FakeVoiceChannel(guild=guild)
    attempts = []

    async def connect(**kwargs):
        assert guild.voice_client is None
        attempts.append(FakePlayer(guild=guild))
        raise failure("connection failed")

    monkeypatch.setattr(channel, "connect", connect)
    with pytest.raises(failure):
        await ensure_player(guild, channel)

    assert len(attempts) == expected_attempts
    assert all(player.disconnect_calls == 1 for player in attempts)
    assert guild.voice_client is None
    assert not get_guild_state(guild.id).connect_lock.locked()


@pytest.mark.parametrize("failure", [wavelink.ChannelTimeoutException, TimeoutError])
async def test_voice_refresh_retries_timeout_and_restores_tracks(monkeypatch, failure) -> None:
    guild = FakeGuild()
    current = FakeTrack("Current")
    current.position = 45_000
    queued = FakeTrack("Next")
    old = FakePlayer(guild=guild, current=current, queue=FakeQueue([queued]), paused=True, volume=80)
    channel = FakeVoiceChannel(guild=guild)
    old.channel = channel
    state = get_guild_state(guild.id)
    state.loop_mode = "queue"
    attempts = []

    async def connect(**kwargs):
        assert state.voice_refresh_in_progress
        assert guild.voice_client is None
        player = FakePlayer(guild=guild)
        player.channel = channel
        attempts.append(player)
        if len(attempts) == 1:
            raise failure("voice handshake timed out")
        return player

    monkeypatch.setattr(channel, "connect", connect)
    result = await reconnect_player_voice(old)

    assert len(attempts) == 2
    assert attempts[0].disconnect_calls == 1
    assert result.current is current
    assert result.play_kwargs == [{"start": 45_000, "add_history": False, "paused": False}]
    assert list(result.queue) == [queued]
    assert result.volume == 80
    assert result.queue.mode == wavelink.QueueMode.loop_all
    assert not state.voice_refresh_in_progress


@pytest.mark.parametrize("failure, expected_attempts", [
    (wavelink.ChannelTimeoutException, 2),
    (RuntimeError, 1),
    (asyncio.CancelledError, 1),
])
async def test_failed_voice_refresh_cleans_up_and_keeps_saved_state(monkeypatch, failure, expected_attempts) -> None:
    guild = FakeGuild()
    current = FakeTrack("Current")
    current.position = 45_000
    old = FakePlayer(guild=guild, current=current, queue=FakeQueue([FakeTrack("Next")]), paused=True, volume=80)
    channel = FakeVoiceChannel(guild=guild)
    old.channel = channel
    attempts = []

    async def connect(**kwargs):
        assert guild.voice_client is None
        player = FakePlayer(guild=guild)
        attempts.append(player)
        raise failure("connection failed")

    monkeypatch.setattr(channel, "connect", connect)
    with pytest.raises(failure):
        await reconnect_player_voice(old)

    assert len(attempts) == expected_attempts
    assert all(player.disconnect_calls == 1 for player in attempts)
    assert guild.voice_client is None
    state = get_guild_state(guild.id)
    assert not state.voice_refresh_in_progress
    assert not state.connect_lock.locked()
    saved = load_state()["guilds"][str(guild.id)]
    assert saved["current"]["title"] == "Current"
    assert saved["queue"][0]["title"] == "Next"
    assert saved["position"] == 45_000
    assert saved["volume"] == 80
    assert saved["paused"] is True


async def test_set_volume_clamps_values() -> None:
    player = FakePlayer()

    await set_volume(player, 250)
    await set_volume(player, -5)

    assert player.volume_calls == [200, 0]


def test_set_loop_mode_updates_state_and_queue_mode() -> None:
    player = FakePlayer()

    set_loop_mode(player, "track")
    assert get_guild_state(player.guild.id).loop_mode == "track"
    assert player.queue.mode == wavelink.QueueMode.loop

    set_loop_mode(player, "queue")
    assert get_guild_state(player.guild.id).loop_mode == "queue"
    assert player.queue.mode == wavelink.QueueMode.loop_all

    set_loop_mode(player, "none")
    assert get_guild_state(player.guild.id).loop_mode == "none"
    assert player.queue.mode == wavelink.QueueMode.normal


def playable(title: str) -> wavelink.Playable:
    info = {
        "identifier": title,
        "isSeekable": True,
        "author": "Artist",
        "length": 180_000,
        "isStream": False,
        "position": 0,
        "title": title,
        "sourceName": "youtube",
    }
    return wavelink.Playable({"encoded": title, "info": info, "pluginInfo": {}, "userData": {}})


def test_has_next_track_follows_wavelink_loop_modes() -> None:
    player = FakePlayer()
    player.queue = wavelink.Queue()
    first, second = playable("First"), playable("Second")

    def play_from_queue() -> wavelink.Playable:
        # wavelink.Player.play() records each played track in the queue history.
        track = player.queue.get()
        player.queue.history.put(track)
        return track

    player.queue.put([first, second])
    assert play_from_queue() is first
    assert play_from_queue() is second
    assert not has_next_track(player)

    set_loop_mode(player, "track")
    assert has_next_track(player)
    assert play_from_queue() is second

    set_loop_mode(player, "queue")
    assert has_next_track(player)
    assert play_from_queue() is first


def test_set_loop_mode_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        set_loop_mode(FakePlayer(), "forever")


async def test_clear_player_resets_queue_history_loop_and_active_playback() -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]))
    player.queue.history.append(FakeTrack("Old"))
    get_guild_state(player.guild.id).loop_mode = "queue"

    await clear_player(player)

    assert player.queue.is_empty
    assert player.queue.history == []
    assert get_guild_state(player.guild.id).loop_mode == "none"
    assert player.skip_calls == [True]


async def test_disconnect_player_clears_and_disconnects() -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]))

    await disconnect_player(player)

    assert player.queue.is_empty
    assert player.disconnect_calls == 1
    assert player.guild.voice_client is None
