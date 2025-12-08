# cogs/stats_tracker.py
"""
Stats Tracker Cog for Merlin Royz
Features:
- Tracks server-wide stats per user: messages, commands used, daily streaks
- Provides leaderboard commands for top stats
- Debug logging for development and issue tracking
"""

import discord
from discord.ext import commands
from logger import logger
from database import DB_PATH, add_user, get_user, update_user
import aiosqlite
import time

class StatsTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._msg_count_cache = {}  # temporary cache to avoid DB writes on every message
        logger.info("[STATS] StatsTracker cog initialized.")

    # ----------------------------
    # Message listener
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        uid = str(message.author.id)
        await add_user(uid)
        # Increment message count in DB
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
            await db.execute("UPDATE users SET messages = messages + 1 WHERE user_id = ?", (uid,))
            await db.commit()
        # Update cache
        self._msg_count_cache[uid] = self._msg_count_cache.get(uid, 0) + 1
        logger.debug(f"[STATS] Incremented message count for {uid}: {self._msg_count_cache[uid]}")

    # ----------------------------
    # Commands
    # ----------------------------
    @commands.command(name="stats", aliases=["userstats"])
    async def stats(self, ctx: commands.Context, member: discord.Member = None):
        """Show detailed stats for a user"""
        try:
            member = member or ctx.author
            uid = str(member.id)
            await add_user(uid)
            row = await get_user(uid)
            if not row:
                return await ctx.send("No stats found for this user.")

            _, xp, level, messages, aura, streak, last_claim = row
            embed = discord.Embed(
                title=f"📊 Stats for {member.display_name}",
                color=discord.Color.green()
            )
            embed.add_field(name="💬 Messages", value=f"{messages}", inline=True)
            embed.add_field(name="⭐ Level", value=f"{level}", inline=True)
            embed.add_field(name="🧿 Aura", value=f"{aura}", inline=True)
            embed.add_field(name="🔥 XP", value=f"{xp}", inline=True)
            embed.add_field(name="📆 Streak", value=f"{streak} day(s)", inline=True)

            try:
                embed.set_thumbnail(url=member.display_avatar.url)
            except Exception as e:
                logger.warning(f"[STATS] Failed to load avatar for {uid}: {e}")

            await ctx.send(embed=embed)
            logger.debug(f"[STATS CMD] Sent stats for {uid}")
        except Exception as e:
            logger.exception(f"[STATS] Error in stats command for user {member.id if member else 'unknown'}: {e}")
            await ctx.send("⚠ An error occurred while fetching stats.")

    @commands.command(name="statsleaderboard", aliases=["statslb", "topstats"])
    async def stats_leaderboard(self, ctx: commands.Context):
        """Show top 10 users by message count"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id, messages FROM users ORDER BY messages DESC LIMIT 10")
                rows = await cur.fetchall()
            if not rows:
                return await ctx.send("No stats available.")
            lines = []
            for i, r in enumerate(rows, start=1):
                uid, msg_count = r
                member = ctx.guild.get_member(int(uid))
                name = member.display_name if member else f"User {uid}"
                lines.append(f"**#{i}** {name} — {msg_count} messages")
            await ctx.send("\n".join(lines))
            logger.debug("[STATS LB] Sent stats leaderboard.")
        except Exception as e:
            logger.exception(f"[STATS] Failed to generate leaderboard: {e}")
            await ctx.send("⚠ Failed to generate leaderboard.")

    # ----------------------------
    # Debug command
    # ----------------------------
    @commands.command(name="statsdebug")
    @commands.is_owner()
    async def stats_debug(self, ctx: commands.Context, member: discord.Member = None):
        """Owner-only debug: shows DB row and cache info"""
        member = member or ctx.author
        uid = str(member.id)
        row = await get_user(uid)
        cache = self._msg_count_cache.get(uid, 0)
        await ctx.send(f"**Debug for {member.display_name}**\nDB Row: {row}\nCache: {cache}")
        logger.debug(f"[STATS DEBUG] User {uid}, DB Row: {row}, Cache: {cache}")

    # ----------------------------
    # Lifecycle
    # ----------------------------
    async def cog_unload(self):
        self._msg_count_cache.clear()
        logger.info("[STATS] StatsTracker cog unloaded.")

# ----------------------------
# Setup
# ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(StatsTracker(bot))
    logger.info("[STATS] StatsTracker cog setup complete.")
