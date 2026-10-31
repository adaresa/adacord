from __future__ import annotations

from types import SimpleNamespace

import pytest

from adacord import commands, ui
from adacord.sources import LoadSummary
from adacord.track_requests import TrackRequestResult
from conftest import (
    REQUEST_FAILURES,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakePlayer,
    FakeQueue,
    FakeTextChannel,
    FakeTrack,
    FakeVoiceChannel,
    assert_no_text_response,
    last_response_text,
)


VOICE_COMMANDS = {
    "play": lambda interaction: commands.play_impl(interaction, "song"),
    "join": lambda interaction: commands.join_impl(interaction),
}


@pytest.mark.parametrize("command", VOICE_COMMANDS)
@pytest.mark.parametrize(
    ("in_guild", "expected"),
    [(False, "This can only be used in a server."), (True, "Join a voice channel first.")],
    ids=["dm", "outside_voice"],
)
async def test_voice_commands_reject_dm_and_user_outside_voice(command, in_guild, expected) -> None:
    interaction = FakeInteraction(guild=FakeGuild(), user=object()) if in_guild else FakeInteraction(guild=None)

    await VOICE_COMMANDS[command](interaction)

    assert last_response_text(interaction) == expected
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


@pytest.mark.parametrize("command", VOICE_COMMANDS)
async def test_voice_commands_reject_missing_voice_permissions_before_defer(monkeypatch, command) -> None:
    guild = FakeGuild()
    voice_channel = FakeVoiceChannel(
        guild=guild,
        permissions=SimpleNamespace(view_channel=True, connect=False, speak=False),
        name="Music",
    )
    interaction = FakeInteraction(guild=guild)

    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: voice_channel)

    await VOICE_COMMANDS[command](interaction)

    assert last_response_text(interaction) == "I need Connect and Speak permissions in Music."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.response.deferred is False
    assert voice_channel.connect_kwargs is None


async def test_play_rejects_stage_channel_before_defer(monkeypatch) -> None:
    class FakeStageChannel(FakeVoiceChannel):
        pass

    guild = FakeGuild()
    stage_channel = FakeStageChannel(guild=guild, name="Town Hall")
    interaction = FakeInteraction(guild=guild)

    monkeypatch.setattr(ui.discord, "StageChannel", FakeStageChannel)
    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: stage_channel)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "I can't play in Stage channels. Join a regular voice channel."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.response.deferred is False
    assert stage_channel.connect_kwargs is None


async def test_join_connects_and_creates_idle_display(monkeypatch) -> None:
    guild = FakeGuild()
    channel = FakeTextChannel()
    interaction = FakeInteraction(guild=guild, channel=channel, user=FakeMember(name="ada"))
    voice_channel = object()
    player = FakePlayer(guild=guild)
    calls = []

    async def fake_ensure_player(seen_guild, seen_channel):
        assert seen_guild is guild
        assert seen_channel is voice_channel
        return player

    async def fake_create_display(guild_id, seen_channel, seen_player, *, manage_refresh=True):
        calls.append((guild_id, seen_channel, seen_player, manage_refresh))

    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: voice_channel)
    monkeypatch.setattr(ui, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "create_or_update_display", fake_create_display)

    await commands.join_impl(interaction)

    assert interaction.response.deferred is True
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    assert calls == [(guild.id, channel, player, True)]
    assert interaction.deleted_original_response is True
    assert_no_text_response(interaction)


