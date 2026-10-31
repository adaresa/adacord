from __future__ import annotations

from types import SimpleNamespace

import pytest
import wavelink

from adacord import events
from adacord.persistence import track_payload
from adacord.state import get_guild_state
from conftest import FakeBot, FakeGuild, FakePlayer, FakeQueue, FakeTrack


@pytest.fixture
def track_end_calls(monkeypatch) -> list:
    calls = []

    async def fake_play_next(seen_player):
        calls.append(("play_next", seen_player))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    monkeypatch.setattr(events, "play_next", fake_play_next)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    return calls


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

    saved = {"voice_channel_id": 456, "current": track_payload(FakeTrack("Current")), "queue": []}

    bot = FakeBot(guild)
    await events.reconnect_saved_voice_playback(bot, guild.id, saved)

    assert calls == [(bot, guild.id, saved)]


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

    saved = {"voice_channel_id": 456, "current": track_payload(FakeTrack("Current")), "queue": []}

    await events.reconnect_saved_voice_playback(FakeBot(guild), guild.id, saved)

    assert len(calls) == len(events.VOICE_RECONNECT_ATTEMPT_DELAYS)
    assert sleeps == [1.0, 3.0]


async def test_bot_voice_disconnect_without_saved_music_does_not_reconnect(monkeypatch, captured_tasks) -> None:
    guild = FakeGuild()

    monkeypatch.setattr(events, "load_state", lambda: {"version": 1, "guilds": {}})

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id)

    assert captured_tasks == []
    assert get_guild_state(guild.id).voice_reconnect_task is None


async def test_bot_voice_disconnect_during_voice_refresh_does_not_reconnect(captured_tasks) -> None:
    guild = FakeGuild()
    state = get_guild_state(guild.id)
    state.voice_refresh_in_progress = True

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id)

    assert captured_tasks == []
    assert state.voice_reconnect_task is None


async def test_bot_voice_disconnect_schedules_reconnect_when_saved_music_exists(monkeypatch, captured_tasks) -> None:
    guild = FakeGuild()
    saved = {"voice_channel_id": 456, "current": track_payload(FakeTrack("Current")), "queue": []}

    monkeypatch.setattr(events, "load_state", lambda: {"version": 1, "guilds": {str(guild.id): saved}})

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id, FakePlayer(guild=guild))

    assert captured_tasks == [get_guild_state(guild.id).voice_reconnect_task]


async def test_bot_voice_disconnect_saves_live_position_before_reconnect(monkeypatch, captured_tasks) -> None:
    guild = FakeGuild()
    current = FakeTrack("Current")
    current.position = 45_000
    player = FakePlayer(guild=guild, current=current, playing=True)
    saved = {"voice_channel_id": 456, "current": track_payload(current), "queue": [], "position": 45_000}
    saves = []

    async def fake_save(seen_player):
        saves.append(seen_player.position)

    monkeypatch.setattr(events, "save_player_state", fake_save)
    monkeypatch.setattr(events, "load_state", lambda: {"version": 1, "guilds": {str(guild.id): saved}})

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id, player)

    assert saves == [45_000]
    assert len(captured_tasks) == 1


async def test_server_disconnect_does_not_resurrect_saved_music(monkeypatch) -> None:
    guild = FakeGuild()
    saved = {"voice_channel_id": 456, "current": track_payload(FakeTrack("Current")), "queue": []}
    calls = []
    state = get_guild_state(guild.id)

    class PendingReconnect:
        def done(self):
            return False

        def cancel(self):
            calls.append("cancel")

    state.voice_reconnect_task = PendingReconnect()

    async def clear(guild_id):
        calls.append(("clear", guild_id))

    async def update(guild_id, player):
        calls.append(("update", guild_id, player))

    monkeypatch.setattr(events, "load_state", lambda: {"guilds": {str(guild.id): saved}})
    monkeypatch.setattr(events, "clear_saved_guild_state", clear)
    monkeypatch.setattr(events, "update_display_for_guild", update)

    await events.handle_bot_voice_disconnect(FakeBot(guild), guild.id)

    assert calls == ["cancel", ("clear", guild.id), ("update", guild.id, None)]
    assert state.voice_reconnect_task is None


@pytest.mark.parametrize(
    ("queued", "looping", "plays_next"),
    [(True, False, True), (False, False, False), (False, True, True)],
    ids=["queued", "empty_queue", "looped_track"],
)
async def test_track_end_plays_next_only_when_a_track_follows(track_end_calls, queued, looping, plays_next) -> None:
    current = FakeTrack("Looped") if looping else None
    player = FakePlayer(current=current, queue=FakeQueue([FakeTrack("Next")] if queued else []))
    if looping:
        player.queue.mode = wavelink.QueueMode.loop
        player.queue.loaded = current

    await events.handle_track_end(SimpleNamespace(player=player))

    expected = [("play_next", player)] if plays_next else []
    assert track_end_calls == expected + [("update", player.guild.id, player)]


