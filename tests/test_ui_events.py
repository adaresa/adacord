from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from adacord import ui
from adacord.recommendations import Recommendation
from adacord.sources import LoadSummary
from adacord.state import get_guild_state, guild_states
from adacord.track_requests import TrackRequestResult
from conftest import (
    REQUEST_FAILURES,
    FakeGuild,
    FakeInteraction,
    FakeMember,
    FakeMessage,
    FakePlayer,
    FakeQueue,
    FakeTask,
    FakeTextChannel,
    FakeTrack,
    FakeVoiceChannel,
    assert_no_text_response,
    last_response_text,
)


def embed_field(embed, name: str):
    return next(field for field in embed.fields if field.name == name)


def custom_ids(view: discord.ui.LayoutView) -> set[str]:
    return {item.custom_id for item in view.walk_children() if getattr(item, "custom_id", None)}


def text_components(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def panel_view(player: FakePlayer) -> ui.PlayerPanelView:
    return ui.PlayerPanelView(player.guild.id, ui.build_player_panel_model(player, player.guild.id))


def panel_message(player: FakePlayer, message_cls: type[FakeMessage] = FakeMessage, **kwargs) -> FakeMessage:
    return message_cls(view=panel_view(player), **kwargs)


def attach_display(state, message: FakeMessage, channel: FakeTextChannel) -> None:
    state.display_message = message
    state.display_message_id = message.id
    state.display_channel = channel
    state.display_channel_id = channel.id


class MissingOnEditMessage(FakeMessage):
    async def edit(self, **kwargs):
        raise discord.HTTPException(SimpleNamespace(status=404, reason="Missing"), "message missing")


class TransientOnEditMessage(FakeMessage):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.edit_attempts = 0

    async def edit(self, **kwargs):
        self.edit_attempts += 1
        raise discord.HTTPException(SimpleNamespace(status=500, reason="Temporary"), "temporary failure")


class RateLimitedOnEditMessage(FakeMessage):
    async def edit(self, **kwargs):
        response = SimpleNamespace(status=429, reason="Rate Limited", headers={"Retry-After": "4.5"})
        raise discord.HTTPException(response, {"message": "rate limited"})


class SlowTextChannel(FakeTextChannel):
    async def send(self, content: str | None = None, **kwargs):
        await asyncio.sleep(0)
        return await super().send(content, **kwargs)


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
    texts = "\n".join(text_components(panel_view(FakePlayer())))

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

    view = panel_view(player)
    texts = "\n".join(text_components(view))

    assert "### Current" in texts
    assert "Playing: Current" not in texts
    assert "Music Player" not in texts
    assert "Volume: 50%" in texts
    assert "`1.` Next [3:30]" in texts
    assert any(isinstance(item, discord.ui.Thumbnail) for item in view.walk_children())


def test_player_panel_view_renders_enabled_add_button_when_connected() -> None:
    view = panel_view(FakePlayer())
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
    legacy_message = FakeMessage(embed=discord.Embed(title="Music Player"))
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
    state.display_message = panel_message(player)
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
    stale_message = panel_message(player, MissingOnEditMessage)
    stale_message.id = 111
    attach_display(state, stale_message, channel)

    message = await ui.create_or_update_display(player.guild.id, channel, player)

    assert message is channel.sent[0]
    assert state.display_message is message
    assert state.display_message_id == message.id
    assert state.display_channel is channel
    assert state.display_channel_id == channel.id


async def test_create_or_update_display_recovers_saved_display_by_id() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    message = panel_message(player)
    message.id = 456
    channel.messages_by_id[message.id] = message
    attach_display(state, message, channel)
    state.display_message = None

    recovered = await ui.create_or_update_display(player.guild.id, channel, player)

    assert recovered is message
    assert channel.fetches == [message.id]
    assert channel.sent == []
    assert state.display_message is message
    assert state.display_message_id == message.id
    assert message.edits[-1]["view"].has_components_v2()


async def test_create_or_update_display_ignores_non_bot_saved_display_message() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    message = panel_message(player, author=SimpleNamespace(bot=False))
    message.id = 456
    channel.messages_by_id[message.id] = message
    attach_display(state, message, channel)
    state.display_message = None

    created = await ui.create_or_update_display(player.guild.id, channel, player)

    assert created is channel.sent[0]
    assert message.edits == []
    assert state.display_message is created
    assert state.display_message_id == created.id


async def test_create_or_update_display_leaves_other_bots_panels_alone() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    own_panel = panel_message(player)
    own_panel.id = 300
    other_bot_panel = panel_message(player, author=SimpleNamespace(bot=True, id=4242))
    other_bot_panel.id = 200
    channel.history_messages = [own_panel, other_bot_panel]
    channel.messages_by_id[other_bot_panel.id] = other_bot_panel
    attach_display(state, other_bot_panel, channel)
    state.display_message = None

    recovered = await ui.create_or_update_display(player.guild.id, channel, player)

    assert recovered is own_panel
    assert channel.sent == []
    assert other_bot_panel.edits == []
    assert other_bot_panel.deleted is False
    assert own_panel.edits[-1]["view"].has_components_v2()


async def test_create_or_update_display_scans_history_and_cleans_duplicate_panels() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    newest = panel_message(player)
    newest.id = 300
    older = panel_message(player)
    older.id = 200
    user_panel = panel_message(player, author=SimpleNamespace(bot=False))
    user_panel.id = 100
    channel.history_messages = [newest, user_panel, older]
    state.display_channel = channel
    state.display_channel_id = channel.id

    recovered = await ui.create_or_update_display(player.guild.id, channel, player)

    assert recovered is newest
    assert channel.sent == []
    assert newest.deleted is False
    assert older.deleted is True
    assert user_panel.deleted is False
    assert state.display_message is newest
    assert state.display_message_id == newest.id
    assert newest.edits[-1]["view"].has_components_v2()


async def test_create_or_update_display_retries_transient_edit_without_replacing(monkeypatch) -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    message = panel_message(player, TransientOnEditMessage)
    attach_display(state, message, channel)
    monkeypatch.setattr(ui, "DISPLAY_EDIT_RETRY_DELAY", 0)

    result = await ui.create_or_update_display(player.guild.id, channel, player)

    assert result is None
    assert message.edit_attempts == ui.DISPLAY_EDIT_RETRIES
    assert channel.sent == []
    assert state.display_message is message
    assert state.display_message_id == message.id


async def test_rate_limited_display_edit_records_retry_after(monkeypatch) -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    state = get_guild_state(player.guild.id)
    attach_display(state, panel_message(player, RateLimitedOnEditMessage), channel)
    monkeypatch.setattr(ui, "DISPLAY_EDIT_RETRY_DELAY", 0)

    result = await ui.create_or_update_display(player.guild.id, channel, player)

    assert result is None
    assert ui.display_refresh_delay(player.guild.id, player) >= 4.0
    assert state.display_rate_limit_until > asyncio.get_running_loop().time()


async def test_concurrent_display_updates_send_only_one_panel() -> None:
    channel = SlowTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))

    first, second = await asyncio.gather(
        ui.create_or_update_display(player.guild.id, channel, player),
        ui.create_or_update_display(player.guild.id, channel, player),
    )

    assert first is channel.sent[0]
    assert second is channel.sent[0]
    assert len(channel.sent) == 1
    assert len(channel.sent[0].edits) == 1


