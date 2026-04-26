import asyncio
from dataclasses import dataclass, field

import discord

from adacord.config import DEFAULT_VOLUME


@dataclass
class GuildState:
    text_channel: discord.abc.Messageable | None = None
    display_message: discord.Message | None = None
    display_channel: discord.abc.Messageable | None = None
    voice_channel_id: int | None = None
    display_channel_id: int | None = None
    display_message_id: int | None = None
    loop_mode: str = "none"
    previous_volume: int = DEFAULT_VOLUME
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    idle_task: asyncio.Task[None] | None = None


guild_states: dict[int, GuildState] = {}


def get_guild_state(guild_id: int) -> GuildState:
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildState()
    return guild_states[guild_id]

