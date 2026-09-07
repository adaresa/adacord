import asyncio
import html
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

import wavelink

from adacord.utils import (
    AVOID_TERMS,
    display_track_author,
    display_track_title,
    is_url,
    spotify_playlist_id,
    text_contains_term,
    youtube_watch_url_without_playlist,
)

logger = logging.getLogger(__name__)


SONG_SEARCH_LIMIT = 8
SPOTIFY_PUBLIC_SEARCH_CONCURRENCY = 16
SONG_MIN_LENGTH_MS = 60_000
SONG_IDEAL_MAX_LENGTH_MS = 7 * 60_000
SONG_SOFT_MAX_LENGTH_MS = 10 * 60_000
SPOTIFY_PUBLIC_FETCH_TIMEOUT = 10


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
            "display_title": display_track_title(track, query if is_url(query) else song_search_query(query)),
        }


def track_text(track: wavelink.Playable) -> str:
    title = getattr(track, "title", "") or ""
    author = getattr(track, "author", "") or ""
    return f"{title} {author}".lower()


def song_search_text(value: str) -> str:
    # Match decorated Unicode text and hyphenated variants such as "sped-up".
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[\W_]+", " ", value).strip()


SEARCH_DECORATIONS = {"official", "audio", "lyrics", "lyric", "video", "hd", "hq", "4k"}
VARIANT_ALIASES = {
    "unplugged": "acoustic",
    "spedup": "sped up",
    "speed up": "sped up",
    "rem x": "remix",
    "12 mix": "extended",
}


def song_variants(text: str) -> set[str]:
    for alias, canonical in VARIANT_ALIASES.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", canonical, text)
    return {term for term in AVOID_TERMS if text_contains_term(text, term)}


