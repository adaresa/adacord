from __future__ import annotations

from dataclasses import dataclass
import logging
import time

import wavelink

from adacord.persistence import save_player_state
from adacord.player import add_tracks, play_next
from adacord.recommendations import clear_guild_recommendation_cache
from adacord.sources import LoadSummary, load_tracks
from adacord.state import get_guild_state
from adacord.utils import compact_log_value, track_log_label

logger = logging.getLogger(__name__)


class TrackRequestLoadError(RuntimeError):
    pass


class TrackRequestPlaybackError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackRequestResult:
    tracks: list[wavelink.Playable]
    summary: LoadSummary | None
    was_idle: bool


async def queue_track_request(
    player: wavelink.Player,
    query: str,
    requester: str,
    *,
    play_first: bool = False,
) -> TrackRequestResult:
    started = time.perf_counter()
    guild_id = player.guild.id
    logger.info(
        "Loading track request for guild %s: query=%r requester=%s current=%s queue=%s playing=%s paused=%s",
        guild_id,
        compact_log_value(query, limit=160),
        compact_log_value(requester, limit=80),
        track_log_label(player.current),
        len(list(player.queue)),
        player.playing,
        player.paused,
    )
    try:
        tracks, summary = await load_tracks(query, requester)
    except Exception as exc:
        logger.warning(
            "Track request load failed for guild %s after %.2fs: query=%r error=%s",
            guild_id,
            time.perf_counter() - started,
            compact_log_value(query, limit=160),
            exc,
        )
        raise TrackRequestLoadError(str(exc)) from exc

    if not tracks:
        logger.info(
            "Track request returned no tracks for guild %s after %.2fs: query=%r",
            guild_id,
            time.perf_counter() - started,
            compact_log_value(query, limit=160),
        )
        return TrackRequestResult([], summary, False)

    was_idle = not player.current and player.queue.is_empty
    try:
        logger.info(
            "Queueing %s track(s) for guild %s: source=%s title=%r was_idle=%s play_first=%s first=%s",
            len(tracks),
            guild_id,
            summary.source if summary else "unknown",
            summary.title if summary else "",
            was_idle,
            play_first,
            track_log_label(tracks[0]),
        )
        if play_first and not was_idle:
            for index, track in enumerate(tracks):
                player.queue.put_at(index, track)
            if not player.playing and not player.paused:
                await play_next(player)
        else:
            await add_tracks(player, tracks)
    except Exception as exc:
        logger.warning(
            "Track request playback start failed for guild %s after %.2fs: current=%s queue=%s error=%s",
            guild_id,
            time.perf_counter() - started,
            track_log_label(player.current),
            len(list(player.queue)),
            exc,
        )
        raise TrackRequestPlaybackError(str(exc)) from exc

    await save_player_state(player)
    state = get_guild_state(player.guild.id)
    if not player.paused:
        state.paused_at = None
    clear_guild_recommendation_cache(player.guild.id)
    logger.info(
        "Queued track request for guild %s in %.2fs: current=%s queue=%s playing=%s paused=%s",
        guild_id,
        time.perf_counter() - started,
        track_log_label(player.current),
        len(list(player.queue)),
        player.playing,
        player.paused,
    )
    return TrackRequestResult(tracks, summary, was_idle)
