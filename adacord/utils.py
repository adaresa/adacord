import re
from collections.abc import Mapping
from urllib.parse import urlparse

SPOTIFY_PLAYLIST_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]+)"
)

AVOID_TERMS = {
    "extended",
    "hour",
    "hours",
    "loop",
    "looped",
    "mix",
    "compilation",
    "live",
    "concert",
    "reaction",
    "tutorial",
    "karaoke",
    "instrumental",
    "bass boosted",
    "nightcore",
    "slowed",
    "reverb",
    "remix",
}


def is_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def spotify_playlist_id(value: str) -> str | None:
    match = SPOTIFY_PLAYLIST_RE.match(value.strip())
    return match.group(1) if match else None


def format_duration(milliseconds: int | None) -> str:
    if not milliseconds or milliseconds <= 0:
        return ""

    total_seconds = milliseconds // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def normalized_words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def text_contains_term(text: str, term: str) -> bool:
    words = r"\s+".join(re.escape(word) for word in term.split())
    return bool(re.search(rf"\b{words}\b", text))


def avoid_terms_for_query(query: str) -> set[str]:
    query_text = query.lower()
    return {term for term in AVOID_TERMS if not text_contains_term(query_text, term)}


def requested_variant_terms(query: str) -> set[str]:
    return AVOID_TERMS - avoid_terms_for_query(query)


def requested_display_variants(query: str) -> list[str]:
    query_text = query.lower()
    return sorted(term for term in AVOID_TERMS if text_contains_term(query_text, term))


GENERIC_TITLE_DECORATION_RE = re.compile(
    r"\s*[\(\[]\s*"
    r"(?:official\s+)?(?:lyrics?|lyric\s+video|audio|music\s+video|visuali[sz]er)"
    r"\s*[\)\]]\s*",
    flags=re.IGNORECASE,
)
TRAILING_GENERIC_TITLE_RE = re.compile(
    r"\s+(?:official\s+)?(?:lyrics?|lyric\s+video|audio)\s*$",
    flags=re.IGNORECASE,
)
ARTIST_TITLE_SEPARATOR_RE = re.compile(r"\s+-\s+")


def display_track_author(track: object) -> str:
    author = str(getattr(track, "author", "") or "").strip()
    for suffix in (" - Topic", "VEVO"):
        if author.endswith(suffix):
            author = author[: -len(suffix)].strip()
    return author


def clean_track_title(title: str) -> str:
    title = str(title or "Unknown track").strip()
    if "|" in title:
        left, right = title.split("|", 1)
        if re.search(r"['\"]", right) or re.search(
            r"\blyrics?\b|\blyric\s+video\b",
            left,
            flags=re.IGNORECASE,
        ):
            title = left.strip()

    title = GENERIC_TITLE_DECORATION_RE.sub(" ", title)
    title = TRAILING_GENERIC_TITLE_RE.sub("", title)
    return re.sub(r"\s+", " ", title).strip(" -") or "Unknown track"


def display_track_title(track: object, query: str | None = None) -> str:
    title = clean_track_title(getattr(track, "title", "") or "Unknown track")
    author = display_track_author(track)
    has_artist_title_separator = ARTIST_TITLE_SEPARATOR_RE.search(title)
    if author and not has_artist_title_separator and normalized_words(author) - normalized_words(title):
        title = f"{author} - {title}"

    if not query:
        return title

    title_text = title.lower()
    variants = [
        term
        for term in requested_display_variants(query)
        if not text_contains_term(title_text, term)
    ]
    if not variants:
        return title

    return f"{title} ({', '.join(variants)})"


def track_requester(track: object) -> str | None:
    extras = getattr(track, "extras", None)
    if isinstance(extras, Mapping):
        requester = extras.get("requester")
    else:
        requester = getattr(extras, "requester", None)

    return str(requester) if requester is not None else None


def track_display_title(track: object) -> str:
    extras = getattr(track, "extras", None)
    if isinstance(extras, Mapping):
        display_title = extras.get("display_title")
    else:
        display_title = getattr(extras, "display_title", None)

    if display_title is not None:
        return str(display_title)

    return getattr(track, "title", "Unknown track")
