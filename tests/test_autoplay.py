from __future__ import annotations

from types import SimpleNamespace

import pytest

from adacord import autoplay, commands, events, persistence, ui
from adacord.state import get_guild_state
from conftest import FakeGuild, FakeInteraction, FakePlayer, FakeQueue, FakeTrack, last_response_text


@pytest.fixture
def radio_pick(monkeypatch):
    pick = FakeTrack("Radio pick", author="Other Artist")
    calls = []

    async def fake_autoplay_track(player, last_track, *, vibe=None):
        calls.append((last_track, vibe))
        return pick

    monkeypatch.setattr(autoplay, "autoplay_track", fake_autoplay_track)
    return SimpleNamespace(track=pick, calls=calls)


@pytest.fixture
def quiet_display(monkeypatch):
    monkeypatch.setattr(ui, "get_player", lambda guild: guild.voice_client)
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(events, "update_display_for_guild", noop)
    monkeypatch.setattr(events, "save_player_state", noop)
    monkeypatch.setattr(ui, "save_player_state", noop)
    monkeypatch.setattr(commands, "save_player_state", noop)
    monkeypatch.setattr(commands, "update_display_for_guild", noop)


def listening_channel(*, humans: int = 1) -> SimpleNamespace:
    members = [SimpleNamespace(bot=True)] + [SimpleNamespace(bot=False) for _ in range(humans)]
    return SimpleNamespace(id=456, members=members)


def test_autoplay_is_off_by_default() -> None:
    assert get_guild_state(123).autoplay is False
    assert autoplay.autoplay_label(123) == "Off"


async def test_finished_track_starts_a_radio_pick_when_autoplay_is_on(radio_pick, quiet_display) -> None:
    ended = FakeTrack("Ended", author="Artist")
    player = FakePlayer()
    player.channel = listening_channel()
    autoplay.set_autoplay(player.guild.id, True)

    await events.handle_track_end(SimpleNamespace(player=player, reason="finished", track=ended))

    assert player.current is radio_pick.track
    assert radio_pick.calls == [(ended, None)]
    assert radio_pick.track.extras["requester"] == "Autoplay"


@pytest.mark.parametrize("reason", ["stopped", "cleanup"])
async def test_stop_and_cleanup_do_not_autoplay(radio_pick, quiet_display, reason) -> None:
    player = FakePlayer()
    player.channel = listening_channel()
    autoplay.set_autoplay(player.guild.id, True)

    await events.handle_track_end(SimpleNamespace(player=player, reason=reason, track=FakeTrack("Ended")))

    assert player.current is None
    assert radio_pick.calls == []


async def test_autoplay_off_leaves_the_player_idle(radio_pick, quiet_display) -> None:
    player = FakePlayer()
    player.channel = listening_channel()

    await events.handle_track_end(SimpleNamespace(player=player, reason="finished", track=FakeTrack("Ended")))

    assert player.current is None
    assert radio_pick.calls == []


async def test_autoplay_waits_for_listeners(radio_pick, quiet_display) -> None:
    player = FakePlayer()
    player.channel = listening_channel(humans=0)
    autoplay.set_autoplay(player.guild.id, True)

    await events.handle_track_end(SimpleNamespace(player=player, reason="finished", track=FakeTrack("Ended")))

    assert player.current is None
    assert radio_pick.calls == []


async def test_autoplay_drops_its_pick_when_a_song_was_queued_meanwhile(monkeypatch) -> None:
    player = FakePlayer()
    autoplay.set_autoplay(player.guild.id, True)
    requested = FakeTrack("Requested")

    async def slow_autoplay_track(player, last_track, *, vibe=None):
        player.queue.put(requested)
        return FakeTrack("Radio pick")

    monkeypatch.setattr(autoplay, "autoplay_track", slow_autoplay_track)

    assert await autoplay.queue_autoplay_track(player, FakeTrack("Ended")) is None
    assert list(player.queue) == [requested]


async def test_skipping_the_last_song_moves_on_to_a_radio_pick(radio_pick, quiet_display) -> None:
    current = FakeTrack("Current", author="Artist")
    player = FakePlayer(current=current)
    player.channel = listening_channel()
    autoplay.set_autoplay(player.guild.id, True)
    interaction = FakeInteraction(guild=player.guild)

    await ui.PlayerPanelView(guild_id=player.guild.id).skip(interaction)

    assert radio_pick.calls == [(current, None)]
    assert list(player.queue) == [radio_pick.track]
    assert player.skip_calls == [True]


async def test_autoplay_button_toggles_and_starts_when_idle(radio_pick, quiet_display, monkeypatch) -> None:
    player = FakePlayer()
    player.channel = listening_channel()
    player.queue.history.append(FakeTrack("Earlier", author="Artist"))
    interaction = FakeInteraction(guild=player.guild)
    view = ui.PlayerPanelView(guild_id=player.guild.id)

    async def no_refresh(interaction):
        return None

    monkeypatch.setattr(view, "refresh", no_refresh)

    await view.autoplay(interaction)
    assert get_guild_state(player.guild.id).autoplay is True
    assert player.current is radio_pick.track

    await view.autoplay(interaction)
    assert get_guild_state(player.guild.id).autoplay is False


async def test_autoplay_command_sets_a_vibe_and_turns_off(radio_pick, quiet_display) -> None:
    guild = FakeGuild()
    player = FakePlayer(guild=guild, current=FakeTrack("Current"))
    interaction = FakeInteraction(guild=guild)

    await commands.autoplay_impl(interaction, "  90s   french house ")
    assert last_response_text(interaction) == "Autoplay: On (90s french house)"
    assert get_guild_state(guild.id).autoplay_vibe == "90s french house"

    await commands.autoplay_impl(FakeInteraction(guild=guild), "off")
    assert get_guild_state(guild.id).autoplay is False
    assert get_guild_state(guild.id).autoplay_vibe is None
    assert player.current.title == "Current"


def test_autoplay_setting_is_saved_with_playback() -> None:
    player = FakePlayer(current=FakeTrack("Current"))
    autoplay.set_autoplay(player.guild.id, True, "lofi")

    _, saved = persistence.player_state_snapshot(player)

    assert saved["autoplay"] is True
    assert saved["autoplay_vibe"] == "lofi"


def test_panel_shows_autoplay_state() -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue())
    autoplay.set_autoplay(player.guild.id, True, "lofi")

    model = ui.build_player_panel_model(player, player.guild.id)

    assert model.autoplay == "On (lofi)"
