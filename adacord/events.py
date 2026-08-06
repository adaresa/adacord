import logging
import asyncio
from typing import Any

from discord.ext import commands
import wavelink

from adacord.persistence import (
    clear_guild_state as clear_saved_guild_state,
    load_state,
    save_player_state,
    save_preserved_player_state,
)
from adacord.player import get_player, play_next
from adacord.recovery import restore_guild_playback_state
from adacord.sources import search_lavalink
from adacord.state import get_guild_state
from adacord.ui import update_display_for_guild
from adacord.utils import track_log_label

logger = logging.getLogger(__name__)

NON_ADVANCING_TRACK_END_REASONS = {"loadfailed", "replaced"}
RECOVERY_REQUESTER = "playback recovery"
VOICE_RECONNECT_ATTEMPT_DELAYS = (0.0, 1.0, 3.0)


def saved_playback_has_music(saved: dict[str, Any]) -> bool:
    current = saved.get("current")
    queue = saved.get("queue")
    return isinstance(current, dict) or bool(queue if isinstance(queue, list) else [])


def saved_playback_for_guild(guild_id: int) -> dict[str, Any] | None:
    data = load_state()
    guilds = data.get("guilds")
    if not isinstance(guilds, dict):
        return None
    saved = guilds.get(str(guild_id))
    if not isinstance(saved, dict):
        return None
    if not isinstance(saved.get("voice_channel_id"), int) or not saved_playback_has_music(saved):
        return None
    return saved


async def reconnect_saved_voice_playback(bot: commands.Bot, guild_id: int, saved: dict[str, Any]) -> None:
    guild = bot.get_guild(guild_id)
    last_error: Exception | None = None
    for attempt, delay in enumerate(VOICE_RECONNECT_ATTEMPT_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            logger.warning(
                "Voice playback reconnect attempt %s/%s starting for guild %s: saved_current=%s saved_queue=%s",
                attempt,
                len(VOICE_RECONNECT_ATTEMPT_DELAYS),
                guild_id,
                track_log_label(saved.get("current")),
                len(saved.get("queue") if isinstance(saved.get("queue"), list) else []),
            )
            await restore_guild_playback_state(bot, guild_id, saved)
            if guild and (player := get_player(guild)) and getattr(player, "connected", True):
                logger.info("Reconnected saved voice playback for guild %s on attempt %s", guild_id, attempt)
                return
            last_error = RuntimeError("restore completed without a connected player")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Voice playback reconnect attempt %s/%s failed for guild %s: %s",
                attempt,
                len(VOICE_RECONNECT_ATTEMPT_DELAYS),
                guild_id,
                exc,
            )

    logger.error("Could not reconnect saved voice playback for guild %s: %s", guild_id, last_error)


async def handle_bot_voice_disconnect(
    bot: commands.Bot,
    guild_id: int,
    player: wavelink.Player | None = None,
) -> None:
    state = get_guild_state(guild_id)
    if state.voice_refresh_in_progress:
        logger.info("Ignoring intentional voice disconnect during session refresh for guild %s", guild_id)
        return

    logger.warning(
        "Bot voice disconnect observed for guild %s: had_player=%s current=%s queue=%s",
        guild_id,
        bool(player),
        track_log_label(player.current) if player else "none",
        len(list(player.queue)) if player else 0,
    )
    if player and (player.current or not player.queue.is_empty):
        await save_player_state(player)

    saved = saved_playback_for_guild(guild_id)
    if not saved:
        logger.info("No saved playback to reconnect after voice disconnect for guild %s", guild_id)
        return

    task = state.voice_reconnect_task
    if task and not task.done():
        logger.info("Voice reconnect already in progress for guild %s", guild_id)
        return

    async def runner() -> None:
        try:
            await reconnect_saved_voice_playback(bot, guild_id, saved)
        finally:
            current_task = asyncio.current_task()
            if state.voice_reconnect_task is current_task:
                state.voice_reconnect_task = None

    state.voice_reconnect_task = asyncio.create_task(runner())


async def handle_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    player = payload.player
    if not player:
        return
    reason = str(getattr(payload, "reason", "") or "").lower()
    ended_track = getattr(payload, "track", None) or player.current
    logger.info(
        "Lavalink track end for guild %s: reason=%s track=%s queue=%s playing=%s paused=%s",
        player.guild.id,
        reason,
        track_log_label(ended_track),
        len(list(player.queue)),
        player.playing,
        player.paused,
    )
    if reason in NON_ADVANCING_TRACK_END_REASONS:
        logger.info("Ignoring non-advancing track end for guild %s: %s", player.guild.id, reason)
        return
    if not player.queue.is_empty:
        await play_next(player)
    await update_display_for_guild(player.guild.id, player)
    await save_player_state(player)