def test_display_refresh_starts_reuses_and_stops(captured_tasks) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()

    ui.ensure_display_refresh(player.guild.id, player)
    [task] = captured_tasks
    assert state.display_refresh_task is task

    player.paused = True
    ui.ensure_display_refresh(player.guild.id, player)
    assert task.cancelled is False
    assert state.display_refresh_task is task
    assert captured_tasks == [task]

    player.connected = False
    ui.ensure_display_refresh(player.guild.id, player)
    assert task.cancelled is True
    assert state.display_refresh_task is None


def test_idle_display_refresh_starts_for_connected_idle_player(captured_tasks) -> None:
    player = FakePlayer()
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()

    ui.ensure_display_refresh(player.guild.id, player)

    assert captured_tasks == [state.display_refresh_task]


async def test_display_refresh_loop_uses_active_then_idle_intervals(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_channel = FakeTextChannel()
    calls = []
    delays = []
    saves = []

    async def fake_sleep(delay):
        delays.append(delay)

    async def fake_save(seen_player):
        saves.append(seen_player)

    async def fake_create_or_update(guild_id, channel, seen_player, *, manage_refresh=True):
        calls.append((guild_id, channel, seen_player, manage_refresh))
        if len(calls) == 1:
            seen_player.paused = True
        else:
            seen_player.connected = False

    monkeypatch.setattr(ui.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ui, "save_player_state", fake_save)
    monkeypatch.setattr(ui, "create_or_update_display", fake_create_or_update)

    await ui.refresh_display_progress(player.guild.id, player)

    assert delays == [ui.DISPLAY_REFRESH_INTERVAL, ui.IDLE_DISPLAY_REFRESH_INTERVAL]
    assert calls == [
        (player.guild.id, state.display_channel, player, False),
        (player.guild.id, state.display_channel, player, False),
    ]
    assert saves == [player]


async def test_successful_display_edit_delays_automatic_refresh_budget() -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"))
    message = panel_message(player)
    state = get_guild_state(player.guild.id)
    attach_display(state, message, channel)

    result = await ui.create_or_update_display(player.guild.id, channel, player)

    assert result is message
    assert state.display_last_edit_at > 0
    assert ui.display_auto_refresh_cooldown(player.guild.id) > 0


async def test_progress_refresh_does_not_refresh_recommendations(monkeypatch) -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    state = get_guild_state(player.guild.id)
    state.display_message = panel_message(player)
    state.display_channel = channel
    calls = []

    async def fake_recommendations(seen_player, *, allow_refresh=True):
        calls.append((seen_player, allow_refresh))
        return ()

    monkeypatch.setattr(ui, "recommendations_for_player", fake_recommendations)

    await ui.create_or_update_display(player.guild.id, channel, player, manage_refresh=False)

    assert calls == [(player, False)]


async def test_suggestion_loading_does_not_hold_the_display_lock(monkeypatch) -> None:
    channel = FakeTextChannel()
    player = FakePlayer(current=FakeTrack("Current"), playing=True)
    get_guild_state(player.guild.id).display_channel = channel
    loading = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow_recommendations(seen_player, *, allow_refresh=True):
        calls.append(allow_refresh)
        if allow_refresh:
            loading.set()
            await release.wait()
        return ()

    monkeypatch.setattr(ui, "recommendations_for_player", slow_recommendations)
    monkeypatch.setattr(ui, "ensure_display_refresh", lambda guild_id, seen_player: None)

    refreshing = asyncio.create_task(ui.update_display_for_guild(player.guild.id, player))
    await loading.wait()
    await asyncio.wait_for(ui.update_display_for_guild(player.guild.id, player, manage_refresh=False), timeout=1)

    assert len(channel.sent) == 1
    assert not refreshing.done()

    release.set()
    await refreshing

    assert calls == [True, False, False]
    assert len(channel.sent) == 1


async def test_player_panel_controls_update_player_silently(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), playing=True, queue=FakeQueue([FakeTrack("Next")]), volume=50)
    updates = []

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    view = panel_view(player)

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


