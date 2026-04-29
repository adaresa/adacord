from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from adacord import events, ui
from adacord.recommendations import Recommendation
from adacord.sources import LoadSummary
from adacord.state import get_guild_state, guild_states
from adacord.track_requests import TrackRequestLoadError, TrackRequestPlaybackError, TrackRequestResult
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


def last_response_text(interaction: FakeInteraction) -> str:
    if interaction.response.sent:
        return interaction.response.sent[-1]["args"][0]
    return interaction.followup.sent[-1]["args"][0]


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


class MissingOnEditMessage(FakeMessage):
    async def edit(self, **kwargs):
        raise discord.HTTPException(SimpleNamespace(status=404, reason="Missing"), "message missing")


def test_build_player_panel_model_for_idle_state() -> None:
    model = ui.build_player_panel_model(None, 123)

    assert model.state == "idle"
    assert model.title == "Nothing playing"
    assert model.progress == "No active track"
    assert model.volume == 50
    assert model.queue_count == 0
    assert model.queue_preview == ()
    assert model.disabled["pause_resume"] is True
    with pytest.raises(TypeError):
        model.disabled["pause_resume"] = False


def test_player_panel_view_renders_idle_leave_hint() -> None:
    view = ui.PlayerPanelView(123, ui.build_player_panel_model(FakePlayer(), 123))
    texts = "\n".join(text_components(view))

    assert "### Nothing playing" in texts
    assert "Use `/dc` to leave." in texts


def test_player_panel_model_without_guild_does_not_create_state() -> None:
    model = ui.build_player_panel_model(None, None)

    assert model.loop_mode == "none"
    assert 0 not in guild_states


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
        "adacord:player:add",
    }
    assert all(component.get("type") != "embed" for component in view.to_components())


def test_player_panel_view_can_register_persistent_suggestion_select() -> None:
    view = ui.PlayerPanelView(register_persistent_controls=True)
    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]

    assert "adacord:player:suggestions" in custom_ids(view)
    assert len(selects) == 1
    assert selects[0].disabled is True


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


def test_player_panel_view_renders_enabled_add_button_when_connected() -> None:
    player = FakePlayer()
    view = ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    add_button = next(item for item in view.walk_children() if getattr(item, "custom_id", None) == "adacord:player:add")

    assert add_button.label == "Add song"
    assert add_button.disabled is False


def test_player_panel_view_renders_suggestion_dropdown_when_available() -> None:
    current = FakeTrack("Current")
    suggestion = FakeTrack("Fresh Song", author="Artist")
    player = FakePlayer(current=current)

    model = ui.build_player_panel_model(
        player,
        player.guild.id,
        (Recommendation(suggestion, "Artist - Fresh Song", "youtube music"),),
    )
    view = ui.PlayerPanelView(player.guild.id, model)
    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]

    assert "adacord:player:suggestions" in custom_ids(view)
    assert "**Suggested Next**" in "\n".join(text_components(view))
    assert len(selects) == 1
    assert selects[0].placeholder == "Choose a song to add"
    assert selects[0].options[0].label == "Artist - Fresh Song"
    assert selects[0].options[0].value == suggestion.uri


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


async def test_create_or_update_display_restores_channel_on_existing_v2_edit() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    state.display_message = FakeMessage(
        view=ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    )
    state.display_channel = None
    state.display_channel_id = None

    message = await ui.create_or_update_display(player.guild.id, channel, player)

    assert message is state.display_message
    assert state.display_channel is channel
    assert state.display_channel_id == channel.id


async def test_create_or_update_display_recreates_missing_v2_message() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    stale_message = MissingOnEditMessage(
        view=ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    )
    stale_message.id = 111
    state.display_message = stale_message
    state.display_message_id = stale_message.id
    state.display_channel = channel
    state.display_channel_id = channel.id

    message = await ui.create_or_update_display(player.guild.id, channel, player)

    assert message is channel.sent[0]
    assert state.display_message is message
    assert state.display_message_id == message.id
    assert state.display_channel is channel
    assert state.display_channel_id == channel.id


