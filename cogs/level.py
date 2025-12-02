# cogs/level.py
import aiosqlite
from discord.ext import commands

# Adjust DB_PATH to point to your actual SQLite DB file.
DB_PATH = "database.sqlite"  # or whatever your file is named

def xp_to_level(xp: int) -> int:
    # Simple XP → level formula: 100 XP per level
    return xp // 100 + 1

class LevelCog(commands.Cog):
    """Cog for handling user XP and level, and profile command."""
    def __init__(self, bot):
        self.bot = bot
        # When cog is loaded, ensure DB/table exists
        bot.loop.create_task(self.init_db())

    async def init_db(self):
        """Create the required table if not exists."""
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
            await db.commit()

    async def get_user(self, guild_id: int, user_id: int):
        """Fetch xp and level for a user, or None if not present."""
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"xp": row[0], "level": row[1]}
        return None

    async def set_user(self, guild_id: int, user_id: int, xp: int, level: int):
        """Insert or update a user's xp and level."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO user_levels (guild_id, user_id, xp, level)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = ?, level = ?
            """, (guild_id, user_id, xp, level, xp, level))
            await db.commit()

    async def add_xp(self, guild_id: int, user_id: int, amount: int):
        """Add XP to user, update level accordingly."""
        user = await self.get_user(guild_id, user_id)
        if user:
            new_xp = user["xp"] + amount
        else:
            new_xp = amount
        new_level = xp_to_level(new_xp)
        await self.set_user(guild_id, user_id, new_xp, new_level)
        return new_xp, new_level

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bots (including self)
        if message.author.bot:
            return
        # If message is in a guild (not DM)
        if message.guild:
            await self.add_xp(message.guild.id, message.author.id, 5)
        # Important: let commands processing continue
        # But since this is a cog, _don't_ call bot.process_commands here,
        # assuming main bot has its own on_message or command processor.

    @commands.command(name="profile")
    async def profile(self, ctx, member: commands.MemberConverter = None):
        """Show xp and level of a user (or yourself)."""
        if member is None:
            member = ctx.author
        data = await self.get_user(ctx.guild.id, member.id)
        if data:
            xp = data["xp"]
            lvl = data["level"]
        else:
            xp = 0
            lvl = 1
        await ctx.send(f"**{member.display_name}** — Level: **{lvl}**, XP: **{xp}**")

async def setup(bot):
    await bot.add_cog(LevelCog(bot))