async def test_pause_records_pause_time(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), paused=False, playing=True)
    updates = []

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    interaction = FakeInteraction(guild=player.guild)

    await panel_view(player).pause_resume(interaction)

    state = get_guild_state(player.guild.id)
    assert player.pause_calls == [True]
    assert state.paused_at is not None
    assert interaction.response.deferred is True
    assert updates == [(player.guild.id, player)]


async def test_resume_after_long_pause_refreshes_voice_before_playing(monkeypatch) -> None:
    old_player = FakePlayer(current=FakeTrack("Current"), paused=True, playing=True)
    new_player = FakePlayer(guild=old_player.guild, current=old_player.current, paused=False, playing=True)
    old_player.guild.voice_client = old_player
    state = get_guild_state(old_player.guild.id)
    state.paused_at = ui.time.monotonic() - ui.LONG_PAUSE_VOICE_RECONNECT_SECONDS - 1
    updates = []
    reconnects = []

    async def fake_reconnect(seen_player):
        reconnects.append(seen_player)
        old_player.guild.voice_client = new_player
        return new_player

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: interaction.guild.voice_client)
    monkeypatch.setattr(ui, "reconnect_player_voice", fake_reconnect)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)

    interaction = FakeInteraction(guild=old_player.guild)

    await panel_view(old_player).pause_resume(interaction)

    assert reconnects == [old_player]
    assert old_player.pause_calls == []
    assert state.paused_at is None
    assert interaction.response.deferred is True
    assert updates == [(old_player.guild.id, new_player)]


@pytest.mark.parametrize("connected", [True, False], ids=["connected", "without_player"])
async def test_player_panel_add_button_opens_modal(monkeypatch, connected) -> None:
    player = FakePlayer() if connected else None
    guild = player.guild if connected else FakeGuild()
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)

    view = ui.PlayerPanelView(guild.id, ui.build_player_panel_model(player, guild.id))
    interaction = FakeInteraction(guild=guild)

    await view.add(interaction)

    assert isinstance(interaction.response.modal, ui.AddSongModal)
    assert interaction.response.modal.guild_id == guild.id


async def test_background_display_refresh_is_kept_until_it_finishes(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    release = asyncio.Event()
    refreshed = []

    async def slow_update(guild_id, seen_player):
        await release.wait()
        refreshed.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "update_display_for_guild", slow_update)

    before = set(ui.background_tasks)
    ui.refresh_display_in_background(player.guild.id, player)
    (task,) = ui.background_tasks - before

    assert not task.done()

    release.set()
    await task

    assert refreshed == [(player.guild.id, player)]
    assert task not in ui.background_tasks


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

    async def fake_create_display(guild_id, channel, seen_player, *, manage_refresh=True):
        updates.append((guild_id, seen_player, manage_refresh))

    def fake_background_refresh(guild_id, seen_player):
        backgrounds.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)
    monkeypatch.setattr(ui, "create_or_update_display", fake_create_display)
    monkeypatch.setattr(ui, "refresh_display_in_background", fake_background_refresh)

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


