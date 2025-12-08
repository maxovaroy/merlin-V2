# cogs/stats_tracker.py
"""
Stats Tracker Cog for Merlin Royz
---------------------------------
Tracks messages, commands, and reactions per user.
Provides a leaderboard command to see top users.
Includes debug logging.
"""

import discord
from discord.ext import commands
import aiosqlite
import time
from logger import logger
from database import DB_PATH, add_user

# Default leaderboard limit
LEADERBOARD_LIMIT = 10

class StatsTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("[STATS] StatsTracker cog initialized.")

    # ----------------------
    # Internal DB helpers
    # ----------------------
    async def _ensure_user_stats(self, user_id: str):
        """Ensure user exists in DB with stats fields."""
        try:
            await add_user(user_id)  # ensures base user row exists
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS stats (
                        user_id TEXT PRIMARY KEY,
                        messages INTEGER DEFAULT 0,
                        commands INTEGER DEFAULT 0,
                        reactions INTEGER DEFAULT 0
                    )
                    """
                )
                await db.execute(
                    "INSERT OR IGNORE INTO stats(user_id) VALUES(?)",
                    (user_id,)
                )
                await db.commit()
        except Exception as e:
            logger.exception("[STATS] Failed to ensure stats row: %s", e)

    async def _increment_stat(self, user_id: str, field: str, amount: int = 1):
        """Increment a stat field for a user."""
        try:
            await self._ensure_user_stats(user_id)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    f"UPDATE stats SET {field} = {field} + ? WHERE user_id = ?",
                    (amount, user_id)
                )
                await db.commit()
            logger.debug(f"[STATS] Incremented {field} for {user_id} by {amount}")
        except Exception as e:
            logger.exception(f"[STATS] Failed to increment {field} for {user_id}: {e}")

    # ----------------------
    # Listeners
    # ----------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self._increment_stat(str(message.author.id), "messages")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or not reaction.message.guild:
            return
        await self._increment_stat(str(user.id), "reactions")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        if ctx.author.bot:
            return
        await self._increment_stat(str(ctx.author.id), "commands")

    # ----------------------
    # Commands
    # ----------------------
    @commands.command(
        name="statsleaderboard",
        aliases=["statslb", "slb"],
        help="Shows the top users by activity. Optional filter: messages, commands, reactions"
    )
    async def stats_leaderboard(self, ctx: commands.Context, filter_type: str = "messages"):
        filter_type = filter_type.lower()
        if filter_type not in ("messages", "commands", "reactions"):
            return await ctx.reply("Invalid filter! Use messages, commands, or reactions.")

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                f"SELECT user_id, {filter_type} FROM stats ORDER BY {filter_type} DESC LIMIT ?",
                (LEADERBOARD_LIMIT,)
            )
            rows = await cur.fetchall()

        if not rows:
            return await ctx.reply("No stats data available yet.")

        embed = discord.Embed(
            title=f"📊 Top {LEADERBOARD_LIMIT} Users — {filter_type.capitalize()}",
            color=discord.Color.blue()
        )

        lines = []
        for i, row in enumerate(rows, start=1):
            user_id, count = row
            member = ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User {user_id}"
            lines.append(f"**#{i}** {name} — {count}")

        embed.description = "\n".join(lines)
        await ctx.reply(embed=embed)

    @commands.command(
        name="statsdebug",
        help="Owner-only: view raw stats for a member"
    )
    @commands.is_owner()
    async def stats_debug(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        await self._ensure_user_stats(str(member.id))
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT * FROM stats WHERE user_id = ?", (str(member.id),))
            row = await cur.fetchone()
        await ctx.reply(f"**Stats Debug for {member.display_name}:** {row}")

    # ----------------------
    # Lifecycle
    # ----------------------
    async def cog_load(self):
        logger.info("[STATS] StatsTracker cog loaded.")

    async def cog_unload(self):
        logger.info("[STATS] StatsTracker cog unloaded.")

# ----------------------
# Setup
# ----------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(StatsTracker(bot))
    logger.info("[STATS] StatsTracker cog setup complete.")