async def test_play_connects_loads_queues_and_updates_display(monkeypatch) -> None:
    guild = FakeGuild()
    channel = FakeTextChannel()
    interaction = FakeInteraction(guild=guild, channel=channel, user=FakeMember(name="ada"))
    voice_channel = object()
    player = FakePlayer(guild=guild, playing=False)
    track = FakeTrack("One More Time")
    calls = SimpleNamespace(display=[], background=[])

    async def fake_ensure_player(seen_guild, seen_channel):
        assert seen_guild is guild
        assert seen_channel is voice_channel
        return player

    async def fake_queue_track_request(seen_player, query: str, requester: str):
        assert seen_player is player
        assert query == "daft punk"
        assert requester == "ada"
        player.current = track
        return TrackRequestResult([track], LoadSummary("One More Time", 1, "youtube"), True)

    async def fake_create_display(guild_id, seen_channel, seen_player, *, manage_refresh=True):
        calls.display.append((guild_id, seen_channel, seen_player, manage_refresh))

    def fake_background_refresh(guild_id, seen_player):
        calls.background.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: voice_channel)
    monkeypatch.setattr(ui, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(commands, "create_or_update_display", fake_create_display)
    monkeypatch.setattr(commands, "refresh_display_in_background", fake_background_refresh)

    await commands.play_impl(interaction, "daft punk")

    assert interaction.response.deferred is True
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    assert player.current is track
    assert calls.display == [(guild.id, channel, player, False)]
    assert calls.background == [(guild.id, player)]
    assert interaction.deleted_original_response is True
    assert_no_text_response(interaction)


async def test_play_reports_connection_failure(monkeypatch) -> None:
    interaction = FakeInteraction(guild=FakeGuild())

    async def fake_ensure_player(guild, channel):
        raise RuntimeError("voice denied")

    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: object())
    monkeypatch.setattr(ui, "ensure_player", fake_ensure_player)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "Could not connect to voice: voice denied"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.deleted_original_response is True


@pytest.mark.parametrize(("outcome", "expected"), REQUEST_FAILURES)
async def test_play_reports_request_failure_and_clears_deferred_response(monkeypatch, outcome, expected) -> None:
    interaction = FakeInteraction(guild=FakeGuild())
    player = FakePlayer(guild=interaction.guild, playing=False)

    async def fake_ensure_player(guild, channel):
        return player

    async def fake_queue_track_request(player, query: str, requester: str):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ui, "user_voice_channel", lambda interaction: object())
    monkeypatch.setattr(ui, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "queue_track_request", fake_queue_track_request)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == expected
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.deleted_original_response is True


async def test_empty_state_commands_respond_ephemerally(monkeypatch) -> None:
    monkeypatch.setattr(commands, "player_for_interaction", lambda interaction: None)

    for impl, expected in [
        (commands.disconnect_impl, "Not connected."),
        (commands.remove_impl, "Queue is empty."),
    ]:
        interaction = FakeInteraction(guild=FakeGuild())
        if impl is commands.remove_impl:
            await impl(interaction, 1)
        else:
            await impl(interaction)
        assert last_response_text(interaction) == expected


async def test_remove_and_move_queue_items(monkeypatch) -> None:
    first = FakeTrack("First")
    second = FakeTrack("Second")
    third = FakeTrack("Third")
    player = FakePlayer(queue=FakeQueue([first, second, third]))

    async def fake_update(guild_id, player):
        return None

    monkeypatch.setattr(commands, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(commands, "update_display_for_guild", fake_update)

    remove_interaction = FakeInteraction(guild=player.guild)
    await commands.remove_impl(remove_interaction, 2)

    assert list(player.queue) == [first, third]
    assert remove_interaction.response.deferred is True
    assert_no_text_response(remove_interaction)

    move_interaction = FakeInteraction(guild=player.guild)
    await commands.move_impl(move_interaction, 2, 1)

    assert list(player.queue) == [third, first]
    assert move_interaction.response.deferred is True
    assert_no_text_response(move_interaction)


async def test_remove_rejects_positions_below_one(monkeypatch) -> None:
    first = FakeTrack("First")
    player = FakePlayer(queue=FakeQueue([first]))
    monkeypatch.setattr(commands, "player_for_interaction", lambda interaction: player)

    interaction = FakeInteraction(guild=player.guild)
    await commands.remove_impl(interaction, 0)

    assert list(player.queue) == [first]
    assert last_response_text(interaction) == "Queue positions start at 1."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_move_rejects_positions_below_one(monkeypatch) -> None:
    first = FakeTrack("First")
    second = FakeTrack("Second")
    player = FakePlayer(queue=FakeQueue([first, second]))
    monkeypatch.setattr(commands, "player_for_interaction", lambda interaction: player)

    interaction = FakeInteraction(guild=player.guild)
    await commands.move_impl(interaction, -1, 1)

    assert list(player.queue) == [first, second]
    assert last_response_text(interaction) == "Queue positions start at 1."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True
