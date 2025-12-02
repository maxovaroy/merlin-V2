# cogs/level.py
"""
Level System Cog for Merlin Royz

Features:
- Award XP for messages
- Level calculation and progress bars
- Admin commands (setxp, setlevel, synclevels, exportcsv)
- Leaderboard
- Level-up announcements
- Role rewards per level
- Testing/debug commands

#GUIDE: Change XP_PER_MESSAGE, LEVEL_XP_BASE, COOLDOWN_SECONDS here to adjust leveling.
"""

import asyncio
import aiosqlite
import discord
from discord.ext import commands
from typing import Optional, Tuple, Dict
from math import floor
import time
from io import StringIO
import csv
import os

# ----------------------------
# CONFIGURATION - CHANGE THESE IF NEEDED
# ----------------------------
COMMAND_PREFIX = "!"  # used to skip awarding XP for commands
XP_PER_MESSAGE = 6  # XP awarded per message
LEVEL_XP_BASE = 100  # XP per level
COOLDOWN_SECONDS = 12  # cooldown between XP gains per user per guild
LEVEL_UP_CHANNEL_ID = 1305771250693705818  # if set to a channel name, level-up messages go there

# Role rewards mapping (guild_id -> {level:int -> role_id:int})
REWARD_ROLES: Dict[int, Dict[int, int]] = {}

# Fallback DB path
DB_PATH = "database.db"  # default, can be overridden by your database.py

# ----------------------------
# IMPORT YOUR DATABASE HELPERS
# ----------------------------
try:
    from database import add_user, update_user, get_user, DB_PATH as DATABASE_PATH
except Exception:
    # fallback to local DB
    DATABASE_PATH = DB_PATH
    try:
        from database import add_user, update_user, get_user
    except Exception:
        raise ImportError("Database helper functions add_user, update_user, get_user not found!")

# ----------------------------
# LOGGING
# ----------------------------
try:
    from logger import logger
except Exception:
    import logging
    logger = logging.getLogger("LevelCog")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[LEVEL] %(asctime)s %(levelname)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ----------------------------
