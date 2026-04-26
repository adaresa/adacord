from __future__ import annotations

from types import SimpleNamespace

from adacord import events, ui
from adacord.state import get_guild_state
from conftest import FakeInteraction, FakeMessage, FakePlayer, FakeQueue, FakeTextChannel, FakeTrack


def embed_field(embed, name: str):
    return next(field for field in embed.fields if field.name == name)


def test_build_player_embed_for_idle_state() -> None:
    embed = ui.build_player_embed(None, 123)

    assert embed.title == "Music Player"
    assert embed_field(embed, "Now Playing").value == "Nothing playing"
    assert embed_field(embed, "Volume").value == "50%"
    assert embed_field(embed, "Queue").value == "Empty"


def test_player_controls_are_persistent() -> None:
    view = ui.PlayerControls()

    assert view.timeout is None
    assert view.is_persistent()
    assert {item.custom_id for item in view.children} == {
        "adacord:player:restart",
        "adacord:player:pause_resume",
        "adacord:player:skip",
        "adacord:player:stop",
        "adacord:player:volume_down",
        "adacord:player:volume_up",
        "adacord:player:mute",
        "adacord:player:shuffle",
        "adacord:player:loop",
        "adacord:player:queue",
    }


def test_build_player_embed_for_paused_looping_queue() -> None:
    current = FakeTrack("Current", length=65_000)
    current.extras = {"requester": "ada", "display_title": "Custom Current"}
    queued = [FakeTrack(f"Track {index}", length=120_000) for index in range(1, 7)]
    player = FakePlayer(current=current, paused=True, queue=FakeQueue(queued), volume=80)
    get_guild_state(player.guild.id).loop_mode = "queue"

    embed = ui.build_player_embed(player, player.guild.id)

    assert "Custom Current" in embed_field(embed, "Now Playing").value
    assert embed_field(embed, "Requested by").value == "ada"
    assert embed_field(embed, "Volume").value == "80%"
    assert embed_field(embed, "Loop").value == "Queue"
    assert "...and 1 more" in embed_field(embed, "Queue (6)").value


def test_build_queue_embed_paginates_tracks() -> None:
    tracks = [FakeTrack(f"Track {index}") for index in range(1, 12)]
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue(tracks))

    first_page = ui.build_queue_embed(player, page=0)
    second_page = ui.build_queue_embed(player, page=1)

    assert first_page.title == "Music Queue"
    assert "`1.` **Track 1**" in embed_field(first_page, "Up Next (11)").value
    assert "`10.` **Track 10**" in embed_field(first_page, "Up Next (11)").value
    assert "`11.` **Track 11**" in embed_field(second_page, "Up Next (11)").value
    assert second_page.footer.text == "Page 2 of 2"


async def test_create_or_update_display_sends_then_edits_existing_message() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)

    message = await ui.create_or_update_display(player.guild.id, channel, player)
    assert message is channel.sent[0]
    assert state.display_message is message
    assert state.display_channel is channel

    edited = await ui.create_or_update_display(player.guild.id, channel, player)
    assert edited is message
    assert len(channel.sent) == 1
    assert message.edits


async def test_update_display_deletes_existing_message_when_player_is_idle() -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    state.display_message = FakeMessage()
    state.display_channel = FakeTextChannel()

    await ui.update_display_for_guild(player.guild.id, player)

    assert state.display_message is None
    assert state.display_channel is None


async def test_queue_view_clamps_page_when_queue_shrinks(monkeypatch) -> None:
    player = FakePlayer(queue=FakeQueue([FakeTrack(f"Track {index}") for index in range(12)]))
    interaction = FakeInteraction(guild=player.guild)
    view = ui.QueueView(player.guild.id, player)
    view.page = 1
    player.queue.clear()

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)

    await view.edit(interaction)

    assert view.page == 0
    assert interaction.response.edits[-1]["embed"].title == "Music Queue"


async def test_track_end_plays_next_and_updates_display(monkeypatch) -> None:
    player = FakePlayer(queue=FakeQueue([FakeTrack("Next")]))
    calls = []

    async def fake_play_next(seen_player):
        calls.append(("play_next", seen_player))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    monkeypatch.setattr(events, "play_next", fake_play_next)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_end(SimpleNamespace(player=player))

    assert calls == [("play_next", player), ("update", player.guild.id, player)]


async def test_track_end_only_updates_display_when_queue_empty(monkeypatch) -> None:
    player = FakePlayer(queue=FakeQueue())
    calls = []

    async def fake_play_next(seen_player):
        calls.append(("play_next", seen_player))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    monkeypatch.setattr(events, "play_next", fake_play_next)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_end(SimpleNamespace(player=player))

    assert calls == [("update", player.guild.id, player)]


async def test_track_start_updates_display(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = []

    async def fake_update(guild_id, seen_player):
        calls.append((guild_id, seen_player))

    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_track_start(SimpleNamespace(player=player))

    assert calls == [(player.guild.id, player)]


async def test_inactive_player_sends_idle_notice_and_updates_display(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    calls = []

    async def fake_send(channel, message):
        calls.append(("send", channel, message))

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    monkeypatch.setattr(events, "send_transient", fake_send)
    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_inactive_player(player)

    assert calls == [
        ("send", state.display_channel, "Disconnected after being idle."),
        ("update", player.guild.id, player),
    ]
