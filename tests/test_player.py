from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import wavelink

from adacord.player import (
    MissingVoicePermissions,
    add_tracks,
    clear_player,
    disconnect_player,
    ensure_player,
    play_next,
    set_loop_mode,
    set_volume,
)
from adacord.state import get_guild_state
from conftest import FakeGuild, FakePlayer, FakeQueue, FakeTrack, FakeVoiceChannel


async def test_add_tracks_starts_playback_when_idle() -> None:
    player = FakePlayer(volume=75, playing=False)
    tracks = [FakeTrack("First"), FakeTrack("Second")]

    await add_tracks(player, tracks)

    assert player.current is tracks[0]
    assert player.play_calls == [(tracks[0], 75)]
    assert list(player.queue) == [tracks[1]]


async def test_add_tracks_can_queue_without_starting() -> None:
    player = FakePlayer(playing=False)
    tracks = [FakeTrack("First")]

    await add_tracks(player, tracks, start_playback=False)

    assert player.current is None
    assert player.play_calls == []
    assert list(player.queue) == tracks


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


async def test_disconnect_player_cancels_idle_task_clears_and_disconnects() -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]))
    state = get_guild_state(player.guild.id)
    state.idle_task = asyncio.create_task(asyncio.sleep(30))

    await disconnect_player(player)
    await asyncio.sleep(0)

    assert state.idle_task is None
    assert player.disconnect_calls == 1
    assert player.guild.voice_client is None
