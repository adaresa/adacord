import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
import wavelink

from adacord.persistence import save_player_state
from adacord.config import default_volume
from adacord.player import clear_player, get_player, queue_items, set_loop_mode, set_volume
from adacord.state import get_guild_state
from adacord.utils import format_duration, track_display_title, track_requester

logger = logging.getLogger(__name__)

PLAYER_CONTROL_IDS = {
    "restart": "adacord:player:restart",
    "pause_resume": "adacord:player:pause_resume",
    "skip": "adacord:player:skip",
    "stop": "adacord:player:stop",
    "volume_down": "adacord:player:volume_down",
    "volume_up": "adacord:player:volume_up",
    "mute": "adacord:player:mute",
    "shuffle": "adacord:player:shuffle",
    "loop": "adacord:player:loop",
    "queue": "adacord:player:queue",
}

PLAYER_ACCENTS = {
    "idle": 0x6B7280,
    "playing": 0x22C55E,
    "paused": 0xEAB308,
}
DISPLAY_REFRESH_INTERVAL = 5.0


def player_for_interaction(interaction: discord.Interaction) -> wavelink.Player | None:
    if not interaction.guild:
        return None
    return get_player(interaction.guild)


async def respond(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = False,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=ephemeral,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=ephemeral,
            )
    except (discord.NotFound, discord.HTTPException):
        logger.exception("Failed to send interaction response")


async def acknowledge(interaction: discord.Interaction) -> None:
    try:
        if interaction.response.is_done():
            await interaction.delete_original_response()
        else:
            await interaction.response.defer()
            if interaction.type is discord.InteractionType.application_command:
                await interaction.delete_original_response()
    except discord.NotFound:
        pass
    except discord.HTTPException:
        logger.exception("Failed to acknowledge interaction")


@dataclass(frozen=True)
class PlayerPanelModel:
    state: str
    title: str
    progress: str
    requester: str | None
    artwork_url: str | None
    volume: int
    loop_mode: str
    queue_count: int
    queue_preview: list[str]
    pause_label: str
    mute_label: str
    disabled: dict[str, bool]

    @property
    def accent_color(self) -> int:
        return PLAYER_ACCENTS.get(self.state, PLAYER_ACCENTS["idle"])


def track_artwork_url(track: object | None) -> str | None:
    if not track:
        return None
    artwork = getattr(track, "artwork", None)
    return str(artwork) if artwork else None


def build_progress_text(player: wavelink.Player | None) -> str:
    current = player.current if player else None
    if not current:
        return "No active track"

    duration = format_duration(getattr(current, "length", None))
    position = format_duration(getattr(player, "position", None))
    if duration and position:
        return f"{position} / {duration}"
    if duration:
        return duration
    return "Live or unknown duration"


def build_queue_preview(player: wavelink.Player | None) -> list[str]:
    tracks = queue_items(player) if player else []
    preview = []
    for index, track in enumerate(tracks[:5], start=1):
        duration = format_duration(track.length)
        suffix = f" [{duration}]" if duration else ""
        preview.append(f"`{index}.` {track_display_title(track)}{suffix}")
    if len(tracks) > 5:
        preview.append(f"...and {len(tracks) - 5} more")
    return preview


def build_player_panel_model(player: wavelink.Player | None, guild_id: int) -> PlayerPanelModel:
    state = get_guild_state(guild_id)
    current = player.current if player else None
    tracks = queue_items(player) if player else []
    has_music = bool(current or tracks)
    panel_state = "idle"
    if current:
        panel_state = "paused" if bool(player and player.paused) else "playing"

    volume = player.volume if player and player.volume is not None else default_volume()
    loop_mode = state.loop_mode
    requester = track_requester(current) if current else None
    title = track_display_title(current) if current else "Nothing playing"

    return PlayerPanelModel(
        state=panel_state,
        title=title,
        progress=build_progress_text(player),
        requester=requester,
        artwork_url=track_artwork_url(current),
        volume=volume,
        loop_mode=loop_mode,
        queue_count=len(tracks),
        queue_preview=build_queue_preview(player),
        pause_label="Resume" if bool(player and player.paused) else "Pause",
        mute_label="Unmute" if volume == 0 else "Mute",
        disabled={
            "restart": not bool(current),
            "pause_resume": not bool(current),
            "skip": not bool(current),
            "stop": not has_music,
            "volume_down": not has_music or volume <= 0,
            "volume_up": not has_music or volume >= 200,
            "mute": not has_music,
            "shuffle": not bool(tracks),
            "loop": not has_music,
            "queue": not bool(tracks),
        },
    )