def test_display_refresh_starts_reuses_and_stops(monkeypatch) -> None:
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
    assert task.cancelled is False
    assert state.display_refresh_task is task

    player.connected = False
    ui.ensure_display_refresh(player.guild.id, player)
    assert task.cancelled is True
    assert state.display_refresh_task is None


def test_idle_display_refresh_starts_for_connected_idle_player(monkeypatch) -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    task = FakeTask()

    def fake_create_task(coro):
        coro.close()
        return task

    monkeypatch.setattr(ui.asyncio, "create_task", fake_create_task)

    ui.ensure_display_refresh(player.guild.id, player)

    assert state.display_refresh_task is task


async def test_display_refresh_loop_uses_active_then_idle_intervals(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    calls = []
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    async def fake_create_or_update(guild_id, channel, seen_player, *, manage_refresh=True):
        calls.append((guild_id, channel, seen_player, manage_refresh))
        if len(calls) == 1:
            seen_player.paused = True
        else:
            seen_player.connected = False

    monkeypatch.setattr(ui.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ui, "create_or_update_display", fake_create_or_update)

    await ui.refresh_display_progress(player.guild.id, player)

    assert delays == [ui.DISPLAY_REFRESH_INTERVAL, ui.IDLE_DISPLAY_REFRESH_INTERVAL]
    assert calls == [
        (player.guild.id, state.display_channel, player, False),
        (player.guild.id, state.display_channel, player, False),
    ]


async def test_progress_refresh_does_not_refresh_recommendations(monkeypatch) -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_message = FakeMessage(
        view=ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    )
    state.display_channel = channel
    calls = []

    async def fake_recommendations(seen_player, *, allow_refresh=True):
        calls.append((seen_player, allow_refresh))
        return ()

    monkeypatch.setattr(ui, "recommendations_for_player", fake_recommendations)

    await ui.create_or_update_display(player.guild.id, channel, player, manage_refresh=False)

    assert calls == [(player, False)]


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


async def test_player_panel_add_button_opens_modal(monkeypatch) -> None:
    player = FakePlayer()
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)

    view = ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    interaction = FakeInteraction(guild=player.guild)

    await view.add(interaction)

    assert isinstance(interaction.response.modal, ui.AddSongModal)
    assert interaction.response.modal.guild_id == player.guild.id


async def test_add_song_modal_queues_track_refreshes_and_acknowledges(monkeypatch) -> None:
    player = FakePlayer(playing=False)
    track = FakeTrack("One More Time")
    updates = []
    backgrounds = []

    async def fake_queue_track_request(seen_player, query, requester, *, play_first=False):
        assert seen_player is player
        assert query == "daft punk"
        assert requester == "tester"
        assert play_first is False
        player.current = track
        return TrackRequestResult([track], LoadSummary("One More Time", 1, "youtube"), True)

    async def fake_update(guild_id, seen_player, *, manage_refresh=True):
        updates.append((guild_id, seen_player, manage_refresh))

    def fake_create_task(coro):
        backgrounds.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)
    monkeypatch.setattr(ui.asyncio, "create_task", fake_create_task)

    modal = ui.AddSongModal(999)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=player.guild)

    await modal.on_submit(interaction)

    assert player.current is track
    assert interaction.response.deferred is True
    assert interaction.response.defer_kwargs == {"ephemeral": True, "thinking": True}
    assert interaction.deleted_original_response is True
    assert_no_text_response(interaction)
    assert updates == [(999, player, False)]
    assert len(backgrounds) == 1


