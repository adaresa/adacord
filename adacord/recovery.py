from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands
import wavelink

from adacord.persistence import load_state, save_player_state, track_from_payload
from adacord.player import ensure_player, set_loop_mode
from adacord.state import get_guild_state
from adacord.ui import update_display_for_guild

logger = logging.getLogger(__name__)


async def fetch_channel(bot: commands.Bot, channel_id: int) -> discord.abc.GuildChannel | None:
    channel = bot.get_channel(channel_id)
    if channel:
        return channel

    try:
        fetched = await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        logger.exception("Could not fetch saved channel %s", channel_id)
        return None

    return fetched if isinstance(fetched, discord.abc.GuildChannel) else None


async def fetch_display_message(
    channel: discord.abc.GuildChannel | None,
    message_id: int | None,
) -> discord.Message | None:
    if not message_id or not channel or not hasattr(channel, "fetch_message"):
        return None

    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        logger.info("Could not fetch saved display message %s", message_id)
        return None


def restored_tracks(saved: list[dict[str, Any]]) -> list[wavelink.Playable]:
    tracks = []
    for item in saved:
        track = track_from_payload(item)
        if track:
            tracks.append(track)
    return tracks


async def restore_playback_state(bot: commands.Bot) -> None:
    data = load_state()
    guilds = data.get("guilds", {})
    if not isinstance(guilds, dict) or not guilds:
        return

    logger.info("Restoring playback state for %s guild(s)", len(guilds))
    for raw_guild_id, saved in guilds.items():
        if not isinstance(saved, dict):
            continue

        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            continue

        try:
            await restore_guild_playback_state(bot, guild_id, saved)
        except Exception:
            logger.exception("Could not restore playback state for guild %s", guild_id)


async def restore_guild_playback_state(bot: commands.Bot, guild_id: int, saved: dict[str, Any]) -> None:
    guild = bot.get_guild(guild_id)
    if not guild:
        logger.info("Skipping playback restore for unknown guild %s", guild_id)
        return

    state = get_guild_state(guild_id)
    state.loop_mode = str(saved.get("loop_mode") or "none")

    display_channel_id = saved.get("display_channel_id")
    if isinstance(display_channel_id, int):
        state.display_channel_id = display_channel_id
        state.display_channel = await fetch_channel(bot, display_channel_id)

    display_message_id = saved.get("display_message_id")
    if isinstance(display_message_id, int):
        state.display_message_id = display_message_id
        state.display_message = await fetch_display_message(state.display_channel, display_message_id)

    current = saved.get("current")
    current_track = track_from_payload(current) if isinstance(current, dict) else None
    queue = saved.get("queue")
    queued_tracks = restored_tracks(queue) if isinstance(queue, list) else []

    voice_channel_id = saved.get("voice_channel_id")
    if not isinstance(voice_channel_id, int):
        await update_display_for_guild(guild_id, None)
        return

    voice_channel = await fetch_channel(bot, voice_channel_id)
    if not isinstance(voice_channel, (discord.VoiceChannel, discord.StageChannel)):
        await update_display_for_guild(guild_id, None)
        return

    state.voice_channel_id = voice_channel_id
    if not current_track and not queued_tracks:
        await update_display_for_guild(guild_id, None)
        return

    player = await ensure_player(guild, voice_channel)
    volume = saved.get("volume")
    if isinstance(volume, int):
        await player.set_volume(max(0, min(200, volume)))

    if queued_tracks:
        player.queue.put(queued_tracks)

    loop_mode = state.loop_mode if state.loop_mode in {"none", "track", "queue"} else "none"
    set_loop_mode(player, loop_mode)

    if current_track:
        position = saved.get("position")
        start = position if isinstance(position, int) else 0
        start = max(0, min(start, max(0, current_track.length - 1000)))
        await player.play(
            current_track,
            start=start,
            volume=player.volume,
            paused=bool(saved.get("paused")),
            add_history=False,
        )

    await update_display_for_guild(guild_id, player)
    save_player_state(player)
    logger.info("Restored playback state for guild %s", guild_id)
