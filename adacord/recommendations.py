"""Song suggestions and autoplay picks built on YouTube Music radio.

YouTube's "Start radio" mix for a video is built from what listeners play
together, so it finds related songs by other artists. Keyword searches such as
"<artist> radio" mostly match the artist's own uploads instead.
"""

import asyncio
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import wavelink

from adacord.player import queue_items
from adacord.sources import SONG_SOFT_MAX_LENGTH_MS, search_lavalink
from adacord.utils import (
    AVOID_TERMS,
    display_track_author,
    display_track_title,
    normalized_words,
    text_contains_term,
    track_display_title,
    track_log_label,
)

logger = logging.getLogger(__name__)

RECOMMENDATION_CACHE_TTL = 10 * 60
RECOMMENDATION_COUNT = 10
RECOMMENDATION_REQUESTER = "Adacord suggestions"
AUTOPLAY_REQUESTER = "Autoplay"
MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD = 20
RADIO_CACHE_TTL = 30 * 60
MAX_RADIO_CACHE_ENTRIES = 200
RADIO_LOAD_CONCURRENCY = 3
# Seeds after the first count for less, so suggestions follow the current song
# but still lean toward where the queue is heading.
QUEUE_SEED_COUNT = 2
QUEUE_SEED_WEIGHT = 2.0
RECENT_HISTORY_LIMIT = 30
AUTOPLAY_HISTORY_LIMIT = 50
VIBE_SEED_COUNT = 3
YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
RECOMMENDATION_VARIANT_TERMS = AVOID_TERMS | {
    "acapella",
    "a cappella",
    "acoustic",
    "edit",
    "orchestral",
    "sped up",
    "version",
}
TITLE_NOISE_WORDS = {
    "4k",
    "audio",
    "feat",
    "ft",
    "full",
    "hd",
    "hq",
    "lyrics",
    "lyric",
    "music",
    "official",
    "remastered",
    "song",
    "video",
    "visualizer",
}
TITLE_NOISE_WORDS |= {word for term in RECOMMENDATION_VARIANT_TERMS for word in normalized_words(term)}


@dataclass(frozen=True)
class Recommendation:
    track: wavelink.Playable
    label: str
    description: str | None = None


@dataclass
class RecommendationCacheEntry:
    expires_at: float
    suggestions: tuple[Recommendation, ...]


@dataclass
class RadioCacheEntry:
    expires_at: float
    tracks: tuple[wavelink.Playable, ...]


recommendation_cache: dict[tuple[int, str], RecommendationCacheEntry] = {}
recommendation_load_locks: dict[tuple[int, str], asyncio.Lock] = {}
radio_cache: dict[str, RadioCacheEntry] = {}
radio_load_locks: dict[str, asyncio.Lock] = {}


def track_title(track: object | None) -> str:
    return str(getattr(track, "title", "") or "").strip()


def track_author(track: object | None) -> str:
    return str(getattr(track, "author", "") or "").strip()


def track_source(track: object | None) -> str:
    source = getattr(track, "source", "") or ""
    raw_data = getattr(track, "raw_data", None)
    if isinstance(raw_data, dict):
        source = raw_data.get("info", {}).get("sourceName") or source
    return str(source).lower()


def track_identifier(track: object | None) -> str | None:
    identifier = getattr(track, "identifier", None)
    if identifier:
        return str(identifier)
    raw_data = getattr(track, "raw_data", None)
    if isinstance(raw_data, dict):
        raw_identifier = raw_data.get("info", {}).get("identifier")
        if raw_identifier:
            return str(raw_identifier)
    return None


def track_uri(track: object | None) -> str | None:
    uri = getattr(track, "uri", None)
    if uri:
        return str(uri)
    raw_data = getattr(track, "raw_data", None)
    if isinstance(raw_data, dict):
        raw_uri = raw_data.get("info", {}).get("uri")
        if raw_uri:
            return str(raw_uri)
    return None


def track_query_text(track: object | None) -> str:
    title = track_title(track)
    author = track_author(track)
    return f"{author} - {title}" if author else title


def recommendation_value(track: object) -> str:
    uri = track_uri(track)
    if uri and len(uri) <= 100:
        return uri

    query = track_query_text(track)
    value = f"ytsearch:{query}" if query else track_identifier(track)
    if value and len(value) <= 100:
        return value

    return format_recommendation_label(track)[:100]


