# utils/commands.py
import disnake
from disnake.ext import commands

try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml

CONFIG_PATH = "config.toml"

def _load_color() -> int:
    with open(CONFIG_PATH, "rb") as f:
        cfg = toml.load(f)
    return int(cfg.get("embed color", 0x8BC6E8))

class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = _load_color()

    @commands.group(name="utils", invoke_without_command=True)
    async def utils_group(self, ctx):
        ms = round(self.bot.latency * 1000)
        await ctx.send(embed=disnake.Embed(title="Pong!", description=f"Latency: {ms} ms", color=self.color))

    @utils_group.command(name="ping")
    async def ping_prefix(self, ctx):
        ms = round(self.bot.latency * 1000)
        await ctx.send(embed=disnake.Embed(title="Pong!", description=f"Latency: `{ms}` ms", color=self.color))

    @commands.slash_command(name="utils")
    async def utils_slash(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @utils_slash.sub_command(name="ping")
    async def ping_slash(self, inter: disnake.ApplicationCommandInteraction):
        ms = round(self.bot.latency * 1000)
        await inter.response.send_message(embed=disnake.Embed(title="Pong!", description=f"Latency: `{ms}` ms", color=self.color))

def setup(bot):
    bot.add_cog(Utils(bot))