def build_player_embed(player: wavelink.Player | None, guild_id: int) -> discord.Embed:
    state = get_guild_state(guild_id)
    current = player.current if player else None
    paused = bool(player and player.paused)
    color = discord.Color.dark_grey()
    if current:
        color = discord.Color.yellow() if paused else discord.Color.green()

    embed = discord.Embed(title="Music Player", color=color, timestamp=datetime.now(timezone.utc))

    if current:
        duration = format_duration(current.length)
        title = f"**{track_display_title(current)}**"
        if duration:
            title += f" `[{duration}]`"
        embed.add_field(name="Now Playing", value=title, inline=False)
        requester = track_requester(current)
        if requester:
            embed.add_field(name="Requested by", value=requester, inline=True)
    else:
        embed.add_field(name="Now Playing", value="Nothing playing", inline=False)

    volume = player.volume if player else default_volume()
    embed.add_field(name="Volume", value=f"{volume}%", inline=True)
    if state.loop_mode != "none":
        embed.add_field(name="Loop", value=state.loop_mode.title(), inline=True)

    tracks = queue_items(player) if player else []
    if tracks:
        preview = []
        for index, track in enumerate(tracks[:5], start=1):
            duration = format_duration(track.length)
            suffix = f" `[{duration}]`" if duration else ""
            preview.append(f"`{index}.` {track_display_title(track)}{suffix}")
        if len(tracks) > 5:
            preview.append(f"...and {len(tracks) - 5} more")
        embed.add_field(name=f"Queue ({len(tracks)})", value="\n".join(preview), inline=False)
    else:
        embed.add_field(name="Queue", value="Empty", inline=False)

    return embed


