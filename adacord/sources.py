import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

import wavelink

from adacord.utils import (
    avoid_terms_for_query,
    display_track_title,
    is_url,
    normalized_words,
    requested_variant_terms,
    spotify_playlist_id,
    text_contains_term,
)

logger = logging.getLogger(__name__)


SONG_SEARCH_LIMIT = 8
SPOTIFY_PUBLIC_SEARCH_CONCURRENCY = 16
SONG_MIN_LENGTH_MS = 60_000
SONG_IDEAL_MAX_LENGTH_MS = 7 * 60_000
SONG_SOFT_MAX_LENGTH_MS = 10 * 60_000
SPOTIFY_PUBLIC_FETCH_TIMEOUT = 10

SONG_HINT_TERMS = {
    "audio",
    "lyrics",
    "lyric",
    "official audio",
    "official lyric",
    "provided to youtube",
    "topic",
}


@dataclass(frozen=True)
class LoadSummary:
    title: str
    added: int
    source: str


def apply_requester(tracks: Iterable[wavelink.Playable], requester: str, query: str) -> None:
    for track in tracks:
        track.extras = {
            "requester": requester,
            "query": query,
            "display_title": display_track_title(track, query),
        }


def track_text(track: wavelink.Playable) -> str:
    title = getattr(track, "title", "") or ""
    author = getattr(track, "author", "") or ""
    return f"{title} {author}".lower()


def track_source_text(track: wavelink.Playable) -> str:
    source = getattr(track, "source", "") or ""
    return str(source).lower()


def score_song_candidate(track: wavelink.Playable, query: str) -> int:
    text = track_text(track)
    score = 0

    query_words = normalized_words(query)
    track_words = normalized_words(text)
    if query_words:
        score += int(40 * len(query_words & track_words) / len(query_words))

    if any(text_contains_term(text, term) for term in SONG_HINT_TERMS):
        score += 20
    if track_source_text(track) in {"youtube music", "youtubemusic", "ytm"}:
        score += 10

    length = getattr(track, "length", None)
    if length:
        if SONG_MIN_LENGTH_MS <= length <= SONG_IDEAL_MAX_LENGTH_MS:
            score += 25
        elif length <= SONG_SOFT_MAX_LENGTH_MS:
            score += 5
        else:
            score -= min(50, (length - SONG_SOFT_MAX_LENGTH_MS) // 60_000 * 5 + 15)

    avoid_hits = [term for term in avoid_terms_for_query(query) if text_contains_term(text, term)]
    score -= 30 * len(avoid_hits)
    variant_hits = [term for term in requested_variant_terms(query) if text_contains_term(text, term)]
    score += 25 * len(variant_hits)
    return score


def choose_best_song_candidate(
    tracks: Iterable[wavelink.Playable],
    query: str,
) -> wavelink.Playable | None:
    candidates = list(tracks)
    if not candidates:
        return None

    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (score_song_candidate(item[1], query), -item[0]),
        reverse=True,
    )
    best = ranked[0][1]
    best_score = score_song_candidate(best, query)
    first_score = score_song_candidate(candidates[0], query)

    if best_score < -20 and first_score >= best_score - 10:
        return candidates[0]
    return best


async def search_youtube(query: str, requester: str) -> list[wavelink.Playable]:
    source = None if is_url(query) else wavelink.TrackSource.YouTubeMusic
    found = await wavelink.Playable.search(query, source=source)

    if isinstance(found, wavelink.Playlist):
        tracks = list(found.tracks)
    elif is_url(query):
        tracks = list(found[:1])
    else:
        candidate = choose_best_song_candidate(list(found[:SONG_SEARCH_LIMIT]), query)
        tracks = [candidate] if candidate else []

    apply_requester(tracks, requester, query)
    return tracks


async def search_lavalink(query: str, requester: str, *, limit: int | None = None) -> list[wavelink.Playable]:
    found = await wavelink.Playable.search(query, source=None)

    if isinstance(found, wavelink.Playlist):
        tracks = list(found.tracks)
    else:
        tracks = list(found)

    if limit is not None:
        tracks = tracks[:limit]

    apply_requester(tracks, requester, query)
    return tracks


def spotify_query_from_parts(title: str | None, artists: str | None) -> str | None:
    if not title:
        return None
    return f"{artists} - {title}" if artists else title


async def spotify_public_playlist_queries(playlist_id: str) -> list[str]:
    def fetch() -> list[str]:
        url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        with urlopen(request, timeout=SPOTIFY_PUBLIC_FETCH_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")

        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            body,
            flags=re.DOTALL,
        )
        if not match:
            return []

        data = json.loads(html.unescape(match.group(1)))
        entity = (
            data.get("props", {})
            .get("pageProps", {})
            .get("state", {})
            .get("data", {})
            .get("entity", {})
        )
        tracks = entity.get("trackList") or []

        queries: list[str] = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            if track.get("entityType") != "track":
                continue
            if track.get("isPlayable") is False:
                continue
            query = spotify_query_from_parts(track.get("title"), track.get("subtitle"))
            if query:
                queries.append(query)
        return queries

    try:
        return await asyncio.to_thread(fetch)
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        logger.info("Spotify public playlist metadata load failed: %s", exc)
        return []


async def spotify_playlist_queries(playlist_id: str) -> list[str]:
    queries = await spotify_public_playlist_queries(playlist_id)
    if not queries:
        raise RuntimeError("Could not read track metadata from this public Spotify playlist.")

    logger.info("Loaded %s Spotify tracks from public embed metadata", len(queries))
    return queries


async def resolve_spotify_public_tracks(queries: list[str], requester: str) -> list[wavelink.Playable]:
    logger.info("Resolving %s Spotify tracks with concurrency %s", len(queries), SPOTIFY_PUBLIC_SEARCH_CONCURRENCY)
    semaphore = asyncio.Semaphore(SPOTIFY_PUBLIC_SEARCH_CONCURRENCY)

    async def resolve(track_query: str) -> list[wavelink.Playable]:
        async with semaphore:
            try:
                return await search_youtube(track_query, requester)
            except Exception as exc:
                logger.warning("Could not resolve Spotify track %r: %s", track_query, exc)
                return []

    results = await asyncio.gather(*(resolve(track_query) for track_query in queries))
    return [track for matches in results for track in matches]


async def load_spotify_with_public_metadata(
    playlist_id: str,
    requester: str,
) -> tuple[list[wavelink.Playable], LoadSummary]:
    started = time.perf_counter()
    queries = await spotify_playlist_queries(playlist_id)
    tracks = await resolve_spotify_public_tracks(queries, requester)
    if not tracks:
        raise RuntimeError("Could not resolve tracks from public Spotify metadata.")

    logger.info(
        "Loaded %s/%s Spotify tracks with public metadata in %.2fs",
        len(tracks),
        len(queries),
        time.perf_counter() - started,
    )
    return tracks, LoadSummary("Spotify playlist", len(tracks), "spotify-public")


async def load_tracks(query: str, requester: str) -> tuple[list[wavelink.Playable], LoadSummary]:
    query = query.strip()
    playlist_id = spotify_playlist_id(query)

    if playlist_id:
        try:
            return await load_spotify_with_public_metadata(playlist_id, requester)
        except Exception as exc:
            logger.info("Spotify public playlist metadata load failed: %s", exc)

        raise RuntimeError("Could not load that Spotify playlist.")

    tracks = await search_youtube(query, requester)
    title = display_track_title(tracks[0], query) if tracks else query
    return tracks, LoadSummary(title, len(tracks), "youtube")