async def test_add_song_modal_connects_like_play_when_player_missing(monkeypatch) -> None:
    guild = FakeGuild()
    player = FakePlayer(guild=guild, playing=False)
    guild.voice_client = None
    voice_channel = FakeVoiceChannel(guild=guild, player=player)
    track = FakeTrack("One More Time")
    updates = []
    backgrounds = []

    async def fake_queue_track_request(seen_player, query, requester, *, play_first=False):
        assert seen_player is player
        assert query == "daft punk"
        assert requester == "tester"
        player.current = track
        return TrackRequestResult([track], LoadSummary("One More Time", 1, "youtube"), True)

    async def fake_create_display(guild_id, channel, seen_player, *, manage_refresh=True):
        updates.append((guild_id, channel, seen_player, manage_refresh))

    def fake_background_refresh(guild_id, seen_player):
        backgrounds.append((guild_id, seen_player))

    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(ui, "create_or_update_display", fake_create_display)
    monkeypatch.setattr(ui, "refresh_display_in_background", fake_background_refresh)

    modal = ui.AddSongModal(guild.id)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=guild, user=FakeMember(voice_channel=voice_channel))

    await modal.on_submit(interaction)

    assert voice_channel.connect_kwargs is not None
    assert guild.voice_client is player
    assert player.current is track
    assert interaction.response.deferred is True
    assert interaction.deleted_original_response is True
    assert updates == [(guild.id, interaction.channel, player, False)]
    assert len(backgrounds) == 1


async def test_add_song_modal_accepts_yes_for_play_next(monkeypatch) -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Queued")]))
    calls = []

    async def fake_queue_track_request(seen_player, query, requester, *, play_first=False):
        calls.append((seen_player, query, requester, play_first))
        return TrackRequestResult([FakeTrack("Next")], LoadSummary("Next", 1, "youtube"), False)

    async def fake_update(guild_id, seen_player, *, manage_refresh=True):
        return None

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)
    monkeypatch.setattr(ui, "update_display_for_guild", fake_update)
    monkeypatch.setattr(ui, "refresh_display_in_background", lambda guild_id, seen_player: None)

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


async def test_add_song_modal_reports_user_outside_voice_when_player_missing(monkeypatch) -> None:
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: None)
    modal = ui.AddSongModal(123)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=FakePlayer().guild)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == "Join a voice channel first."
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


async def test_add_song_modal_rejects_missing_voice_permissions_when_connecting() -> None:
    guild = FakeGuild()
    voice_channel = FakeVoiceChannel(
        guild=guild,
        permissions=SimpleNamespace(view_channel=True, connect=False, speak=True),
    )
    modal = ui.AddSongModal(guild.id)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=guild, user=FakeMember(voice_channel=voice_channel))

    await modal.on_submit(interaction)

    assert voice_channel.connect_kwargs is None
    assert "Connect" in last_response_text(interaction)
    assert interaction.response.sent[-1]["kwargs"]["ephemeral"] is True


@pytest.mark.parametrize(("outcome", "expected"), REQUEST_FAILURES)
async def test_add_song_modal_reports_request_failure(monkeypatch, outcome, expected) -> None:
    player = FakePlayer()

    async def fake_queue_track_request(player, query, requester, *, play_first=False):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "queue_track_request", fake_queue_track_request)

    modal = ui.AddSongModal(player.guild.id)
    modal.query._value = "daft punk"
    interaction = FakeInteraction(guild=player.guild)

    await modal.on_submit(interaction)

    assert last_response_text(interaction) == expected
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

    interaction = FakeInteraction(guild=player.guild)

    await panel_view(player).stop(interaction)

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
    message = panel_message(player)
    attach_display(state, message, channel)
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
    attach_display(state, message, FakeTextChannel())
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
    message = panel_message(player)
    attach_display(state, message, channel)

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
    attach_display(state, message, channel)
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


async def test_resume_starts_waiting_queue_without_current_track(monkeypatch) -> None:
    player = FakePlayer(paused=True, queue=FakeQueue([FakeTrack("Waiting")]))
    async def update(guild_id, seen_player):
        pass
    monkeypatch.setattr(ui, "player_for_interaction", lambda interaction: player)
    monkeypatch.setattr(ui, "update_display_for_guild", update)
    model = ui.build_player_panel_model(player, player.guild.id)
    assert model.disabled["pause_resume"] is False
    assert model.pause_label == "Resume"
    view = ui.PlayerPanelView(player.guild.id, model)
    await view.pause_resume(FakeInteraction(guild=player.guild))
    assert player.current.title == "Waiting"
    assert player.paused is False