async def test_add_song_modal_accepts_yes_for_play_next(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Queued")]))
    calls = []

    async def fake_queue_track_request(seen_player, query, requester, *, play_first=False):
        calls.append((seen_player, query, requester, play_first))
        return TrackRequestResult([FakeTrack("Next")], LoadSummary("Next", 1, "youtube"), False)

    async def fake_update(guild_id, seen_player, *, manage_refresh=True):
        return None

    def fake_create_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)
    monkeypatch.setattr(ui.asyncio, "create_task", fake_create_task)

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "daft punk"
    modal.play_next._value = "yes"
    interaction = FakeInteraction(guild=player.guild)

    await modal.on_submit(interaction)

    assert calls == [(player, "daft punk", "tester", True)]
    assert interaction.response.deferred is True
    assert_no_text_response(interaction)


async def test_add_song_modal_rejects_invalid_play_next_value(monkeypatch) -> None:
    calls = []
    player = FakePlayer()

    async def fake_queue_track_request(*args, **kwargs):
        calls.append((args, kwargs))
        return TrackRequestResult([FakeTrack("Track")], LoadSummary("Track", 1, "youtube"), False)

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "daft punk"
    modal.play_next._value = "soon"
    interaction = FakeInteraction(guild=player.guild)

    await modal.on_submit(interaction)

    assert calls == []
    assert last_response_text(interaction) == "Leave Play next empty, or type y/yes."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_add_song_modal_reports_no_player(monkeypatch) -> None:
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: None)
    modal = ui.AddSongModal(123)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=FakePlayer().guild)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == "Not connected."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_add_song_modal_reports_empty_results(monkeypatch) -> None:
    player = FakePlayer()

    async def fake_queue_track_request(player, query, requester, *, play_first=False):
        return TrackRequestResult([], LoadSummary("Nothing", 0, "youtube"), False)

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "missing song"
    interaction = FakeInteraction(guild=player.guild)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == "No playable tracks were found."
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.deleted_original_response is True


async def test_add_song_modal_reports_load_and_playback_failures(monkeypatch) -> None:
    player = FakePlayer()
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)

    async def fail_load(player, query, requester, *, play_first=False):
        raise TrackRequestLoadError("source down")

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "bad source"
    interaction = FakeInteraction(guild=player.guild)
    monkeypatch.setattr(ui, "queue_track_request", fail_load)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == "Could not load that request: source down"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.deleted_original_response is True

    async def fail_playback(player, query, requester, *, play_first=False):
        raise TrackRequestPlaybackError("queue rejected")

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "bad playback"
    interaction = FakeInteraction(guild=player.guild)
    monkeypatch.setattr(ui, "queue_track_request", fail_playback)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == "Could not start playback: queue rejected"
    assert interaction.followup.sent[-1]["kwargs"]["ephemeral"] is True
    assert interaction.deleted_original_response is True


async def test_player_panel_suggestion_select_queues_track_and_refreshes(monkeypatch) -> None:
    suggestion = FakeTrack("Fresh Song", author="Artist")
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    updates = []
    invalidated = []

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)
    monkeypatch.setattr(ui, "clear_guild_recommendation_cache", lambda guild_id: invalidated.append(guild_id))

    model = ui.build_player_panel_model(
        player,
        player.guild.id,
        (Recommendation(suggestion, "Artist - Fresh Song", "youtube music"),),
    )
    view = ui.PlayerPanelView(player.guild.id, model)
    interaction = FakeInteraction(guild=player.guild)
    interaction.data = {"values": [suggestion.uri]}

    await view.add_suggestion(interaction)

    assert list(player.queue) == [suggestion]
    assert suggestion.extras["requester"] == "tester"
    assert interaction.response.deferred is True
    assert_no_text_response(interaction)
    assert invalidated == [player.guild.id]
    assert updates == [(player.guild.id, player)]


async def test_player_panel_suggestion_select_resolves_stable_value_after_restart(monkeypatch) -> None:
    resolved = FakeTrack("Resolved Song", author="Artist")
    player = FakePlayer(current=FakeTrack("Current"), playing=True)

    async def fake_resolve(value, requester):
        assert value == "https://example.test/recommendation"
        return resolved

    async def fake_update(guild_id, seen_player):
        return None

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "resolve_recommendation_value", fake_resolve)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    view = ui.PlayerPanelView(register_persistent_controls=True)
    interaction = FakeInteraction(guild=player.guild)
    interaction.data = {"values": ["https://example.test/recommendation"]}

    await view.add_suggestion(interaction)

    assert list(player.queue) == [resolved]
    assert interaction.response.deferred is True


