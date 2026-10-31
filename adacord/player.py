import asyncio
import logging

import discord
import wavelink

from adacord.persistence import clear_guild_state as clear_saved_guild_state
from adacord.persistence import save_player_state
from adacord.config import (
    LAVALINK_PASSWORD,
    LAVALINK_URI,
    LAVALINK_VOICE_READY_INTERVAL,
    LAVALINK_VOICE_READY_TIMEOUT,
    default_volume,
    player_idle_timeout,
    voice_connect_timeout,
)
from adacord.state import get_guild_state
from adacord.utils import track_log_label

logger = logging.getLogger(__name__)


VOICE_PERMISSION_LABELS = {
    "view_channel": "View Channel",
    "connect": "Connect",
    "speak": "Speak",
}


class MissingVoicePermissions(RuntimeError):
    def __init__(self, channel: discord.VoiceChannel | discord.StageChannel, missing: list[str]):
        self.channel = channel
        self.missing = missing
        super().__init__(voice_permission_message(channel, missing))


def format_permission_list(permissions: list[str]) -> str:
    labels = [VOICE_PERMISSION_LABELS.get(permission, permission) for permission in permissions]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def voice_permission_message(channel: discord.VoiceChannel | discord.StageChannel, missing: list[str]) -> str:
    channel_name = getattr(channel, "name", None) or str(channel)
    permission_word = "permission" if len(missing) == 1 else "permissions"
    return f"I need {format_permission_list(missing)} {permission_word} in {channel_name}."


def validate_voice_channel_permissions(
    guild: discord.Guild,
    target_channel: discord.VoiceChannel | discord.StageChannel,
) -> None:
    bot_member = getattr(guild, "me", None) or getattr(getattr(target_channel, "guild", None), "me", None)
    if not bot_member or not hasattr(target_channel, "permissions_for"):
        return

    permissions = target_channel.permissions_for(bot_member)
    missing = [
        permission
        for permission in ("view_channel", "connect", "speak")
        if not getattr(permissions, permission, False)
    ]
    if missing:
        raise MissingVoicePermissions(target_channel, missing)


async def connect_lavalink(bot: discord.Client) -> None:
    node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
    # Wavelink keeps retrying an unreachable node and only logs a rejected password, so check the result.
    nodes = await wavelink.Pool.connect(client=bot, nodes=[node], cache_capacity=100)
    if node.identifier not in nodes:
        raise RuntimeError(f"Could not connect to Lavalink at {LAVALINK_URI}; see the wavelink errors above.")
    logger.info("Connected to Lavalink at %s", LAVALINK_URI)


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


async def connect_voice_with_retry(
    guild: discord.Guild,
    target_channel: discord.VoiceChannel | discord.StageChannel,
) -> wavelink.Player:
    # Callers hold the guild connection lock so retries cannot race another join.
    for attempt in range(2):
        try:
            return await target_channel.connect(
                cls=wavelink.Player,
                self_deaf=True,
                reconnect=True,
                timeout=voice_connect_timeout(),
            )
        except (wavelink.ChannelTimeoutException, TimeoutError):
            await cleanup_voice_client(guild, "voice connection timed out")
            if attempt == 1:
                raise
            logger.warning("Retrying voice connection for guild %s after timeout", guild.id)
            await asyncio.sleep(1)


async def ensure_player(
    guild: discord.Guild,
    target_channel: discord.VoiceChannel | discord.StageChannel,
) -> wavelink.Player:
    validate_voice_channel_permissions(guild, target_channel)
    state = get_guild_state(guild.id)
    state.voice_channel_id = getattr(target_channel, "id", None)
    async with state.connect_lock:
        player = get_player(guild)
        if player and player.connected:
            logger.debug(
                "Reusing connected player for guild %s: channel=%s target=%s current=%s queue=%s playing=%s paused=%s",
                guild.id,
                getattr(getattr(player, "channel", None), "id", None),
                getattr(target_channel, "id", None),
                track_log_label(player.current),
                len(list(player.queue)),
                player.playing,
                player.paused,
            )
            if player.channel != target_channel:
                logger.info(
                    "Moving connected player for guild %s: from=%s to=%s",
                    guild.id,
                    getattr(getattr(player, "channel", None), "id", None),
                    getattr(target_channel, "id", None),
                )
                await player.move_to(target_channel)
            await wait_for_lavalink_voice(player)
            return player

        if guild.voice_client:
            await cleanup_voice_client(guild, "stale voice client before reconnect")

        try:
            logger.info(
                "Connecting player for guild %s to voice channel %s",
                guild.id,
                getattr(target_channel, "id", None),
            )
            player = await connect_voice_with_retry(guild, target_channel)
            player.inactive_timeout = player_idle_timeout()
            player.inactive_channel_tokens = 1
            await wait_for_lavalink_voice(player)
            await player.set_volume(default_volume())
            logger.info(
                "Connected player for guild %s: channel=%s volume=%s",
                guild.id,
                getattr(target_channel, "id", None),
                player.volume,
            )
            return player
        except (Exception, asyncio.CancelledError):
            await cleanup_voice_client(guild, "voice connection failed")
            raise


