import asyncio
import datetime as dt
import disnake
import mafic
from disnake.ext import commands
from typing import Optional, Deque, Dict, List, NamedTuple
from collections import deque
from urllib.parse import urlparse

try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml

from lavalink import ensure_lavalink

CONFIG_PATH = "config.toml"


def _load_color() -> int:
    with open(CONFIG_PATH, "rb") as f:
        cfg = toml.load(f)
    return int(cfg.get("embed color", 0x8BC6E8))


def _fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "0:00"
    s = int(ms // 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _yt_thumb(track: mafic.Track) -> Optional[str]:
    ident = getattr(track, "identifier", None)
    if not ident:
        return None
    return f"https://img.youtube.com/vi/{ident}/hqdefault.jpg"


def _art_url(track: mafic.Track) -> Optional[str]:
    art = getattr(track, "artworkUrl", None) or getattr(track, "artwork_url", None)
    return art or _yt_thumb(track)


def _track_link_line(track: mafic.Track) -> str:
    uri = getattr(track, "uri", None)
    title = getattr(track, "title", "Unknown title")
    author = getattr(track, "author", "Unknown")
    return f"[{title}]({uri}) by **{author}**" if uri else f"**{title}** by **{author}**"


def _source_name(uri: Optional[str]) -> str:
    if not uri:
        return "Unknown"
    host = urlparse(uri).netloc.lower()
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    if "soundcloud" in host:
        return "SoundCloud"
    if "spotify" in host:
        return "Spotify"
    return host or "Unknown"


class QItem(NamedTuple):
    track: mafic.Track
    requester_id: Optional[int] = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.color = _load_color()
        self.node = None
        self._players: Dict[int, mafic.Player] = {}
        self._vc_map: Dict[int, int] = {}
        self._queues: Dict[int, Deque[QItem]] = {}
        self._current: Dict[int, Optional[mafic.Track]] = {}
        self._current_req: Dict[int, Optional[int]] = {}
        self._last: Dict[int, Optional[mafic.Track]] = {}
        self._synced = False

    async def _ensure_node(self):
        if self.node is None:
            self.node = await ensure_lavalink(self.bot)

    def _author_channel(self, author: disnake.Member) -> Optional[disnake.VoiceChannel]:
        vs = getattr(author, "voice", None)
        return getattr(vs, "channel", None)

    def _get_player(self, guild: disnake.Guild) -> Optional[mafic.Player]:
        return self._players.get(guild.id)

    async def _connect(self, guild: disnake.Guild, channel: disnake.VoiceChannel):
        await self._ensure_node()
        player: mafic.Player = await channel.connect(cls=mafic.Player)
        me = guild.me
        if me and me.voice and me.voice.channel == channel:
            try:
                await me.edit(deafen=True)
            except Exception as e:
                print(f"Failed to deafen bot: {e}")
        self._players[guild.id] = player
        self._vc_map[guild.id] = channel.id
        self._queues.setdefault(guild.id, deque())

    async def _disconnect(self, guild: disnake.Guild):
        player = self._players.get(guild.id)
        if player:
            try:
                await player.disconnect()
            except Exception:
                try:
                    await player.destroy()
                except Exception:
                    pass
        self._players.pop(guild.id, None)
        self._vc_map.pop(guild.id, None)
        self._queues.pop(guild.id, None)
        self._current.pop(guild.id, None)
        self._current_req.pop(guild.id, None)
        self._last.pop(guild.id, None)

    async def _check_empty_and_leave(self, guild: disnake.Guild):
        chan_id = self._vc_map.get(guild.id)
        if not chan_id:
            return
        chan = guild.get_channel(chan_id)
        if not isinstance(chan, disnake.VoiceChannel):
            await self._disconnect(guild)
            return
        humans = [m for m in chan.members if not m.bot]
        if len(humans) == 0:
            await self._disconnect(guild)

    def _enqueue(self, guild_id: int, track: mafic.Track, requester_id: Optional[int]) -> int:
        q = self._queues.setdefault(guild_id, deque())
        q.append(QItem(track=track, requester_id=requester_id))
        return len(q)

    async def _play_track(self, player: mafic.Player, track: mafic.Track, requester_id: Optional[int] = None):
        gid = player.guild.id
        await player.play(track, start_time=0)
        self._current[gid] = track
        self._current_req[gid] = requester_id
        self._last[gid] = track

    async def _play_next_or_autoplay(self, player: mafic.Player):
        gid = player.guild.id
        q = self._queues.get(gid)
        if q and len(q) > 0:
            item = q.popleft()
            await self._play_track(player, item.track, item.requester_id)
            return
        seed = self._last.get(gid)
        ident = getattr(seed, "identifier", None)
        if ident:
            url = f"https://www.youtube.com/watch?v={ident}&list=RD{ident}"
            try:
                results = await player.fetch_tracks(url, search_type=mafic.SearchType.YOUTUBE)
            except Exception:
                results = None
            if isinstance(results, mafic.Playlist) and results.tracks:
                for t in results.tracks:
                    if getattr(t, "identifier", None) != ident:
                        await self._play_track(player, t, None)
                        return
        self._current.pop(gid, None)
        self._current_req.pop(gid, None)

    def _mention(self, guild: disnake.Guild, user_id: Optional[int]) -> str:
        if user_id is None:
            return "Autoplay"
        m = guild.get_member(user_id)
        return m.mention if m else f"<@{user_id}>"

    def _progress_bar(self, pos_ms: int, length_ms: Optional[int], width: int = 12) -> str:
        if not length_ms or length_ms <= 0:
            return "─" * width
        ratio = max(0.0, min(1.0, pos_ms / length_ms))
        filled = int(round(ratio * width))
        filled = min(max(filled, 0), width)
        return "█" * filled + "─" * (width - filled)

    def _now_playing_embed(self, guild: disnake.Guild) -> disnake.Embed:
        player = self._players.get(guild.id)
        track = getattr(player, "current", None) if player else None
        if not track:
            return disnake.Embed(
                title="Now Playing",
                description="Nothing is playing.",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        pos = getattr(player, "position", 0) if player else 0
        length = getattr(track, "length", None)
        prog_txt = f"{_fmt_ms(pos)} / {_fmt_ms(length)}"
        bar = self._progress_bar(pos, length)
        status = "⏸️" if getattr(player, "paused", False) else "▶️"
        emb = disnake.Embed(
            title=f"{status} Now Playing",
            description=_track_link_line(track),
            color=self.color,
            timestamp=dt.datetime.utcnow(),
        )
        art = _art_url(track)
        if art:
            emb.set_thumbnail(url=art)
        emb.add_field(name="Artist", value=getattr(track, "author", "Unknown"), inline=True)
        vol = getattr(player, "volume", None)
        if vol is not None:
            emb.add_field(name="Volume", value=f"{vol}%", inline=True)
        emb.add_field(name="Source", value=_source_name(getattr(track, "uri", None)), inline=True)
        emb.add_field(name="Progress", value=f"`{bar}`\n`{prog_txt}`", inline=False)
        rq = self._mention(guild, self._current_req.get(guild.id))
        emb.set_footer(text=f"Requested by {rq}")
        q = list(self._queues.get(guild.id, deque()))
        if q:
            preview = []
            for i, item in enumerate(q[:3], start=1):
                t = item.track
                preview.append(f"`#{i}` {getattr(t,'title','Unknown')} — `{_fmt_ms(getattr(t,'length', None))}`")
            emb.add_field(name="Up Next", value="\n".join(preview), inline=False)
        return emb

    def _queue_embed(self, guild: disnake.Guild, page: int = 1, per_page: int = 10) -> disnake.Embed:
        gid = guild.id
        player = self._players.get(gid)
        current = getattr(player, "current", None) if player else None
        q_items = list(self._queues.get(gid, deque()))
        total_tracks = (1 if current else 0) + len(q_items)
        total_ms = (getattr(current, "length", 0) or 0) + sum((getattr(item.track, "length", 0) or 0) for item in q_items)
        pages = max(1, (len(q_items) + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        end = start + per_page
        slice_q = q_items[start:end]
        lines: List[str] = []
        if current:
            pos_ms = getattr(player, "position", 0) if player else 0
            rq = self._mention(guild, self._current_req.get(gid))
            lines.append(f"**Now:** {_track_link_line(current)}\n`{_fmt_ms(pos_ms)} / {_fmt_ms(getattr(current,'length', None))}` · {rq}\n")
        else:
            lines.append("**Now:** Nothing is playing.\n")
        if slice_q:
            for idx, item in enumerate(slice_q, start=start + 1):
                t = item.track
                rq = self._mention(guild, item.requester_id)
                lines.append(f"`#{idx}` {_track_link_line(t)} — `{_fmt_ms(getattr(t,'length', None))}` · {rq}")
        else:
            lines.append("Queue is empty.")
        emb = disnake.Embed(
            title=f"Queue ({total_tracks} track{'s' if total_tracks != 1 else ''}, total {_fmt_ms(total_ms)})",
            description="\n".join(lines),
            color=self.color,
            timestamp=dt.datetime.utcnow(),
        )
        thumb = _art_url(current) if current else (_art_url(q_items[0].track) if q_items else None)
        if thumb:
            emb.set_thumbnail(url=thumb)
        emb.set_footer(text=f"Page {page}/{pages}")
        return emb

    @commands.group(name="music", invoke_without_command=True)
    async def music_group(self, ctx: commands.Context):
        await ctx.send(
            embed=disnake.Embed(
                title="Music",
                description=(
                    "Use:\n"
                    "`>music join`, `>music play <query>`, `>music skip`, `>music pause`, "
                    "`>music nowplaying`, `>music queue [page]`, `>music stop`, `>music leave`"
                ),
                color=self.color,
            )
        )

    @music_group.command(name="join")
    async def join_prefix(self, ctx: commands.Context):
        ch = self._author_channel(ctx.author)
        if not ch:
            await ctx.send(
                embed=disnake.Embed(
                    title="Error!",
                    description="Join a voice channel first.",
                    color=self.color,
                )
            )
            return
        await self._connect(ctx.guild, ch)
        await ctx.send(
            embed=disnake.Embed(
                title="Joined", description=f"{ch.mention}", color=self.color
            )
        )

    @music_group.command(name="leave")
    async def leave_prefix(self, ctx: commands.Context):
        await self._disconnect(ctx.guild)
        await ctx.send(
            embed=disnake.Embed(
                title="Left", description="Disconnected.", color=self.color
            )
        )

    @music_group.command(name="play")
    async def play_prefix(self, ctx: commands.Context, *, query: str):
        player = self._get_player(ctx.guild)
        if not player:
            ch = self._author_channel(ctx.author)
            if not ch:
                await ctx.send(
                    embed=disnake.Embed(
                        title="Error!",
                        description="Join a voice channel first.",
                        color=self.color,
                    )
                )
                return
            await self._connect(ctx.guild, ch)
            player = self._get_player(ctx.guild)
        try:
            results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
        except Exception as e:
            await ctx.send(
                embed=disnake.Embed(
                    title="Search failed",
                    description=f"```{e}```",
                    color=self.color,
                )
            )
            return
        track = None
        if results is None:
            pass
        elif isinstance(results, mafic.Playlist) and results.tracks:
            track = results.tracks[0]
        elif isinstance(results, list) and results:
            track = results[0]
        if not track:
            await ctx.send(
                embed=disnake.Embed(
                    title="No results",
                    description=f"Couldn't find anything for `{query}`.",
                    color=self.color,
                )
            )
            return
        if getattr(player, "current", None):
            pos = self._enqueue(ctx.guild.id, track, getattr(ctx.author, "id", None))
            emb = disnake.Embed(
                title="Queued",
                description=_track_link_line(track),
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
            thumb = _art_url(track)
            if thumb:
                emb.set_thumbnail(url=thumb)
            emb.add_field(name="Position", value=f"#{pos}", inline=True)
            await ctx.send(embed=emb)
        else:
            await self._play_track(player, track, getattr(ctx.author, "id", None))
            await ctx.send(embed=self._now_playing_embed(ctx.guild))

    @music_group.command(name="skip")
    async def skip_prefix(self, ctx: commands.Context):
        player = self._get_player(ctx.guild)
        if not player or not getattr(player, "current", None):
            await ctx.send(
                embed=disnake.Embed(
                    title="Nothing to skip",
                    description="No track is currently playing.",
                    color=self.color,
                )
            )
            return
        await player.stop()
        await ctx.send(
            embed=disnake.Embed(
                title="Skipped",
                description="Moving to the next track…",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @music_group.command(name="pause")
    async def pause_prefix(self, ctx: commands.Context):
        player = self._get_player(ctx.guild)
        if not player or not getattr(player, "current", None):
            await ctx.send(
                embed=disnake.Embed(
                    title="Nothing to pause",
                    description="No track is currently playing.",
                    color=self.color,
                )
            )
            return
        paused = getattr(player, "paused", False)
        await player.pause(not paused)
        await ctx.send(
            embed=disnake.Embed(
                title="Paused" if not paused else "Resumed",
                description="Playback paused." if not paused else "Playback resumed.",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @music_group.command(name="nowplaying", aliases=["np"])
    async def nowplaying_prefix(self, ctx: commands.Context):
        player = self._get_player(ctx.guild)
        track = getattr(player, "current", None) if player else None
        if not track:
            await ctx.send(
                embed=disnake.Embed(
                    title="Now Playing",
                    description="Nothing is playing.",
                    color=self.color,
                    timestamp=dt.datetime.utcnow(),
                )
            )
            return
        await ctx.send(embed=self._now_playing_embed(ctx.guild))

    @music_group.command(name="queue")
    async def queue_prefix(self, ctx: commands.Context, page: int = 1):
        if page < 1:
            page = 1
        embed = self._queue_embed(ctx.guild, page=page)
        await ctx.send(embed=embed)

    @music_group.command(name="stop")
    async def stop_prefix(self, ctx: commands.Context):
        player = self._get_player(ctx.guild)
        if not player:
            await ctx.send(
                embed=disnake.Embed(
                    title="Nothing to stop",
                    description="I'm not playing anything right now.",
                    color=self.color,
                )
            )
            return
        await player.stop()
        self._queues.get(ctx.guild.id, deque()).clear()
        await ctx.send(
            embed=disnake.Embed(
                title="Stopped",
                description="Playback stopped and queue cleared.",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @commands.slash_command(name="music", description="Music controls", dm_permission=False)
    async def music_slash(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @music_slash.sub_command(name="join", description="Join your voice channel")
    async def join_slash(self, inter: disnake.ApplicationCommandInteraction):
        if not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!", description="Guild only.", color=self.color
                ),
                ephemeral=True,
            )
            return
        ch = self._author_channel(inter.author)
        if not ch:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!",
                    description="Join a voice channel first.",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        await self._connect(inter.guild, ch)
        await inter.response.send_message(
            embed=disnake.Embed(
                title="Joined", description=f"{ch.mention}", color=self.color
            )
        )

    @music_slash.sub_command(name="leave", description="Disconnect the bot")
    async def leave_slash(self, inter: disnake.ApplicationCommandInteraction):
        await self._disconnect(inter.guild)
        await inter.response.send_message(
            embed=disnake.Embed(
                title="Left", description="Disconnected.", color=self.color
            )
        )

    @music_slash.sub_command(name="play", description="Search YouTube and play / queue")
    async def play_slash(self, inter: disnake.ApplicationCommandInteraction, query: str):
        if not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!", description="Guild only.", color=self.color
                ),
                ephemeral=True,
            )
            return
        player = self._get_player(inter.guild)
        if not player:
            ch = self._author_channel(inter.author)
            if not ch:
                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Error!",
                        description="Join a voice channel first.",
                        color=self.color,
                    ),
                    ephemeral=True,
                )
                return
            await self._connect(inter.guild, ch)
            player = self._get_player(inter.guild)
        try:
            results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
        except Exception as e:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Search failed",
                    description=f"```{e}```",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        track = None
        if results is None:
            pass
        elif isinstance(results, mafic.Playlist) and results.tracks:
            track = results.tracks[0]
        elif isinstance(results, list) and results:
            track = results[0]
        if not track:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="No results",
                    description=f"Couldn't find anything for `{query}`.",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        if getattr(player, "current", None):
            pos = self._enqueue(inter.guild.id, track, getattr(inter.author, "id", None))
            emb = disnake.Embed(
                title="Queued",
                description=_track_link_line(track),
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
            thumb = _art_url(track)
            if thumb:
                emb.set_thumbnail(url=thumb)
            emb.add_field(name="Position", value=f"#{pos}", inline=True)
            await inter.response.send_message(embed=emb)
        else:
            await self._play_track(player, track, getattr(inter.author, "id", None))
            await inter.response.send_message(embed=self._now_playing_embed(inter.guild))

    @music_slash.sub_command(name="skip", description="Skip the current track")
    async def skip_slash(self, inter: disnake.ApplicationCommandInteraction):
        player = self._get_player(inter.guild)
        if not player or not getattr(player, "current", None):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Nothing to skip",
                    description="No track is currently playing.",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        await player.stop()
        await inter.response.send_message(
            embed=disnake.Embed(
                title="Skipped",
                description="Moving to the next track…",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @music_slash.sub_command(name="pause", description="Pause or resume playback")
    async def pause_slash(self, inter: disnake.ApplicationCommandInteraction):
        player = self._get_player(inter.guild)
        if not player or not getattr(player, "current", None):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Nothing to pause",
                    description="No track is currently playing.",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        paused = getattr(player, "paused", False)
        await player.pause(not paused)
        await inter.response.send_message(
            embed=disnake.Embed(
                title="Paused" if not paused else "Resumed",
                description="Playback paused." if not paused else "Playback resumed.",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @music_slash.sub_command(name="nowplaying", description="Show the current track")
    async def nowplaying_slash(self, inter: disnake.ApplicationCommandInteraction):
        player = self._get_player(inter.guild)
        track = getattr(player, "current", None) if player else None
        if not track:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Now Playing",
                    description="Nothing is playing.",
                    color=self.color,
                    timestamp=dt.datetime.utcnow(),
                ),
                ephemeral=True,
            )
            return
        await inter.response.send_message(embed=self._now_playing_embed(inter.guild))

    @music_slash.sub_command(name="queue", description="Show the queue (paged)")
    async def queue_slash(self, inter: disnake.ApplicationCommandInteraction, page: int = 1):
        if page < 1:
            page = 1
        embed = self._queue_embed(inter.guild, page=page)
        await inter.response.send_message(embed=embed)

    @music_slash.sub_command(name="stop", description="Stop and clear the queue")
    async def stop_slash(self, inter: disnake.ApplicationCommandInteraction):
        player = self._get_player(inter.guild)
        if not player:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Nothing to stop",
                    description="I'm not playing anything right now.",
                    color=self.color,
                ),
                ephemeral=True,
            )
            return
        await player.stop()
        self._queues.get(inter.guild.id, deque()).clear()
        await inter.response.send_message(
            embed=disnake.Embed(
                title="Stopped",
                description="Playback stopped and queue cleared.",
                color=self.color,
                timestamp=dt.datetime.utcnow(),
            )
        )

    @commands.Cog.listener()
    async def on_track_end(self, event):
        await self._play_next_or_autoplay(event.player)

    @commands.Cog.listener()
    async def on_track_exception(self, event):
        await self._play_next_or_autoplay(event.player)

    @commands.Cog.listener()
    async def on_track_stuck(self, event):
        await self._play_next_or_autoplay(event.player)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        after: disnake.VoiceState,
    ):
        if not member.guild or member.bot:
            return
        gid = member.guild.id
        if gid not in self._vc_map:
            return
        tracked = self._vc_map[gid]
        if (before and getattr(before.channel, "id", None) == tracked) or (
            after and getattr(after.channel, "id", None) == tracked
        ):
            await self._check_empty_and_leave(member.guild)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._synced:
            try:
                await self.bot.sync_commands()
            except Exception as e:
                print(f"Failed to sync commands: {e}")
            else:
                self._synced = True


def setup(bot):
    bot.add_cog(Music(bot))