def identity_for_track(track: object) -> str:
    return track_identifier(track) or track_uri(track) or track_query_text(track) or track_display_title(track)


def normalized_track_key(track: object) -> str:
    identifier = track_identifier(track)
    if identifier:
        return f"id:{identifier.casefold()}"
    return f"text:{track_author(track).casefold()}:{track_title(track).casefold()}"


def song_key(track: object) -> frozenset[str]:
    """Words naming a song, the same across official videos, audio uploads and lyric reuploads."""
    return frozenset(normalized_words(display_track_title(track)) - TITLE_NOISE_WORDS)


def title_signature_words(track: object) -> set[str]:
    words = normalized_words(track_title(track))
    author_words = normalized_words(track_author(track))
    return words - author_words - TITLE_NOISE_WORDS


def has_variant_term(track: object) -> bool:
    text = track_title(track).lower()
    return any(text_contains_term(text, term) for term in RECOMMENDATION_VARIANT_TERMS)


def is_same_song_variant(candidate: object, seed: object) -> bool:
    seed_words = title_signature_words(seed)
    candidate_words = title_signature_words(candidate)
    if not seed_words or not candidate_words:
        return False

    overlap = len(seed_words & candidate_words) / len(seed_words)
    if len(seed_words) == 1:
        return seed_words <= candidate_words and has_variant_term(candidate)
    return overlap >= 0.8


def artist_key(track: object) -> str:
    return display_track_author(track).casefold()


def youtube_video_id(track: object | None) -> str | None:
    if track is None:
        return None
    source = track_source(track)
    uri = track_uri(track) or ""
    identifier = track_identifier(track)
    if not identifier or not YOUTUBE_VIDEO_ID_RE.match(identifier):
        return None
    if source.startswith("youtube") or "youtube.com/" in uri or "youtu.be/" in uri:
        return identifier
    return None


def radio_url(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}&list=RDAMVM{video_id}"


