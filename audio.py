import os
import yt_dlp
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Any
import discord
import aiohttp
import re

logger = logging.getLogger(__name__)

@dataclass
class Track:
    url: str
    title: str
    duration: Optional[int] = None
    requester: Optional[str] = None
    stream_url: Optional[str] = None

@dataclass
class GuildState:
    queue: deque
    text_channel: Optional[discord.TextChannel] = None
    current_track: Optional[Track] = None
    is_playing: bool = False
    is_paused: bool = False
    loop_mode: str = 'none'
    volume: float = 1.0
    volume_transformer: Optional[discord.PCMVolumeTransformer] = None
    display_message: Optional[discord.Message] = None
    display_channel: Optional[discord.TextChannel] = None
    previous_volume: float = 1.0  # for mute/unmute functionality

guild_states: Dict[int, GuildState] = {}

def get_guild_state(guild_id: int) -> GuildState:
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildState(queue=deque())
    return guild_states[guild_id]

def is_url(string: str) -> bool:
    return re.match(r'https?://', string) is not None

class AudioExtractor:
    def __init__(self):
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio/best',
            'noplaylist': True,
            'default_search': 'ytsearch1',
            'extractaudio': True,
            'audioformat': 'mp3',
            'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        }
    
    async def extract_info(self, query: str, download: bool = False) -> Dict[str, Any]:
        """Extract video info with timeout and error handling"""
        loop = asyncio.get_event_loop()
        
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: ydl.extract_info(query, download=download)),
                    timeout=15
                )
                return info
        except asyncio.TimeoutError:
            raise Exception("Request timed out")
        except Exception as e:
            raise Exception(f"Extraction failed: {str(e)[:100]}")
    
    async def get_stream_url(self, query: str) -> str:
        """Get direct stream URL without downloading"""
        info = await self.extract_info(query, download=False)
        
        if 'entries' in info and info['entries']:
            entry = info['entries'][0]
        else:
            entry = info
        
        # Get the best audio stream URL
        if 'url' in entry:
            return entry['url']
        
        # Fallback to formats if direct URL not available
        formats = entry.get('formats', [])
        audio_formats = [f for f in formats if f.get('acodec') != 'none']
        
        if audio_formats:
            # Prefer highest quality audio
            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
            return best_audio['url']
        
        raise Exception("No audio stream found")

extractor = AudioExtractor()

async def fetch_track_info(query: str, requester: str = None) -> Track:
    """Fetch comprehensive track information"""
    try:
        info = await extractor.extract_info(query, download=False)
        
        if 'entries' in info and info['entries']:
            entry = info['entries'][0]
        else:
            entry = info
        
        return Track(
            url=query,
            title=entry.get('title', query),
            duration=entry.get('duration'),
            requester=requester
        )
    
    except Exception as e:
        logger.debug(f"Info fetch failed for {query}: {str(e)[:100]}")        
        return Track(url=query, title=query, requester=requester)

async def play_next(guild_id: int, vc: discord.VoiceClient, bot: 'commands.Bot'):
    """Enhanced play_next with real-time volume control"""
    state = get_guild_state(guild_id)
    
    # Import here to avoid circular import
    from commands import update_display_for_guild
    
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
        # Queue empty
        state.is_playing = False
        state.current_track = None
        state.volume_transformer = None
        
        # Update display to show empty state
        await update_display_for_guild(guild_id)
        
        # Auto-disconnect logic
        await asyncio.sleep(30)
        if not state.queue and vc and vc.is_connected() and not vc.is_playing():
            await vc.disconnect()
            # Clean up display
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
        # Get stream URL
        stream_url = await extractor.get_stream_url(next_track.url)
        next_track.stream_url = stream_url
        
        if state.text_channel:
            await state.text_channel.send(f"🎵 Now playing: **{next_track.title}**", delete_after=2)
        
    except Exception as e:
        logger.error(f"Failed to get stream for {next_track.title}: {str(e)[:200]}")
        if state.text_channel:
            await state.text_channel.send(f"❌ Failed to play: **{next_track.title}**")
        await play_next(guild_id, vc, bot)
    
    # Verify connection again
    if not vc or not vc.is_connected():
        state.is_playing = False
        state.current_track = None
        state.volume_transformer = None
        await update_display_for_guild(guild_id)
        return
    
    def after_playing(error):
        if error:
            logger.error(f"Playback error: {error}")
        
        # Clear volume transformer reference
        state.volume_transformer = None
        
        # Queue next track
        if vc and vc.is_connected():
            fut = asyncio.run_coroutine_threadsafe(play_next(guild_id, vc, bot), bot.loop)
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
        
        # Create base audio source
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
        
        # Wrap with PCMVolumeTransformer for real-time volume control
        volume_source = discord.PCMVolumeTransformer(source, volume=state.volume)
        state.volume_transformer = volume_source
        
        vc.play(volume_source, after=after_playing)
        state.is_playing = True
        
        # Update display
        await update_display_for_guild(guild_id)
        
    except Exception as e:
        logger.error(f"Failed to play stream: {e}")
        after_playing(e)

def set_volume(guild_id: int, volume: float) -> bool:
    """Set volume for currently playing track without stopping playback"""
    state = get_guild_state(guild_id)
    state.volume = volume
    
    # Apply to current transformer if playing
    if state.volume_transformer:
        state.volume_transformer.volume = volume
        return True
    return False
