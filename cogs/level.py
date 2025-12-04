# cogs/level.py
"""
Level System Cog for Merlin Royz (Rewritten - Full, Documented, 300+ lines)
Author: ChatGPT (for max_5037)
Version: 1.0
-----------------------------------------------------------------------
Overview / Purpose
-----------------------------------------------------------------------
This cog implements a complete leveling system integrated with your
existing `database.py` helpers (if present). It awards XP for messages,
keeps cooldowns, detects level-ups, and posts a single embed message that
contains a single fixed GIF (no random GIFs). All existing admin commands
and utilities (setxp, setlevel, synclevels, exportcsv, leaderboard, debug)
are included and preserved.

The file is intentionally verbose and heavily commented (# GUIDE) so you
can easily follow, maintain, and modify behavior later.

Key points:
- Uses database.add_user, database.update_user, database.get_user when available.
- Falls back to direct sqlite queries if DB helpers are not importable.
- Uses the same leveling math as your database's update_user (sqrt-based) via
  db_level_from_xp() so levels remain consistent.
- Single fixed GIF (set LEVEL_UP_GIF) used in the embed's main image.
- No sounds, no animations, no random GIF lists.
- Includes an improved profile embed (feature #2) and file-wide guides (feature #10).
-----------------------------------------------------------------------
USAGE
-----------------------------------------------------------------------
Place this file at: cogs/level.py
Load with:
    await bot.load_extension("cogs.level")
or ensure setup() is called by your bot loader.

Commands:
  - lvlup (admin)     : instantly increase levels for a member
  - lvlup_test        : self-test level up for author
  - setxp (admin)     : set a user's xp
  - setlevel (admin)  : set a user's level
  - debuglevel        : owner-only debug info
  - leaderboard / lb  : show top users by XP
  - exportcsv         : admin CSV export
  - synclevels        : recalc levels from xp
  - profile / level   : profile / level display (profile has progress bar & stats)
-----------------------------------------------------------------------
"""

# ----------------------------
# Standard imports
# ----------------------------
import asyncio
import aiosqlite
import discord
from discord.ext import commands
from typing import Optional, Tuple, Dict, List
import math
import time
from io import StringIO, BytesIO
import csv
import logging

# ----------------------------
# CONFIGURATION - edit these
# ----------------------------
COMMAND_PREFIX = "!"                     # messages starting with this won't get XP
XP_PER_MESSAGE = 6                       # XP awarded per message (if using cog-local awarding)
COOLDOWN_SECONDS = 12                    # cooldown between XP gains (per user per guild)
LEVEL_UP_CHANNEL_ID = 1305771250693705818  # channel ID for level-up messages (None => fallback to message channel)
DB_FALLBACK_PATH = "database.db"         # fallback sqlite path if database.py doesn't export DB_PATH

# Single fixed GIF URL (user requested single gif only)
LEVEL_UP_GIF = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWo5eTJ3bW5ocTM0YWZhZzVtbXdyNnJ0YjM1bHhmcXUzMWk1bzNsMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tMH2lSNTy0MX2AYCoz/giphy.gif"

# Role rewards mapping (guild_id -> {level: role_id})
REWARD_ROLES: Dict[int, Dict[int, int]] = {
    # Example:
    # 123456789012345678: {5: 987654321098765432, 10: 876543210987654321}
}

# ----------------------------
# TRY TO IMPORT DATABASE HELPERS (if your database.py exposes them)
# We attempt to import add_user, update_user, get_user, DB_PATH. If the import
# fails we will fallback to raw sqlite operations using DB_FALLBACK_PATH.
# ----------------------------
try:
    from database import add_user, update_user, get_user, DB_PATH as DATABASE_PATH
    DB_IMPORT_OK = True
except Exception:
    try:
        from database import add_user, update_user, get_user
        DATABASE_PATH = DB_FALLBACK_PATH
        DB_IMPORT_OK = True
    except Exception:
        # No database helper functions available; will use raw sqlite calls.
        DATABASE_PATH = DB_FALLBACK_PATH
        add_user = None
        update_user = None
        get_user = None
        DB_IMPORT_OK = False

# ----------------------------
# Logging
# ----------------------------
try:
    from logger import logger
