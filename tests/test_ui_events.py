from __future__ import annotations

from types import SimpleNamespace

import discord

from adacord import events, ui
from adacord.state import get_guild_state
from conftest import FakeInteraction, FakeMessage, FakePlayer, FakeQueue, FakeTextChannel, FakeTrack


def embed_field(embed, name: str):
    return next(field for field in embed.fields if field.name == name)


def custom_ids(view: discord.ui.LayoutView) -> set[str]:
    return {item.custom_id for item in view.walk_children() if getattr(item, "custom_id", None)}


def text_components(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def assert_no_text_response(interaction: FakeInteraction) -> None:
    assert interaction.response.sent == []
    assert interaction.followup.sent == []


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def test_build_player_panel_model_for_idle_state() -> None:
    model = ui.build_player_panel_model(None, 123)

    assert model.state == "idle"
    assert model.title == "Nothing playing"
    assert model.progress == "No active track"
    assert model.volume == 50
    assert model.queue_count == 0
    assert model.queue_preview == []
    assert model.disabled["pause_resume"] is True


def test_player_panel_view_is_persistent_v2_with_stable_controls() -> None:
    view = ui.PlayerPanelView()

    assert view.timeout is None
    assert view.is_persistent()
    assert view.has_components_v2()
    assert custom_ids(view) == {
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
    assert all(component.get("type") != "embed" for component in view.to_components())


def test_build_player_panel_model_for_paused_looping_queue_with_artwork() -> None:
    current = FakeTrack("Current", length=65_000)
    current.position = 12_000
    current.artwork = "https://example.test/current.jpg"
    current.extras = {"requester": "ada", "display_title": "Custom Current"}
    queued = [FakeTrack(f"Track {index}", length=120_000) for index in range(1, 7)]
    player = FakePlayer(current=current, paused=True, queue=FakeQueue(queued), volume=80)
    get_guild_state(player.guild.id).loop_mode = "queue"

    model = ui.build_player_panel_model(player, player.guild.id)

    assert model.state == "paused"
    assert model.title == "Custom Current"
    assert model.progress == "0:12 / 1:05"
    assert model.requester == "ada"
    assert model.artwork_url == "https://example.test/current.jpg"
    assert model.volume == 80
    assert model.loop_mode == "queue"
    assert model.queue_count == 6
    assert model.queue_preview[-1] == "...and 1 more"
    assert model.pause_label == "Resume"
    assert model.disabled["shuffle"] is False


def test_player_panel_view_renders_text_and_thumbnail() -> None:
    current = FakeTrack("Current", length=65_000)
    current.artwork = "https://example.test/current.jpg"
    player = FakePlayer(current=current, queue=FakeQueue([FakeTrack("Next")]))

    view = ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    texts = "\n".join(text_components(view))

    assert "### Current" in texts
    assert "Playing: Current" not in texts
    assert "Music Player" not in texts
    assert "Volume: 50%" in texts
    assert "`1.` Next [3:30]" in texts
    assert any(isinstance(item, discord.ui.Thumbnail) for item in view.walk_children())


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
    assert message.embed is None
    assert isinstance(message.view, ui.PlayerPanelView)
    assert message.flags.components_v2 is True
    assert state.display_message is message
    assert state.display_channel is channel

    edited = await ui.create_or_update_display(player.guild.id, channel, player)
    assert edited is message
    assert len(channel.sent) == 1
    assert message.edits[-1]["view"].has_components_v2()


async def test_create_or_update_display_replaces_legacy_embed_message() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    legacy_message = FakeMessage(embed=ui.build_player_embed(player, player.guild.id))
    state.display_message = legacy_message
    state.display_channel = channel

    message = await ui.create_or_update_display(player.guild.id, channel, player)

    assert legacy_message.deleted is True
    assert message is channel.sent[0]
    assert message.flags.components_v2 is True
    assert state.display_message is message
    assert state.display_message_id == message.id


def test_display_refresh_starts_and_stops(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    task = FakeTask()

    def fake_create_task(coro):
        coro.close()
        return task

    monkeypatch.setattr(ui.asyncio, "create_task", fake_create_task)

    ui.ensure_display_refresh(player.guild.id, player)
    assert state.display_refresh_task is task

    player.paused = True
    ui.ensure_display_refresh(player.guild.id, player)
    assert task.cancelled is True
    assert state.display_refresh_task is None


async def test_display_refresh_loop_edits_progress_without_rescheduling(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    calls = []

    async def fake_sleep(delay):
        assert delay == ui.DISPLAY_REFRESH_INTERVAL

    async def fake_create_or_update(guild_id, channel, seen_player, *, manage_refresh=True):
        calls.append((guild_id, channel, seen_player, manage_refresh))
        seen_player.paused = True

    monkeypatch.setattr(ui.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ui, "create_or_update_display", fake_create_or_update)

    await ui.refresh_display_progress(player.guild.id, player)

    assert calls == [(player.guild.id, state.display_channel, player, False)]


async def test_player_panel_controls_update_player_silently(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]), volume=50)
    updates = []

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    view = ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))

    pause_interaction = FakeInteraction(guild=player.guild)
    await view.pause_resume(pause_interaction)
    assert player.pause_calls[-1] is True
    assert pause_interaction.response.deferred is True
    assert_no_text_response(pause_interaction)

    resume_interaction = FakeInteraction(guild=player.guild)
    await view.pause_resume(resume_interaction)
    assert player.pause_calls[-1] is False
    assert resume_interaction.response.deferred is True
    assert_no_text_response(resume_interaction)

    volume_down_interaction = FakeInteraction(guild=player.guild)
    await view.volume_down(volume_down_interaction)
    assert player.volume_calls[-1] == 40
    assert volume_down_interaction.response.deferred is True
    assert_no_text_response(volume_down_interaction)

    volume_up_interaction = FakeInteraction(guild=player.guild)
    await view.volume_up(volume_up_interaction)
    assert player.volume_calls[-1] == 50
    assert volume_up_interaction.response.deferred is True
    assert_no_text_response(volume_up_interaction)

    shuffle_interaction = FakeInteraction(guild=player.guild)
    await view.shuffle(shuffle_interaction)
    assert player.queue.shuffled is True
    assert shuffle_interaction.response.deferred is True
    assert_no_text_response(shuffle_interaction)

    loop_interaction = FakeInteraction(guild=player.guild)
    await view.loop(loop_interaction)
    assert get_guild_state(player.guild.id).loop_mode == "track"
    assert loop_interaction.response.deferred is True
    assert_no_text_response(loop_interaction)
    assert len(updates) == 6


async def test_player_panel_controls_report_empty_states_ephemerally(monkeypatch) -> None:
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: None)
    view = ui.PlayerPanelView()

    interaction = FakeInteraction(guild=FakePlayer().guild)
    await view.pause_resume(interaction)

    assert interaction.response.sent[-1]["args"][0] == "Nothing to pause or resume."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_update_display_deletes_existing_message_when_player_is_idle() -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    state.display_message = FakeMessage()
    state.display_channel = FakeTextChannel()
    task = FakeTask()
    state.display_refresh_task = task

    await ui.update_display_for_guild(player.guild.id, player)

    assert task.cancelled is True
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


async def test_inactive_player_updates_display(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    calls = []

    async def fake_update(guild_id, seen_player):
        calls.append(("update", guild_id, seen_player))

    monkeypatch.setattr(events, "update_display_for_guild", fake_update)

    await events.handle_inactive_player(player)

    assert calls == [("update", player.guild.id, player)]
