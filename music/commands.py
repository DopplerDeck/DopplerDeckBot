import asyncio
import datetime as dt
import disnake
import mafic
from disnake.ext import commands
from typing import Optional, Deque, Dict, List, NamedTuple
from collections import deque
from urllib.parse import urlparse
import os
import subprocess
try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml


from lavalink import ensure_lavalink
from database import RestrictionDB

CONFIG_PATH = "config.toml"

RADIO_STATIONS = {
    "capital xtra": {
        "name": "Capital Xtra",
        "url": "https://ice-sov.musicradio.com/CapitalXTRANationalHD?hdauth=:2000000000:a290d4b39a16153061d4c743008b9fe8d424a7e5ec6469d40acf51fdcd336e80",
        "description": "UK's biggest hip hop and R&B station"
    },
    "heart uk": {
        "name": "Heart UK",
        "url": "https://media-ice.musicradio.com/HeartUK",
        "description": "More music variety"
    },
    "truckers fm": {
        "name": "Truckers FM",
        "url": "https://live.truckers.fm/",
        "description": "Music for truckers and road enthusiasts"
    },
    "lbc london": {
        "name": "LBC London",
        "url": "https://ice-sov.musicradio.com/LBCLondonHD?hdauth=:2000000000:a290d4b39a16153061d4c743008b9fe8d424a7e5ec6469d40acf51fdcd336e80",
        "description": "London's biggest conversation"
    },

}


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
    if uri and ("globalplayer.com" in uri or "ice-sov.musicradio.com" in uri or "media-ice.musicradio.com" in uri):
        return f"**{title}** by **{author}**"
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
    if "globalplayer.com" in host or "ice-sov.musicradio.com" in host or "media-ice.musicradio.com" in host:
        return "Global Player"
    return host or "Unknown"


def _is_spotify_url(s: str) -> bool:
    try:
        host = urlparse(s).netloc.lower()
        return "spotify.com" in host or s.startswith("spotify:")
    except Exception:
        return False


def _is_spotify_track(track: mafic.Track) -> bool:
    uri = getattr(track, "uri", None) or ""
    try:
        host = urlparse(uri).netloc.lower()
    except Exception:
        host = ""
    return "spotify.com" in host or uri.startswith("spotify:")


