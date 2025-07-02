import discord
from discord.ext import commands
from discord import option
from audio.track import get_guild_state, fetch_track_info
from audio.playback import play_next, set_volume
from commands.control_panels import PersistentControlPanel, VolumeView
from config import logger
from typing import Optional
import asyncio
from commands.helpers import create_or_update_display, update_display_for_guild

def register_music_commands(bot):
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
            await play_next(ctx, vc, bot)

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
