import discord
from discord.ext import commands
from discord import option
from collections import deque
from audio import get_guild_state, fetch_track_info, play_next, Track, set_volume
import logging
import asyncio
from typing import Optional
from datetime import datetime
import random

logger = logging.getLogger(__name__)

# ============= PERSISTENT DISPLAY CLASSES =============

class PersistentControlPanel(discord.ui.View):
    """Combined control panel with all music controls"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)  # No timeout for persistent view
        self.guild_id = guild_id
        self.last_update = datetime.now()
    
    def get_display_embed(self) -> discord.Embed:
        """Generate the current display embed"""
        state = get_guild_state(self.guild_id)
        
        # Determine embed color based on state
        if not state.current_track:
            color = discord.Color.dark_grey()
        elif state.is_paused:
            color = discord.Color.yellow()
        else:
            color = discord.Color.green()
        
        embed = discord.Embed(
            title="🎵 Music Player",
            color=color
        )
        
        # Current track section
        if state.current_track:
            track = state.current_track
            status_emoji = "⏸️" if state.is_paused else "▶️"
            
            # Track info with duration
            track_info = f"{status_emoji} **{track.title}**"
            if track.duration:
                minutes, seconds = divmod(track.duration, 60)
                track_info += f" `[{minutes}:{seconds:02d}]`"
            
            embed.add_field(
                name="Now Playing",
                value=track_info,
                inline=False
            )
            
            # Add requester if available
            if track.requester:
                embed.add_field(name="Requested by", value=track.requester, inline=True)
        else:
            embed.add_field(
                name="Now Playing",
                value="*Nothing playing*",
                inline=False
            )
        
        # Volume and loop status
        volume_percentage = int(state.volume * 100)
        volume_emoji = "🔇" if volume_percentage == 0 else "🔉" if volume_percentage < 50 else "🔊"
        embed.add_field(
            name="Volume",
            value=f"{volume_emoji} {volume_percentage}%",
            inline=True
        )
        
        if state.loop_mode != 'none':
            loop_emoji = "🔂" if state.loop_mode == 'track' else "🔁"
            embed.add_field(
                name="Loop",
                value=f"{loop_emoji} {state.loop_mode.title()}",
                inline=True
            )
        
        # Queue preview (next 3 tracks)
        if state.queue:
            queue_preview = []
            for i, track in enumerate(list(state.queue)[:3]):
                duration_str = ""
                if track.duration:
                    m, s = divmod(track.duration, 60)
                    duration_str = f" `[{m}:{s:02d}]`"
                queue_preview.append(f"`{i+1}.` {track.title}{duration_str}")
            
            if len(state.queue) > 3:
                queue_preview.append(f"*...and {len(state.queue) - 3} more*")
            
            embed.add_field(
                name=f"Queue ({len(state.queue)} track{'s' if len(state.queue) != 1 else ''})",
                value="\n".join(queue_preview),
                inline=False
            )
        else:
            embed.add_field(
                name="Queue",
                value="*Empty*",
                inline=False
            )
        
        # Footer with last update
        embed.set_footer(text=f"Last updated")
        embed.timestamp = self.last_update
        
        return embed
    
    # Playback controls row
    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def restart_track(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Restart current track"""
        vc = interaction.guild.voice_client
        state = get_guild_state(self.guild_id)
        
        if vc and state.current_track:
            # Re-queue current track at front
            state.queue.appendleft(state.current_track)
            vc.stop()
            await interaction.response.send_message("⏮️ Restarting track!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing to restart!", ephemeral=True)
    
    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Pause/Resume playback"""
        vc = interaction.guild.voice_client
        state = get_guild_state(self.guild_id)
        
        if not vc:
            await interaction.response.send_message("❌ Not connected!", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            state.is_paused = True
            await interaction.response.send_message("⏸️ Paused!", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            state.is_paused = False
            await interaction.response.send_message("▶️ Resumed!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing to pause/resume!", ephemeral=True)
        
        # Update display
        await update_display_for_guild(self.guild_id)
    
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip_track(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Skip to next track"""
        vc = interaction.guild.voice_client
        
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing to skip!", ephemeral=True)
    
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop_playback(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Stop and clear queue"""
        vc = interaction.guild.voice_client
        state = get_guild_state(self.guild_id)
        
        if vc:
            vc.stop()
            state.queue.clear()
            state.current_track = None
            state.is_playing = False
            await interaction.response.send_message("⏹️ Stopped and cleared queue!", ephemeral=True, delete_after=3)
            await update_display_for_guild(self.guild_id)
        else:
            await interaction.response.send_message("❌ Nothing to stop!", ephemeral=True)
    
    # Volume controls row
    @discord.ui.button(emoji="🔉", label="-10%", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        new_volume = max(0, state.volume - 0.1)
        set_volume(self.guild_id, new_volume)
        
        await interaction.response.send_message(
            f"🔉 Volume: {int(new_volume * 100)}%", 
            ephemeral=True,
            delete_after=3
        )
        await update_display_for_guild(self.guild_id)
    
    @discord.ui.button(emoji="🔊", label="+10%", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        new_volume = min(2.0, state.volume + 0.1)
        set_volume(self.guild_id, new_volume)
        
        await interaction.response.send_message(
            f"🔊 Volume: {int(new_volume * 100)}%", 
            ephemeral=True,
            delete_after=3
        )
        await update_display_for_guild(self.guild_id)
    
    @discord.ui.button(emoji="🔇", label="Mute", style=discord.ButtonStyle.secondary, row=1)
    async def mute_toggle(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        
        if state.volume > 0:
            state.previous_volume = state.volume
            set_volume(self.guild_id, 0)
            await interaction.response.send_message("🔇 Muted!", ephemeral=True)
        else:
            restore_vol = getattr(state, 'previous_volume', 1.0)
            set_volume(self.guild_id, restore_vol)
            await interaction.response.send_message(
                f"🔊 Unmuted: {int(restore_vol * 100)}%", 
                ephemeral=True
            )
        
        await update_display_for_guild(self.guild_id)
    
    # Queue controls row
    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_queue(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        
        if not state.queue:
            await interaction.response.send_message("❌ Queue is empty!", ephemeral=True)
            return
        
        queue_list = list(state.queue)
        random.shuffle(queue_list)
        state.queue = deque(queue_list)
        
        await interaction.response.send_message(
            f"🔀 Shuffled {len(queue_list)} tracks!", 
            ephemeral=True,
            delete_after=3
        )
        await update_display_for_guild(self.guild_id)
    
    @discord.ui.button(emoji="🔁", label="Loop", style=discord.ButtonStyle.secondary, row=2)
    async def cycle_loop(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        
        # Cycle through loop modes
        modes = ['none', 'track', 'queue']
        current_index = modes.index(state.loop_mode)
        state.loop_mode = modes[(current_index + 1) % 3]
        
        mode_emoji = {"none": "➡️", "track": "🔂", "queue": "🔁"}
        await interaction.response.send_message(
            f"{mode_emoji[state.loop_mode]} Loop: {state.loop_mode.title()}", 
            ephemeral=True
        )
        await update_display_for_guild(self.guild_id)
    
    @discord.ui.button(emoji="📜", label="Full Queue", style=discord.ButtonStyle.secondary, row=2)
    async def show_queue(self, button: discord.ui.Button, interaction: discord.Interaction):
        """Show full queue in ephemeral message"""
        state = get_guild_state(self.guild_id)
        
        if not state.queue and not state.current_track:
            await interaction.response.send_message("❌ Nothing in queue!", ephemeral=True)
            return
        
        total_tracks = len(state.queue)
        total_pages = max(1, (total_tracks + 9) // 10)
        
        view = QueueView(self.guild_id, total_pages)
        embed = view.get_queue_embed(0)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ============= DISPLAY MANAGEMENT FUNCTIONS =============

async def create_or_update_display(guild_id: int, channel: discord.TextChannel) -> Optional[discord.Message]:
    """Create or update the persistent display"""
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

# ============= ORIGINAL VIEW CLASSES =============

class VolumeView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.guild_id = guild_id
    
    @discord.ui.button(label="🔉 -10%", style=discord.ButtonStyle.secondary)
    async def volume_down(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        new_volume = max(0, state.volume - 0.1)
        volume_applied = set_volume(self.guild_id, new_volume)
        
        percentage = int(new_volume * 100)
        volume_emoji = "🔇" if percentage == 0 else "🔉" if percentage < 50 else "🔊"
        
        # Update the embed
        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "🔊 Volume":
                embed.set_field_at(i, name="🔊 Volume", value=f"{volume_emoji} {percentage}%", inline=True)
                break
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="🔊 +10%", style=discord.ButtonStyle.secondary)
    async def volume_up(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        new_volume = min(2.0, state.volume + 0.1)
        volume_applied = set_volume(self.guild_id, new_volume)
        
        percentage = int(new_volume * 100)
        volume_emoji = "🔇" if percentage == 0 else "🔉" if percentage < 50 else "🔊"
        
        # Update the embed
        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "🔊 Volume":
                embed.set_field_at(i, name="🔊 Volume", value=f"{volume_emoji} {percentage}%", inline=True)
                break
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="🔇 Mute", style=discord.ButtonStyle.danger)
    async def mute_toggle(self, button: discord.ui.Button, interaction: discord.Interaction):
        state = get_guild_state(self.guild_id)
        
        if state.volume > 0:
            # Store current volume and mute
            state.previous_volume = state.volume
            set_volume(self.guild_id, 0)
            button.label = "🔊 Unmute"
            button.style = discord.ButtonStyle.success
            volume_text = "🔇 0% (Muted)"
        else:
            # Restore previous volume
            restore_vol = getattr(state, 'previous_volume', 1.0)
            set_volume(self.guild_id, restore_vol)
            button.label = "🔇 Mute"
            button.style = discord.ButtonStyle.danger
            percentage = int(restore_vol * 100)
            volume_emoji = "🔉" if percentage < 50 else "🔊"
            volume_text = f"{volume_emoji} {percentage}%"
        
        # Update the embed
        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "🔊 Volume":
                embed.set_field_at(i, name="🔊 Volume", value=volume_text, inline=True)
                break
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.primary)
    async def pause_resume(self, button: discord.ui.Button, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        state = get_guild_state(self.guild_id)
        
        if not vc:
            await interaction.response.send_message("❌ Not connected to voice!", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            state.is_paused = True
            button.label = "▶️ Resume"
            button.style = discord.ButtonStyle.success
            
            # Update embed color and status
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.yellow()
            for i, field in enumerate(embed.fields):
                if field.name == "📊 Status":
                    embed.set_field_at(i, name="📊 Status", value="⏸️ Paused", inline=True)
                    break
            
        elif vc.is_paused():
            vc.resume()
            state.is_paused = False
            button.label = "⏸️ Pause"
            button.style = discord.ButtonStyle.primary
            
            # Update embed color and status
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            for i, field in enumerate(embed.fields):
                if field.name == "📊 Status":
                    embed.set_field_at(i, name="📊 Status", value="▶️ Playing", inline=True)
                    break
        else:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
            return
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.secondary)
    async def skip_track(self, button: discord.ui.Button, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nothing is playing!", ephemeral=True)
    
    async def on_timeout(self):
        # Disable all buttons when view times out
        for item in self.children:
            item.disabled = True
        
        # Try to edit the message to show disabled buttons
        try:
            if hasattr(self, 'message') and self.message:
                await self.message.edit(view=self)
        except:
            pass

class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, total_pages: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.current_page = 0
        self.total_pages = total_pages
        
        # Disable navigation buttons if only one page
        if total_pages <= 1:
            self.prev_page.disabled = True
            self.next_page.disabled = True
    
    def get_queue_embed(self, page: int = 0):
        state = get_guild_state(self.guild_id)
        embed = discord.Embed(title="🎶 Music Queue", color=discord.Color.blurple())
        
        # Current track
        if state.current_track:
            status_emoji = "⏸️" if state.is_paused else "🎵"
            embed.add_field(
                name="Now Playing", 
                value=f"{status_emoji} **{state.current_track.title}**",
                inline=False
            )
        
        # Queue pagination (10 tracks per page)
        if state.queue:
            start_idx = page * 10
            end_idx = min(start_idx + 10, len(state.queue))
            queue_slice = list(state.queue)[start_idx:end_idx]
            
            queue_list = []
            for i, track in enumerate(queue_slice):
                duration = ""
                if track.duration:
                    minutes, seconds = divmod(track.duration, 60)
                    duration = f" `[{minutes}:{seconds:02d}]`"
                queue_list.append(f"`{start_idx + i + 1}.` **{track.title}**{duration}")
            
            embed.add_field(
                name=f"Up Next ({len(state.queue)} track{'s' if len(state.queue) != 1 else ''})",
                value="\n".join(queue_list) if queue_list else "*Empty*",
                inline=False
            )
            
            if self.total_pages > 1:
                embed.set_footer(text=f"Page {page + 1} of {self.total_pages}")
        else:
            embed.add_field(name="Queue", value="*Empty*", inline=False)
        
        # Add loop status
        if state.loop_mode != 'none':
            loop_emoji = "🔂" if state.loop_mode == 'track' else "🔁"
            current_footer = embed.footer.text if embed.footer else ""
            footer_text = f"{loop_emoji} Loop: {state.loop_mode.title()}"
            if current_footer:
                footer_text = f"{current_footer} • {footer_text}"
            embed.set_footer(text=footer_text)
        
        return embed
    
    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.secondary)
    async def prev_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            
            # Update button states
            self.next_page.disabled = False
            if self.current_page == 0:
                self.prev_page.disabled = True
            
            embed = self.get_queue_embed(self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="➡️ Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            
            # Update button states
            self.prev_page.disabled = False
            if self.current_page == self.total_pages - 1:
                self.next_page.disabled = True
            
            embed = self.get_queue_embed(self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.success)
    async def refresh_queue(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Recalculate pages in case queue changed
        state = get_guild_state(self.guild_id)
        self.total_pages = max(1, (len(state.queue) + 9) // 10)
        
        # Reset to first page if current page is now out of bounds
        if self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)
        
        # Update button states
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == self.total_pages - 1 or self.total_pages <= 1
        
        embed = self.get_queue_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)

# ============= COMMAND SETUP FUNCTION =============

def setup_all_commands(bot):
    # Play command with persistent display
    @bot.slash_command(name="play", description="Play audio from YouTube or search query")
    @bot.slash_command(name="p", description="Play audio from YouTube or search query")
    @option("query", description="YouTube URL or search terms", required=True)
    async def play(ctx: discord.ApplicationContext, query: str):
        await ctx.defer()
        
        if not ctx.author.voice:
            await ctx.followup.send("❌ You must be in a voice channel!", ephemeral=True)
            return
        
        channel = ctx.author.voice.channel
        try:
            vc = ctx.voice_client or await channel.connect()
            if vc.channel != channel:
                await vc.move_to(channel)
        except Exception as e:
            await ctx.followup.send(f"❌ Failed to join voice: {e}")
            return
        
        try:
            track = await asyncio.wait_for(
                fetch_track_info(query, str(ctx.author)), 
                timeout=15
            )
        except asyncio.TimeoutError:
            await ctx.followup.send("⏱️ Timed out fetching track info.")
            return
        except Exception as e:
            await ctx.followup.send(f"❌ Error fetching track: {e}")
            return
        
        state = get_guild_state(ctx.guild.id)

        state.text_channel = ctx.channel
        
        # Check if we need to create display (first track added)
        needs_display = not state.current_track and not state.queue
        
        state.queue.append(track)
        
       # Show queue position with auto-delete
        position = len(state.queue)
        if state.current_track:
           await ctx.followup.send(f"✅ Added to queue (#{position}): **{track.title}**", delete_after=3)
        else:
           await ctx.followup.send(f"✅ Added: **{track.title}**", delete_after=3)
    
        
        # Create display if this is the first track
        if needs_display:
            await create_or_update_display(ctx.guild.id, ctx.channel)
        else:
            # Update existing display
            await update_display_for_guild(ctx.guild.id)
        
        if not state.is_playing:
            await play_next(ctx.guild.id, vc, bot)
    
    # Enhanced control commands
    @bot.slash_command(name="pause", description="Pause the current track")
    async def pause(ctx: discord.ApplicationContext):
        vc = ctx.voice_client
        state = get_guild_state(ctx.guild.id)
        
        if vc and vc.is_playing():
            vc.pause()
            state.is_paused = True
            await ctx.respond("⏸️ Paused!")
            await update_display_for_guild(ctx.guild.id)
        else:
            await ctx.respond("❌ Nothing is playing.", ephemeral=True)
    
    @bot.slash_command(name="resume", description="Resume the paused track")
    async def resume(ctx: discord.ApplicationContext):
        vc = ctx.voice_client
        state = get_guild_state(ctx.guild.id)
        
        if vc and vc.is_paused():
            vc.resume()
            state.is_paused = False
            await ctx.respond("▶️ Resumed!")
            await update_display_for_guild(ctx.guild.id)
        else:
            await ctx.respond("❌ Nothing is paused.", ephemeral=True)
    
    @bot.slash_command(name="loop", description="Set loop mode")
    @option("mode", description="Loop mode", choices=["none", "track", "queue"])
    async def loop(ctx: discord.ApplicationContext, mode: str):
        state = get_guild_state(ctx.guild.id)
        state.loop_mode = mode
        
        mode_emoji = {"none": "➡️", "track": "🔂", "queue": "🔁"}
        await ctx.respond(f"{mode_emoji[mode]} Loop mode set to: **{mode}**")
        await update_display_for_guild(ctx.guild.id)
    
    # Real-time volume command
    @bot.slash_command(name="volume", description="Set playback volume (0-200%)")
    @option("level", description="Volume level (0-200)", min_value=0, max_value=200)
    async def volume(ctx: discord.ApplicationContext, level: int):
        state = get_guild_state(ctx.guild.id)
        volume_multiplier = level / 100.0
        
        # Set volume (applies immediately if playing)
        volume_applied = set_volume(ctx.guild.id, volume_multiplier)
        
        volume_emoji = "🔇" if level == 0 else "🔉" if level < 50 else "🔊"
        
        if volume_applied and state.is_playing:
            await ctx.respond(f"{volume_emoji} Volume changed to **{level}%** (applied immediately)")
        else:
            await ctx.respond(f"{volume_emoji} Volume set to **{level}%** (will apply to next track)")
        
        await update_display_for_guild(ctx.guild.id)
    
    # Enhanced now playing with interactive controls
    @bot.slash_command(name="nowplaying", description="Show current track with interactive controls")
    @bot.slash_command(name="np", description="Show current track with interactive controls")
    async def nowplaying(ctx: discord.ApplicationContext):
        state = get_guild_state(ctx.guild.id)
        
        if not state.current_track:
            await ctx.respond("❌ Nothing is playing.", ephemeral=True)
            return
        
        track = state.current_track
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{track.title}**",
            color=discord.Color.green() if not state.is_paused else discord.Color.yellow()
        )
        
        if track.duration:
            minutes, seconds = divmod(track.duration, 60)
            duration_str = f"{minutes}:{seconds:02d}"
            embed.add_field(name="⏱️ Duration", value=duration_str, inline=True)
        
        if track.requester:
            embed.add_field(name="👤 Requested by", value=track.requester, inline=True)
        
        status = "⏸️ Paused" if state.is_paused else "▶️ Playing"
        embed.add_field(name="📊 Status", value=status, inline=True)
        
        if state.loop_mode != 'none':
            loop_emoji = "🔂" if state.loop_mode == 'track' else "🔁"
            embed.add_field(name="🔄 Loop", value=f"{loop_emoji} {state.loop_mode.title()}", inline=True)
        
        volume_emoji = "🔇" if state.volume == 0 else "🔉" if state.volume < 0.5 else "🔊"
        embed.add_field(name="🔊 Volume", value=f"{volume_emoji} {int(state.volume * 100)}%", inline=True)
        
        queue_size = len(state.queue)
        embed.add_field(name="📝 Queue", value=f"{queue_size} track{'s' if queue_size != 1 else ''}", inline=True)
        
        # Add interactive controls
        view = VolumeView(ctx.guild.id)
        await ctx.respond(embed=embed, view=view)
    
    # Enhanced queue display with pagination
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
    
    # Utility commands
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
    
    @bot.slash_command(name="stop", description="Stop playback and clear queue")
    async def stop(ctx: discord.ApplicationContext):
        vc = ctx.voice_client
        state = get_guild_state(ctx.guild.id)
        
        if vc:
            vc.stop()
            state.queue.clear()
            state.current_track = None
            state.is_playing = False
            state.volume_transformer = None
            await ctx.respond("⏹️ Stopped playback and cleared queue!", delete_after=3)
            await update_display_for_guild(ctx.guild.id)
        else:
            await ctx.respond("❌ Nothing is playing.", ephemeral=True, delete_after=3)
    
    @bot.slash_command(name="disconnect", description="Disconnect from voice channel")
    @bot.slash_command(name="dc", description="Disconnect from voice channel")
    async def disconnect(ctx: discord.ApplicationContext):
        vc = ctx.voice_client
        if vc:
            await vc.disconnect()
            state = get_guild_state(ctx.guild.id)
            
            # Clean up display
            if state.display_message:
                try:
                    await state.display_message.delete()
                except:
                    pass
            
            # Reset state
            state.current_track = None
            state.is_playing = False
            state.volume_transformer = None
            state.display_message = None
            state.display_channel = None
            
            await ctx.respond("👋 Disconnected from voice channel!")
        else:
            await ctx.respond("❌ Not connected to a voice channel.", ephemeral=True)
    
    # Additional utility commands
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