def _yt_search_query_from_track(track: mafic.Track) -> Optional[str]:
    title = getattr(track, "title", None)
    author = getattr(track, "author", None)
    if not title:
        return None
    clean_title = title.replace(" - Single", "").replace(" - EP", "").strip()
    parts = [clean_title]
    if author and author.lower() != "unknown":
        parts.append(author)
    return " ".join(parts)





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
        self.db = RestrictionDB()
        self._last_text_channel: Dict[int, Optional[disnake.TextChannel]] = {}
        self._stopped: Dict[int, bool] = {}
        self._intro_played: Dict[int, bool] = {}

    async def _ensure_node(self):
        if self.node is None:
            self.node = await ensure_lavalink(self.bot)

    def _author_channel(self, author: disnake.Member) -> Optional[disnake.VoiceChannel]:
        vs = getattr(author, "voice", None)
        return getattr(vs, "channel", None)
    
    def _check_restriction(self, guild: disnake.Guild, channel: disnake.VoiceChannel) -> bool:
        if not self.db.has_restriction(guild.id):
            return True
        restricted_channel_id = self.db.get_restriction(guild.id)
        return channel.id == restricted_channel_id

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
        self._last_text_channel.pop(guild.id, None)
        self._stopped.pop(guild.id, None)
        self._intro_played.pop(guild.id, None)

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

    async def _play_intro_disnake(self, channel: disnake.VoiceChannel):
        intro_file = os.getenv("INTRO_FILE", "botintro.wav")
        if not os.path.exists(intro_file):
            print(f"[intro] File not found: {intro_file}")
            return

        # 1) Connect natively (NOT Lavalink)
        vc: disnake.VoiceClient = await channel.connect()
        try:
            src = disnake.FFmpegPCMAudio(
                intro_file,
                before_options="-nostdin",
                options="-vn -ac 2 -ar 48000"
            )
            vc.play(disnake.PCMVolumeTransformer(src, volume=1.0))

            # Wait until done or 10s max
            for _ in range(100):
                if not vc.is_playing():
                    break
                await asyncio.sleep(0.1)
        finally:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

        # 2) Cool-down: give Discord time to release the old voice session
        await asyncio.sleep(0.5)

        # 3) Extra safety: wait until guild.voice_client is really None
        for _ in range(20):  # up to ~2s
            if channel.guild.voice_client is None:
                break
            await asyncio.sleep(0.1)



    async def _play_track(self, player: mafic.Player, track: mafic.Track, text_channel, requester_id: Optional[int] = None):
        gid = player.guild.id
        await player.play(track, start_time=0)
        self._current[gid] = track
        self._current_req[gid] = requester_id
        self._last[gid] = track
        try:
            embed = self._now_playing_embed(player.guild)
            await text_channel.send(embed=embed)
        except Exception as e:
            print(f"Failed to send now playing message: {e}")

    async def _play_next_or_autoplay(self, player: mafic.Player):
        gid = player.guild.id
        q = self._queues.get(gid)

        if q and len(q) > 0:
            item = q.popleft()
            await self._play_track(player, item.track, self._last_text_channel.get(gid), item.requester_id)
            return

        seed = self._last.get(gid)
        if not seed:
            self._current.pop(gid, None)
            self._current_req.pop(gid, None)
            return

        if _is_spotify_track(seed):
            query = _yt_search_query_from_track(seed)
            if query:
                try:
                    yt_results = await player.fetch_tracks(query, search_type=mafic.SearchType.YOUTUBE)
                except Exception:
                    yt_results = None

                if isinstance(yt_results, list) and yt_results:
                    yt_track = yt_results[0]
                    await self._play_track(player, yt_track, self._last_text_channel.get(gid), None)
                    return

            self._current.pop(gid, None)
            self._current_req.pop(gid, None)
            return

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
                        await self._play_track(player, t, self._last_text_channel.get(gid), None)
                        return

        self._current.pop(gid, None)
        self._current_req.pop(gid, None)

    def _mention(self, guild: disnake.Guild, user_id: Optional[int]) -> str:
        if user_id is None:
            return "auto-play"
        m = guild.get_member(user_id)
        return m.name if m else f"User {user_id}"

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
        emb = disnake.Embed(
            title=f"Now Playing",
            description=_track_link_line(track),
            color=self.color,
            timestamp=dt.datetime.utcnow(),
        )
        art = _art_url(track)
        if art:
            emb.set_thumbnail(url=art)
        uri = getattr(track, "uri", None)
        is_radio = uri and ("ice-sov.musicradio.com" in uri or "media-ice.musicradio.com" in uri or "globalplayer.com" in uri)
        if not is_radio:
            emb.add_field(name="Artist", value=getattr(track, "author", "Unknown"), inline=True)
        vol = getattr(player, "volume", None)
        if vol is not None:
            emb.add_field(name="Volume", value=f"{vol}%", inline=True)
        emb.add_field(name="Source", value=_source_name(uri), inline=True)
        rq = self._mention(guild, self._current_req.get(guild.id))
        emb.set_footer(text=f"Requested by {rq}.")
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
        
        if not self._check_restriction(ctx.guild, ch):
            restricted_channel_id = self.db.get_restriction(ctx.guild.id)
            restricted_channel = ctx.guild.get_channel(restricted_channel_id)
            channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
            await ctx.send(
                embed=disnake.Embed(
                    title="Error!",
                    description=f"The bot is restricted to {channel_name}. Please join that channel or use `>utils restrict` to change the restriction.",
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
        gid = ctx.guild.id
        self._last_text_channel[gid] = ctx.channel
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

            if not self._check_restriction(ctx.guild, ch):
                restricted_channel_id = self.db.get_restriction(ctx.guild.id)
                restricted_channel = ctx.guild.get_channel(restricted_channel_id)
                channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
                await ctx.send(
                    embed=disnake.Embed(
                        title="Error!",
                        description=f"The bot is restricted to {channel_name}. Please join that channel or use `>utils restrict` to change the restriction.",
                        color=self.color,
                    )
                )
                return

            gid = ctx.guild.id
            if not self._intro_played.get(gid, False):
                try:
                    await self._play_intro_disnake(ch)
                except Exception as e:
                    print(f"[intro] local intro failed (music prefix): {e}")
                finally:
                    self._intro_played[gid] = True

            # ensure a native VC isn't lingering
            if ctx.guild.voice_client and ctx.guild.voice_client.__class__.__name__ == "VoiceClient":
                try:
                    await ctx.guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            await self._connect(ctx.guild, ch)  # Lavalink connect (mafic)
            player = self._get_player(ctx.guild)
        try:
            is_spotify = _is_spotify_url(query)
            if is_spotify:
                results = await player.fetch_tracks(query, search_type=None)
            else:
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
            tracks = results.tracks[:100]
            for t in tracks:
                self._enqueue(ctx.guild.id, t, getattr(ctx.author, "id", None))
            track = tracks[0]
            await ctx.send(
                embed=disnake.Embed(
                    title="Playlist Queued",
                    description=f"Added {len(tracks)} tracks from playlist '{results.name}' (max 100).",
                    color=self.color,
                )
            )
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
            await self._play_track(player, track, ctx.channel, getattr(ctx.author, "id", None))
        self._stopped[ctx.guild.id] = False

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
        self._stopped[ctx.guild.id] = True
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
        
        if not self._check_restriction(inter.guild, ch):
            restricted_channel_id = self.db.get_restriction(inter.guild.id)
            restricted_channel = inter.guild.get_channel(restricted_channel_id)
            channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!",
                    description=f"The bot is restricted to {channel_name}. Please join that channel or use `/utils restrict` to change the restriction.",
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
        gid = inter.guild.id
        self._last_text_channel[gid] = inter.channel
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

            if not self._check_restriction(inter.guild, ch):
                restricted_channel_id = self.db.get_restriction(inter.guild.id)
                restricted_channel = inter.guild.get_channel(restricted_channel_id)
                channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Error!",
                        description=f"The bot is restricted to {channel_name}. Please join that channel or use `/utils restrict` to change the restriction.",
                        color=self.color,
                    ),
                    ephemeral=True,
                )
                return

            gid = inter.guild.id
            if not self._intro_played.get(gid, False):
                try:
                    await self._play_intro_disnake(ch)
                except Exception as e:
                    print(f"[intro] local intro failed (music slash): {e}")
                finally:
                    self._intro_played[gid] = True

            # ensure a native VC isn't lingering
            if inter.guild.voice_client and inter.guild.voice_client.__class__.__name__ == "VoiceClient":
                try:
                    await inter.guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            await self._connect(inter.guild, ch)  # Lavalink connect (mafic)
            player = self._get_player(inter.guild)
        try:
            is_spotify = _is_spotify_url(query)
            if is_spotify:
                results = await player.fetch_tracks(query, search_type=None)
            else:
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
            tracks = results.tracks[:100]
            for t in tracks:
                self._enqueue(inter.guild.id, t, getattr(inter.author, "id", None))
            track = tracks[0]
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Playlist Queued",
                    description=f"Added {len(tracks)} tracks from playlist '{results.name}' (max 100).",
                    color=self.color,
                ),
                ephemeral=True,
            )
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
            await self._play_track(player, track, inter.channel, getattr(inter.author, "id", None))
        self._stopped[inter.guild.id] = False

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
        self._stopped[inter.guild.id] = True
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
        guild_id = event.player.guild.id
        if self._stopped.get(guild_id):
            return
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

    @commands.group(name="radio", invoke_without_command=True)
    async def radio_group(self, ctx: commands.Context):
        stations = "\n".join([f"`{key}` - {info['name']}" for key, info in RADIO_STATIONS.items()])
        await ctx.send(
            embed=disnake.Embed(
                title="Radio Stations",
                description=f"Available stations:\n{stations}\n\nUse `>radio play <station>` to play a station.",
                color=self.color,
            )
        )

    @radio_group.command(name="play")
    async def radio_play_prefix(self, ctx: commands.Context, *, station: str = None):
        if not station:
            stations = "\n".join([f"`{key}` - {info['name']}" for key, info in RADIO_STATIONS.items()])
            await ctx.send(
                embed=disnake.Embed(
                    title="Radio Stations",
                    description=f"Available stations:\n{stations}",
                    color=self.color,
                )
            )
            return
        
        station_key = station.lower()
        if station_key not in RADIO_STATIONS:
            await ctx.send(
                embed=disnake.Embed(
                    title="Station not found",
                    description=f"Station `{station}` not available.",
                    color=self.color,
                )
            )
            return
        
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

            if not self._check_restriction(ctx.guild, ch):
                restricted_channel_id = self.db.get_restriction(ctx.guild.id)
                restricted_channel = ctx.guild.get_channel(restricted_channel_id)
                channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
                await ctx.send(
                    embed=disnake.Embed(
                        title="Error!",
                        description=f"The bot is restricted to {channel_name}. Please join that channel or use `>utils restrict` to change the restriction.",
                        color=self.color,
                    )
                )
                return

            gid = ctx.guild.id
            if not self._intro_played.get(gid, False):
                try:
                    await self._play_intro_disnake(ch)
                except Exception as e:
                    print(f"[intro] local intro failed: {e}")
                finally:
                    self._intro_played[gid] = True

            # ensure we're not still connected with a native VC
            if ctx.guild.voice_client and ctx.guild.voice_client.__class__.__name__ == "VoiceClient":
                try:
                    await ctx.guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            await self._connect(ctx.guild, ch)
            player = self._get_player(ctx.guild)
        
        station_info = RADIO_STATIONS[station_key]
        try:
            results = await player.fetch_tracks(station_info["url"])
            if isinstance(results, list) and results:
                track = results[0]
                gid = ctx.guild.id

                await player.set_volume(0)
                await player.play(track, start_time=0)
                self._current[gid] = track
                self._current_req[gid] = getattr(ctx.author, "id", None)
                self._last[gid] = track

                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    return

                for vol in (20, 40, 60, 80, 100):
                    await player.set_volume(vol)
                    await asyncio.sleep(0.12)

                await ctx.send(
                    embed=disnake.Embed(
                        title="Now Playing Radio",
                        description=f"**{station_info['name']}**\n{station_info['description']}",
                        color=self.color,
                    )
                )
            else:
                await ctx.send(
                    embed=disnake.Embed(
                        title="Radio Error",
                        description=f"Could not connect to {station_info['name']}.",
                        color=self.color,
                    )
                )
        except Exception as e:
            await ctx.send(
                embed=disnake.Embed(
                    title="Radio Error",
                    description=f"Failed to play {station_info['name']}: {e}",
                    color=self.color,
                )
            )

    @commands.slash_command(name="radio", description="Radio controls", dm_permission=False)
    async def radio_slash(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @radio_slash.sub_command(name="play", description="Play a radio station")
    async def radio_play_slash(self, inter: disnake.ApplicationCommandInteraction, station: str = None):
        if not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!", description="Guild only.", color=self.color
                ),
                ephemeral=True,
            )
            return
        
        if not station:
            stations = "\n".join([f"`{key}` - {info['name']}" for key, info in RADIO_STATIONS.items()])
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Radio Stations",
                    description=f"Available stations:\n{stations}",
                    color=self.color,
                )
            )
            return
        
        station_key = station.lower()
        if station_key not in RADIO_STATIONS:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Station not found",
                    description=f"Station `{station}` not available.",
                    color=self.color,
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
            
            if not self._check_restriction(inter.guild, ch):
                restricted_channel_id = self.db.get_restriction(inter.guild.id)
                restricted_channel = inter.guild.get_channel(restricted_channel_id)
                channel_name = restricted_channel.name if restricted_channel else "Unknown Channel"
                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Error!",
                        description=f"The bot is restricted to {channel_name}. Please join that channel or use `/utils restrict` to change the restriction.",
                        color=self.color,
                    ),
                    ephemeral=True,
                )
                return
            
            gid = inter.guild.id
            if not self._intro_played.get(gid, False):
                try:
                    await self._play_intro_disnake(ch)
                except Exception as e:
                    print(f"[intro] local intro failed (slash): {e}")
                finally:
                    self._intro_played[gid] = True

            # ensure we're not still connected with a native VC
            if inter.guild.voice_client and inter.guild.voice_client.__class__.__name__ == "VoiceClient":
                try:
                    await inter.guild.voice_client.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            await self._connect(inter.guild, ch)
            player = self._get_player(inter.guild)
        
        station_info = RADIO_STATIONS[station_key]
        try:
            results = await player.fetch_tracks(station_info["url"])
            if isinstance(results, list) and results:
                track = results[0]
                gid = inter.guild.id

                await player.set_volume(0)
                await player.play(track, start_time=0)
                self._current[gid] = track
                self._current_req[gid] = getattr(inter.author, "id", None)
                self._last[gid] = track

                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    return

                for vol in (20, 40, 60, 80, 100):
                    await player.set_volume(vol)
                    await asyncio.sleep(0.12)

                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Now Playing Radio",
                        description=f"**{station_info['name']}**\n{station_info['description']}",
                        color=self.color,
                    )
                )
            else:
                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Radio Error",
                        description=f"Could not connect to {station_info['name']}.",
                        color=self.color,
                    ),
                    ephemeral=True,
                )
        except Exception as e:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Radio Error",
                    description=f"Failed to play {station_info['name']}: {e}",
                    color=self.color,
                ),
                ephemeral=True,
            )

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
