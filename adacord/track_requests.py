from __future__ import annotations

from dataclasses import dataclass

import wavelink

from adacord.persistence import save_player_state
from adacord.player import add_tracks
from adacord.recommendations import clear_guild_recommendation_cache
from adacord.sources import LoadSummary, load_tracks


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
) -> TrackRequestResult:
    try:
        tracks, summary = await load_tracks(query, requester)
    except Exception as exc:
        raise TrackRequestLoadError(str(exc)) from exc

    if not tracks:
        return TrackRequestResult([], summary, False)

    was_idle = not player.current and player.queue.is_empty
    try:
        await add_tracks(player, tracks)
    except Exception as exc:
        raise TrackRequestPlaybackError(str(exc)) from exc

    await save_player_state(player)
    clear_guild_recommendation_cache(player.guild.id)
    return TrackRequestResult(tracks, summary, was_idle)
