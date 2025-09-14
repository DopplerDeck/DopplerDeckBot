import os
import logging
import disnake
from disnake.ext import commands, tasks
from lavalink import ensure_lavalink, NODE_CONFIG
from database import RestrictionDB

try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("DopplerDeck")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

OWNER_ID = 1362053982444454119
CONFIG_PATH = "config.toml"

def get_token() -> str:
    token = os.getenv("prod") or os.getenv("PROD")
    if not token:
        raise RuntimeError("Bot token not found. Set environment variable 'prod' (or 'PROD').")
    return token

def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as f:
        return toml.load(f)

def embed_color_from(cfg: dict) -> int:
    return int(cfg.get("embed color", 0x8BC6E8))

def configured_modules(cfg: dict) -> list[str]:
    c = cfg.get("cogs", {})
    m = c.get("modules", [])
    return [str(x) for x in m]

def normalize_target(name: str, allowed: list[str]) -> str | None:
    if name in allowed:
        return name
    if "." not in name:
        for cand in (
            f"{name}.commands",
            f"music.{name}",
            f"music.{name}.commands",
            f"utils.{name}",
            f"utils.{name}.commands",
        ):
            if cand in allowed:
                return cand
    return None

def error_embed(color: int, message: str) -> disnake.Embed:
    return disnake.Embed(title="Error!", description=message, color=color)

class DopplerDeckBot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._presence_started = False
        self._lavalink_started = False
        self._boot_loaded = False

    async def on_ready(self):
        log.info("Logged in as %s (%s) — in %d guild(s).", str(self.user), self.user.id if self.user else "unknown", len(self.guilds))
        
        try:
            db = RestrictionDB()
            log.info("Database initialized successfully")
        except Exception as exc:
            log.error("Database initialization failed: %r", exc)
        
        if not self._presence_started:
            self.update_presence.start()
            self._presence_started = True
            await self._refresh_presence()
        if not self._lavalink_started:
            log.info("Lavalink config: identifier=%s host=%s port=%s secure=%s", NODE_CONFIG["identifier"], NODE_CONFIG["host"], NODE_CONFIG["port"], NODE_CONFIG["secure"])
            try:
                node = await ensure_lavalink(self)
                log.info("Lavalink connected: %s", node.label)
            except Exception as exc:
                log.error("Lavalink connection failed: %r", exc)
            self._lavalink_started = True
        if not self._boot_loaded:
            try:
                cfg = load_config()
                for mod in configured_modules(cfg):
                    try:
                        self.load_extension(mod)
                        log.info("Loaded extension: %s", mod)
                    except Exception as exc:
                        log.warning("Failed to load %s: %r", mod, exc)
            except Exception as exc:
                log.error("Config load failure: %r", exc)
            self._boot_loaded = True

    async def on_voice_state_update(self, member, before, after):
        try:
            if member.id == self.user.id:
                await self._refresh_presence()
        except Exception as exc:
            log.warning("Error refreshing presence on voice_state_update: %r", exc)

    def _voice_connection_count(self) -> int:
        return len(self.voice_clients)

    async def _refresh_presence(self):
        count = self._voice_connection_count()
        label = f"{count} voice channel{'s' if count != 1 else ''}"
        activity = disnake.Activity(type=disnake.ActivityType.watching, name=label)
        await self.change_presence(status=disnake.Status.online, activity=activity)
        log.debug("Presence updated: watching %s", label)

    @tasks.loop(seconds=20)
    async def update_presence(self):
        try:
            await self._refresh_presence()
        except Exception as exc:
            log.warning("Presence update failed: %r", exc)

    @update_presence.before_loop
    async def before_update_presence(self):
        await self.wait_until_ready()

def main():
    intents = disnake.Intents.all()
    bot = DopplerDeckBot(
        command_prefix=">",
        intents=intents,
        allowed_mentions=disnake.AllowedMentions.none(),
        command_sync_flags=commands.CommandSyncFlags.default(),
        help_command=None
    )

    def is_owner_ctx(ctx):
        return ctx.author and ctx.author.id == OWNER_ID

    @bot.command(name="refresh")
    @commands.check(is_owner_ctx)
    async def _refresh(ctx, module: str):
        try:
            cfg = load_config()
            color = embed_color_from(cfg)
            mods = configured_modules(cfg)
            target = normalize_target(module, mods)
            if not target:
                msg = f"Command raised an exception: ModuleNotFoundError: No module named '{module}'"
                await ctx.send(embed=error_embed(color, msg))
                return
            try:
                bot.reload_extension(target)
                await ctx.send(f"refreshed: {target}")
            except Exception as exc:
                msg = f"Command raised an exception: {exc.__class__.__name__}: {exc}"
                await ctx.send(embed=error_embed(color, msg))
        except Exception as exc:
            try:
                color = embed_color_from(load_config())
            except Exception:
                color = 0x8BC6E8
            msg = f"Command raised an exception: {exc.__class__.__name__}: {exc}"
            await ctx.send(embed=error_embed(color, msg))

    @bot.command(name="load")
    @commands.check(is_owner_ctx)
    async def _load(ctx, module: str):
        try:
            cfg = load_config()
            color = embed_color_from(cfg)
            mods = configured_modules(cfg)
            target = normalize_target(module, mods)
            if not target:
                msg = f"Command raised an exception: ModuleNotFoundError: No module named '{module}'"
                await ctx.send(embed=error_embed(color, msg))
                return
            try:
                bot.load_extension(target)
                await ctx.send(f"loaded {target}")
            except commands.ExtensionAlreadyLoaded:
                await ctx.send(f"already loaded {target}")
            except Exception as exc:
                msg = f"Command raised an exception: {exc.__class__.__name__}: {exc}"
                await ctx.send(embed=error_embed(color, msg))
        except Exception as exc:
            try:
                color = embed_color_from(load_config())
            except Exception:
                color = 0x8BC6E8
            msg = f"Command raised an exception: {exc.__class__.__name__}: {exc}"
            await ctx.send(embed=error_embed(color, msg))

    bot.run(get_token())

if __name__ == "__main__":
    main()
