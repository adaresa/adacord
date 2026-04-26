import asyncio
import logging

import discord
import wavelink

from adacord.persistence import clear_guild_state as clear_saved_guild_state
from adacord.config import (
    default_volume,
    lavalink_connect_delay,
    lavalink_connect_retries,
    lavalink_password,
    lavalink_uri,
    lavalink_voice_ready_interval,
    lavalink_voice_ready_timeout,
    player_idle_timeout,
    voice_connect_timeout,
)
from adacord.state import get_guild_state

logger = logging.getLogger(__name__)


async def connect_lavalink(bot: discord.Client) -> None:
    uri = lavalink_uri()
    retries = lavalink_connect_retries()
    delay = lavalink_connect_delay()
    node = wavelink.Node(uri=uri, password=lavalink_password())
    for attempt in range(1, retries + 1):
        try:
            await wavelink.Pool.connect(client=bot, nodes=[node], cache_capacity=100)
            logger.info("Connected to Lavalink at %s", uri)
            return
        except Exception as exc:
            logger.warning(
                "Lavalink connection attempt %s/%s failed: %s",
                attempt,
                retries,
                exc,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(f"Could not connect to Lavalink at {uri}")


def get_player(guild: discord.Guild) -> wavelink.Player | None:
    player = guild.voice_client
    return player if isinstance(player, wavelink.Player) else None


async def cleanup_voice_client(guild: discord.Guild, reason: str) -> None:
    voice_client = guild.voice_client
    if not voice_client:
        return

    logger.warning("Cleaning up voice client for guild %s: %s", guild.id, reason)
    try:
        await voice_client.disconnect(force=True)
    except TypeError:
        await voice_client.disconnect()
    except Exception:
        logger.exception("Failed to clean up voice client for guild %s", guild.id)


async def ensure_player(
    guild: discord.Guild,
    target_channel: discord.VoiceChannel | discord.StageChannel,
) -> wavelink.Player:
    state = get_guild_state(guild.id)
    state.voice_channel_id = getattr(target_channel, "id", None)
    async with state.connect_lock:
        player = get_player(guild)
        if player and player.connected:
            if player.channel != target_channel:
                await player.move_to(target_channel)
            await wait_for_lavalink_voice(player)
            return player

        if guild.voice_client:
            await cleanup_voice_client(guild, "stale voice client before reconnect")

        try:
            player = await target_channel.connect(
                cls=wavelink.Player,
                self_deaf=True,
                reconnect=True,
                timeout=voice_connect_timeout(),
            )
            player.inactive_timeout = player_idle_timeout()
            player.inactive_channel_tokens = 1
            await wait_for_lavalink_voice(player)
            await player.set_volume(default_volume())
            return player
        except Exception:
            await cleanup_voice_client(guild, "voice connection failed")
            raise


async def wait_for_lavalink_voice(player: wavelink.Player) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + lavalink_voice_ready_timeout()

    while True:
        try:
            payload = await player.node.fetch_player_info(player.guild.id)
        except Exception as exc:
            logger.debug("Could not fetch Lavalink player info while waiting for voice: %s", exc)
            payload = None

        if payload and payload.state.connected:
            return

        if loop.time() >= deadline:
            state = "missing"
            if payload:
                state = f"connected={payload.state.connected}, ping={payload.state.ping}"
            raise RuntimeError(f"Lavalink did not finish connecting to voice ({state}).")

        await asyncio.sleep(lavalink_voice_ready_interval())


async def add_tracks(
    player: wavelink.Player,
    tracks: list[wavelink.Playable],
    *,
    start_playback: bool = True,
) -> None:
    if not tracks:
        return

    player.queue.put(tracks)
    if start_playback and not player.playing and not player.paused:
        await play_next(player)


async def play_next(player: wavelink.Player) -> wavelink.Playable | None:
    if player.queue.is_empty:
        return None

    await wait_for_lavalink_voice(player)
    track = player.queue.get()
    volume = player.volume if player.volume is not None else default_volume()
    await player.play(track, volume=volume)
    return track


async def set_volume(player: wavelink.Player, volume: int) -> None:
    await player.set_volume(max(0, min(200, volume)))


def set_loop_mode(player: wavelink.Player, mode: str) -> None:
    if mode not in {"none", "track", "queue"}:
        raise ValueError("loop mode must be one of: none, track, queue")

    state = get_guild_state(player.guild.id)
    state.loop_mode = mode

    if mode == "track":
        player.queue.mode = wavelink.QueueMode.loop
    elif mode == "queue":
        player.queue.mode = wavelink.QueueMode.loop_all
    else:
        player.queue.mode = wavelink.QueueMode.normal


def queue_items(player: wavelink.Player) -> list[wavelink.Playable]:
    return list(player.queue)


async def clear_player(player: wavelink.Player) -> None:
    set_loop_mode(player, "none")
    player.queue.clear()
    player.queue.history.clear()
    clear_saved_guild_state(player.guild.id)
    if player.playing or player.paused:
        await player.skip(force=True)


async def disconnect_player(player: wavelink.Player) -> None:
    state = get_guild_state(player.guild.id)
    if state.idle_task:
        state.idle_task.cancel()
        state.idle_task = None
    await clear_player(player)
    await player.disconnect()