async def test_retired_player_track_end_preserves_replacement_display_and_state(monkeypatch) -> None:
    player = FakePlayer(queue=FakeQueue([FakeTrack("Old queued track")]))
    player.connected = False
    state = get_guild_state(player.guild.id)
    replacement_message = object()
    state.display_message = replacement_message
    calls = []

    async def unexpected(*args):
        calls.append(args)

    monkeypatch.setattr(events, "play_next", unexpected)
    monkeypatch.setattr(events, "update_display_for_guild", unexpected)
    monkeypatch.setattr(events, "save_player_state", unexpected)

    await events.handle_track_end(SimpleNamespace(player=player, reason="stopped"))

    assert calls == []
    assert state.display_message is replacement_message
    assert len(list(player.queue)) == 1


async def test_track_end_ignores_non_advancing_reasons(track_end_calls) -> None:
    queued = [FakeTrack("Next")]
    player = FakePlayer(queue=FakeQueue(queued))

    await events.handle_track_end(SimpleNamespace(player=player, reason="loadFailed"))
    await events.handle_track_end(SimpleNamespace(player=player, reason="replaced"))

    assert track_end_calls == []
    assert list(player.queue) == queued


async def test_track_end_refreshes_display_and_saves_when_next_track_fails_to_start(monkeypatch) -> None:
    player = FakePlayer(queue=FakeQueue([FakeTrack("Next")]))
    calls = []

    async def failing_play_next(seen_player):
        raise RuntimeError("Lavalink did not finish connecting to voice (missing).")

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    async def fake_save(seen_player):
        calls.append(("save", seen_player))

    monkeypatch.setattr(events, "play_next", failing_play_next)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    monkeypatch.setattr(events, "save_player_state", fake_save)

    await events.handle_track_end(SimpleNamespace(player=player, reason="finished"))

    assert calls == [("update", player.guild.id, player), ("save", player)]


async def test_track_exception_skips_to_next_track_when_recovery_fails(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    following = FakeTrack("Next")
    # Lavalink's TrackEnd (loadFailed) has already cleared the player's current track.
    player = FakePlayer(current=None, playing=False, queue=FakeQueue([following]), volume=70)
    calls = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(("search", query, requester, limit))
        return []

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player.current))

    async def fake_save(seen_player):
        calls.append(("save", seen_player.current))

    monkeypatch.setattr(events, "search_lavalink", fake_search)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    monkeypatch.setattr(events, "save_player_state", fake_save)

    await events.handle_track_exception(SimpleNamespace(player=player, track=failed, exception="expired stream"))

    assert player.current is following
    assert player.play_calls == [(following, 70)]
    assert list(player.queue) == []
    assert calls == [
        ("search", failed.uri, events.RECOVERY_REQUESTER, 1),
        ("update", player.guild.id, following),
        ("save", following),
    ]