def prune_radio_cache(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for key, entry in list(radio_cache.items()):
        if entry.expires_at <= now:
            del radio_cache[key]
            radio_load_locks.pop(key, None)
    overflow = len(radio_cache) - MAX_RADIO_CACHE_ENTRIES
    for key in list(radio_cache)[: max(0, overflow)]:
        del radio_cache[key]
        radio_load_locks.pop(key, None)


async def load_radio(video_id: str) -> tuple[wavelink.Playable, ...]:
    """Load (and cache) the YouTube Music radio for one video."""
    now = time.monotonic()
    cached = radio_cache.get(video_id)
    if cached and cached.expires_at > now:
        return cached.tracks

    lock = radio_load_locks.setdefault(video_id, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = radio_cache.get(video_id)
        if cached and cached.expires_at > now:
            return cached.tracks
        tracks = tuple(await search_lavalink(radio_url(video_id), RECOMMENDATION_REQUESTER))
        radio_cache[video_id] = RadioCacheEntry(now + RADIO_CACHE_TTL, tracks)
        prune_radio_cache(now)
        return tracks


async def seed_video_id(track: object) -> str | None:
    """Return a YouTube video to start a radio from, searching for non-YouTube tracks."""
    video_id = youtube_video_id(track)
    if video_id:
        return video_id
    query = display_track_title(track)
    if not query or query == "Unknown track":
        return None
    found = await search_lavalink(f"ytsearch:{query}", RECOMMENDATION_REQUESTER, limit=1)
    return youtube_video_id(found[0]) if found else None


async def radio_candidates(seeds: Sequence[tuple[object, float]]) -> list[wavelink.Playable]:
    """Merge the radios of weighted seeds, best first.

    A track's priority is its position in its seed's radio times the seed's
    weight, so a weight of 2 lets the main seed contribute about twice as many
    of the top picks.
    """
    semaphore = asyncio.Semaphore(RADIO_LOAD_CONCURRENCY)

    async def load(seed: object) -> tuple[wavelink.Playable, ...]:
        async with semaphore:
            try:
                video_id = await seed_video_id(seed)
                return await load_radio(video_id) if video_id else ()
            except Exception as exc:
                logger.debug("Radio for %s failed: %s", track_log_label(seed), exc)
                return ()

    radios = await asyncio.gather(*(load(seed) for seed, _ in seeds))
    ranked: list[tuple[float, int, int, wavelink.Playable]] = []
    for seed_index, ((_, weight), tracks) in enumerate(zip(seeds, radios)):
        for position, track in enumerate(tracks):
            ranked.append((position * weight, seed_index, position, track))
    ranked.sort(key=lambda item: item[:3])
    return [track for *_, track in ranked]


def recent_history(player: wavelink.Player, limit: int) -> list[wavelink.Playable]:
    history = getattr(player.queue, "history", None)
    if not history:
        return []
    try:
        return list(history)[-limit:]
    except TypeError:
        return []


def is_usable_candidate(track: object) -> bool:
    if getattr(track, "is_stream", False):
        return False
    length = getattr(track, "length", None)
    return not (length and length > SONG_SOFT_MAX_LENGTH_MS)


def filter_candidates(
    candidates: Iterable[wavelink.Playable],
    excluded_tracks: Sequence[object],
) -> list[wavelink.Playable]:
    """Drop played, queued and duplicate songs; move remixes and live versions to the back."""
    seen_ids = {normalized_track_key(track) for track in excluded_tracks}
    seen_songs = {song_key(track) for track in excluded_tracks}
    seen_songs.discard(frozenset())
    originals: list[wavelink.Playable] = []
    variants: list[wavelink.Playable] = []

    for track in candidates:
        key = normalized_track_key(track)
        song = song_key(track)
        if key in seen_ids or (song and song in seen_songs) or not is_usable_candidate(track):
            continue
        if any(is_same_song_variant(track, existing) for existing in excluded_tracks):
            continue
        seen_ids.add(key)
        if song:
            seen_songs.add(song)
        (variants if has_variant_term(track) else originals).append(track)

    return originals + variants


def diversify(tracks: list[wavelink.Playable], avoid_artists: Iterable[str], count: int) -> list[wavelink.Playable]:
    """Pick up to `count` tracks, one per artist at first and two once the variety runs out.

    Artists in `avoid_artists` (the one playing now, or just played) get one
    slot fewer, so they only appear when nothing else is left.
    """
    avoid = {artist for artist in avoid_artists if artist}
    selected: list[wavelink.Playable] = []
    selected_ids: set[int] = set()
    artist_counts: dict[str, int] = {}
    for cap in (1, 2):
        for track in tracks:
            if len(selected) >= count:
                return selected
            if id(track) in selected_ids:
                continue
            artist = artist_key(track)
            allowed = cap - 1 if artist in avoid else cap
            if artist and artist_counts.get(artist, 0) >= allowed:
                continue
            selected.append(track)
            selected_ids.add(id(track))
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
    return selected


def format_recommendation_label(track: object) -> str:
    return (display_track_title(track) or track_display_title(track))[:100]


def format_recommendation_description(track: object) -> str | None:
    author = display_track_author(track)
    if author and author.casefold() not in display_track_title(track).casefold():
        return f"Uploaded by {author}"[:100]
    return None


def as_recommendations(tracks: Iterable[wavelink.Playable]) -> tuple[Recommendation, ...]:
    return tuple(
        Recommendation(
            track=track,
            label=format_recommendation_label(track),
            description=format_recommendation_description(track),
        )
        for track in tracks
    )


def suggestion_seeds(player: wavelink.Player) -> list[tuple[object, float]]:
    if not player.current:
        return []
    seeds: list[tuple[object, float]] = [(player.current, 1.0)]
    seeds.extend((track, QUEUE_SEED_WEIGHT) for track in queue_items(player)[:QUEUE_SEED_COUNT])
    return seeds


def rank_recommendations(
    candidates: list[wavelink.Playable],
    player: wavelink.Player,
    count: int,
) -> tuple[Recommendation, ...]:
    existing: list[object] = [*recent_history(player, RECENT_HISTORY_LIMIT), *queue_items(player)]
    if player.current:
        existing.append(player.current)
    fresh = filter_candidates(candidates, existing)
    current_artist = artist_key(player.current) if player.current else ""
    return as_recommendations(diversify(fresh, [current_artist], count))


def cache_key_for_player(player: wavelink.Player) -> tuple[int, str] | None:
    current = player.current
    if not current:
        return None
    parts = [identity_for_track(current)]
    parts.extend(identity_for_track(track) for track in queue_items(player)[:3])
    return player.guild.id, "|".join(parts)


def prune_recommendation_cache(now: float | None = None, guild_id: int | None = None) -> None:
    now = time.monotonic() if now is None else now

    for key, entry in list(recommendation_cache.items()):
        if guild_id is not None and key[0] != guild_id:
            continue
        if entry.expires_at <= now:
            del recommendation_cache[key]
            recommendation_load_locks.pop(key, None)

    guild_ids = {guild_id} if guild_id is not None else {key[0] for key in recommendation_cache}
    for current_guild_id in guild_ids:
        keys = [key for key in recommendation_cache if key[0] == current_guild_id]
        overflow = len(keys) - MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD
        for key in keys[: max(0, overflow)]:
            del recommendation_cache[key]
            recommendation_load_locks.pop(key, None)


async def load_recommendation_candidates(player: wavelink.Player) -> list[wavelink.Playable]:
    started = time.perf_counter()
    candidates = await radio_candidates(suggestion_seeds(player))
    logger.debug(
        "Loaded %s radio candidate(s) for guild %s in %.2fs",
        len(candidates),
        player.guild.id,
        time.perf_counter() - started,
    )
    return candidates


async def recommendations_for_player(
    player: wavelink.Player | None,
    *,
    allow_refresh: bool = True,
) -> tuple[Recommendation, ...]:
    if not player or not player.current:
        return ()

    key = cache_key_for_player(player)
    if not key:
        return ()

    now = time.monotonic()
    cached = recommendation_cache.get(key)
    if cached and not allow_refresh:
        return cached.suggestions
    prune_recommendation_cache(now, player.guild.id)
    cached = recommendation_cache.get(key)
    if cached and (cached.expires_at > now or not allow_refresh):
        return cached.suggestions
    if not allow_refresh:
        return ()

    lock = recommendation_load_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = recommendation_cache.get(key)
        if cached and cached.expires_at > now:
            return cached.suggestions

        candidates = await load_recommendation_candidates(player)
        suggestions = rank_recommendations(candidates, player, RECOMMENDATION_COUNT)
        recommendation_cache[key] = RecommendationCacheEntry(now + RECOMMENDATION_CACHE_TTL, suggestions)
        prune_recommendation_cache(now, player.guild.id)
        return suggestions


async def resolve_recommendation_value(value: str, requester: str) -> wavelink.Playable | None:
    tracks = await search_lavalink(value, requester, limit=1)
    return tracks[0] if tracks else None


async def vibe_seed_tracks(vibe: str) -> list[wavelink.Playable]:
    found = await search_lavalink(f"ytsearch:{vibe}", AUTOPLAY_REQUESTER)
    return [track for track in found if youtube_video_id(track) and is_usable_candidate(track)][:VIBE_SEED_COUNT]


async def autoplay_track(
    player: wavelink.Player,
    last_track: object | None,
    *,
    vibe: str | None = None,
) -> wavelink.Playable | None:
    """Choose the next song once the queue runs out.

    The song that just ended is the main seed. An earlier song from the session
    (or the vibe's top search results) is mixed in so a long run does not drift
    away from what the room was listening to.
    """
    history = recent_history(player, AUTOPLAY_HISTORY_LIMIT)
    seeds: list[tuple[object, float]] = []
    if last_track is not None:
        seeds.append((last_track, 1.0))
    if vibe:
        try:
            seeds.extend((track, 1.5) for track in await vibe_seed_tracks(vibe))
        except Exception as exc:
            logger.info("Autoplay vibe search %r failed for guild %s: %s", vibe, player.guild.id, exc)
    elif len(history) >= 4:
        seeds.append((history[-4], 2.0))
    if not seeds:
        return None

    candidates = await radio_candidates(seeds)
    excluded: list[object] = [*history, *queue_items(player)]
    if last_track is not None:
        excluded.append(last_track)
    if player.current:
        excluded.append(player.current)
    recent_artists = [artist_key(track) for track in [*history[-2:], last_track] if track is not None]
    picks = diversify(filter_candidates(candidates, excluded), recent_artists, 1)
    return picks[0] if picks else None


def clear_recommendation_cache() -> None:
    recommendation_cache.clear()
    recommendation_load_locks.clear()
    radio_cache.clear()
    radio_load_locks.clear()


def clear_guild_recommendation_cache(guild_id: int) -> None:
    for key in list(recommendation_cache):
        if key[0] == guild_id:
            del recommendation_cache[key]
    for key in list(recommendation_load_locks):
        if key[0] == guild_id:
            del recommendation_load_locks[key]
