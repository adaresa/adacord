from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

import adacord.config as config
from adacord.state import guild_states


class FakeTrack:
    def __init__(
        self,
        title: str,
        *,
        author: str = "",
        length: int = 210_000,
        source: str = "youtube music",
    ):
        self.title = title
        self.author = author
        self.length = length
        self.source = source
        self.extras = {}
        self.encoded = f"encoded:{title}"
        self.identifier = title.lower().replace(" ", "-")
        self.is_seekable = True
        self.is_stream = False
        self.position = 0
        self.uri = f"https://example.test/{self.identifier}"
        self.artwork = None
        self.isrc = None
        self.raw_data = {
            "encoded": self.encoded,
            "info": {
                "identifier": self.identifier,
                "isSeekable": self.is_seekable,
                "author": self.author,
                "length": self.length,
                "isStream": self.is_stream,
                "position": self.position,
                "title": self.title,
                "uri": self.uri,
                "artworkUrl": self.artwork,
                "isrc": self.isrc,
                "sourceName": self.source,
            },
            "pluginInfo": {},
            "userData": {},
        }


class FakeQueue:
    def __init__(self, items: list[FakeTrack] | None = None):
        self.items = list(items or [])
        self.history: list[FakeTrack] = []
        self.mode = None
        self.shuffled = False

    @property
    def is_empty(self) -> bool:
        return not self.items

    def put(self, tracks):
        if isinstance(tracks, list):
            self.items.extend(tracks)
        else:
            self.items.append(tracks)

    def get(self):
        return self.items.pop(0)

    def clear(self) -> None:
        self.items.clear()

    def shuffle(self) -> None:
        self.shuffled = True
        self.items.reverse()

    def put_at(self, index: int, track: FakeTrack) -> None:
        self.items.insert(index, track)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        return self.items[index]

    def __delitem__(self, index: int) -> None:
        del self.items[index]


class FakeNode:
    def __init__(self, *, connected: bool = True):
        self.connected = connected
        self.fetches: list[int] = []

    async def fetch_player_info(self, guild_id: int):
        self.fetches.append(guild_id)
        state = SimpleNamespace(connected=self.connected, ping=10)
        return SimpleNamespace(state=state)


class FakeGuild:
    def __init__(self, guild_id: int = 123):
        self.id = guild_id
        self.voice_client = None


class FakePlayer:
    def __init__(
        self,
        *,
        guild: FakeGuild | None = None,
        queue: FakeQueue | None = None,
        current: FakeTrack | None = None,
        volume: int | None = 50,
        paused: bool = False,
        playing: bool | None = None,
    ):
        self.guild = guild or FakeGuild()
        self.guild.voice_client = self
        self.queue = queue or FakeQueue()
        self.current = current
        self.volume = volume
        self.paused = paused
        self.playing = bool(current) if playing is None else playing
        self.connected = True
        self.channel = None
        self.node = FakeNode()
        self.inactive_timeout = None
        self.inactive_channel_tokens = None
        self.play_calls: list[tuple[FakeTrack, int | None]] = []
        self.play_kwargs = []
        self.pause_calls: list[bool] = []
        self.skip_calls: list[bool] = []
        self.volume_calls: list[int] = []
        self.disconnect_calls = 0
        self.move_calls = []
        self.seek_calls: list[int] = []

    @property
    def position(self) -> int:
        return self.current.position if self.current else 0

    async def play(self, track: FakeTrack, *, volume: int | None = None, **kwargs) -> None:
        self.current = track
        self.playing = True
        self.paused = bool(kwargs.get("paused", False))
        self.volume = volume
        self.play_calls.append((track, volume))
        self.play_kwargs.append(kwargs)

    async def pause(self, value: bool) -> None:
        self.paused = value
        self.pause_calls.append(value)

    async def skip(self, *, force: bool = False) -> None:
        self.skip_calls.append(force)
        self.current = None
        self.playing = False

    async def set_volume(self, volume: int) -> None:
        self.volume = volume
        self.volume_calls.append(volume)

    async def disconnect(self, *args, **kwargs) -> None:
        self.disconnect_calls += 1
        self.connected = False
        self.guild.voice_client = None

    async def move_to(self, channel) -> None:
        self.channel = channel
        self.move_calls.append(channel)

    async def seek(self, position: int) -> None:
        self.seek_calls.append(position)


class FakeVoiceState:
    def __init__(self, channel=None):
        self.channel = channel


class FakeMember:
    def __init__(self, *, name: str = "tester", voice_channel=None):
        self.name = name
        self.voice = FakeVoiceState(voice_channel) if voice_channel else None

    def __str__(self) -> str:
        return self.name


class FakeVoiceChannel:
    def __init__(self, *, guild: FakeGuild | None = None, player: FakePlayer | None = None):
        self.guild = guild or FakeGuild()
        self.player = player
        self.connect_kwargs = None
        self.id = 456

    async def connect(self, **kwargs):
        self.connect_kwargs = kwargs
        player = self.player or FakePlayer(guild=self.guild)
        player.channel = self
        self.guild.voice_client = player
        return player


class FakeMessage:
    def __init__(self, content: str | None = None, *, embed=None, view=None):
        self.content = content
        self.embed = embed
        self.view = view
        self.flags = SimpleNamespace(components_v2=bool(view and view.has_components_v2()))
        self.deleted = False
        self.edits = []
        self.id = 789

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        self.embed = kwargs.get("embed", self.embed)
        self.view = kwargs.get("view", self.view)
        self.flags = SimpleNamespace(components_v2=bool(self.view and self.view.has_components_v2()))
        return self

    async def delete(self):
        self.deleted = True


class FakeTextChannel:
    def __init__(self):
        self.sent: list[FakeMessage] = []
        self.id = 321

    async def send(self, content: str | None = None, **kwargs):
        message = FakeMessage(content, embed=kwargs.get("embed"), view=kwargs.get("view"))
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int):
        message = FakeMessage()
        message.id = message_id
        return message


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.sent = []
        self.edits = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, **kwargs):
        self.deferred = True
        self.defer_kwargs = kwargs
        self._done = True

    async def send_message(self, *args, **kwargs):
        self.sent.append({"args": args, "kwargs": kwargs})
        self._done = True

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)
        self._done = True


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append({"args": args, "kwargs": kwargs})
        if kwargs.get("wait"):
            return FakeMessage(args[0] if args else None)
        return None


class FakeInteraction:
    def __init__(
        self,
        *,
        guild: FakeGuild | None = None,
        user=None,
        channel: FakeTextChannel | None = None,
        guild_id: int | None = None,
        interaction_type: discord.InteractionType | None = discord.InteractionType.application_command,
    ):
        self.guild = guild
        self.guild_id = guild_id if guild_id is not None else (guild.id if guild else None)
        self.user = user if user is not None else FakeMember()
        self.channel = channel or FakeTextChannel()
        self.type = interaction_type
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.deleted_original_response = False

    async def delete_original_response(self):
        self.deleted_original_response = True


@pytest.fixture(autouse=True)
def clear_guild_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PLAYBACK_STATE_FILE", str(tmp_path / "playback_state.json"))
    guild_states.clear()
    yield
    guild_states.clear()


@pytest.fixture
def fake_track_factory():
    return FakeTrack
