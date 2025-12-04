import asyncio
import aiosqlite
import discord
from discord.ext import commands
import math, time, csv, logging
from typing import Optional, Dict, List
from io import StringIO, BytesIO

# ---------------- CONFIG ----------------
COMMAND_PREFIX = "!"
XP_PER_MESSAGE = 6
COOLDOWN_SECONDS = 3
LEVEL_UP_CHANNEL_ID = 1305771250693705818  # <== Your channel where level-up embeds go

# Single GIF for level-up (no randomization)
LEVEL_UP_GIF = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWo5eTJ3bW5ocTM0YWZhZzVtbXdyNnJ0YjM1bHhmcXUzMWk1bzNsMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tMH2lSNTy0MX2AYCoz/giphy.gif"

# Optional role rewards per level
REWARD_ROLES: Dict[int, Dict[int, int]] = {}

# ---------------- DB IMPORT ----------------
try:
    from database import add_user, update_user, get_user, DB_PATH as DATABASE_PATH
except:
    from database import add_user, update_user, get_user
    DATABASE_PATH = "database.db"

# ---------------- LOGGING ----------------
try:
    from logger import logger
except:
    logger = logging.getLogger("LevelCog")
    if not logger.handlers:
        h = logging.StreamHandler()
        f = logging.Formatter('[LEVEL] %(asctime)s %(levelname)s %(message)s')
        h.setFormatter(f)
        logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ---------------- UTILITY FUNCTIONS ----------------
