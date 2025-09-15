import disnake
from disnake.ext import commands
import aiohttp
import asyncio
import os
from typing import Optional

TOPGG_TOKEN = os.getenv("TOPGG_TOKEN")


class TopGG(commands.Cog):
    """Handles interactions with the top.gg API"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.token = TOPGG_TOKEN
        self.headers = {"Authorization": self.token} if self.token else None
        self.topgg_api = "https://top.gg/api"
        self.update_stats_task = None

    def cog_unload(self):
        """Cancel the stats updating task when the cog is unloaded"""
        if self.update_stats_task:
            self.update_stats_task.cancel()

    async def post_guild_count(self):
        """Post the guild count to top.gg"""
        if not self.token:
            return
            
        guild_count = len(self.bot.guilds)
        url = f"{self.topgg_api}/bots/{self.bot.user.id}/stats"
        payload = {"server_count": guild_count}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self.headers) as resp:
                    if resp.status == 200:
                        print(f"Posted server count to top.gg: {guild_count}")
                    else:
                        text = await resp.text()
                        print(f"Failed to post server count to top.gg: {resp.status} - {text}")
        except Exception as e:
            print(f"Error posting server count to top.gg: {e}")

    async def update_stats_loop(self):
        """Background task to update stats every 5 minutes"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.post_guild_count()
            await asyncio.sleep(300) 

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the stats updating task when the bot is ready"""
        if not self.update_stats_task and self.token:
            # Start the background task for periodic updates
            self.update_stats_task = self.bot.loop.create_task(self.update_stats_loop())
            print("Started top.gg stats posting task")
        elif not self.token:
            print("No top.gg token found. Stats posting disabled.")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Update stats when the bot joins a guild"""
        await self.post_guild_count()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Update stats when the bot leaves a guild"""
        await self.post_guild_count()

    @commands.slash_command(name="votes", description="Check your votes on top.gg")
    async def votes(self, inter: disnake.ApplicationCommandInteraction):
        """Check if a user has voted for the bot on top.gg"""
        if not self.token:
            await inter.response.send_message(
                "This feature is not available as the bot is not configured with top.gg",
                ephemeral=True
            )
            return

        user_id = inter.author.id
        url = f"{self.topgg_api}/bots/{self.bot.user.id}/check?userId={user_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        voted = bool(data.get("voted", 0))
                        
                        if voted:
                            await inter.response.send_message(
                                "Thank you for voting for the bot! Your support is appreciated! ❤️",
                                ephemeral=True
                            )
                        else:
                            bot_id = self.bot.user.id
                            await inter.response.send_message(
                                f"You haven't voted for the bot yet! You can vote at https://top.gg/bot/{bot_id}/vote",
                                ephemeral=True
                            )
                    else:
                        await inter.response.send_message(
                            "Failed to check vote status. Please try again later.",
                            ephemeral=True
                        )
        except Exception as e:
            await inter.response.send_message(
                f"An error occurred while checking your vote status: {e}",
                ephemeral=True
            )
    
    @commands.group(name="topgg", invoke_without_command=True)
    async def topgg_group(self, ctx: commands.Context):
        await ctx.send(
            embed=disnake.Embed(
                title="TopGG Commands",
                description="Use:\n`>topgg votes` - Check if you have voted for the bot",
                color=0x08bc6e8
            )
        )
    
    @topgg_group.command(name="votes")
    async def votes_prefix(self, ctx: commands.Context):
        """Check if you have voted for the bot on top.gg"""
        if not self.token:
            await ctx.send(
                embed=disnake.Embed(
                    title="Not Available",
                    description="This feature is not available as the bot is not configured with top.gg",
                    color=0x08bc6e8
                )
            )
            return

        user_id = ctx.author.id
        url = f"{self.topgg_api}/bots/{self.bot.user.id}/check?userId={user_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        voted = bool(data.get("voted", 0))
                        
                        if voted:
                            await ctx.send(
                                embed=disnake.Embed(
                                    title="Thanks for Voting!",
                                    description="Thank you for voting for the bot! Your support is appreciated! ❤️",
                                    color=0x08bc6e8
                                )
                            )
                        else:
                            bot_id = self.bot.user.id
                            await ctx.send(
                                embed=disnake.Embed(
                                    title="Vote for the Bot",
                                    description=f"You haven't voted for the bot yet! You can vote at https://top.gg/bot/{bot_id}/vote",
                                    color=0x08bc6e8
                                )
                            )
                    else:
                        await ctx.send(
                            embed=disnake.Embed(
                                title="Error",
                                description="Failed to check vote status. Please try again later.",
                                color=0x08bc6e8
                            )
                        )
        except Exception as e:
            await ctx.send(
                embed=disnake.Embed(
                    title="Error",
                    description=f"An error occurred while checking your vote status: {e}",
                    color=0x08bc6e8
                )
            )


def setup(bot):
    if TOPGG_TOKEN:
        cog = TopGG(bot)
        bot.add_cog(cog)
        # Post server count immediately on cog load/reload
        bot.loop.create_task(cog.post_guild_count())
        print("TopGG cog loaded and posting server count")
    else:
        print("TopGG token not found. Cog not loaded.")