async def reconnect_player_voice(player: wavelink.Player) -> wavelink.Player:
    current = player.current
    queued = list(player.queue)
    position = max(0, int(getattr(player, "position", 0) or 0))
    volume = player.volume if player.volume is not None else default_volume()
    target_channel = player.channel
    guild = player.guild
    if target_channel is None:
        raise RuntimeError("Cannot reconnect voice without a known voice channel.")

    validate_voice_channel_permissions(guild, target_channel)
    state = get_guild_state(guild.id)
    loop_mode = state.loop_mode
    async with state.connect_lock:
        state.voice_refresh_in_progress = True
        try:
            await save_player_state(player)
            logger.info(
                "Refreshing voice session for guild %s: channel=%s current=%s queue=%s position=%s volume=%s",
                guild.id,
                getattr(target_channel, "id", None),
                track_log_label(current),
                len(queued),
                position,
                volume,
            )
            await cleanup_voice_client(guild, "refreshing stale voice session")
            new_player = await connect_voice_with_retry(guild, target_channel)
            new_player.inactive_timeout = player_idle_timeout()
            new_player.inactive_channel_tokens = 1
            await wait_for_lavalink_voice(new_player)
            await new_player.set_volume(volume)
            new_player.queue.put(queued)
            set_loop_mode(new_player, loop_mode)
            if current:
                logger.info(
                    "Replaying current track after voice refresh for guild %s: start=%s track=%s",
                    guild.id,
                    position,
                    track_log_label(current),
                )
                await new_player.play(current, start=position, volume=volume, add_history=False, paused=False)
            logger.info(
                "Refreshed voice session for guild %s: connected=%s current=%s queue=%s",
                guild.id,
                new_player.connected,
                track_log_label(new_player.current),
                len(list(new_player.queue)),
            )
            return new_player
        except (Exception, asyncio.CancelledError):
            await cleanup_voice_client(guild, "voice refresh failed")
            raise
        finally:
            state.voice_refresh_in_progress = False


async def wait_for_lavalink_voice(player: wavelink.Player) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = loop.time() + LAVALINK_VOICE_READY_TIMEOUT

    while True:
        try:
            payload = await player.node.fetch_player_info(player.guild.id)
        except Exception as exc:
            logger.debug("Could not fetch Lavalink player info while waiting for voice: %s", exc)
            payload = None

        if payload and payload.state.connected:
            logger.debug(
                "Lavalink voice ready for guild %s after %.2fs: ping=%s",
                player.guild.id,
                loop.time() - started,
                payload.state.ping,
            )
            return

        if loop.time() >= deadline:
            state = "missing"
            if payload:
                state = f"connected={payload.state.connected}, ping={payload.state.ping}"
            logger.warning(
                "Timed out waiting for Lavalink voice in guild %s after %.2fs: %s",
                player.guild.id,
                loop.time() - started,
                state,
            )
            raise RuntimeError(f"Lavalink did not finish connecting to voice ({state}).")

        await asyncio.sleep(LAVALINK_VOICE_READY_INTERVAL)


async def add_tracks(
    player: wavelink.Player,
    tracks: list[wavelink.Playable],
) -> None:
    if not tracks:
        return

    was_idle = not player.current and player.queue.is_empty
    logger.debug(
        "Adding %s track(s) to guild %s queue: was_idle=%s current=%s queue_before=%s",
        len(tracks),
        player.guild.id,
        was_idle,
        track_log_label(player.current),
        len(list(player.queue)),
    )
    player.queue.put(tracks)
    if not player.current:
        await play_next(player)
    logger.debug(
        "Added tracks to guild %s: current=%s queue_after=%s playing=%s paused=%s",
        player.guild.id,
        track_log_label(player.current),
        len(list(player.queue)),
        player.playing,
        player.paused,
    )


def has_next_track(player: wavelink.Player) -> bool:
    # Wavelink's Queue.get() replays or wraps in loop modes even when no tracks are queued.
    queue = player.queue
    if queue.mode is wavelink.QueueMode.loop and queue.loaded:
        return True
    if queue.mode is wavelink.QueueMode.loop_all and queue.history:
        return True
    return not queue.is_empty


async def play_next(player: wavelink.Player) -> wavelink.Playable | None:
    if not has_next_track(player):
        logger.debug("play_next skipped for guild %s: queue is empty", player.guild.id)
        return None

    logger.debug(
        "Starting next track for guild %s: current=%s queue_before=%s connected=%s",
        player.guild.id,
        track_log_label(player.current),
        len(list(player.queue)),
        player.connected,
    )
    await wait_for_lavalink_voice(player)
    track = player.queue.get()
    volume = player.volume if player.volume is not None else default_volume()
    logger.debug(
        "Calling Lavalink play for guild %s: volume=%s track=%s",
        player.guild.id,
        volume,
        track_log_label(track),
    )
    extras = dict(track.extras)
    start = extras.pop("requested_start_ms", 0)
    await player.play(track, volume=volume, start=start, paused=False)
    get_guild_state(player.guild.id).paused_at = None
    track.extras = extras
    logger.debug(
        "Lavalink play call completed for guild %s: current=%s queue_after=%s playing=%s paused=%s",
        player.guild.id,
        track_log_label(player.current),
        len(list(player.queue)),
        player.playing,
        player.paused,
    )
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
    state = get_guild_state(player.guild.id)
    state.paused_at = None
    set_loop_mode(player, "none")
    player.queue.clear()
    player.queue.history.clear()
    await clear_saved_guild_state(player.guild.id)
    if player.playing or player.paused:
        await player.skip(force=True)
    if player.paused:
        await player.pause(False)


async def disconnect_player(player: wavelink.Player) -> None:
    await clear_player(player)
    await player.disconnect()
