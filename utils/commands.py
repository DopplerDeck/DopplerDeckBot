# utils/commands.py
import disnake
from disnake.ext import commands
from database import RestrictionDB

try:
    import tomllib as toml
except ModuleNotFoundError:
    import tomli as toml

CONFIG_PATH = "config.toml"

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

def setup(bot):
    bot.add_cog(Utils(bot))
