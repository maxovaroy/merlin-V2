import asyncio
import aiosqlite
import discord
from discord.ext import commands
import math, time, csv, logging
from typing import Optional, Tuple, Dict, List
from io import StringIO, BytesIO
import random

# ---------------- CONFIG ----------------
COMMAND_PREFIX = "!"
XP_PER_MESSAGE = 6
COOLDOWN_SECONDS = 12
LEVEL_UP_CHANNEL_ID = 1305771250693705818  # <== Your channel where level-up embeds go

# 🔥 Add GIFs here (you can add unlimited URLs)
LEVEL_UP_GIFS = [
    "https://media.tenor.com/ahMeY0K1U8AAAAAM/epic-victory.gif",
    "https://media.tenor.com/fnBbfmmSgfQAAAAC/level-up-anime.gif",
    "https://media.tenor.com/I-1sr-vfNIMAAAAC/level-up.gif",
    "https://media.tenor.com/6LPjPQxFpWIAAAAC/power-up-anime.gif"
]

# Level rewards (optional)
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


def db_level_from_xp(xp: int) -> int:
    try: return int(math.sqrt(xp // 10)) + 1
    except: return 1

def nice_progress_bar(frac: float, width=18) -> str:
    return "█" * int(frac*width) + "░"*(width-int(frac*width))


# ====================== MAIN COG ======================
class LevelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_awarded = {}
        self._cache = {}
        self._db_lock = asyncio.Lock()
        logger.info("LevelCog Loaded with GIF support.")

    async def _raw_db_execute(self, query, params=()):
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(query, params)
                rows = await cur.fetchall()
                await cur.close()
                return rows
        except:
            return []

    async def get_user_level_data(self, guild_id, user_id):
        key = (guild_id,user_id)
        now = time.monotonic()

        if key in self._cache and now-self._cache[key][2] < 5:
            return self._cache[key][0], self._cache[key][1]

        data = await get_user(str(user_id))
        if data:
            xp,level = int(data[1]),int(data[2])
        else: xp,level=0,1

        self._cache[key]=(xp,level,now)
        return xp,level

    async def _update_cache(self,guild_id,user_id,xp,level):
        self._cache[(guild_id,user_id)] = (xp,level,time.monotonic())

    async def force_add_xp(self,guild_id,user_id,amount):
        await add_user(str(user_id))
        async with self._db_lock:
            await update_user(str(user_id),xp_gain=amount)

        row=await get_user(str(user_id))
        xp,level=int(row[1]),int(row[2])
        px,pl=await self.get_user_level_data(guild_id,user_id)
        leveled=level>pl
        await self._update_cache(guild_id,user_id,xp,level)
        return xp,level,leveled

    async def add_xp(self,guild_id,user_id,amount,message=None):
        now=time.monotonic()
        key=(guild_id,user_id)

        if key in self._last_awarded and now-self._last_awarded[key]<COOLDOWN_SECONDS:
            return await self.get_user_level_data(guild_id,user_id) + (False,)

        self._last_awarded[key]=now

        xp,level,leveled=await self.force_add_xp(guild_id,user_id,amount)

        # ==============================
        #   LEVEL UP EVENT WITH GIF
        # ==============================
        if leveled:
            guild = self.bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(LEVEL_UP_CHANNEL_ID)

                gif = random.choice(LEVEL_UP_GIFS)  # 🔥 random gif every time

                embed = discord.Embed(
                    title=f"⚔ LEVEL UP!",
                    description=f"<@{user_id}> has reached **Level {level}**!",
                    color=discord.Color.gold()
                )
                embed.set_image(url=gif)  # <== GIF INSIDE EMBED 🔥🔥
                embed.set_footer(text="Your journey continues...")

                if channel:
                    await channel.send(embed=embed)
                else:
                    if message: await message.channel.send(embed=embed)

        return xp,level,leveled

    # ----------------------------
    # COG LISTNER
    # ----------------------------

    
    @commands.Cog.listener()
    async def on_message(self,message):
        if message.author.bot or not message.guild: return
        if message.content.startswith(COMMAND_PREFIX): return

        try:
            await self.add_xp(message.guild.id,message.author.id,XP_PER_MESSAGE,message)
        except Exception: pass

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
            await channel.send(
                f"{member.mention}, leveled up to **Level {new_level}**! 🎉\n"
                f"{random.choice(LEVEL_GIFS)}"
            )
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
