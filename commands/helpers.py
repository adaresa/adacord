import discord
from typing import Optional
from datetime import datetime
from commands.control_panels import PersistentControlPanel
import logging

logger = logging.getLogger(__name__)

async def create_or_update_display(guild_id: int, channel: discord.TextChannel) -> Optional[discord.Message]:
    """Create or update the persistent display"""
    from audio.track import get_guild_state
    state = get_guild_state(guild_id)
    # Create the view and embed
    view = PersistentControlPanel(guild_id)
    view.last_update = datetime.now()
    embed = view.get_display_embed()
    try:
        if state.display_message:
            # Try to edit existing message
            try:
                await state.display_message.edit(embed=embed, view=view)
                return state.display_message
            except (discord.NotFound, discord.HTTPException):
                # Message was deleted or can't be edited
                state.display_message = None
        # Create new message
        message = await channel.send(embed=embed, view=view)
        state.display_message = message
        state.display_channel = channel
        return message
    except Exception as e:
        logger.error(f"Failed to create/update display: {e}")
        return None

async def update_display_for_guild(guild_id: int):
    """Update the display for a specific guild"""
    from audio.track import get_guild_state
    state = get_guild_state(guild_id)
    if state.display_channel and (state.current_track or state.queue):
        await create_or_update_display(guild_id, state.display_channel)
    elif state.display_message and not state.current_track and not state.queue:
        # Clean up display when nothing is playing/queued
        try:
            await state.display_message.delete()
        except:
            pass
        state.display_message = None
        state.display_channel = None
