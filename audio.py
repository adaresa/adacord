import os
import yt_dlp
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import discord
from discord.ext import commands as dpy_commands
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
    stream_headers: Optional[Dict[str, str]] = None

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
            'geo_bypass': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'cachedir': False,
            'source_address': '0.0.0.0',  # force IPv4 to avoid some 403s on IPv6-only routes
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Linux; Android 10; SM-G975F) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Mobile Safari/537.36'
                ),
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': '*/*',
                'Origin': 'https://www.youtube.com',
                'Referer': 'https://www.youtube.com/',
            },
            # Prefer Android client to avoid some age/region restrictions and 403s
            'extractor_args': {
                'youtube': {
                    'player_client': ['android']
                }
            },
        }

        # Optional cookies for restricted videos
        cookies_path = os.getenv('YTDLP_COOKIES')
        if cookies_path and os.path.exists(cookies_path):
            self.ydl_opts['cookiefile'] = cookies_path
    
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
    
    async def get_stream_url(self, query: str) -> Tuple[str, Dict[str, str]]:
        """Get direct stream URL and required HTTP headers without downloading"""
        # If a raw URL is passed, prefer that directly; otherwise rely on ytsearch1
        info = await self.extract_info(query, download=False)
        
        if 'entries' in info and info['entries']:
            entry = info['entries'][0]
        else:
            entry = info
        
        # yt-dlp may provide required HTTP headers that must be forwarded to ffmpeg
        http_headers: Dict[str, str] = entry.get('http_headers') or info.get('http_headers') or {}

        # Get the best audio stream URL
        if 'url' in entry:
            return entry['url'], http_headers
        
        # Fallback to formats if direct URL not available
        formats = entry.get('formats', [])
        audio_formats = [f for f in formats if (f.get('acodec') and f.get('acodec') != 'none')]
        
        if audio_formats:
            # Prefer highest quality audio
            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
            return best_audio['url'], http_headers

        # If formats missing (common for some search results), try re-extracting via webpage_url
        webpage_url = entry.get('webpage_url') or entry.get('url')
        if webpage_url and not is_url(query):
            info2 = await self.extract_info(webpage_url, download=False)
            if 'entries' in info2 and info2['entries']:
                entry2 = info2['entries'][0]
            else:
                entry2 = info2

            http_headers = entry2.get('http_headers') or info2.get('http_headers') or http_headers
            formats2 = entry2.get('formats', [])
            audio_formats2 = [f for f in formats2 if (f.get('acodec') and f.get('acodec') != 'none')]
            if audio_formats2:
                best_audio = max(audio_formats2, key=lambda x: x.get('abr', 0) or 0)
                return best_audio['url'], http_headers
        
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

async def play_next(guild_id: int, vc: discord.VoiceClient, bot: 'dpy_commands.Bot'):
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
        # Get stream URL and headers
        stream_url, stream_headers = await extractor.get_stream_url(next_track.url)
        next_track.stream_url = stream_url
        next_track.stream_headers = stream_headers
        
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
        # Build headers for ffmpeg (pass both -user_agent/-referer and -headers)
        ua = None
        ref = None
        headers_option_parts = []
        if next_track.stream_headers:
            ua = next_track.stream_headers.get('User-Agent')
            ref = next_track.stream_headers.get('Referer') or next_track.stream_headers.get('Referer'.lower())
            headers_lines = ''.join(f"{k}: {v}\r\n" for k, v in next_track.stream_headers.items())
            headers_option_parts.append(f'-headers "{headers_lines}"')
        if not ua:
            ua = (
                'Mozilla/5.0 (Linux; Android 10; SM-G975F) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Mobile Safari/537.36'
            )
        if not ref:
            ref = 'https://www.youtube.com/'
        headers_option_parts.insert(0, f'-user_agent "{ua}"')
        headers_option_parts.insert(1, f'-referer "{ref}"')
        headers_option = ' '.join(headers_option_parts) + ' '

        ffmpeg_options = {
            'before_options': (
                f"{headers_option}-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                '-nostdin'
            ),
            'options': '-vn -b:a 128k'
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
