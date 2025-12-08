# cogs/profile.py
"""
Profile Cog for Merlin Realm Royz
Features:
- Shows user stats: Level, XP, Messages, Aura, Streak
- Uses LevelCog DB directly
- Displays progress bar towards next level
- Avatar & polished embed
"""

import discord
from discord.ext import commands
from logger import logger
from database import get_user, add_user
import time

# Progress bar characters
PROGRESS_FILLED = "█"
PROGRESS_EMPTY = "░"
PROGRESS_WIDTH = 18

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._profile_cache = {}  # uid -> (expiry, embed/file)
        self.CACHE_TTL = 30
        logger.info("[PROFILE] Profile cog initialized.")

    async def _ensure_user_exists(self, user_id: str):
        """Ensure the user has a DB row."""
        try:
            await add_user(user_id)
        except Exception as e:
            logger.exception("Failed to ensure user exists: %s", e)

    async def _get_user_stats(self, user_id: str):
        """Fetch user XP, level, aura, streak, messages from DB."""
        row = await get_user(user_id)
        if not row:
            return None
        # user_id, xp, level, messages, aura, streak_count, last_streak_claim
        return {
            "xp": int(row[1] or 0),
            "level": int(row[2] or 1),
            "messages": int(row[3] or 0),
            "aura": int(row[4] or 0),
            "streak": int(row[5] or 0)
        }

    def _progress_bar(self, frac: float) -> str:
        filled = int(round(frac * PROGRESS_WIDTH))
        empty = PROGRESS_WIDTH - filled
        return f"{PROGRESS_FILLED * filled}{PROGRESS_EMPTY * empty} {int(frac * 100)}%"

    def _calc_progress(self, xp: int, level: int) -> float:
        """Return fraction progress to next level."""
        min_xp = (level - 1) ** 2 * 10
        next_xp = level ** 2 * 10
        if next_xp == min_xp:
            return 0.0
        return max(0.0, min(1.0, (xp - min_xp) / (next_xp - min_xp)))

    @commands.command(
        name="profile",
        aliases=["rank", "stats", "xpinfo"],
        help="Displays your Realm Royz profile stats."
    )
    async def profile(self, ctx: commands.Context, member: discord.Member = None):
        """Show profile for yourself or another member."""
        member = member or ctx.author
        uid = str(member.id)

        # Check cache
        cached = self._profile_cache.get(uid)
        if cached and cached[0] > time.time():
            await ctx.send(embed=cached[1])
            return

        await self._ensure_user_exists(uid)
        stats = await self._get_user_stats(uid)
        if not stats:
            return await ctx.send("⚠ No profile found. Try chatting first to create a profile.")

        frac = self._calc_progress(stats["xp"], stats["level"])
        progress_bar = self._progress_bar(frac)

        embed = discord.Embed(
            title=f"🌌 {member.display_name}'s Realm Profile",
            color=discord.Color.teal()
        )

        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except:
            pass

        embed.add_field(name="⭐ Level", value=f"```{stats['level']}```", inline=True)
        embed.add_field(name="🔥 XP", value=f"```{stats['xp']}```", inline=True)
        embed.add_field(name="💬 Messages", value=f"```{stats['messages']}```", inline=True)
        embed.add_field(name="✨ Aura", value=f"```{stats['aura']}```", inline=True)
        embed.add_field(name="📆 Streak", value=f"```{stats['streak']} day(s)```", inline=True)
        embed.add_field(name="Progress to next level", value=progress_bar, inline=False)

        embed.set_footer(text=f"🌙 Merlin Royz Profile | ID: {uid}")
        embed.set_author(name=self.bot.user.name, icon_url=getattr(self.bot.user.avatar, "url", None))

        # Cache for 30s
        self._profile_cache[uid] = (time.time() + self.CACHE_TTL, embed)

        await ctx.send(embed=embed)
        logger.info("Sent profile for %s (%s)", member.display_name, uid)

    # Owner debug
    @commands.command(name="profiledebug")
    @commands.is_owner()
    async def profiledebug(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        uid = str(member.id)
        stats = await self._get_user_stats(uid)
        await ctx.send(f"**Debug {member.display_name}**\n{stats}")

    async def cog_unload(self):
        self._profile_cache.clear()
        logger.info("[PROFILE] Cog unloaded.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    logger.info("[PROFILE] Cog setup complete.")