async def test_failed_track_does_not_skip_a_track_that_already_replaced_it(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    already_playing = FakeTrack("Already playing")
    queued = [FakeTrack("Queued")]
    player = FakePlayer(current=already_playing, playing=True, queue=FakeQueue(queued))

    async def fake_search(query, requester, *, limit=None):
        return []

    async def ignore(*args):
        pass

    monkeypatch.setattr(events, "search_lavalink", fake_search)
    monkeypatch.setattr(events, "update_display_for_guild", ignore)
    monkeypatch.setattr(events, "save_player_state", ignore)

    await events.handle_track_exception(SimpleNamespace(player=player, track=failed, exception="broken stream"))

    assert player.current is already_playing
    assert player.play_calls == []
    assert player.skip_calls == []
    assert list(player.queue) == queued


async def test_failed_track_from_retired_player_does_not_advance_its_queue(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    queued = [FakeTrack("Old queued track")]
    player = FakePlayer(current=failed, playing=True, queue=FakeQueue(queued))
    player.connected = False
    calls = []

    async def unexpected(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(events, "search_lavalink", unexpected)
    monkeypatch.setattr(events, "update_display_for_guild", unexpected)
    monkeypatch.setattr(events, "save_player_state", unexpected)

    await events.handle_track_stuck(SimpleNamespace(player=player, track=failed, threshold=10_000))

    assert calls == []
    assert player.play_calls == []
    assert list(player.queue) == queued


@pytest.mark.parametrize("mode", [wavelink.QueueMode.loop, wavelink.QueueMode.loop_all])
async def test_unrecoverable_track_is_not_replayed_by_loop_modes(monkeypatch, mode) -> None:
    failed = FakeTrack("Failed")
    failed.extras = {events.RECOVERY_ATTEMPTED_EXTRA: True}
    player = FakePlayer(current=failed, playing=True)
    player.queue.mode = mode
    player.queue.loaded = failed
    player.queue.history = [failed]

    async def ignore(*args):
        pass

    monkeypatch.setattr(events, "update_display_for_guild", ignore)
    monkeypatch.setattr(events, "save_player_state", ignore)

    await events.handle_track_stuck(SimpleNamespace(player=player, track=failed, threshold=10_000))

    assert player.play_calls == []
    assert player.skip_calls == [True]
    assert player.queue.loaded is None
    assert player.queue.history == []


async def test_track_exception_replays_current_track_without_consuming_queue(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    failed.position = 80_000
    replacement = FakeTrack("Replacement", length=120_000)
    queued = [FakeTrack("Next")]
    player = FakePlayer(current=failed, playing=True, queue=FakeQueue(queued), volume=65)
    calls = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(("search", query, requester, limit))
        return [replacement]

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    async def fake_save(seen_player):
        calls.append(("save", seen_player))

    monkeypatch.setattr(events, "search_lavalink", fake_search)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    monkeypatch.setattr(events, "save_player_state", fake_save)

    await events.handle_track_exception(SimpleNamespace(player=player, track=failed, exception="expired stream"))

    assert list(player.queue) == queued
    assert player.current is replacement
    assert player.play_calls == [(replacement, 65)]
    assert player.play_kwargs == [{"start": 80_000, "add_history": False}]
    assert calls == [
        ("search", failed.uri, events.RECOVERY_REQUESTER, 1),
        ("update", player.guild.id, player),
        ("save", player),
    ]


async def test_track_exception_uses_alternate_for_youtube_login_rejection(monkeypatch) -> None:
    failed = FakeTrack("Failed", author="Artist")
    failed.extras = {"query": "artist failed"}
    replacement = FakeTrack("Replacement", author="Artist")
    player = FakePlayer(current=failed, playing=True)
    calls = []

    async def fake_alternative(query, requester, *, exclude_uris):
        calls.append(("alternative", query, requester, exclude_uris))
        return replacement

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    async def fake_save(seen_player):
        calls.append(("save", seen_player))

    monkeypatch.setattr(events, "search_youtube_alternative", fake_alternative)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    monkeypatch.setattr(events, "save_player_state", fake_save)

    exception = "All clients failed to load the item. Client [WEB] failed: This video requires login."
    await events.handle_track_exception(SimpleNamespace(player=player, track=failed, exception=exception))

    assert player.current is replacement
    assert dict(failed.extras)[events.RECOVERY_ATTEMPTED_EXTRA] is True
    assert dict(replacement.extras)[events.RECOVERY_ATTEMPTED_EXTRA] is True
    assert calls == [
        ("alternative", "artist failed", events.RECOVERY_REQUESTER, {failed.uri}),
        ("update", player.guild.id, player),
        ("save", player),
    ]


async def test_direct_link_recovery_keeps_exact_upload(monkeypatch) -> None:
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


async def test_track_exception_does_not_retry_a_recovery_track(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    failed.extras = {events.RECOVERY_ATTEMPTED_EXTRA: True}
    player = FakePlayer(current=failed, playing=True)
    calls = []

    async def fake_search(query, requester, *, limit=None):
        calls.append(("search", query, requester, limit))
        return [FakeTrack("Unexpected")]

    async def fake_save(seen_player):
        calls.append(("save", seen_player.current))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id))

    monkeypatch.setattr(events, "search_lavalink", fake_search)
    monkeypatch.setattr(events, "save_player_state", fake_save)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_exception(SimpleNamespace(player=player, track=failed, exception="expired stream"))

    assert player.skip_calls == [True]
    assert calls == [("update", player.guild.id), ("save", None)]


async def test_track_stuck_skips_to_next_track_when_recovery_errors(monkeypatch) -> None:
    failed = FakeTrack("Failed")
    following = FakeTrack("Next")
    player = FakePlayer(current=failed, playing=True, queue=FakeQueue([following]))
    calls = []

    async def fake_search(query, requester, *, limit=None):
        raise RuntimeError("source still broken")

    async def fake_save(seen_player):
        calls.append(("save", seen_player.current))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player.current))

    monkeypatch.setattr(events, "search_lavalink", fake_search)
    monkeypatch.setattr(events, "save_player_state", fake_save)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_stuck(SimpleNamespace(player=player, track=failed, threshold=10_000))

    assert player.current is following
    assert player.play_calls == [(following, 50)]
    assert list(player.queue) == []
    assert calls == [
        ("update", player.guild.id, following),
        ("save", following),
    ]


async def test_track_start_updates_display(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = []

    async def fake_update(guild_id, seen_player):
        calls.append((guild_id, seen_player))

    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_start(SimpleNamespace(player=player))

    assert calls == [(player.guild.id, player)]


async def test_inactive_player_removes_display_and_clears_saved_state(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = []
    clears = []

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    async def fake_clear(guild_id):
        clears.append(guild_id)

    monkeypatch.setattr(events, "update_display_for_guild", fake_update)
    monkeypatch.setattr(events, "clear_saved_guild_state", fake_clear)

    await events.handle_inactive_player(player)

    assert calls == [("update", player.guild.id, None)]
    assert clears == [player.guild.id]
