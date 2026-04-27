from __future__ import annotations

from types import SimpleNamespace

from adacord import recovery
from adacord.state import get_guild_state
from conftest import FakeGuild, FakePlayer, FakeQueue, FakeTextChannel, FakeTrack, FakeVoiceChannel


class FakeBot:
    def __init__(self, guild: FakeGuild):
        self.guild = guild

    def get_guild(self, guild_id: int):
        return self.guild if guild_id == self.guild.id else None


def saved_track(track: FakeTrack) -> dict:
    return {"raw_data": track.raw_data, "extras": track.extras, "title": track.title, "uri": track.uri}


async def test_restore_guild_playback_state_reconnects_and_restores_player(monkeypatch) -> None:
    guild = FakeGuild()
    player = FakePlayer(guild=guild, queue=FakeQueue(), volume=25)
    voice_channel = FakeVoiceChannel(guild=guild, player=player)
    text_channel = FakeTextChannel()
    current = FakeTrack("Current", length=120_000)
    current.position = 0
    queued = FakeTrack("Queued")
    updates = []

    async def fake_fetch_channel(bot, channel_id):
        return text_channel if channel_id == text_channel.id else voice_channel

    async def fake_ensure_player(seen_guild, seen_channel):
        assert seen_guild is guild
        assert seen_channel is voice_channel
        return player

    async def fake_update(guild_id, seen_player):
        updates.append((guild_id, seen_player))

    monkeypatch.setattr(recovery, "fetch_channel", fake_fetch_channel)
    monkeypatch.setattr(recovery, "ensure_player", fake_ensure_player)
    monkeypatch.setattr(recovery, "update_display_for_guild", fake_update)
    monkeypatch.setattr(recovery.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(recovery.discord, "StageChannel", type("FakeStageChannel", (), {}))

    await recovery.restore_guild_playback_state(
        FakeBot(guild),
        guild.id,
        {
            "voice_channel_id": voice_channel.id,
            "display_channel_id": text_channel.id,
            "display_message_id": 999,
            "volume": 80,
            "loop_mode": "queue",
            "paused": True,
            "position": 30_000,
            "current": saved_track(current),
            "queue": [saved_track(queued)],
        },
    )

    state = get_guild_state(guild.id)
    assert state.voice_channel_id == voice_channel.id
    assert state.display_channel is text_channel
    assert state.display_message is not None
    assert player.volume == 80
    assert player.current.title == "Current"
    assert list(player.queue)[0].title == "Queued"
    assert player.paused is True
    assert player.play_kwargs[-1]["start"] == 30_000
    assert updates == [(guild.id, player)]


async def test_restore_guild_playback_state_skips_missing_voice_channel(monkeypatch) -> None:
    guild = FakeGuild()
    updates = []

    async def fake_fetch_channel(bot, channel_id):
        return None

    async def fake_update(guild_id, player):
        updates.append((guild_id, player))

    monkeypatch.setattr(recovery, "fetch_channel", fake_fetch_channel)
    monkeypatch.setattr(recovery, "update_display_for_guild", fake_update)

    await recovery.restore_guild_playback_state(
        FakeBot(guild),
        guild.id,
        {"voice_channel_id": 456, "current": saved_track(FakeTrack("Current")), "queue": []},
    )

    assert updates == [(guild.id, None)]


async def test_restore_guild_playback_state_handles_empty_saved_queue(monkeypatch) -> None:
    guild = FakeGuild()
    voice_channel = FakeVoiceChannel(guild=guild)
    updates = []

    async def fake_fetch_channel(bot, channel_id):
        return voice_channel

    async def fake_update(guild_id, player):
        updates.append((guild_id, player))

    monkeypatch.setattr(recovery, "fetch_channel", fake_fetch_channel)
    monkeypatch.setattr(recovery, "update_display_for_guild", fake_update)
    monkeypatch.setattr(recovery.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(recovery.discord, "StageChannel", type("FakeStageChannel", (), {}))

    await recovery.restore_guild_playback_state(FakeBot(guild), guild.id, {"voice_channel_id": voice_channel.id})

    assert updates == [(guild.id, None)]


async def test_restore_guild_playback_state_skips_missing_voice_permissions(monkeypatch) -> None:
    guild = FakeGuild()
    voice_channel = FakeVoiceChannel(
        guild=guild,
        permissions=SimpleNamespace(view_channel=True, connect=False, speak=True),
    )
    updates = []

    async def fake_fetch_channel(bot, channel_id):
        return voice_channel

    async def fake_update(guild_id, player):
        updates.append((guild_id, player))

    monkeypatch.setattr(recovery, "fetch_channel", fake_fetch_channel)
    monkeypatch.setattr(recovery, "update_display_for_guild", fake_update)
    monkeypatch.setattr(recovery.discord, "VoiceChannel", FakeVoiceChannel)
    monkeypatch.setattr(recovery.discord, "StageChannel", type("FakeStageChannel", (), {}))

    await recovery.restore_guild_playback_state(
        FakeBot(guild),
        guild.id,
        {"voice_channel_id": voice_channel.id, "current": saved_track(FakeTrack("Current")), "queue": []},
    )

    assert voice_channel.connect_kwargs is None
    assert updates == [(guild.id, None)]


def test_restored_tracks_skips_malformed_entries() -> None:
    valid = FakeTrack("Valid")

    tracks = recovery.restored_tracks([saved_track(valid), "bad", None])

    assert [track.title for track in tracks] == ["Valid"]
