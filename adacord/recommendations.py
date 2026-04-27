import asyncio
import logging
import time
from dataclasses import dataclass

import wavelink

from adacord.player import queue_items
from adacord.sources import SONG_SEARCH_LIMIT, score_song_candidate, search_lavalink
from adacord.utils import AVOID_TERMS, normalized_words, text_contains_term, track_display_title

logger = logging.getLogger(__name__)

RECOMMENDATION_CACHE_TTL = 10 * 60
RECOMMENDATION_COUNT = 10
RECOMMENDATION_REQUESTER = "Adacord suggestions"
RECOMMENDATION_POOL_LIMIT = 25
MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD = 20
SPOTIFY_SEED_LIMIT = 4
SPOTIFY_SEED_BACKOFF_SECONDS = 15 * 60
spotify_seed_disabled_until = 0.0
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
    "song",
    "video",
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


recommendation_cache: dict[tuple[int, str], RecommendationCacheEntry] = {}
recommendation_load_locks: dict[tuple[int, str], asyncio.Lock] = {}


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


def recommendation_value(track: object) -> str:
    uri = track_uri(track)
    if uri and len(uri) <= 100:
        return uri

    query = track_query_text(track)
    value = f"ytmsearch:{query}" if query else track_identifier(track)
    if value and len(value) <= 100:
        return value

    return format_recommendation_label(track)[:100]


def track_isrc(track: object | None) -> str | None:
    isrc = getattr(track, "isrc", None)
    if isrc:
        return str(isrc)
    raw_data = getattr(track, "raw_data", None)
    if isinstance(raw_data, dict):
        raw_isrc = raw_data.get("info", {}).get("isrc")
        if raw_isrc:
            return str(raw_isrc)
    return None


def identity_for_track(track: object) -> str:
    return track_identifier(track) or track_uri(track) or track_query_text(track) or track_display_title(track)


def cache_key_for_player(player: wavelink.Player) -> tuple[int, str] | None:
    current = player.current
    if not current:
        return None
    parts = [identity_for_track(current)]
    parts.extend(identity_for_track(track) for track in queue_items(player)[:3])
    identity = "|".join(parts)
    return player.guild.id, identity


def normalized_track_key(track: object) -> str:
    title = track_title(track).casefold()
    author = track_author(track).casefold()
    identifier = track_identifier(track)
    if identifier:
        return f"id:{identifier.casefold()}"
    return f"text:{author}:{title}"


def track_query_text(track: object | None) -> str:
    title = track_title(track)
    author = track_author(track)
    return f"{author} - {title}" if author else title


def spotify_track_id(track: object | None) -> str | None:
    source = track_source(track)
    identifier = track_identifier(track)
    uri = track_uri(track) or ""
    if source == "spotify" and identifier:
        return identifier
    marker = "open.spotify.com/track/"
    if marker in uri:
        return uri.split(marker, 1)[1].split("?", 1)[0].split("/", 1)[0]
    return None


def recommendation_queries(player: wavelink.Player) -> list[str]:
    current = player.current
    if not current:
        return []

    queries: list[str] = []
    spotify_id = spotify_track_id(current)
    isrc = track_isrc(current)
    title = track_title(current)
    author = track_author(current)
    current_query = track_query_text(current)

    if spotify_id:
        queries.append(f"sprec:mix:track:{spotify_id}")
    if isrc:
        queries.append(f"sprec:mix:isrc:{isrc}")
    queued = queue_items(player)[:3]
    queue_terms = [track_query_text(track) for track in queued if track_query_text(track)]
    queue_authors = [track_author(track) for track in queued if track_author(track)]

    unique_authors = list(dict.fromkeys([name for name in [author, *queue_authors] if name]))
    if len(unique_authors) > 1:
        queries.append(f"ytmsearch:{' '.join(unique_authors)} similar artists songs")
        queries.append(f"ytmsearch:{' '.join(unique_authors)} indie alternative mix")
    if author:
        queries.append(f"ytmsearch:{author} similar artists songs")
        queries.append(f"ytmsearch:{author} radio")
    for queued_author in queue_authors:
        queries.append(f"ytmsearch:{queued_author} radio")
    for queued_query in queue_terms:
        queries.append(f"ytmsearch:{queued_query} radio")
    if current_query and queue_terms:
        queries.append(f"ytmsearch:{current_query} {' '.join(queue_terms)} mix")
    if author and queue_authors:
        queries.append(f"ytmsearch:{' '.join([author, *queue_authors])} mix")
    if current_query:
        queries.append(f"ytmsearch:{current_query} radio")
        queries.append(f"ytmsearch:{current_query} similar songs")

    if title and author:
        queries.append(f"ytmsearch:{author} songs like {title}")

    return list(dict.fromkeys(queries))


def format_recommendation_label(track: wavelink.Playable) -> str:
    title = track_title(track) or track_display_title(track)
    author = track_author(track)
    label = f"{author} - {title}" if author and author.casefold() not in title.casefold() else title
    return label[:100]


def format_recommendation_description(track: wavelink.Playable) -> str | None:
    source = track_source(track)
    if source:
        return source[:100]
    return None


async def resolve_recommendation_value(value: str, requester: str) -> wavelink.Playable | None:
    tracks = await search_lavalink(value, requester, limit=1)
    return tracks[0] if tracks else None


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


