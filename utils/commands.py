import disnake
from disnake.ext import commands, tasks
from database import RestrictionDB
import asyncio
import logging

try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml

CONFIG_PATH = "config.toml"
logger = logging.getLogger("DopplerDeck")

def _load_color() -> int:
    with open(CONFIG_PATH, "rb") as f:
        cfg = toml.load(f)
    return int(cfg.get("embed color", 0x8BC6E8))

class RestrictView(disnake.ui.View):
    def __init__(self, bot, guild_id, color):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.color = color
        self.db = RestrictionDB()
    
    @disnake.ui.select(
        placeholder="Choose an option...",
        options=[
            disnake.SelectOption(label="Change voice channel", value="change"),
            disnake.SelectOption(label="Remove restriction", value="remove")
        ]
    )
    async def restrict_select(self, select: disnake.ui.Select, interaction: disnake.MessageInteraction):
        if select.values[0] == "change":
            voice_channels = [ch for ch in interaction.guild.voice_channels]
            if not voice_channels:
                await interaction.response.send_message(
                    embed=disnake.Embed(
                        title="Error!",
                        description="No voice channels found in this server.",
                        color=self.color
                    ),
                    ephemeral=True
                )
                return
            
            options = []
            for ch in voice_channels[:25]:
                options.append(disnake.SelectOption(
                    label=ch.name,
                    value=str(ch.id),
                    description=f"ID: {ch.id}"
                ))
            
            view = VoiceChannelSelectView(self.bot, self.guild_id, self.color, options)
            embed = disnake.Embed(
                title="Select Voice Channel",
                description="Choose which voice channel to restrict the bot to:",
                color=self.color
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        elif select.values[0] == "remove":
            self.db.remove_restriction(self.guild_id)
            await interaction.response.send_message(
                embed=disnake.Embed(
                    title="Restriction Removed",
                    description="The bot can now join any voice channel.",
                    color=self.color
                ),
                ephemeral=True
            )

class VoiceChannelSelectView(disnake.ui.View):
    def __init__(self, bot, guild_id, color, options):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.color = color
        self.db = RestrictionDB()
        
        select = disnake.ui.Select(
            placeholder="Choose a voice channel...",
            options=options
        )
        select.callback = self.on_select
        self.add_item(select)
    
    async def on_select(self, interaction: disnake.MessageInteraction):
        channel_id = int(interaction.data["values"][0])
        channel = interaction.guild.get_channel(channel_id)
        
        if not channel:
            await interaction.response.send_message(
                embed=disnake.Embed(
                    title="Error!",
                    description="Channel not found.",
                    color=self.color
                ),
                ephemeral=True
            )
            return
        
        self.db.set_restriction(self.guild_id, channel_id)
        await interaction.response.send_message(
            embed=disnake.Embed(
                title="Restriction Updated",
                description=f"The bot is now restricted to {channel.mention}",
                color=self.color
            ),
            ephemeral=True
        )

class Utils(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.color = _load_color()
        self.db = RestrictionDB()
        self.keep_alive_task = None

    def cog_unload(self):
        if self.keep_alive_task:
            self.keep_alive_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.keep_alive_task:
            self.keep_alive_heartbeat.start()
            logger.info("Started keep-alive heartbeat task")

    @tasks.loop(seconds=40)
    async def keep_alive_heartbeat(self):
        """Send a heartbeat to keep the connection alive and prevent timeouts"""
        try:
            await self.bot.change_presence(activity=self.bot.activity, status=self.bot.status)
            logger.debug("Sent keep-alive heartbeat")
        except Exception as e:
            logger.warning(f"Error in keep-alive heartbeat: {e}")

    @keep_alive_heartbeat.before_loop
    async def before_keep_alive(self):
        await self.bot.wait_until_ready()

    @commands.group(name="utils", invoke_without_command=True)
    async def utils_group(self, ctx):
        ms = round(self.bot.latency * 1000)
        await ctx.send(embed=disnake.Embed(title="Pong!", description=f"Latency: {ms} ms", color=self.color))

    @utils_group.command(name="ping")
    async def ping_prefix(self, ctx):
        ms = round(self.bot.latency * 1000)
        await ctx.send(embed=disnake.Embed(title="Pong!", description=f"Latency: `{ms}` ms", color=self.color))

    @utils_group.command(name="restrict")
    async def restrict_prefix(self, ctx):
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.send(
                embed=disnake.Embed(
                    title="Error!",
                    description="You need the 'Manage Server' permission to use this command.",
                    color=self.color
                )
            )
            return
        
        if self.db.has_restriction(ctx.guild.id):
            channel_id = self.db.get_restriction(ctx.guild.id)
            channel = ctx.guild.get_channel(channel_id)
            channel_name = channel.name if channel else "Unknown Channel"
            
            embed = disnake.Embed(
                title="Voice Channel Restriction",
                description=f"Bot is currently restricted to: **{channel_name}**",
                color=self.color
            )
            view = RestrictView(self.bot, ctx.guild.id, self.color)
            await ctx.send(embed=embed, view=view)
        else:
            voice_channels = [ch for ch in ctx.guild.voice_channels]
            if not voice_channels:
                await ctx.send(
                    embed=disnake.Embed(
                        title="Error!",
                        description="No voice channels found in this server.",
                        color=self.color
                    )
                )
                return
            
            options = []
            for ch in voice_channels[:25]:
                options.append(disnake.SelectOption(
                    label=ch.name,
                    value=str(ch.id),
                    description=f"ID: {ch.id}"
                ))
            
            view = VoiceChannelSelectView(self.bot, ctx.guild.id, self.color, options)
            embed = disnake.Embed(
                title="Select Voice Channel",
                description="Choose which voice channel to restrict the bot to:",
                color=self.color
            )
            await ctx.send(embed=embed, view=view)

    @utils_group.command(name="servers")
    async def servers_prefix(self, ctx):
        guilds = getattr(self.bot, "guilds", [])
        guild_count = len(guilds)

        total_members = 0
        for g in guilds:
            count = getattr(g, "member_count", None)
            if count is None:
                try:
                    count = len(g.members)
                except Exception:
                    count = 0
            total_members += count

        embed = disnake.Embed(
            title="Server Stats",
            description=f"Servers: **{guild_count}**\nTotal Members: **{total_members}**",
            color=self.color
        )
        await ctx.send(embed=embed)

    @commands.slash_command(name="utils")
    async def utils_slash(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @utils_slash.sub_command(name="ping")
    async def ping_slash(self, inter: disnake.ApplicationCommandInteraction):
        ms = round(self.bot.latency * 1000)
        await inter.response.send_message(embed=disnake.Embed(title="Pong!", description=f"Latency: `{ms}` ms", color=self.color))

    @utils_slash.sub_command(name="restrict", description="Restrict bot to specific voice channel")
    async def restrict_slash(self, inter: disnake.ApplicationCommandInteraction):
        if not isinstance(inter.author, disnake.Member):
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!",
                    description="This command can only be used in servers.",
                    color=self.color
                ),
                ephemeral=True
            )
            return
        
        if not inter.author.guild_permissions.manage_guild:
            await inter.response.send_message(
                embed=disnake.Embed(
                    title="Error!",
                    description="You need the 'Manage Server' permission to use this command.",
                    color=self.color
                ),
                ephemeral=True
            )
            return
        
        if self.db.has_restriction(inter.guild.id):
            channel_id = self.db.get_restriction(inter.guild.id)
            channel = inter.guild.get_channel(channel_id)
            channel_name = channel.name if channel else "Unknown Channel"
            
            embed = disnake.Embed(
                title="Voice Channel Restriction",
                description=f"Bot is currently restricted to: **{channel_name}**",
                color=self.color
            )
            view = RestrictView(self.bot, inter.guild.id, self.color)
            await inter.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            voice_channels = [ch for ch in inter.guild.voice_channels]
            if not voice_channels:
                await inter.response.send_message(
                    embed=disnake.Embed(
                        title="Error!",
                        description="No voice channels found in this server.",
                        color=self.color
                    ),
                    ephemeral=True
                )
                return
            
            options = []
            for ch in voice_channels[:25]:
                options.append(disnake.SelectOption(
                    label=ch.name,
                    value=str(ch.id),
                    description=f"ID: {ch.id}"
                ))
            
            view = VoiceChannelSelectView(self.bot, inter.guild.id, self.color, options)
            embed = disnake.Embed(
                title="Select Voice Channel",
                description="Choose which voice channel to restrict the bot to:",
                color=self.color
            )
            await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    @utils_slash.sub_command(name="servers", description="Show how many servers the bot is in and the total members across them")
    async def servers_slash(self, inter: disnake.ApplicationCommandInteraction):
        guilds = getattr(self.bot, "guilds", [])
        guild_count = len(guilds)

        # prefering member count from discord
        total_members = 0
        for g in guilds:
            count = getattr(g, "member_count", None)
            if count is None:
                # fall back = getting membercount from cache
                try:
                    count = len(g.members)
                except Exception:
                    count = 0
            total_members += int(count) # no i cant code for shit

        embed = disnake.Embed(title="Servers & Members", color=self.color)
        embed.add_field(name="Servers", value=f"{guild_count:,}")
        embed.add_field(name="Total members", value=f"{total_members:,}")
        await inter.response.send_message(embed=embed)


def setup(bot):
    bot.add_cog(Utils(bot))