async def handle_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    if payload.player:
        track = getattr(payload, "track", None) or payload.player.current
        logger.info(
            "Lavalink track start for guild %s: track=%s queue=%s playing=%s paused=%s",
            payload.player.guild.id,
            track_log_label(track),
            len(list(payload.player.queue)),
            payload.player.playing,
            payload.player.paused,
        )
        await update_display_for_guild(payload.player.guild.id, payload.player)
        await save_player_state(payload.player)


def track_recovery_query(track: object) -> str | None:
    uri = getattr(track, "uri", None)
    if uri:
        return str(uri)

    extras = getattr(track, "extras", None)
    query = extras.get("query") if isinstance(extras, dict) else getattr(extras, "query", None)
    return str(query) if query else None


async def preserve_failed_playback(
    player: wavelink.Player,
    failed_track: wavelink.Playable | None,
    queued: list[wavelink.Playable],
    position: int,
    *,
    paused: bool,
) -> None:
    await save_preserved_player_state(
        player,
        current=failed_track,
        queued=queued,
        position=position,
        paused=paused,
    )
    await update_display_for_guild(player.guild.id, player)


async def recover_failed_track(
    player: wavelink.Player,
    failed_track: wavelink.Playable,
    *,
    position: int,
    volume: int | None,
) -> bool:
    query = track_recovery_query(failed_track)
    if not query:
        logger.info("No recovery query available for failed track in guild %s", player.guild.id)
        return False

    tracks = await search_lavalink(query, RECOVERY_REQUESTER, limit=1)
    if not tracks:
        logger.info("Recovery query returned no tracks for guild %s: %s", player.guild.id, query)
        return False

    replacement = tracks[0]
    length = getattr(replacement, "length", None)
    start = max(0, position)
    if isinstance(length, int) and length > 0:
        start = min(start, max(0, length - 1000))

    await player.play(
        replacement,
        start=start,
        volume=volume,
        add_history=False,
    )
    return True


async def handle_track_exception(payload: wavelink.TrackExceptionEventPayload) -> None:
    player = payload.player
    if not player:
        return

    failed_track = getattr(payload, "track", None) or player.current
    queued = list(player.queue)
    position = max(0, int(getattr(player, "position", 0) or 0))
    volume = player.volume
    paused = bool(player.paused)
    logger.warning(
        "Lavalink track exception in guild %s: track=%s queue=%s position=%s paused=%s exception=%s",
        player.guild.id,
        track_log_label(failed_track),
        len(queued),
        position,
        paused,
        payload.exception,
    )

    try:
        if failed_track and await recover_failed_track(player, failed_track, position=position, volume=volume):
            logger.info("Recovered failed track for guild %s without consuming queue", player.guild.id)
            await update_display_for_guild(player.guild.id, player)
            await save_player_state(player)
            return
    except Exception:
        logger.exception("Could not recover failed track for guild %s", player.guild.id)

    logger.info("Preserving failed track and queue for guild %s after track exception", player.guild.id)
    await preserve_failed_playback(player, failed_track, queued, position, paused=paused)


async def handle_track_stuck(payload: wavelink.TrackStuckEventPayload) -> None:
    player = payload.player
    if not player:
        return

    failed_track = getattr(payload, "track", None) or player.current
    queued = list(player.queue)
    position = max(0, int(getattr(player, "position", 0) or 0))
    volume = player.volume
    paused = bool(player.paused)
    logger.warning(
        "Lavalink track stuck in guild %s after threshold %s: track=%s queue=%s position=%s paused=%s",
        player.guild.id,
        getattr(payload, "threshold", None),
        track_log_label(failed_track),
        len(queued),
        position,
        paused,
    )

    try:
        if failed_track and await recover_failed_track(player, failed_track, position=position, volume=volume):
            logger.info("Recovered stuck track for guild %s without consuming queue", player.guild.id)
            await update_display_for_guild(player.guild.id, player)
            await save_player_state(player)
            return
    except Exception:
        logger.exception("Could not recover stuck track for guild %s", player.guild.id)

    logger.info("Preserving failed track and queue for guild %s after track stuck", player.guild.id)
    await preserve_failed_playback(player, failed_track, queued, position, paused=paused)


async def handle_inactive_player(player: wavelink.Player) -> None:
    logger.info(
        "Lavalink inactive player for guild %s: current=%s queue=%s connected=%s",
        player.guild.id,
        track_log_label(player.current),
        len(list(player.queue)),
        player.connected,
    )
    await update_display_for_guild(player.guild.id, None)
    await clear_saved_guild_state(player.guild.id)