def recommendation_score(track: wavelink.Playable) -> int:
    score = score_song_candidate(track, track_query_text(track))
    if has_variant_term(track):
        score -= 40
    return score


def artist_key(track: object) -> str:
    author = track_author(track).casefold()
    for suffix in (" - topic", "vevo"):
        if author.endswith(suffix):
            author = author[: -len(suffix)].strip()
    return author


def diversify_recommendations(tracks: list[wavelink.Playable], player: wavelink.Player, count: int) -> list[wavelink.Playable]:
    current_artist = artist_key(player.current) if player.current else ""
    selected: list[wavelink.Playable] = []
    artist_counts: dict[str, int] = {}

    for track in tracks:
        artist = artist_key(track)
        limit = 1 if artist and artist == current_artist else 2
        if artist and artist_counts.get(artist, 0) >= limit:
            continue
        selected.append(track)
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        if len(selected) >= count:
            return selected

    for track in tracks:
        if track not in selected:
            artist = artist_key(track)
            if artist and artist == current_artist and artist_counts.get(artist, 0) >= 1:
                continue
            selected.append(track)
        if len(selected) >= count:
            break
    return selected


def rank_recommendations(
    candidates: list[wavelink.Playable],
    player: wavelink.Player,
    count: int,
) -> tuple[Recommendation, ...]:
    current = player.current
    existing_tracks = queue_items(player)
    if current:
        existing_tracks = [current, *existing_tracks]

    existing = {normalized_track_key(track) for track in existing_tracks}
    if current:
        existing.add(normalized_track_key(current))

    unique: list[wavelink.Playable] = []
    seen = set(existing)
    for track in candidates:
        key = normalized_track_key(track)
        if key in seen:
            continue
        if any(is_same_song_variant(track, existing_track) for existing_track in existing_tracks):
            continue
        seen.add(key)
        unique.append(track)

    ranked = sorted(
        enumerate(unique),
        key=lambda item: (recommendation_score(item[1]), -item[0]),
        reverse=True,
    )
    diversified = diversify_recommendations([track for _, track in ranked], player, count)
    return tuple(
        Recommendation(
            track=track,
            label=format_recommendation_label(track),
            description=format_recommendation_description(track),
        )
        for track in diversified
    )


async def spotify_seed_tracks(player: wavelink.Player) -> list[wavelink.Playable]:
    global spotify_seed_disabled_until

    now = time.monotonic()
    if spotify_seed_disabled_until > now:
        return []

    seeds = [player.current, *queue_items(player)[: SPOTIFY_SEED_LIMIT - 1]]
    queries = [track_query_text(track) for track in seeds if track_query_text(track)]
    found: list[wavelink.Playable] = []
    for query in dict.fromkeys(queries):
        try:
            found.extend(await search_lavalink(f"spsearch:{query}", RECOMMENDATION_REQUESTER, limit=1))
        except Exception as exc:
            spotify_seed_disabled_until = time.monotonic() + SPOTIFY_SEED_BACKOFF_SECONDS
            logger.debug("Spotify seed query %r failed: %s", query, exc)
            break
    return found


def prune_recommendation_cache(now: float | None = None, guild_id: int | None = None) -> None:
    now = time.monotonic() if now is None else now

    for key, entry in list(recommendation_cache.items()):
        if guild_id is not None and key[0] != guild_id:
            continue
        if entry.expires_at <= now:
            del recommendation_cache[key]

    guild_ids = {guild_id} if guild_id is not None else {key[0] for key in recommendation_cache}
    for current_guild_id in guild_ids:
        keys = [key for key in recommendation_cache if key[0] == current_guild_id]
        overflow = len(keys) - MAX_RECOMMENDATION_CACHE_ENTRIES_PER_GUILD
        for key in keys[: max(0, overflow)]:
            del recommendation_cache[key]


async def load_recommendation_candidates(player: wavelink.Player) -> list[wavelink.Playable]:
    candidates: list[wavelink.Playable] = []
    for seed in await spotify_seed_tracks(player):
        seed_id = spotify_track_id(seed)
        seed_isrc = track_isrc(seed)
        seed_queries = []
        if seed_id:
            seed_queries.append(f"sprec:mix:track:{seed_id}")
        if seed_isrc:
            seed_queries.append(f"sprec:mix:isrc:{seed_isrc}")
        for query in seed_queries:
            try:
                candidates.extend(await search_lavalink(query, RECOMMENDATION_REQUESTER, limit=RECOMMENDATION_POOL_LIMIT))
            except Exception as exc:
                logger.debug("Spotify recommendation query %r failed: %s", query, exc)

    for query in recommendation_queries(player):
        try:
            limit = RECOMMENDATION_POOL_LIMIT if query.startswith("sprec:") else SONG_SEARCH_LIMIT
            candidates.extend(await search_lavalink(query, RECOMMENDATION_REQUESTER, limit=limit))
        except Exception as exc:
            logger.debug("Recommendation query %r failed: %s", query, exc)
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
    try:
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
    finally:
        if not lock.locked():
            recommendation_load_locks.pop(key, None)


def clear_recommendation_cache() -> None:
    recommendation_cache.clear()
    recommendation_load_locks.clear()


def clear_guild_recommendation_cache(guild_id: int) -> None:
    for key in list(recommendation_cache):
        if key[0] == guild_id:
            del recommendation_cache[key]
    for key in list(recommendation_load_locks):
        if key[0] == guild_id:
            del recommendation_load_locks[key]
