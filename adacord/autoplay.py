"""Keep music going from YouTube radio once the queue runs out."""

import logging

import wavelink

from adacord.player import add_tracks, has_next_track
from adacord.recommendations import AUTOPLAY_REQUESTER, autoplay_track
from adacord.state import get_guild_state
from adacord.utils import track_log_label

logger = logging.getLogger(__name__)

MAX_VIBE_LENGTH = 100


def listeners_present(player: wavelink.Player) -> bool:
    channel = getattr(player, "channel", None)
    members = getattr(channel, "members", None)
    if members is None:
        return True
    return any(not getattr(member, "bot", False) for member in members)


def autoplay_label(guild_id: int) -> str:
    state = get_guild_state(guild_id)
    if not state.autoplay:
        return "Off"
    return f"On ({state.autoplay_vibe})" if state.autoplay_vibe else "On"


def set_autoplay(guild_id: int, enabled: bool, vibe: str | None = None) -> None:
    state = get_guild_state(guild_id)
    state.autoplay = enabled
    vibe = " ".join((vibe or "").split())[:MAX_VIBE_LENGTH]
    state.autoplay_vibe = vibe if enabled and vibe else None


async def queue_autoplay_track(player: wavelink.Player, last_track: object | None) -> wavelink.Playable | None:
    """Queue (and start, if idle) one radio pick when autoplay is on and nothing is queued."""
    state = get_guild_state(player.guild.id)
    if not state.autoplay or has_next_track(player):
        return None
    if not getattr(player, "connected", True) or not listeners_present(player):
        return None

    track = await autoplay_track(player, last_track, vibe=state.autoplay_vibe)
    # A song may have been queued, or autoplay turned off, while the radio loaded.
    if not track or not state.autoplay or has_next_track(player) or not getattr(player, "connected", True):
        if not track:
            logger.info("Autoplay found nothing new for guild %s after %s", player.guild.id, track_log_label(last_track))
        return None

    extras = dict(track.extras) if isinstance(getattr(track, "extras", None), dict) else {}
    extras["requester"] = AUTOPLAY_REQUESTER
    track.extras = extras
    logger.info("Autoplay queued %s for guild %s", track_log_label(track), player.guild.id)
    await add_tracks(player, [track])
    return track
