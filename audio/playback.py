import asyncio
import logging
import discord
from audio.track import get_guild_state
from commands.helpers import update_display_for_guild

logger = logging.getLogger(__name__)

async def play_next(ctx, vc, bot):
    guild_id = ctx.guild.id
    state = get_guild_state(guild_id)
    # Verify connection
    if not vc or not vc.is_connected():
        state.is_playing = False
        state.current_track = None
        state.volume_transformer = None
        await update_display_for_guild(guild_id)
        return
    # Handle loop modes
    if state.loop_mode == 'track' and state.current_track:
        next_track = state.current_track
    elif state.queue:
        next_track = state.queue.popleft()
        if state.loop_mode == 'queue' and state.current_track:
            state.queue.append(state.current_track)
    else:
        state.is_playing = False
        state.current_track = None
        state.volume_transformer = None
        await update_display_for_guild(guild_id)
        await asyncio.sleep(30)
        if not state.queue and vc and vc.is_connected() and not vc.is_playing():
            await vc.disconnect()
            if state.display_message:
                try:
                    await state.display_message.delete()
                except:
                    pass
                state.display_message = None
                state.display_channel = None
        return
    state.current_track = next_track
    try:
        from audio.extractor import extractor
        stream_url = await extractor.get_stream_url(next_track.url)
        next_track.stream_url = stream_url
        await ctx.send_followup(f"🎵 Now playing: **{next_track.title}**", delete_after=3)
    except Exception as e:
        logger.error(f"Failed to get stream for {next_track.title}: {str(e)[:200]}")
        await ctx.send_followup(f"❌ Failed to play: **{next_track.title}**")
        await play_next(ctx, vc, bot)
        return
    if not vc or not vc.is_connected():
        state.is_playing = False
        state.current_track = None
        state.volume_transformer = None
        await update_display_for_guild(guild_id)
        return
    def after_playing(error):
        if error:
            logger.error(f"Playback error: {error}")
        state.volume_transformer = None
        if vc and vc.is_connected():
            fut = asyncio.run_coroutine_threadsafe(play_next(ctx, vc, bot), bot.loop)
            try:
                fut.result()
            except Exception as exc:
                logger.error(f"Error queuing next: {exc}")
        else:
            state.is_playing = False
            state.current_track = None
    try:
        ffmpeg_options = {
            'before_options': (
                '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
                '-nostdin'
            ),
            'options': (
                '-vn -ar 48000 -ac 2 -b:a 128k'
            )
        }
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
        volume_source = discord.PCMVolumeTransformer(source, volume=state.volume)
        state.volume_transformer = volume_source
        vc.play(volume_source, after=after_playing)
        state.is_playing = True
        await update_display_for_guild(guild_id)
    except Exception as e:
        logger.error(f"Failed to play stream: {e}")
        after_playing(e)

def set_volume(guild_id: int, volume: float) -> bool:
    state = get_guild_state(guild_id)
    state.volume = volume
    if state.volume_transformer:
        state.volume_transformer.volume = volume
        return True
    return False
