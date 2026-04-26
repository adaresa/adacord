import wavelink

from adacord.persistence import clear_guild_state as clear_saved_guild_state, save_player_state
from adacord.player import play_next
from adacord.state import get_guild_state
from adacord.ui import send_transient, update_display_for_guild


async def handle_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    player = payload.player
    if not player:
        return
    if not player.queue.is_empty:
        await play_next(player)
    await update_display_for_guild(player.guild.id, player)
    save_player_state(player)


async def handle_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    if payload.player:
        await update_display_for_guild(payload.player.guild.id, payload.player)
        save_player_state(payload.player)


async def handle_inactive_player(player: wavelink.Player) -> None:
    state = get_guild_state(player.guild.id)
    if state.display_channel:
        await send_transient(state.display_channel, "Disconnected after being idle.")
    await update_display_for_guild(player.guild.id, player)
    clear_saved_guild_state(player.guild.id)

