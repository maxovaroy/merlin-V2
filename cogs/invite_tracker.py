# cogs/invite_tracker.py
"""
Invite Tracker Cog for Merlin Realm Royz
----------------------------------------
Tracks invites per user, handles resets, logs joins/leaves, and provides stats.
Includes debug commands for testing.
"""

import discord
from discord.ext import commands
from database import add_user, get_user, update_user
import aiosqlite
import logging

# ---------------- CONFIG ----------------
WELCOME_CHANNEL_ID = 935111577974218762
LEAVE_CHANNEL_ID = 1305782186535944264
DATABASE_PATH = "database.db"

# ---------------- LOGGING ----------------
logger = logging.getLogger("InviteTracker")
if not logger.handlers:
    h = logging.StreamHandler()
    f = logging.Formatter('[INVITE] %(asctime)s %(levelname)s %(message)s')
    h.setFormatter(f)
    logger.addHandler(h)
logger.setLevel(logging.INFO)


# ---------------- INVITE TRACKER COG ----------------
class InviteTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db_lock = asyncio.Lock()
        logger.info("InviteTracker Cog Loaded.")

    # ---------------- HELPER FUNCTIONS ----------------
    async def _raw_db_execute(self, query, params=()):
        """Execute a raw DB query and return rows."""
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(query, params)
                rows = await cur.fetchall()
                await cur.close()
                return rows
        except Exception as e:
            logger.exception("DB query failed: %s", e)
            return []

    async def _ensure_invite_user(self, user_id: str):
        """Ensure the user exists in invites table."""
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("CREATE TABLE IF NOT EXISTS invites(user_id TEXT PRIMARY KEY, invites INTEGER DEFAULT 0)")
                await db.execute("INSERT OR IGNORE INTO invites(user_id, invites) VALUES(?,0)", (user_id,))
                await db.commit()
        except Exception as e:
            logger.exception("Failed to ensure invite user exists: %s", e)

    async def _get_invites(self, user_id: str) -> int:
        """Get current invite count of a user."""
        await self._ensure_invite_user(user_id)
        rows = await self._raw_db_execute("SELECT invites FROM invites WHERE user_id=?", (user_id,))
        if rows:
            return int(rows[0]["invites"])
        return 0

    async def _set_invites(self, user_id: str, amount: int):
        """Set a user's invites to a specific amount."""
        await self._ensure_invite_user(user_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE invites SET invites=? WHERE user_id=?", (amount, user_id))
            await db.commit()

    async def _add_invites(self, user_id: str, amount: int):
        """Add invites to a user."""
        current = await self._get_invites(user_id)
        await self._set_invites(user_id, current + amount)

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """When someone joins the server."""
        # Log join
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            await channel.send(f"🎉 {member.mention} joined the server!")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """When someone leaves the server."""
        channel = member.guild.get_channel(LEAVE_CHANNEL_ID)
        if channel:
            await channel.send(f"⚠ {member.display_name} left the server.")

    # ---------------- COMMANDS ----------------
    @commands.command(name="invites")
    async def invites(self, ctx: commands.Context, member: discord.Member = None):
        """Show invites of yourself or another member."""
        if member is None:
            member = ctx.author
        invites_count = await self._get_invites(str(member.id))
        await ctx.send(f"📊 {member.display_name} has **{invites_count}** invites.")

    @commands.command(name="resetinvites")
    @commands.has_permissions(administrator=True)
    async def resetinvites(self, ctx: commands.Context, member: discord.Member = None):
        """Reset invites for a specific user or everyone (admin only)."""
        if member:
            await self._set_invites(str(member.id), 0)
            await ctx.send(f"✅ Reset invites for {member.display_name}.")
        else:
            # Reset all invites
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE invites SET invites=0")
                await db.commit()
            await ctx.send("✅ Reset invites for all users.")

    # ---------------- DEBUG COMMAND ----------------
    @commands.command(name="invitedebug")
    @commands.is_owner()
    async def invitedebug(self, ctx: commands.Context, member: discord.Member = None):
        """Show full debug info of invites for a member or all users."""
        if member:
            invites_count = await self._get_invites(str(member.id))
            await ctx.send(f"🔍 Debug — {member.display_name} has {invites_count} invites.")
        else:
            rows = await self._raw_db_execute("SELECT user_id, invites FROM invites ORDER BY invites DESC")
            desc = ""
            for r in rows:
                uid = int(r["user_id"])
                m = ctx.guild.get_member(uid)
                name = m.display_name if m else f"User {uid}"
                desc += f"{name}: {r['invites']} invites\n"
            if not desc:
                desc = "No invite data found."
            await ctx.send(f"📄 **All invite data:**\n{desc}")

    # ---------------- LIFECYCLE ----------------
    async def cog_load(self):
        logger.info("InviteTracker Cog loaded successfully.")

    async def cog_unload(self):
        logger.info("InviteTracker Cog unloaded.")


# ---------------- SETUP ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(InviteTracker(bot))
    logger.info("InviteTracker Cog setup complete.")
