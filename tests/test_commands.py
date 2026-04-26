from __future__ import annotations

from types import SimpleNamespace

import pytest

from adacord import commands
from adacord.sources import LoadSummary
from conftest import FakeGuild, FakeInteraction, FakeMember, FakePlayer, FakeQueue, FakeTextChannel, FakeTrack


def last_response_text(interaction: FakeInteraction) -> str:
    if interaction.response.sent:
        return interaction.response.sent[-1]["args"][0]
    return interaction.followup.sent[-1]["args"][0]


def assert_no_text_response(interaction: FakeInteraction) -> None:
    assert interaction.response.sent == []
    assert interaction.followup.sent == []


async def test_play_rejects_dm() -> None:
    interaction = FakeInteraction(guild=None)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "This command can only be used in a server."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_play_rejects_user_outside_voice(monkeypatch) -> None:
    interaction = FakeInteraction(guild=FakeGuild(), user=object())

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "Join a voice channel first."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_play_connects_loads_queues_and_updates_display(monkeypatch) -> None:
    guild = FakeGuild()
    channel = FakeTextChannel()
    interaction = FakeInteraction(guild=guild, channel=channel, user=FakeMember(name="ada"))
    voice_channel = object()
    player = FakePlayer(guild=guild, playing=False)
    track = FakeTrack("One More Time")
    calls = SimpleNamespace(display=[])

    async def fake_ensure_player(seen_guild, seen_channel):
        assert seen_guild is guild
        assert seen_channel is voice_channel
        return player

    async def fake_load_tracks(query: str, requester: str):
        assert query == "daft punk"
        assert requester == "ada"
        return [track], LoadSummary("One More Time", 1, "youtube")

    async def fake_create_display(guild_id, seen_channel, seen_player):
        calls.display.append((guild_id, seen_channel, seen_player))

    monkeypatch.setattr(commands, "user_voice_channel", lambda interaction: voice_channel)
    monkeypatch.setattr(commands, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "load_tracks", fake_load_tracks)
    monkeypatch.setattr(commands, "create_or_update_display", fake_create_display)

    await commands.play_impl(interaction, "daft punk")

    assert interaction.response.deferred is True
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    assert player.current is track
    assert calls.display == [(guild.id, channel, player)]
    assert interaction.deleted_original_response is True
    assert_no_text_response(interaction)


async def test_play_reports_connection_failure(monkeypatch) -> None:
    interaction = FakeInteraction(guild=FakeGuild())

    async def fake_ensure_player(guild, channel):
        raise RuntimeError("voice denied")

    monkeypatch.setattr(commands, "user_voice_channel", lambda interaction: object())
    monkeypatch.setattr(commands, "ensure_player", fake_ensure_player)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "Could not connect to voice: voice denied"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True


async def test_play_reports_load_failure(monkeypatch) -> None:
    interaction = FakeInteraction(guild=FakeGuild())
    player = FakePlayer(guild=interaction.guild, playing=False)

    async def fake_ensure_player(guild, channel):
        return player

    async def fake_load_tracks(query: str, requester: str):
        raise RuntimeError("source down")

    monkeypatch.setattr(commands, "user_voice_channel", lambda interaction: object())
    monkeypatch.setattr(commands, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "load_tracks", fake_load_tracks)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "Could not load that request: source down"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True


async def test_play_reports_playback_start_failure(monkeypatch) -> None:
    interaction = FakeInteraction(guild=FakeGuild())
    player = FakePlayer(guild=interaction.guild, playing=False)

    async def fake_ensure_player(guild, channel):
        return player

    async def fake_load_tracks(query: str, requester: str):
        return [FakeTrack("Track")], LoadSummary("Track", 1, "youtube")

    async def fake_add_tracks(player, tracks):
        raise RuntimeError("queue rejected")

    monkeypatch.setattr(commands, "user_voice_channel", lambda interaction: object())
    monkeypatch.setattr(commands, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(commands, "load_tracks", fake_load_tracks)
    monkeypatch.setattr(commands, "add_tracks", fake_add_tracks)

    await commands.play_impl(interaction, "song")

    assert last_response_text(interaction) == "Could not start playback: queue rejected"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True


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
