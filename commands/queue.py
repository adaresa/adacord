import discord
from discord.ext import commands
from discord import option
from audio.track import get_guild_state
from commands.control_panels import QueueView
from config import logger
from collections import deque
import random
from commands.helpers import update_display_for_guild

def register_queue_commands(bot):
    @bot.slash_command(name="queue", description="Show the music queue with interactive controls")
    @bot.slash_command(name="q", description="Show the music queue with interactive controls")  
    async def queue_cmd(ctx: discord.ApplicationContext):
        state = get_guild_state(ctx.guild.id)
        
        # Calculate total pages (10 tracks per page)
        total_tracks = len(state.queue)
        total_pages = max(1, (total_tracks + 9) // 10)
        
        view = QueueView(ctx.guild.id, total_pages)
        embed = view.get_queue_embed(0)
        
        await ctx.respond(embed=embed, view=view)

    @bot.slash_command(name="skip", description="Skip the current track")
    @bot.slash_command(name="s", description="Skip the current track")
    async def skip(ctx: discord.ApplicationContext):
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await ctx.respond("⏭️ Skipped!", delete_after=3)
        else:
            await ctx.respond("❌ Nothing is playing.", ephemeral=True, delete_after=3)

    @bot.slash_command(name="clear", description="Clear the queue")
    @bot.slash_command(name="c", description="Clear the queue")
    async def clear(ctx: discord.ApplicationContext):
        state = get_guild_state(ctx.guild.id)
        if state.queue:
            count = len(state.queue)
            state.queue.clear()
            await ctx.respond(f"🗑️ Cleared {count} track{'s' if count != 1 else ''} from queue!", delete_after=3)
            await update_display_for_guild(ctx.guild.id)
        else:
            await ctx.respond("❌ Queue is already empty.", ephemeral=True, delete_after=3)

    @bot.slash_command(name="shuffle", description="Shuffle the current queue")
    async def shuffle(ctx: discord.ApplicationContext):
        state = get_guild_state(ctx.guild.id)
        if not state.queue:
            await ctx.respond("❌ Queue is empty.", ephemeral=True)
            return
        
        # Convert to list, shuffle, then back to deque
        queue_list = list(state.queue)
        random.shuffle(queue_list)
        state.queue = deque(queue_list)
        
        await ctx.respond(f"🔀 Shuffled {len(queue_list)} track{'s' if len(queue_list) != 1 else ''}!")
        await update_display_for_guild(ctx.guild.id)

    @bot.slash_command(name="remove", description="Remove a track from the queue")
    @option("position", description="Position in queue to remove (1-based)", min_value=1)
    async def remove(ctx: discord.ApplicationContext, position: int):
        state = get_guild_state(ctx.guild.id)
        
        if not state.queue:
            await ctx.respond("❌ Queue is empty.", ephemeral=True)
            return
        
        if position > len(state.queue):
            await ctx.respond(f"❌ Position {position} is out of range. Queue has {len(state.queue)} track{'s' if len(state.queue) != 1 else ''}.", ephemeral=True)
            return
        
        # Convert to list for easier manipulation
        queue_list = list(state.queue)
        removed_track = queue_list.pop(position - 1)
        state.queue = deque(queue_list)
        
        await ctx.respond(f"🗑️ Removed **{removed_track.title}** from position {position}")
        await update_display_for_guild(ctx.guild.id)

    @bot.slash_command(name="move", description="Move a track to a different position in the queue")
    @option("from_pos", description="Current position (1-based)", min_value=1)
    @option("to_pos", description="New position (1-based)", min_value=1)
    async def move(ctx: discord.ApplicationContext, from_pos: int, to_pos: int):
        state = get_guild_state(ctx.guild.id)
        
        if not state.queue:
            await ctx.respond("❌ Queue is empty.", ephemeral=True)
            return
        
        queue_len = len(state.queue)
        if from_pos > queue_len or to_pos > queue_len:
            await ctx.respond(f"❌ Position out of range. Queue has {queue_len} track{'s' if queue_len != 1 else ''}.", ephemeral=True)
            return
        
        # Convert to list for easier manipulation
        queue_list = list(state.queue)
        track = queue_list.pop(from_pos - 1)
        queue_list.insert(to_pos - 1, track)
        state.queue = deque(queue_list)
        
        await ctx.respond(f"📦 Moved **{track.title}** from position {from_pos} to {to_pos}")
        await update_display_for_guild(ctx.guild.id)