def db_level_from_xp(xp: int) -> int:
    """Convert XP to level (database formula)"""
    try:
        return int(math.sqrt(xp // 10)) + 1
    except:
        return 1

def nice_progress_bar(frac: float, width=18) -> str:
    """Return a text progress bar"""
    return "█" * int(frac * width) + "░" * (width - int(frac * width))


# ====================== MAIN COG ======================
class LevelCog(commands.Cog):
    """
    Level System Cog
    ----------------
    Features:
    - Adds XP per message
    - Cooldown support
    - Level calculation
    - Level-up embed with single GIF
    - Admin commands: lvlup, setxp, setlevel
    - Leaderboard, CSV export
    - Sync levels
    """

    def __init__(self, bot):
        self.bot = bot
        self._last_awarded = {}  # cooldown tracker
        self._cache = {}  # XP/Level cache
        self._db_lock = asyncio.Lock()
        logger.info("LevelCog Loaded with single GIF support.")

    # ---------------- RAW DB ----------------
    async def _raw_db_execute(self, query, params=()):
        """Execute raw DB query (SELECT)"""
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(query, params)
                rows = await cur.fetchall()
                await cur.close()
                return rows
        except:
            return []

    # ---------------- GET LEVEL DATA ----------------
    async def get_user_level_data(self, guild_id: int, user_id: int):
        """
        Returns (xp, level) for a user from cache or DB
        """
        key = (guild_id, user_id)
        now = time.monotonic()

        # use cache if recent
        if key in self._cache and now - self._cache[key][2] < 5:
            return self._cache[key][0], self._cache[key][1]

        data = await get_user(str(user_id))
        if data:
            xp, level = int(data[1]), int(data[2])
        else:
            xp, level = 0, 1

        self._cache[key] = (xp, level, now)
        return xp, level

    # ---------------- CACHE UPDATE ----------------
    async def _update_cache(self, guild_id, user_id, xp, level):
        """Update XP/Level cache"""
        self._cache[(guild_id, user_id)] = (xp, level, time.monotonic())

    # ---------------- ADD XP ----------------
    async def force_add_xp(self, guild_id, user_id, amount):
        """Add XP ignoring cooldown"""
        await add_user(str(user_id))
        async with self._db_lock:
            await update_user(str(user_id), xp_gain=amount)

        row = await get_user(str(user_id))
        xp, level = int(row[1]), int(row[2])
        px, pl = await self.get_user_level_data(guild_id, user_id)
        leveled = level > pl
        await self._update_cache(guild_id, user_id, xp, level)
        return xp, level, leveled

    async def add_xp(self, guild_id, user_id, amount, message=None):
        """
        Add XP to user with cooldown check
        Fires level-up embed if level increased
        """
        now = time.monotonic()
        key = (guild_id, user_id)

        # check cooldown
        if key in self._last_awarded and now - self._last_awarded[key] < COOLDOWN_SECONDS:
            return await self.get_user_level_data(guild_id, user_id) + (False,)

        self._last_awarded[key] = now
        xp, level, leveled = await self.force_add_xp(guild_id, user_id, amount)

        # ---------------- LEVEL-UP EVENT ----------------
        if leveled:
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(LEVEL_UP_CHANNEL_ID)

                embed = discord.Embed(
                    title=f"⚔ LEVEL UP!",
                    description=f"<@{user_id}> has reached **Level {level}**!",
                    color=discord.Color.gold()
                )
                embed.set_image(url=LEVEL_UP_GIF)
                embed.set_footer(text="Your journey continues...")

                if channel:
                    await channel.send(embed=embed)
                elif message:
                    await message.channel.send(embed=embed)

        return xp, level, leveled

    # ---------------- MESSAGE LISTENER ----------------
    @commands.Cog.listener()
    async def on_message(self, message):
        """Award XP on user messages"""
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(COMMAND_PREFIX):
            return
        try:
            await self.add_xp(message.guild.id, message.author.id, XP_PER_MESSAGE, message)
        except Exception:
            pass

    # ---------------- ROLE REWARD HANDLER ----------------
    async def _maybe_assign_role_reward(self, guild: discord.Guild, member: discord.Member, level: int):
        """Assign role rewards based on REWARD_ROLES"""
        guild_rewards = REWARD_ROLES.get(guild.id)
        if not guild_rewards:
            return
        role_id = guild_rewards.get(level)
        if not role_id:
            return
        role = guild.get_role(role_id)
        if not role or role in member.roles:
            return
        try:
            await member.add_roles(role, reason="Level reward")
            logger.info("Assigned role %s to %s for reaching level %s", role.name, member.display_name, level)
        except Exception:
            logger.exception("Failed to assign reward role.")

    # ---------------- ADMIN COMMANDS ----------------
    @commands.command(name="lvlup")
    @commands.has_permissions(administrator=True)
    async def lvlup(self, ctx: commands.Context, member: discord.Member, levels: int = 1):
        """
        Instantly level up a user by N levels (default 1)
        Sends the level-up embed in LEVEL_UP_CHANNEL_ID
        """
        if levels < 1:
            await ctx.send("❌ Please provide a positive number of levels.")
            return

        guild_id = ctx.guild.id
        user_id = member.id

        # Get current XP/level
        xp, level = await self.get_user_level_data(guild_id, user_id)

        # Calculate target level and XP
        target_level = level + levels
        target_xp = ((target_level - 1) ** 2) * 10  # same formula as db_level_from_xp

        # Update DB
        await self._set_user_values(str(user_id), target_xp, target_level)

        # -------------------------------
        # Manually trigger level-up embed
        # -------------------------------
        leveled = True  # since we just increased levels manually
        guild = ctx.guild
        channel = guild.get_channel(LEVEL_UP_CHANNEL_ID)

        embed = discord.Embed(
            title=f"⚔ LEVEL UP!",
            description=f"<@{user_id}> has reached **Level {target_level}**!",
            color=discord.Color.gold()
        )
        embed.set_image(url="https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWo5eTJ3bW5ocTM0YWZhZzVtbXdyNnJ0YjM1bHhmcXUzMWk1bzNsMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tMH2lSNTy0MX2AYCoz/giphy.gif")  # your single GIF
        embed.set_footer(text="Your journey continues...")

        if channel:
            await channel.send(embed=embed)
        else:
            await ctx.send(embed=embed)

        # Confirmation in command
        await ctx.send(f"✅ {member.display_name} is now Level **{target_level}** ({target_xp} XP)!")


    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def setxp(self, ctx: commands.Context, member: discord.Member, xp: int):
        """Set a user's XP (admin)"""
        if xp < 0:
            await ctx.send("XP must be >= 0.")
            return
        level = db_level_from_xp(xp)
        await self._set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s XP to {xp} (Level {level})")

    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def setlevel(self, ctx: commands.Context, member: discord.Member, level: int):
        """Set a user's level (admin)"""
        if level < 1:
            await ctx.send("Level must be >= 1.")
            return
        xp = ((level - 1) ** 2) * 10
        await self._set_user_values(str(member.id), xp, level)
        await ctx.send(f"Set {member.display_name}'s Level to {level} ({xp} XP)")

    async def _set_user_values(self, user_id: str, xp: int, level: int):
        """Directly set XP/level in DB"""
        async with self._db_lock:
            try:
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
                    await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, user_id))
                    await db.commit()
                    logger.debug("Set values for %s -> xp=%s level=%s", user_id, xp, level)
            except Exception:
                logger.exception("Failed to set user values for %s", user_id)
        # clear cache
        keys_to_remove = [k for k in self._cache.keys() if k[1] == int(user_id)]
        for k in keys_to_remove:
            self._cache.pop(k, None)

    # ---------------- LEADERBOARD ----------------
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context, limit: int = 10):
        """Show top N users by XP"""
        limit = max(1, min(limit, 25))
        rows = await self._raw_db_execute("SELECT user_id,xp,level FROM users ORDER BY xp DESC LIMIT ?", (limit,))
        if not rows:
            await ctx.send("No leaderboard data.")
            return
        embed = discord.Embed(title=f"{ctx.guild.name} — Leaderboard", color=discord.Color.blurple())
        desc_lines: List[str] = []
        for i, r in enumerate(rows, start=1):
            try: uid = int(r["user_id"])
            except: uid = int(r[0])
            xp = int(r["xp"]) if "xp" in r.keys() else int(r[1])
            lvl = int(r["level"]) if "level" in r.keys() else int(r[2])
            member = ctx.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            desc_lines.append(f"**#{i}** • {name} — Level {lvl} ({xp} XP)")
        embed.description = "\n".join(desc_lines)
        await ctx.send(embed=embed)

    # ---------------- CSV EXPORT ----------------
    @commands.command(name="exportcsv")
    @commands.has_permissions(administrator=True)
    async def exportcsv(self, ctx: commands.Context, limit: int = 1000):
        """Export top N users to CSV"""
        limit = max(1, min(limit, 50000))
        rows = await self._raw_db_execute("SELECT user_id,xp,level,messages,aura FROM users ORDER BY xp DESC LIMIT ?", (limit,))
        if not rows:
            await ctx.send("No data to export.")
            return
        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["user_id","xp","level","messages","aura"])
        for r in rows:
            uid = r["user_id"] if "user_id" in r.keys() else r[0]
            xp = r["xp"] if "xp" in r.keys() else r[1]
            level = r["level"] if "level" in r.keys() else r[2]
            messages = r["messages"] if "messages" in r.keys() else (r[3] if len(r) > 3 else "")
            aura = r["aura"] if "aura" in r.keys() else (r[4] if len(r) > 4 else "")
            writer.writerow([uid, xp, level, messages, aura])
        bio = BytesIO(csv_buffer.getvalue().encode("utf-8"))
        bio.seek(0)
        file = discord.File(fp=bio, filename="leaderboard.csv")
        await ctx.send("CSV Export:", file=file)

    # ---------------- SYNC LEVELS ----------------
    @commands.command(name="synclevels")
    @commands.has_permissions(administrator=True)
    async def synclevels(self, ctx: commands.Context, limit: int = 5000):
        """Recalculate and set levels based on XP"""
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

    # ---------------- COG LOAD/UNLOAD ----------------
    async def cog_unload(self):
        logger.info("LevelCog unloading — clearing caches.")
        self._cache.clear()
        self._last_awarded.clear()


# ---------------------------- COG SETUP ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
    logger.info("LevelCog loaded.")
