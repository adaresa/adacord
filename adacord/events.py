import logging

import wavelink

from adacord.persistence import (
    clear_guild_state as clear_saved_guild_state,
    save_player_state,
    save_preserved_player_state,
)
from adacord.player import play_next
from adacord.sources import search_lavalink
from adacord.ui import update_display_for_guild

logger = logging.getLogger(__name__)

NON_ADVANCING_TRACK_END_REASONS = {"loadfailed", "replaced"}
RECOVERY_REQUESTER = "playback recovery"


async def handle_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    player = payload.player
    if not player:
        return
    reason = str(getattr(payload, "reason", "") or "").lower()
    if reason in NON_ADVANCING_TRACK_END_REASONS:
        logger.info("Ignoring non-advancing track end for guild %s: %s", player.guild.id, reason)
        return
    if not player.queue.is_empty:
        await play_next(player)
    await update_display_for_guild(player.guild.id, player)
    await save_player_state(player)


async def handle_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    if payload.player:
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
    logger.warning("Lavalink track exception in guild %s: %s", player.guild.id, payload.exception)

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
        "Lavalink track stuck in guild %s after threshold %s",
        player.guild.id,
        getattr(payload, "threshold", None),
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
    await update_display_for_guild(player.guild.id, None)
    await clear_saved_guild_state(player.guild.id)

