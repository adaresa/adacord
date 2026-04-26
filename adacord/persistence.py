from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import wavelink

from adacord.config import playback_state_file
from adacord.state import get_guild_state

logger = logging.getLogger(__name__)

STATE_VERSION = 1
state_lock = threading.Lock()


def state_path() -> Path:
    return Path(playback_state_file())


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.exists():
        return {"version": STATE_VERSION, "guilds": {}}

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read playback state from %s", path)
        return {"version": STATE_VERSION, "guilds": {}}

    if not isinstance(data, dict):
        return {"version": STATE_VERSION, "guilds": {}}

    guilds = data.get("guilds")
    if not isinstance(guilds, dict):
        data["guilds"] = {}
    data["version"] = STATE_VERSION
    return data


def write_state(data: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=True, indent=2, sort_keys=True)
        os.replace(temp_path, path)
    except OSError:
        logger.exception("Could not write playback state to %s", path)


def track_payload(track: object) -> dict[str, Any]:
    raw_data = getattr(track, "raw_data", None)
    if not isinstance(raw_data, dict):
        raw_data = {
            "encoded": getattr(track, "encoded", ""),
            "info": {
                "identifier": getattr(track, "identifier", ""),
                "isSeekable": getattr(track, "is_seekable", True),
                "author": getattr(track, "author", ""),
                "length": getattr(track, "length", 0),
                "isStream": getattr(track, "is_stream", False),
                "position": getattr(track, "position", 0),
                "title": getattr(track, "title", "Unknown track"),
                "uri": getattr(track, "uri", None),
                "artworkUrl": getattr(track, "artwork", None),
                "isrc": getattr(track, "isrc", None),
                "sourceName": getattr(track, "source", "unknown"),
            },
            "pluginInfo": {},
            "userData": {},
        }

    extras = getattr(track, "extras", {})
    try:
        extras_data = dict(extras)
    except (TypeError, ValueError):
        extras_data = {}

    return {
        "raw_data": raw_data,
        "extras": extras_data,
        "title": getattr(track, "title", "Unknown track"),
        "uri": getattr(track, "uri", None),
    }


def track_from_payload(data: dict[str, Any]) -> wavelink.Playable | None:
    raw_data = data.get("raw_data")
    if not isinstance(raw_data, dict):
        return None

    try:
        track = wavelink.Playable(raw_data)
    except (KeyError, TypeError, ValueError):
        logger.exception("Could not restore track from saved payload")
        return None

    extras = data.get("extras")
    if isinstance(extras, dict):
        track.extras = extras
    return track


def saved_tracks(tracks: list[object]) -> list[dict[str, Any]]:
    return [track_payload(track) for track in tracks]


def player_state_snapshot(player: wavelink.Player | None) -> tuple[int, dict[str, Any] | None] | None:
    if not player:
        return None

    current = player.current
    queued = list(player.queue)
    state = get_guild_state(player.guild.id)

    if not current and not queued:
        return player.guild.id, None

    channel = getattr(player, "channel", None)
    voice_channel_id = getattr(channel, "id", None) or state.voice_channel_id
    display_channel_id = state.display_channel_id or getattr(state.display_channel, "id", None)
    display_message_id = state.display_message_id or getattr(state.display_message, "id", None)

    return player.guild.id, {
        "guild_id": player.guild.id,
        "voice_channel_id": voice_channel_id,
        "display_channel_id": display_channel_id,
        "display_message_id": display_message_id,
        "volume": player.volume,
        "loop_mode": state.loop_mode,
        "paused": bool(player.paused),
        "position": player.position,
        "current": track_payload(current) if current else None,
        "queue": saved_tracks(queued),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_player_state_now(player: wavelink.Player | None) -> None:
    snapshot = player_state_snapshot(player)
    if not snapshot:
        return

    guild_id, saved = snapshot
    if saved is None:
        clear_guild_state_now(guild_id)
        return

    save_guild_snapshot(guild_id, saved)


async def save_player_state(player: wavelink.Player | None) -> None:
    snapshot = player_state_snapshot(player)
    if not snapshot:
        return

    guild_id, saved = snapshot
    if saved is None:
        await clear_guild_state(guild_id)
        return

    await asyncio.to_thread(save_guild_snapshot, guild_id, saved)


def save_guild_snapshot(guild_id: int, saved: dict[str, Any]) -> None:
    with state_lock:
        data = load_state()
        guilds = data.setdefault("guilds", {})
        guilds[str(guild_id)] = saved
        write_state(data)


def clear_guild_state_now(guild_id: int) -> None:
    with state_lock:
        data = load_state()
        guilds = data.setdefault("guilds", {})
        if str(guild_id) not in guilds:
            return

        del guilds[str(guild_id)]
        write_state(data)


async def clear_guild_state(guild_id: int) -> None:
    await asyncio.to_thread(clear_guild_state_now, guild_id)