# HELPER FUNCTIONS FOR LEVELS
# ----------------------------
def xp_to_level(xp: int) -> int:
    """Convert XP to level."""
    if xp < 0:
        xp = 0
    return (xp // LEVEL_XP_BASE) + 1

def xp_for_level(level: int) -> int:
    """Return minimum XP required for a level."""
    if level < 1:
        level = 1
    return (level - 1) * LEVEL_XP_BASE

def progress_fraction(xp: int) -> Tuple[int,int,float]:
    """Return (current_xp_in_level, xp_needed_for_next, fraction)."""
    level = xp_to_level(xp)
    cur = xp - xp_for_level(level)
    needed = LEVEL_XP_BASE
    frac = cur / needed if needed > 0 else 0.0
    return cur, needed, frac

def nice_progress_bar(frac: float, width: int = 18) -> str:
    """Return a simple text progress bar."""
    filled = int(round(frac * width))
    empty = width - filled
    return "█" * filled + "░" * empty

# ----------------------------
# LEVEL COG
# ----------------------------
class LevelCog(commands.Cog, name="LevelSystem"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_awarded: Dict[Tuple[int,int], float] = {}  # cooldown timestamps
        self._cache: Dict[Tuple[int,int], Tuple[int,int,float]] = {}  # cache xp/level
        self._lock = asyncio.Lock()
        logger.info("LevelCog initialized. Using DB: %s", DATABASE_PATH)

    # ----------------------------
    # DATABASE HELPERS
    # ----------------------------
    async def _raw_db_execute(self, query: str, params: Tuple = ()):
        """Direct query to DB."""
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                return rows

    async def get_user_level_data(self, guild_id: int, user_id: int) -> Tuple[int,int]:
        """Return (xp, level) for a user. Cached for 5 seconds."""
        key = (guild_id, user_id)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[2] < 5.0):
            return cached[0], cached[1]

        # try DB helper
        try:
            row = await get_user(str(user_id))
        except Exception:
            row = None

        if row:
            _, xp, level, *_ = row
            self._cache[key] = (xp, level, now)
            return xp, level

        # fallback raw DB query
        try:
            rows = await self._raw_db_execute("SELECT xp, level FROM users WHERE user_id = ?", (str(user_id),))
            if rows:
                xp = int(rows[0]["xp"])
                level = int(rows[0]["level"])
                self._cache[key] = (xp, level, now)
                return xp, level
        except Exception:
            pass

        # default
        self._cache[key] = (0,1,now)
        return 0,1

    async def _ensure_set_user_values(self, user_id: str, xp: int, level: int):
        """Ensure xp/level are set if update_user fails."""
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (xp,level,user_id))
                await db.commit()
        except Exception:
            logger.exception("Failed _ensure_set_user_values for %s", user_id)

    # ----------------------------
    # CORE XP/LEVEL FUNCTIONS
    # ----------------------------
    async def force_add_xp(self, guild_id: int, user_id: int, amount: int) -> Tuple[int,int,bool]:
        """
        Add XP to a user (ignores cooldowns, for testing/admin purposes).
        Returns (new_xp, new_level, leveled_up_bool)
        """
        xp, level = await self.get_user_level_data(guild_id, user_id)
        new_xp = xp + amount
        new_level = xp_to_level(new_xp)
        leveled = new_level > level
        await self._ensure_set_user_values(str(user_id), new_xp, new_level)
        return new_xp, new_level, leveled

    async def add_xp(self, guild_id: int, user_id: int, amount: int, message: discord.Message = None) -> Tuple[int,int,bool]:
        """
        Normal XP addition, respects cooldown.
        Returns (new_xp, new_level, leveled_up_bool)
        """
        now = time.monotonic()
        key = (guild_id, user_id)
        last = self._last_awarded.get(key, 0)
        if now - last < COOLDOWN_SECONDS:
            xp, level = await self.get_user_level_data(guild_id, user_id)
            return xp, level, False

        self._last_awarded[key] = now
        new_xp, new_level, leveled = await self.force_add_xp(guild_id, user_id, amount)

        # Send level-up message if leveled up
        if leveled:
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    channel = guild.get_channel(LEVEL_UP_CHANNEL_ID)  # using ID, not name
                    if channel:
                        member = guild.get_member(user_id)
                        await channel.send(f"{member.mention}, leveled up to **Level {new_level}**! 🎉")
            except Exception as e:
                if hasattr(self.bot, "logger"):
                    self.bot.logger.error(f"[LevelCog] Failed to send level-up message: {e}")
                else:
                    print(f"[LevelCog] Failed to send level-up message: {e}")


    # ----------------------------
    # MESSAGE LISTENER
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Award XP for non-command messages."""
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(COMMAND_PREFIX):
            return
        await self.add_xp(message.guild.id, message.author.id, XP_PER_MESSAGE, message)


    # ----------------------------
    # ROLE REWARD HANDLER
    # ----------------------------
    async def _maybe_assign_role_reward(self, guild: discord.Guild, member: discord.Member, level: int):
        guild_rewards = REWARD_ROLES.get(guild.id)
        if not guild_rewards:
            return
        role_id = guild_rewards.get(level)
        if not role_id:
            return
        role = guild.get_role(role_id)
        if not role:
            return
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Level reward")
                logger.info("Assigned role %s to %s for reaching level %s", role.name, member.display_name, level)
            except Exception:
                logger.exception("Failed to assign reward role.")

    # ----------------------------
    # ADMIN / TEST COMMANDS
    # ----------------------------
    @commands.command(name="lvlup")
    @commands.has_permissions(administrator=True)
    async def lvlup(self, ctx: commands.Context, member: discord.Member, levels: int = 1):
        """Instantly level up a user by N levels."""
        xp, level = await self.get_user_level_data(ctx.guild.id, member.id)
        new_level = level + max(levels, 1)
        new_xp = xp_for_level(new_level)
        await self._ensure_set_user_values(str(member.id), new_xp, new_level)
        await ctx.send(f"✅ {member.display_name} leveled up to **Level {new_level}**!")

    @commands.command(name="lvlup_test")
    async def lvlup_test(self, ctx: commands.Context):
        """Instantly level up yourself by 1 level (testing)."""
        user = ctx.author
        xp, level = await self.get_user_level_data(ctx.guild.id, user.id)
        new_level = level + 1
        new_xp = xp_for_level(new_level)
        await self._ensure_set_user_values(str(user.id), new_xp, new_level)

    # Send message in LEVEL_UP_CHANNEL_ID
    channel = ctx.guild.get_channel(LEVEL_UP_CHANNEL_ID)
    if channel:
        await channel.send(f"{user.mention}, leveled up to **Level {new_level}**! 🎉")
    else:
        # fallback to ctx if channel not found
        await ctx.send(f"✅ {user.display_name} instantly leveled up to **Level {new_level}**!")


    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def setxp(self, ctx: commands.Context, member: discord.Member, xp: int):
        level = xp_to_level(xp)
        await self._ensure_set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s XP to {xp} (Level {level})")

    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def setlevel(self, ctx: commands.Context, member: discord.Member, level: int):
        xp = xp_for_level(level)
        await self._ensure_set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s Level to {level} ({xp} XP)")

    @commands.command(name="debuglevel")
    @commands.is_owner()
    async def debuglevel(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show XP/level and cooldown for debugging."""
        if member is None:
            member = ctx.author
        xp, level = await self.get_user_level_data(ctx.guild.id, member.id)
        key = (ctx.guild.id, member.id)
        last = self._last_awarded.get(key)
        await ctx.send(f"Debug: {member.display_name} — XP={xp}, Level={level}, LastAwarded={last}")

    # ----------------------------
    # LEADERBOARD
    # ----------------------------
    @commands.command(name="leaderboard", aliases=["lb","top"])
    async def leaderboard(self, ctx: commands.Context, limit: int = 10):
        """Show top N users."""
        limit = max(1, min(limit, 25))
        rows = await self._raw_db_execute(
            "SELECT user_id,xp,level FROM users ORDER BY xp DESC LIMIT ?", (limit,)
        )
        if not rows:
            await ctx.send("No leaderboard data.")
            return
        embed = discord.Embed(title=f"{ctx.guild.name} — Leaderboard", color=discord.Color.blurple())
        lines = []
        for i, r in enumerate(rows, start=1):
            uid, xp, lvl = int(r["user_id"]), int(r["xp"]), int(r["level"])
            member = ctx.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            lines.append(f"**#{i}** • {name} — Level {lvl} ({xp} XP)")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    # ----------------------------
    # EXPORT CSV
    # ----------------------------
    @commands.command(name="exportcsv")
    @commands.has_permissions(administrator=True)
    async def exportcsv(self, ctx: commands.Context, limit: int = 1000):
        rows = await self._raw_db_execute(
            "SELECT user_id,xp,level,messages,aura FROM users ORDER BY xp DESC LIMIT ?", (limit,)
        )
        if not rows:
            await ctx.send("No data to export.")
            return
        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["user_id","xp","level","messages","aura"])
        for r in rows:
            writer.writerow([r["user_id"], r["xp"], r["level"], r.get("messages",""), r.get("aura","")])
        csv_buffer.seek(0)
        file = discord.File(fp=StringIO(csv_buffer.read()), filename="leaderboard.csv")
        await ctx.send("CSV Export:", file=file)

# ----------------------------
# COG SETUP
# ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
    logger.info("LevelCog loaded.")
