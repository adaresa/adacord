import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable

import discord
import wavelink

from source_utils import format_duration, is_url, spotify_playlist_id

logger = logging.getLogger(__name__)


@dataclass
class GuildState:
    text_channel: discord.abc.Messageable | None = None
    display_message: discord.Message | None = None
    display_channel: discord.abc.Messageable | None = None
    loop_mode: str = "none"
    previous_volume: int = 100
    connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    idle_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class LoadSummary:
    title: str
    added: int
    source: str


guild_states: dict[int, GuildState] = {}


def get_guild_state(guild_id: int) -> GuildState:
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildState()
    return guild_states[guild_id]


async def connect_lavalink(bot: discord.Client) -> None:
    uri = os.getenv("LAVALINK_URI", "http://lavalink:2333")
    password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
    retries = int(os.getenv("LAVALINK_CONNECT_RETRIES", "30"))
    delay = float(os.getenv("LAVALINK_CONNECT_DELAY", "2"))

    node = wavelink.Node(uri=uri, password=password)
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


async def ensure_player(
    guild: discord.Guild,
    target_channel: discord.VoiceChannel | discord.StageChannel,
) -> wavelink.Player:
    state = get_guild_state(guild.id)
    async with state.connect_lock:
        player = get_player(guild)
        if player and player.connected:
            if player.channel != target_channel:
                await player.move_to(target_channel)
            await wait_for_lavalink_voice(player)
            return player

        player = await target_channel.connect(
            cls=wavelink.Player,
            self_deaf=True,
            reconnect=True,
            timeout=15,
        )
        player.inactive_timeout = int(os.getenv("PLAYER_IDLE_TIMEOUT", "30"))
        player.inactive_channel_tokens = 1
        await wait_for_lavalink_voice(player)
        return player


async def wait_for_lavalink_voice(player: wavelink.Player) -> None:
    timeout = float(os.getenv("LAVALINK_VOICE_READY_TIMEOUT", "10"))
    interval = float(os.getenv("LAVALINK_VOICE_READY_INTERVAL", "0.25"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

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

        await asyncio.sleep(interval)


def apply_requester(tracks: Iterable[wavelink.Playable], requester: str, query: str) -> None:
    for track in tracks:
        track.extras = {"requester": requester, "query": query}


async def search_youtube(query: str, requester: str) -> list[wavelink.Playable]:
    source = None if is_url(query) else wavelink.TrackSource.YouTubeMusic
    found = await wavelink.Playable.search(query, source=source)

    if isinstance(found, wavelink.Playlist):
        tracks = list(found.tracks)
    else:
        tracks = list(found[:1])

    apply_requester(tracks, requester, query)
    return tracks


async def spotify_playlist_queries(playlist_id: str) -> list[str]:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Spotify playlist fallback needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
        )

    def fetch() -> list[str]:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials

        spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
        )

        queries: list[str] = []
        offset = 0
        while True:
            page = spotify.playlist_items(
                playlist_id,
                fields="items(is_local,track(name,artists(name))),next",
                additional_types=("track",),
                limit=100,
                offset=offset,
            )
            for item in page.get("items", []):
                if item.get("is_local"):
                    continue
                track = item.get("track") or {}
                title = track.get("name")
                artists = ", ".join(
                    artist.get("name", "")
                    for artist in track.get("artists", [])
                    if artist.get("name")
                )
                if title:
                    queries.append(f"{artists} - {title}" if artists else title)

            if not page.get("next"):
                return queries
            offset += 100

    return await asyncio.to_thread(fetch)


async def load_tracks(query: str, requester: str) -> tuple[list[wavelink.Playable], LoadSummary]:
    query = query.strip()
    playlist_id = spotify_playlist_id(query)

    if playlist_id:
        try:
            found = await wavelink.Playable.search(query)
            if isinstance(found, wavelink.Playlist) and found.tracks:
                tracks = list(found.tracks)
                apply_requester(tracks, requester, query)
                return tracks, LoadSummary(found.name or "Spotify playlist", len(tracks), "lavasrc")
        except Exception as exc:
            logger.info("LavaSrc Spotify load failed; trying Spotipy fallback: %s", exc)

        queries = await spotify_playlist_queries(playlist_id)
        tracks: list[wavelink.Playable] = []
        for track_query in queries:
            try:
                matches = await search_youtube(track_query, requester)
            except Exception as exc:
                logger.warning("Could not resolve Spotify track %r: %s", track_query, exc)
                continue
            tracks.extend(matches)

        return tracks, LoadSummary("Spotify playlist", len(tracks), "spotify-youtube")

    tracks = await search_youtube(query, requester)
    title = tracks[0].title if tracks else query
    return tracks, LoadSummary(title, len(tracks), "youtube")


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
    await player.play(track, volume=player.volume or 100)
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
    if player.playing or player.paused:
        await player.skip(force=True)


async def disconnect_player(player: wavelink.Player) -> None:
    state = get_guild_state(player.guild.id)
    if state.idle_task:
        state.idle_task.cancel()
        state.idle_task = None
    await clear_player(player)
    await player.disconnect()