except Exception:
    logger = logging.getLogger("LevelCog")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[LEVEL] %(asctime)s %(levelname)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ----------------------------
# HELPER: LEVEL MATH (keeps in sync with database.update_user)
# ----------------------------
def db_level_from_xp(xp: int) -> int:
    """
    DB level formula that mirrors database.update_user's logic:
      new_level = int(math.sqrt(xp // 10)) + 1
    Keep this consistent with database.py if you change update_user there.
    """
    try:
        return int(math.sqrt(xp // 10)) + 1
    except Exception:
        return 1

def xp_for_level(level: int) -> int:
    """
    Inverse approximation of db_level_from_xp:
    For a desired level L: minimal xp approx = (L-1)^2 * 10
    This is used for admin setlevel/lvlup commands.
    """
    if level < 1:
        level = 1
    return ((level - 1) ** 2) * 10

def nice_progress_bar(frac: float, width: int = 18) -> str:
    """Return a simple text progress bar string of width characters."""
    filled = int(round(frac * width))
    empty = max(0, width - filled)
    return "█" * filled + "░" * empty

# ----------------------------
# Level Cog - main implementation
# ----------------------------
class LevelCog(commands.Cog, name="LevelSystem"):
    """
    LevelCog encapsulates the leveling system.
    # GUIDE: This Cog relies on either database helper functions (recommended)
           OR raw DB queries as fallback.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_awarded: Dict[Tuple[int,int], float] = {}  # (guild_id,user_id) -> timestamp
        self._cache: Dict[Tuple[int,int], Tuple[int,int,float]] = {}  # (guild,user) -> (xp,level,timestamp)
        self._db_lock = asyncio.Lock()  # protect writes to sqlite to reduce locking issues
        logger.info("LevelCog initialized. DB path: %s", DATABASE_PATH)

    # ----------------------------
    # Raw DB helper (read-only) - returns list of aiosqlite.Row
    # ----------------------------
    async def _raw_db_execute(self, query: str, params: Tuple = ()):
        """
        Execute a read query against DATABASE_PATH and return list of rows.
        # GUIDE: Use this for queries if you don't use get_user helper.
        """
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(query, params)
                rows = await cur.fetchall()
                await cur.close()
                return rows
        except Exception:
            logger.exception("Raw DB execute failed: %s %s", query, params)
            return []

    # ----------------------------
    # Get user xp/level (cached)
    # ----------------------------
    async def get_user_level_data(self, guild_id: int, user_id: int) -> Tuple[int,int]:
        """
        Return (xp, level) for user. Uses a short cache (5s) to reduce DB calls.
        If get_user helper exists (from database.py) it will be preferred.
        Fallback: raw query.
        """
        key = (guild_id, user_id)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and (now - cached[2] < 5.0):
            return cached[0], cached[1]

        # Try database helper if present
        row = None
        if get_user:
            try:
                row = await get_user(str(user_id))
            except Exception:
                logger.debug("database.get_user failed, fallback to raw query.")

        if row:
            # database.get_user might return a tuple or sqlite Row
            try:
                # if tuple-like
                xp = int(row[1])
                level = int(row[2])
            except Exception:
                # try mapping style
                try:
                    xp = int(row.get("xp") or row["xp"])
                    level = int(row.get("level") or row["level"])
                except Exception:
                    xp, level = 0, 1
            self._cache[key] = (xp, level, now)
            return xp, level

        # Fallback raw query
        try:
            rows = await self._raw_db_execute("SELECT xp, level FROM users WHERE user_id = ?", (str(user_id),))
            if rows:
                xp = int(rows[0]["xp"])
                level = int(rows[0]["level"])
                self._cache[key] = (xp, level, now)
                return xp, level
        except Exception:
            logger.exception("fallback get_user_level_data raw query failed.")

        # default if nothing found
        self._cache[key] = (0, 1, now)
        return 0, 1

    async def _ensure_set_user_values(self, user_id: str, xp: int, level: int):
        """
        Ensure xp/level are set directly in DB. Used as fallback if update_user fails.
        # GUIDE: This function updates the raw users table. It will create the row if necessary.
        """
        async with self._db_lock:
            try:
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
                    await db.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (xp, level, user_id))
                    await db.commit()
                    logger.debug("Ensured user %s set to xp=%s level=%s", user_id, xp, level)
            except Exception:
                logger.exception("Failed to _ensure_set_user_values for %s", user_id)

    # ----------------------------
    # CORE XP / LEVEL WRITE PATHS
    # ----------------------------
    async def force_add_xp(self, guild_id: int, user_id: int, amount: int) -> Tuple[int,int,bool]:
        """
        Force-add xp to a user (ignores cooldowns). This calls database.update_user if available
        to preserve aura / other side effects. Returns (new_xp, new_level, leveled_bool).
        """
        # Ensure user row exists via helper or raw insert
        if add_user:
            try:
                await add_user(str(user_id))
            except Exception:
                logger.debug("add_user helper failed; proceeding.")

        async with self._db_lock:
            # Prefer using update_user helper so aura logic triggers
            if update_user:
                try:
                    await update_user(str(user_id), xp_gain=amount)
                except Exception:
                    logger.exception("database.update_user failed; falling back to raw increment.")
                    # fallback: increment xp directly
                    try:
                        async with aiosqlite.connect(DATABASE_PATH) as db:
                            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (str(user_id),))
                            await db.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (amount, str(user_id)))
                            await db.commit()
                    except Exception:
                        logger.exception("Fallback raw xp add failed.")
            else:
                # No helper available: perform raw increment
                try:
                    async with aiosqlite.connect(DATABASE_PATH) as db:
                        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (str(user_id),))
                        await db.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (amount, str(user_id)))
                        await db.commit()
                except Exception:
                    logger.exception("Raw fallback xp add failed with no update_user helper.")

        # Fetch fresh values after update
        fresh = None
        if get_user:
            try:
                fresh = await get_user(str(user_id))
            except Exception:
                logger.debug("get_user helper failed during force_add_xp fresh fetch.")
        if fresh:
            try:
                xp = int(fresh[1])
                level = int(fresh[2])
            except Exception:
                # try index/key access
                xp = int(fresh.get("xp") or fresh[1])
                level = int(fresh.get("level") or fresh[2])
        else:
            # raw fallback select
            rows = await self._raw_db_execute("SELECT xp, level FROM users WHERE user_id = ?", (str(user_id),))
            if rows:
                xp = int(rows[0]["xp"])
                level = int(rows[0]["level"])
            else:
                xp, level = 0, 1

        # Get previous cached level to decide leveled_up
        prev_xp, prev_level = await self.get_user_level_data(guild_id, user_id)
        leveled = level > prev_level

        # update cache
        self._cache[(guild_id, user_id)] = (xp, level, time.monotonic())

        return xp, level, leveled

    async def add_xp(self, guild_id: int, user_id: int, amount: int, message: Optional[discord.Message] = None) -> Tuple[int,int,bool]:
        """
        Add XP with cooldown check; returns (xp, level, leveled).
        This is the function typically called by on_message listener.
        """
        now = time.monotonic()
        key = (guild_id, user_id)
        last = self._last_awarded.get(key, 0)
        if now - last < COOLDOWN_SECONDS:
            xp, level = await self.get_user_level_data(guild_id, user_id)
            return xp, level, False

        # mark timestamp immediately to prevent races
        self._last_awarded[key] = now

        # Ensure user row exists. Preferred helper add_user will create if needed.
        if add_user:
            try:
                await add_user(str(user_id))
            except Exception:
                logger.debug("add_user helper failed in add_xp; continuing.")

        # Do the update (this also triggers aura in database.update_user if available)
        xp, level, leveled = await self.force_add_xp(guild_id, user_id, amount)

        # If leveled -> announce (single embed with GIF)
        if leveled:
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # use configured channel or fallback to message channel
                    channel = guild.get_channel(LEVEL_UP_CHANNEL_ID) if LEVEL_UP_CHANNEL_ID else None
                    member = guild.get_member(user_id)

                    # Build embed (Style 1: GIF as main embed image)
                    mention = member.mention if member else f"<@{user_id}>"
                    embed = discord.Embed(
                        title="⚔ LEVEL UP!",
                        description=f"{mention} has reached **Level {level}**!",
                        color=discord.Color.gold()
                    )

                    # Add optional details / progress
                    # compute current xp and xp needed using DB formula semantics
                    # We use cached values (freshly updated above)
                    cur_xp = xp
                    cur_level = level
                    xp_min_for_level = xp_for_level(cur_level)
                    xp_next = xp_for_level(cur_level + 1)
                    xp_into = cur_xp - xp_min_for_level
                    xp_needed = max(1, xp_next - cur_xp)  # avoid zero division

                    frac = xp_into / (xp_next - xp_min_for_level) if (xp_next - xp_min_for_level) > 0 else 0.0
                    bar = nice_progress_bar(frac, width=16)

                    embed.add_field(name="Progress", value=f"{bar} `{xp_into}/{xp_next - xp_min_for_level}`", inline=False)
                    embed.set_image(url=LEVEL_UP_GIF)  # single GIF inside embed (main image)
                    embed.set_footer(text="Keep grinding to grow your legend.")

                    # Send embed in channel or fallback
                    if channel:
                        try:
                            await channel.send(embed=embed)
                        except Exception:
                            # fallback to message channel if provided
                            if message and message.channel:
                                await message.channel.send(embed=embed)
                    else:
                        # no configured channel; prefer using the message's channel if available
                        if message and message.channel:
                            await message.channel.send(embed=embed)
                        else:
                            # as ultimate fallback, attempt to find a default system channel
                            system_ch = guild.system_channel
                            if system_ch:
                                await system_ch.send(embed=embed)
                else:
                    logger.debug("Guild not found for level-up announcement (guild_id=%s)", guild_id)
            except Exception:
                logger.exception("Failed to announce level up for user %s in guild %s", user_id, guild_id)

        # Assign reward role (best-effort)
        if leveled:
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    member = guild.get_member(user_id)
                    if member:
                        await self._maybe_assign_role_reward(guild, member, level)
            except Exception:
                logger.exception("Role reward assignment failed after level-up.")

        return xp, level, leveled

    # ----------------------------
    # MESSAGE LISTENER (awards XP)
    # ----------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Award XP for non-command messages.
        # GUIDE: Messages that begin with COMMAND_PREFIX are ignored.
        """
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(COMMAND_PREFIX):
            return

        try:
            await self.add_xp(message.guild.id, message.author.id, XP_PER_MESSAGE, message)
        except Exception:
            logger.exception("Failed to add xp on_message for user %s", message.author.id)

    # ----------------------------
    # ROLE REWARDS
    # ----------------------------
    async def _maybe_assign_role_reward(self, guild: discord.Guild, member: discord.Member, level: int):
        """
        Assign a configured role to the member if a mapping exists in REWARD_ROLES.
        Mapping example:
          REWARD_ROLES = {
              123456789012345678: {5: 987654321098765432, 10: 876543210987654321}
          }
        """
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
    # ADMIN / DEBUG COMMANDS
    # ----------------------------
    @commands.command(name="lvlup")
    @commands.has_permissions(administrator=True)
    async def lvlup(self, ctx: commands.Context, member: discord.Member, levels: int = 1):
        """
        Instant level-up a member by N levels (admin).
        # GUIDE: This computes target xp using inverse formula and writes direct values.
        """
        if levels < 1:
            return await ctx.send("Provide a positive number of levels.")
        xp, level = await self.get_user_level_data(ctx.guild.id, member.id)
        target_level = level + max(levels, 1)
        target_xp = xp_for_level(target_level)
        await self._set_user_values(str(member.id), target_xp, target_level)
        await ctx.send(f"✅ {member.display_name} set to Level **{target_level}** (xp={target_xp}).")

    @commands.command(name="lvlup_test")
    async def lvlup_test(self, ctx: commands.Context):
        """
        Test-level: level up yourself by 1 (for testing).
        """
        user = ctx.author
        xp, level = await self.get_user_level_data(ctx.guild.id, user.id)
        target_level = level + 1
        target_xp = xp_for_level(target_level)
        await self._set_user_values(str(user.id), target_xp, target_level)

        # announce in configured channel or fallback to ctx
        guild = ctx.guild
        if guild and LEVEL_UP_CHANNEL_ID:
            channel = guild.get_channel(LEVEL_UP_CHANNEL_ID)
            if channel:
                await channel.send(f"{user.mention}, leveled up to **Level {target_level}**! 🎉")
                return
        await ctx.send(f"✅ {user.display_name} instantly leveled up to **Level {target_level}**!")

    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def setxp(self, ctx: commands.Context, member: discord.Member, xp: int):
        """
        Admin: set a user's XP directly.
        # GUIDE: Level is recalculated using db_level_from_xp() for consistency.
        """
        if xp < 0:
            return await ctx.send("XP must be >= 0.")
        level = db_level_from_xp(xp)
        await self._set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s XP to {xp} (Level {level})")

    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def setlevel(self, ctx: commands.Context, member: discord.Member, level: int):
        """
        Admin: set level (approximate xp is set using xp_for_level()).
        """
        if level < 1:
            return await ctx.send("Level must be >= 1.")
        xp = xp_for_level(level)
        await self._set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s Level to {level} ({xp} XP)")

    async def _set_user_values(self, user_id: str, xp: int, level: int):
        """
        Helper to set xp and level directly. Uses DB lock to avoid concurrency issues.
        After update, clears any relevant cache entries.
        """
        async with self._db_lock:
            try:
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
                    await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, user_id))
                    await db.commit()
                    logger.debug("Set values for %s -> xp=%s level=%s", user_id, xp, level)
            except Exception:
                logger.exception("Failed to set user values for %s", user_id)

        # Clear cached entries for the user
        keys_to_remove = [k for k in self._cache.keys() if k[1] == int(user_id)]
        for k in keys_to_remove:
            self._cache.pop(k, None)

    @commands.command(name="debuglevel")
    @commands.is_owner()
    async def debuglevel(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """
        Owner-only debug command to inspect XP/level and last-awarded cooldown timestamp.
        """
        if member is None:
            member = ctx.author
        xp, level = await self.get_user_level_data(ctx.guild.id, member.id)
        key = (ctx.guild.id, member.id)
        last = self._last_awarded.get(key)
        await ctx.send(f"Debug: {member.display_name} — XP={xp}, Level={level}, LastAwarded={last}")

    # ----------------------------
    # LEADERBOARD
    # ----------------------------
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context, limit: int = 10):
        """
        Show the top N users in the guild by XP. Limit up to 25 for safety.
        """
        limit = max(1, min(limit, 25))
        rows = await self._raw_db_execute(
            "SELECT user_id, xp, level FROM users WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (str(ctx.guild.id), limit)
        )
        if not rows:
            return await ctx.send("No leaderboard data.")

        embed = discord.Embed(title=f"{ctx.guild.name} — Leaderboard", color=discord.Color.blurple())
        lines = []
        for i, r in enumerate(rows, start=1):
            try:
                uid = int(r["user_id"])
            except Exception:
                uid = int(r[0])
            xp = int(r["xp"]) if "xp" in r.keys() else int(r[1])
            lvl = int(r["level"]) if "level" in r.keys() else int(r[2])
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
        """
        Export top N users as CSV (admin).
        # GUIDE: This generates a CSV in-memory and sends as a Discord file.
        """
        limit = max(1, min(limit, 50000))
        rows = await self._raw_db_execute(
            "SELECT user_id,xp,level,messages,aura FROM users WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (str(ctx.guild.id), limit)
        )
        if not rows:
            return await ctx.send("No data to export.")

        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["user_id","xp","level","messages","aura"])
        for r in rows:
            # r may be aiosqlite.Row
            uid = r["user_id"] if "user_id" in r.keys() else r[0]
            xp = r["xp"] if "xp" in r.keys() else r[1]
            level = r["level"] if "level" in r.keys() else r[2]
            messages = r["messages"] if "messages" in r.keys() else (r[3] if len(r) > 3 else "")
            aura = r["aura"] if "aura" in r.keys() else (r[4] if len(r) > 4 else "")
            writer.writerow([uid, xp, level, messages, aura])

        csv_content = csv_buffer.getvalue().encode("utf-8")
        bio = BytesIO(csv_content)
        bio.seek(0)
        file = discord.File(fp=bio, filename="leaderboard.csv")
        await ctx.send("CSV Export:", file=file)

    # ----------------------------
    # SYNC LEVELS
    # ----------------------------
    @commands.command(name="synclevels")
    @commands.has_permissions(administrator=True)
    async def synclevels(self, ctx: commands.Context, limit: int = 5000):
        """
        Recalculate and set level column for users based on stored XP.
        WARNING: This uses db_level_from_xp() logic which mirrors database.update_user.
        """
        await ctx.send("Starting level sync... This may take a while for many users.")
        rows = await self._raw_db_execute("SELECT user_id, xp FROM users LIMIT ?", (limit,))
        updated = 0
        async with self._db_lock:
            try:
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    for r in rows:
                        uid = r["user_id"] if "user_id" in r.keys() else r[0]
                        xp = int(r["xp"] if "xp" in r.keys() else r[1])
                        new_level = db_level_from_xp(xp)
                        await db.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, uid))
                        updated += 1
                    await db.commit()
            except Exception:
                logger.exception("synclevels failed.")
        self._cache.clear()
        await ctx.send(f"Sync complete — updated {updated} users (limit {limit}).")

    # ----------------------------
    # PROFILE / RANK CARD (Feature #2)
    # ----------------------------
    @commands.command(name="profile", aliases=["level","rank"])
    async def profile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """
        Display a detailed profile embed for a user including:
         - Level
         - XP and progress bar
         - Messages count
         - Aura (if present)
        # GUIDE: This function builds a fairly rich embed that is readable and mobile-friendly.
        """
        if member is None:
            member = ctx.author

        # Attempt to fetch via get_user helper first
        data = None
        if get_user:
            try:
                data = await get_user(str(member.id))
            except Exception:
                logger.debug("get_user helper failed in profile; falling back to raw query.")

        if data:
            try:
                xp = int(data[1])
                level = int(data[2])
                messages = int(data[3]) if len(data) > 3 else 0
                aura = int(data[4]) if len(data) > 4 else 0
            except Exception:
                # if data is a mapping-like row
                xp = int(data.get("xp") or data[1])
                level = int(data.get("level") or data[2])
                messages = int(data.get("messages") or (data[3] if len(data) > 3 else 0))
                aura = int(data.get("aura") or (data[4] if len(data) > 4 else 0))
        else:
            # fallback raw select
            rows = await self._raw_db_execute("SELECT xp, level, messages, aura FROM users WHERE user_id = ?", (str(member.id),))
            if rows:
                row = rows[0]
                xp = int(row["xp"])
                level = int(row["level"])
                messages = int(row.get("messages", 0) or 0)
                aura = int(row.get("aura", 0) or 0)
            else:
                xp, level, messages, aura = 0, 1, 0, 0

        # compute progress
        current_level_min_xp = xp_for_level(level)
        next_level_xp = xp_for_level(level + 1)
        xp_into = xp - current_level_min_xp
        xp_needed = next_level_xp - current_level_min_xp if next_level_xp - current_level_min_xp > 0 else 1
        frac = max(0.0, min(1.0, xp_into / xp_needed))
        bar = nice_progress_bar(frac, width=18)

        # Build profile embed
        embed = discord.Embed(title=f"{member.display_name}'s Profile", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=member.display_avatar.url if hasattr(member, "display_avatar") else member.avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp} XP", inline=True)
        embed.add_field(name="Messages", value=str(messages), inline=True)
        embed.add_field(name="Aura", value=str(aura), inline=True)
        embed.add_field(name="Progress", value=f"{bar} `{xp_into}/{xp_needed}`", inline=False)
        embed.set_footer(text="Keep contributing to grow your power.")
        await ctx.send(embed=embed)

    # ----------------------------
    # COG CLEANUP
    # ----------------------------
    async def cog_unload(self):
        """Cleanup cache on unload."""
        logger.info("LevelCog unloading — clearing caches.")
        self._cache.clear()
        self._last_awarded.clear()

# ----------------------------
# COG SETUP
# ----------------------------
async def setup(bot: commands.Bot):
    """Standard setup entrypoint for adding cog."""
    await bot.add_cog(LevelCog(bot))
    logger.info("LevelCog loaded.")