class PlayerPanelView(discord.ui.LayoutView):
    def __init__(self, guild_id: int | None = None, model: PlayerPanelModel | None = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.model = model or build_player_panel_model(None, guild_id or 0)
        self.build_layout()

    def guild_id_for(self, interaction: discord.Interaction) -> int:
        return interaction.guild_id or self.guild_id or 0

    async def refresh(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await update_display_for_guild(self.guild_id_for(interaction), player)

    def build_layout(self) -> None:
        container = discord.ui.Container(accent_color=self.model.accent_color)
        header = [
            f"### {self.model.title}",
            f"Progress: {self.model.progress}",
        ]
        if self.model.requester:
            header.append(f"Requested by: {self.model.requester}")

        summary = [
            f"Volume: {self.model.volume}%",
            f"Loop: {self.model.loop_mode.title()}",
            f"Queue: {self.model.queue_count}",
        ]

        accessory = None
        if self.model.artwork_url:
            accessory = discord.ui.Thumbnail(self.model.artwork_url, description=self.model.title)

        if accessory:
            container.add_item(discord.ui.Section("\n".join(header), accessory=accessory))
        else:
            container.add_item(discord.ui.TextDisplay("\n".join(header)))
        container.add_item(discord.ui.TextDisplay(" | ".join(summary)))

        container.add_item(discord.ui.Separator())
        queue_text = "\n".join(self.model.queue_preview) if self.model.queue_preview else "Queue is empty"
        container.add_item(discord.ui.TextDisplay(f"**Up Next**\n{queue_text}"))
        container.add_item(discord.ui.Separator())

        transport_row = discord.ui.ActionRow()
        transport_row.add_item(
            self.make_button(
                "restart",
                style=discord.ButtonStyle.secondary,
                emoji="\u23ee\ufe0f",
                callback=self.restart,
            )
        )
        transport_row.add_item(
            self.make_button(
                "pause_resume",
                style=discord.ButtonStyle.primary,
                label=self.model.pause_label,
                emoji="\u23ef\ufe0f",
                callback=self.pause_resume,
            )
        )
        transport_row.add_item(
            self.make_button("skip", style=discord.ButtonStyle.secondary, emoji="\u23ed\ufe0f", callback=self.skip)
        )
        transport_row.add_item(
            self.make_button("stop", style=discord.ButtonStyle.danger, emoji="\u23f9\ufe0f", callback=self.stop)
        )
        container.add_item(transport_row)

        volume_row = discord.ui.ActionRow()
        volume_row.add_item(
            self.make_button("volume_down", label="-10%", style=discord.ButtonStyle.secondary, callback=self.volume_down)
        )
        volume_row.add_item(
            self.make_button("volume_up", label="+10%", style=discord.ButtonStyle.secondary, callback=self.volume_up)
        )
        volume_row.add_item(
            self.make_button("mute", label=self.model.mute_label, style=discord.ButtonStyle.secondary, callback=self.mute)
        )
        container.add_item(volume_row)

        queue_row = discord.ui.ActionRow()
        queue_row.add_item(
            self.make_button("shuffle", label="Shuffle", style=discord.ButtonStyle.secondary, callback=self.shuffle)
        )
        queue_row.add_item(self.make_button("loop", label="Loop", style=discord.ButtonStyle.secondary, callback=self.loop))
        queue_row.add_item(
            self.make_button("queue", label="Queue", style=discord.ButtonStyle.secondary, callback=self.queue)
        )
        container.add_item(queue_row)
        self.add_item(container)

    def make_button(
        self,
        key: str,
        *,
        style: discord.ButtonStyle,
        callback,
        label: str | None = None,
        emoji: str | None = None,
    ) -> discord.ui.Button:
        button = discord.ui.Button(
            style=style,
            label=label,
            emoji=emoji,
            disabled=self.model.disabled.get(key, False),
            custom_id=PLAYER_CONTROL_IDS[key],
        )
        button.callback = callback
        return button

    async def restart(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to restart.", ephemeral=True)
            return
        await player.seek(0)
        await save_player_state(player)
        await acknowledge(interaction)

    async def pause_resume(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to pause or resume.", ephemeral=True)
            return
        should_pause = not player.paused
        await player.pause(should_pause)
        await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def skip(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or not player.current:
            await respond(interaction, "Nothing to skip.", ephemeral=True)
            return
        await player.skip(force=True)
        await acknowledge(interaction)

    async def stop(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        await clear_player(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def volume_down(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        current_volume = player.volume if player.volume is not None else default_volume()
        new_volume = max(0, current_volume - 10)
        await set_volume(player, new_volume)
        await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def volume_up(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        current_volume = player.volume if player.volume is not None else default_volume()
        new_volume = min(200, current_volume + 10)
        await set_volume(player, new_volume)
        await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def mute(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        state = get_guild_state(self.guild_id_for(interaction))
        current_volume = player.volume if player.volume is not None else default_volume()
        if current_volume > 0:
            state.previous_volume = current_volume
            await set_volume(player, 0)
            await save_player_state(player)
        else:
            volume = state.previous_volume or default_volume()
            await set_volume(player, volume)
            await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player or player.queue.is_empty:
            await respond(interaction, "Queue is empty.", ephemeral=True)
            return
        player.queue.shuffle()
        await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def loop(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        if not player:
            await respond(interaction, "Not connected.", ephemeral=True)
            return
        state = get_guild_state(self.guild_id_for(interaction))
        modes = ["none", "track", "queue"]
        mode = modes[(modes.index(state.loop_mode) + 1) % len(modes)]
        set_loop_mode(player, mode)
        await save_player_state(player)
        await acknowledge(interaction)
        await self.refresh(interaction)

    async def queue(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        await interaction.response.send_message(
            embed=build_queue_embed(player, page=0),
            view=QueueView(self.guild_id_for(interaction), player),
            ephemeral=True,
        )


class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, player: wavelink.Player | None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.page = 0
        self.total_pages = queue_page_count(player)

    async def edit(self, interaction: discord.Interaction) -> None:
        player = player_for_interaction(interaction)
        self.total_pages = queue_page_count(player)
        self.page = min(self.page, self.total_pages - 1)
        await interaction.response.edit_message(
            embed=build_queue_embed(player, self.page),
            view=self,
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(0, self.page - 1)
        await self.edit(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)
        await self.edit(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.success)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.edit(interaction)


def queue_page_count(player: wavelink.Player | None) -> int:
    count = len(queue_items(player)) if player else 0
    return max(1, (count + 9) // 10)


def build_queue_embed(player: wavelink.Player | None, page: int = 0) -> discord.Embed:
    embed = discord.Embed(title="Music Queue", color=discord.Color.blurple())
    if player and player.current:
        embed.add_field(name="Now Playing", value=f"**{track_display_title(player.current)}**", inline=False)

    tracks = queue_items(player) if player else []
    if not tracks:
        embed.add_field(name="Up Next", value="Empty", inline=False)
        return embed

    start = page * 10
    visible = tracks[start : start + 10]
    lines = []
    for offset, track in enumerate(visible, start=1):
        duration = format_duration(track.length)
        suffix = f" `[{duration}]`" if duration else ""
        lines.append(f"`{start + offset}.` **{track_display_title(track)}**{suffix}")
    embed.add_field(name=f"Up Next ({len(tracks)})", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Page {page + 1} of {queue_page_count(player)}")
    return embed


def display_message_uses_v2(message: discord.Message) -> bool:
    flags = getattr(message, "flags", None)
    return bool(flags and getattr(flags, "components_v2", False))


def should_refresh_progress(player: wavelink.Player | None) -> bool:
    return bool(player and player.current and not player.paused)


def stop_display_refresh(guild_id: int) -> None:
    state = get_guild_state(guild_id)
    task = state.display_refresh_task
    if task and not task.done():
        task.cancel()
    state.display_refresh_task = None


def ensure_display_refresh(guild_id: int, player: wavelink.Player | None) -> None:
    state = get_guild_state(guild_id)
    if not should_refresh_progress(player) or not state.display_channel:
        stop_display_refresh(guild_id)
        return

    task = state.display_refresh_task
    if task and not task.done():
        return
    state.display_refresh_task = asyncio.create_task(refresh_display_progress(guild_id, player))


async def refresh_display_progress(guild_id: int, player: wavelink.Player) -> None:
    try:
        while should_refresh_progress(player):
            await asyncio.sleep(DISPLAY_REFRESH_INTERVAL)
            state = get_guild_state(guild_id)
            if not state.display_channel or not should_refresh_progress(player):
                break
            await create_or_update_display(
                guild_id,
                state.display_channel,
                player,
                manage_refresh=False,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to refresh player progress display")
    finally:
        state = get_guild_state(guild_id)
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if state.display_refresh_task is current_task:
            state.display_refresh_task = None


async def create_or_update_display(
    guild_id: int,
    channel: discord.abc.Messageable,
    player: wavelink.Player | None,
    *,
    manage_refresh: bool = True,
) -> discord.Message | None:
    state = get_guild_state(guild_id)
    view = PlayerPanelView(guild_id, build_player_panel_model(player, guild_id))

    try:
        if state.display_message:
            if display_message_uses_v2(state.display_message):
                try:
                    await state.display_message.edit(view=view)
                    state.display_channel_id = getattr(state.display_channel, "id", None)
                    state.display_message_id = getattr(state.display_message, "id", None)
                    if manage_refresh:
                        ensure_display_refresh(guild_id, player)
                    return state.display_message
                except (discord.NotFound, discord.HTTPException):
                    state.display_message = None
            else:
                try:
                    await state.display_message.delete()
                except discord.HTTPException:
                    pass
                state.display_message = None

        message = await channel.send(view=view)
        state.display_message = message
        state.display_channel = channel
        state.display_channel_id = getattr(channel, "id", None)
        state.display_message_id = getattr(message, "id", None)
        if manage_refresh:
            ensure_display_refresh(guild_id, player)
        return message
    except Exception:
        logger.exception("Failed to create or update music display")
        return None


async def update_display_for_guild(
    guild_id: int,
    player: wavelink.Player | None = None,
) -> None:
    state = get_guild_state(guild_id)
    has_music = bool(player and (player.current or not player.queue.is_empty))
    if state.display_channel and has_music:
        await create_or_update_display(guild_id, state.display_channel, player)
    elif state.display_message and not has_music:
        stop_display_refresh(guild_id)
        try:
            await state.display_message.delete()
        except discord.HTTPException:
            pass
        state.display_message = None
        state.display_channel = None
        state.display_channel_id = None
        state.display_message_id = None
    else:
        ensure_display_refresh(guild_id, player)