def song_search_query(query: str) -> str:
    """Do not send negated versions as positive search terms to the provider."""
    terms = sorted(AVOID_TERMS | VARIANT_ALIASES.keys(), key=len, reverse=True)
    versions = "|".join(r"[\s-]+".join(re.escape(word) for word in term.split()) for term in terms)
    query = re.sub(rf"\b(?:no|not|without)\s+(?:{versions})\b", " ", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", query).strip()


def score_song_candidate(
    track: wavelink.Playable, query: str, *, explicit_artist: bool = False,
) -> int:
    title = song_search_text(getattr(track, "title", "") or "")
    text = song_search_text(track_text(track))
    query = song_search_text(song_search_query(query))
    requested = song_variants(query)
    # Remove an artist prefix before looking for versions: "Live - Lightning
    # Crashes" is a band credit, not a live performance.
    artist = song_search_text(display_track_author(track))
    version_title = title
    artist_requests = set()
    if artist and title.startswith(artist + " "):
        version_title = title[len(artist):].strip()
        artist_requests = requested & song_variants(artist)
    variants = song_variants(version_title)
    ignored = SEARCH_DECORATIONS | {word for term in requested for word in term.split()}
    ignored.update(word for alias, term in VARIANT_ALIASES.items() if term in requested for word in alias.split())
    query_words = set(query.split()) - ignored
    track_words = set(text.split())
    score = 0
    if query_words:
        score += int(100 * len(query_words & track_words) / len(query_words))
        # A complete song title can be accompanied by movie/album context in
        # the request that is absent from the upload's metadata.
        core_title = re.split(r"[\(\[|]", getattr(track, "title", "") or "", maxsplit=1)[0]
        core_words = set(song_search_text(core_title).split()) - SEARCH_DECORATIONS
        if not explicit_artist and core_words and core_words <= query_words:
            score = 100

    # Provider order carries relevance/popularity information we do not have.
    # Generic upload labels (or words in an artist name) must not override it.

    length = getattr(track, "length", None)
    if length and not requested & {"extended", "hour", "hours", "loop", "looped"}:
        if SONG_MIN_LENGTH_MS <= length <= SONG_IDEAL_MAX_LENGTH_MS:
            score += 25
        elif length <= SONG_SOFT_MAX_LENGTH_MS:
            score += 5
        else:
            score -= min(50, (length - SONG_SOFT_MAX_LENGTH_MS) // 60_000 * 5 + 15)

    allowed = set(requested)
    if requested & {"slowed", "reverb"}:
        allowed.update({"slowed", "reverb"})
    if requested & {"extended", "remix", "nightcore"}:
        allowed.add("mix")
    if "acoustic" in requested:
        allowed.add("live")
    score -= 40 * len(variants - allowed)
    score -= 25 * len(requested - variants - artist_requests)
    if getattr(track, "is_stream", False):
        score -= 100
    return score


def choose_best_song_candidate(
    tracks: Iterable[wavelink.Playable],
    query: str,
) -> wavelink.Playable | None:
    candidates = list(tracks)
    if not candidates:
        return None

    query_words = set(song_search_text(query).split())
    explicit_artist = any(
        artist_words and artist_words <= query_words
        for track in candidates
        for artist_words in [set(song_search_text(display_track_author(track)).split())]
    )
    # Preserve provider relevance against small metadata differences. Rerank
    # only when a later result is meaningfully better, e.g. an original rather
    # than a cover, or the version explicitly requested by the user.
    return max(
        enumerate(candidates),
        key=lambda item: score_song_candidate(item[1], query, explicit_artist=explicit_artist) - 8 * item[0],
    )[1]


async def search_youtube(query: str, requester: str) -> list[wavelink.Playable]:
    source = None if is_url(query) else wavelink.TrackSource.YouTube
    provider_query = query if is_url(query) else song_search_query(query)
    try:
        found = await wavelink.Playable.search(provider_query, source=source)
    except Exception:
        fallback_query = youtube_watch_url_without_playlist(query)
        if not fallback_query:
            raise
        logger.info("Retrying YouTube watch URL without playlist parameters: %s", fallback_query)
        found = await wavelink.Playable.search(fallback_query, source=source)
        query = fallback_query

    if not is_url(query) and not isinstance(found, wavelink.Playlist):
        # Soundtrack searches can lead with a multilingual video compilation.
        # Ask the music catalogue for a normal recording in that case.
        multilingual = {"multi language", "multilingual"}
        first_variants = song_variants(song_search_text(found[0].title)) if found else set()
        unwanted_language = (first_variants - song_variants(song_search_text(provider_query))) & multilingual
        if not found or unwanted_language:
            try:
                music = await wavelink.Playable.search(provider_query, source=wavelink.TrackSource.YouTubeMusic)
            except Exception:
                if not found:
                    raise
                logger.info("Music catalogue fallback unavailable; keeping YouTube results")
                music = []
            if music:
                found = music

    if isinstance(found, wavelink.Playlist):
        tracks = list(found.tracks)
    elif is_url(query):
        tracks = list(found[:1])
    else:
        candidate = choose_best_song_candidate(list(found[:SONG_SEARCH_LIMIT]), query)
        tracks = [candidate] if candidate else []

    apply_requester(tracks, requester, query)
    return tracks


async def search_youtube_alternative(
    query: str,
    requester: str,
    *,
    exclude_uris: set[str],
) -> wavelink.Playable | None:
    """Return the best regular YouTube result not already rejected for playback."""
    found = await wavelink.Playable.search(song_search_query(query), source=wavelink.TrackSource.YouTube)
    if isinstance(found, wavelink.Playlist):
        candidates = list(found.tracks)
    else:
        candidates = list(found[:SONG_SEARCH_LIMIT])

    candidates = [track for track in candidates if str(getattr(track, "uri", "") or "") not in exclude_uris]
    replacement = choose_best_song_candidate(candidates, query)
    if replacement:
        apply_requester([replacement], requester, query)
    return replacement


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
    title = display_track_title(tracks[0], query if is_url(query) else song_search_query(query)) if tracks else query
    return tracks, LoadSummary(title, len(tracks), "youtube")
