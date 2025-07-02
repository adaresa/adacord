import re
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Any
import discord

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

async def fetch_track_info(query: str, requester: str = None) -> Track:
    """Fetch comprehensive track information"""
    try:
        from audio.extractor import extractor
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
