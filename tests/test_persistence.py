from __future__ import annotations

from adacord.persistence import (
    clear_guild_state_now as clear_saved_guild_state,
    load_state,
    save_player_state,
    track_from_payload,
    track_payload,
)
from conftest import FakePlayer, FakeQueue, FakeTrack


def test_track_payload_round_trips_to_playable() -> None:
    track = FakeTrack("Round Trip", author="Tester")
    track.extras = {"requester": "ada", "display_title": "Custom title"}

    restored = track_from_payload(track_payload(track))

    assert restored is not None
    assert restored.title == "Round Trip"
    assert restored.author == "Tester"
    assert dict(restored.extras) == {"requester": "ada", "display_title": "Custom title"}


async def test_save_and_clear_player_state() -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Next")]))

    await save_player_state(player)
    data = load_state()

    saved = data["guilds"][str(player.guild.id)]
    assert saved["current"]["title"] == "Current"
    assert saved["queue"][0]["title"] == "Next"

    clear_saved_guild_state(player.guild.id)
    assert load_state()["guilds"] == {}


async def test_save_player_state_skips_disconnected_player() -> None:
    player = FakePlayer(current=FakeTrack("Current"), queue=FakeQueue([FakeTrack("Next")]))
    player.connected = False

    await save_player_state(player)

    assert load_state()["guilds"] == {}
