# cogs/level.py
import aiosqlite
import asyncio
import time
from typing import Optional, List, Tuple
from discord.ext import commands
import discord

DB_PATH = "database.sqlite"  # change to your project's DB path if different
XP_PER_MESSAGE = 5            # default XP per message awarded
LEVEL_XP_BASE = 100           # XP required per level using xp // LEVEL_XP_BASE + 1
SPAM_COOLDOWN_SECONDS = 10    # per-user cooldown per guild (prevents spamming XP)

def xp_to_level(xp: int) -> int:
    """Basic xp -> level formula: each LEVEL_XP_BASE xp gives +1 level."""
    return xp // LEVEL_XP_BASE + 1

def xp_needed_for_level(level: int) -> int:
    """XP required to reach a given level (lower bound)."""
    return (level - 1) * LEVEL_XP_BASE

class LevelCog(commands.Cog):
    """Cog for user leveling and XP using SQLite (aiosqlite)."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # in-memory cooldown tracker: { (guild_id, user_id): last_awarded_ts }
        self._last_awarded = {}
        # load DB table at startup
        bot.loop.create_task(self._init_db())

    # ------------------------
    # Database init and helpers
    # ------------------------
    async def _init_db(self):
        """Ensure the user_levels table exists."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS user_levels (
                        guild_id INTEGER,
                        user_id INTEGER,
                        xp INTEGER DEFAULT 0,
                        level INTEGER DEFAULT 1,
                        PRIMARY KEY (guild_id, user_id)
                    )
                """)
                # optional: index to make leaderboard queries faster
                await db.execute("CREATE INDEX IF NOT EXISTS idx_levels_guild_xp ON user_levels (guild_id, xp DESC)")
                await db.commit()
        except Exception as e:
            # if DB fails, log via bot.logger if available
            if hasattr(self.bot, "logger"):
                self.bot.logger.error(f"[LevelCog] DB init failed: {e}")
            else:
                print(f"[LevelCog] DB init failed: {e}")

    async def get_user(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Return dict {'xp': int, 'level': int} or None."""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cur:
                row = await cur.fetchone()
        if row:
            return {"xp": row[0], "level": row[1]}
        return None

    async def set_user(self, guild_id: int, user_id: int, xp: int, level: int):
        """Insert or update a user's xp and level (upsert)."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO user_levels (guild_id, user_id, xp, level)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level
            """, (guild_id, user_id, xp, level))
            await db.commit()

    async def _fetch_top(self, guild_id: int, limit: int = 10) -> List[Tuple[int,int,int]]:
        """Return list of (user_id, xp, level) sorted by xp desc."""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, xp, level FROM user_levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
                (guild_id, limit)
            ) as cur:
                rows = await cur.fetchall()
        return rows

    # ------------------------
    # XP awarding (public)
    # ------------------------
    async def add_xp(self, guild_id: int, user_id: int, amount: int, message: Optional[discord.Message]=None) -> Tuple[int,int,bool]:
        """
        Add XP for a user.
        Returns (new_xp, new_level, leveled_up_bool).
        - Respects a small per-user cooldown to prevent spam farming.
        - Optionally accepts the message object (to use channel logging or react).
        """
        now = time.monotonic()
        key = (guild_id, user_id)
        last = self._last_awarded.get(key, 0)
        if now - last < SPAM_COOLDOWN_SECONDS:
            # Too soon to award XP again
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)) as cur:
                    row = await cur.fetchone()
            if row:
                return row[0], row[1], False
            else:
                return 0, 1, False

        # update last awarded timestamp
        self._last_awarded[key] = now

        # fetch and update
        user = await self.get_user(guild_id, user_id)
        if user:
            new_xp = user["xp"] + amount
        else:
            new_xp = amount

        new_level = xp_to_level(new_xp)
        old_level = user["level"] if user else 1
        await self.set_user(guild_id, user_id, new_xp, new_level)

        leveled = new_level > old_level

        # optional: if leveled up, react or send small notice (non-spammy)
        if leveled and message:
            try:
                # try to add a celebratory reaction; fail silently if not permitted
                await message.add_reaction("⬆️")
            except Exception:
                pass

        return new_xp, new_level, leveled

    # ------------------------
    # Commands
    # ------------------------
    @commands.command(name="profile")
    async def profile(self, ctx, member: Optional[commands.MemberConverter] = None):
        """Show xp and level of a user (or yourself)."""
        if member is None:
            member = ctx.author
        if not ctx.guild:
            return await ctx.send("❌ This command must be used in a server.")

        data = await self.get_user(ctx.guild.id, member.id)
        if data:
            xp = data["xp"]
            lvl = data["level"]
        else:
            xp, lvl = 0, 1

        # compute progress to next level
        next_level = lvl + 1
        xp_for_current = xp_needed_for_level(lvl)
        xp_for_next = xp_needed_for_level(next_level)
        progress = xp - xp_for_current
        progress_total = xp_for_next - xp_for_current if xp_for_next - xp_for_current > 0 else LEVEL_XP_BASE

        # create embed for nicer display
        embed = discord.Embed(title=f"{member.display_name}'s Profile", color=discord.Color.green())
        embed.set_thumbnail(url=member.display_avatar.url if hasattr(member, "display_avatar") else None)
        embed.add_field(name="Level", value=str(lvl), inline=True)
        embed.add_field(name="XP", value=f"{xp} ({progress}/{progress_total} towards lvl {next_level})", inline=True)
        # optionally show rank by xp (not exact if many members; expensive to compute - we can query rank)
        try:
            # compute rank by counting users in guild with higher xp
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT COUNT(*) FROM user_levels WHERE guild_id = ? AND xp > ?", (ctx.guild.id, xp)) as cur:
                    row = await cur.fetchone()
                    rank = row[0] + 1 if row else 1
            embed.add_field(name="Rank", value=f"#{rank}", inline=True)
        except Exception:
            pass

        await ctx.send(embed=embed)

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx, limit:int = 10):
        """Show leaderboard for server (top N)."""
        if not ctx.guild:
            return await ctx.send("❌ This command must be used in a server.")
        if limit < 1: limit = 1
        if limit > 25: limit = 25  # cap to prevent abuse

        rows = await self._fetch_top(ctx.guild.id, limit)
        if not rows:
            return await ctx.send("No XP data for this server yet.")

        embed = discord.Embed(title=f"Leaderboard — Top {len(rows)}", color=discord.Color.blurple())
        description_lines = []
        pos = 1
        for (user_id, xp, level) in rows:
            # try to resolve member (may be None if user left)
            member = ctx.guild.get_member(user_id)
            name = member.display_name if member else f"User ID {user_id}"
            description_lines.append(f"**#{pos}** • {name} — Level {level} ({xp} XP)")
            pos += 1

        embed.description = "\n".join(description_lines)
        await ctx.send(embed=embed)

    # ------------------------
    # ADMIN commands for manual adjustments
    # ------------------------
    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def setxp(self, ctx, member: commands.MemberConverter, xp: int):
        """Set a user's XP (admin only). Usage: !setxp @user 1500"""
        if xp < 0:
            return await ctx.send("XP must be zero or positive.")
        level = xp_to_level(xp)
        await self.set_user(ctx.guild.id, member.id, xp, level)
        await ctx.send(f"Set {member.display_name}'s XP to {xp} (Level {level}).")

    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def setlevel(self, ctx, member: commands.MemberConverter, level: int):
        """Set a user's level (admin only). Usage: !setlevel @user 5"""
        if level < 1:
            return await ctx.send("Level must be at least 1.")
        xp = xp_needed_for_level(level)
        await self.set_user(ctx.guild.id, member.id, xp, level)
        await ctx.send(f"Set {member.display_name}'s Level to {level} ({xp} XP).")

    # ------------------------
    # Cog lifecycle
    # ------------------------
    async def cog_unload(self):
        # nothing fancy needed, but clear in-memory cooldowns to free memory
        self._last_awarded.clear()

async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