async def test_player_panel_stop_clears_playback_but_keeps_voice_and_refreshes(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]))
    get_guild_state(player.guild.id).loop_mode = "queue"
    updates = []

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    view = ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    interaction = FakeInteraction(guild=player.guild)

    await view.stop(interaction)

    assert player.queue.is_empty
    assert player.skip_calls == [True]
    assert player.disconnect_calls == 0
    assert player.guild.voice_client is player
    assert get_guild_state(player.guild.id).loop_mode == "none"
    assert interaction.response.deferred is True
    assert_no_text_response(interaction)
    assert updates == [(player.guild.id, player)]


async def test_player_panel_controls_report_empty_states_ephemerally(monkeypatch) -> None:
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: None)
    view = ui.PlayerPanelView()

    interaction = FakeInteraction(guild=FakePlayer().guild)
    await view.pause_resume(interaction)

    assert interaction.response.sent[-1]["args"][0] == "Nothing to pause or resume."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_update_display_keeps_existing_message_when_player_is_idle() -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    channel = FakeTextChannel()
    message = FakeMessage(
        view=ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    )
    state.display_message = message
    state.display_message_id = message.id
    state.display_channel = channel
    state.display_channel_id = channel.id
    task = FakeTask()
    state.display_refresh_task = task

    await ui.update_display_for_guild(player.guild.id, player)

    assert task.cancelled is False
    assert state.display_refresh_task is task
    assert message.deleted is False
    assert state.display_message is message
    assert state.display_message_id == message.id
    assert state.display_channel is channel
    assert state.display_channel_id == channel.id
    assert "### Nothing playing" in "\n".join(text_components(message.edits[-1]["view"]))


async def test_update_display_deletes_existing_message_when_player_is_disconnected() -> None:
    guild_id = 123
    state = get_guild_state(guild_id)
    message = FakeMessage()
    state.display_message = message
    state.display_message_id = message.id
    state.display_channel = FakeTextChannel()
    state.display_channel_id = state.display_channel.id
    task = FakeTask()
    state.display_refresh_task = task

    await ui.update_display_for_guild(guild_id, None)

    assert task.cancelled is True
    assert message.deleted is True
    assert state.display_message is None
    assert state.display_message_id is None
    assert state.display_channel is None
    assert state.display_channel_id is None


async def test_deleted_idle_display_is_recreated_while_player_is_connected() -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    channel = FakeTextChannel()
    message = FakeMessage(
        view=ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))
    )
    state.display_message = message
    state.display_message_id = message.id
    state.display_channel = channel
    state.display_channel_id = channel.id

    await ui.handle_display_message_delete(player.guild.id, channel.id, message.id, player)

    assert state.display_message is channel.sent[0]
    assert state.display_message_id == channel.sent[0].id
    assert state.display_channel is channel
    assert "Use `/dc` to leave." in "\n".join(text_components(channel.sent[0].view))


async def test_deleted_display_clears_state_when_player_is_disconnected() -> None:
    guild_id = 123
    state = get_guild_state(guild_id)
    channel = FakeTextChannel()
    message = FakeMessage()
    task = FakeTask()
    state.display_message = message
    state.display_message_id = message.id
    state.display_channel = channel
    state.display_channel_id = channel.id
    state.display_refresh_task = task

    await ui.handle_display_message_delete(guild_id, channel.id, message.id, None)

    assert task.cancelled is True
    assert state.display_refresh_task is None
    assert state.display_message is None
    assert state.display_message_id is None
    assert state.display_channel is None
    assert state.display_channel_id is None


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
