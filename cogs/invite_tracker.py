# cogs/invite_tracker.py
"""
Invite Tracker Cog for Merlin Realm Royz
----------------------------------------
Features:
- Track invites per user
- Reset invites per user or all users
- Welcome & leave messages
- Debug commands to test invite tracking
- Invite preview command to simulate welcome messages
- Logs all invite events
- Safe database operations
"""

import discord
from discord.ext import commands
from logger import logger
from database import add_user, get_user, update_user
import aiosqlite
from typing import Optional

# ---------------- CONFIG ----------------
WELCOME_CHANNEL_ID = 935111577974218762
LEAVE_CHANNEL_ID = 1305782186535944264
LOG_CHANNEL_ID = 130578999999999999  # Change this to your log channel if needed

DB_PATH = "database.db"

# ---------------- COG ----------------
class InviteTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._db_lock = None  # aiosqlite.Lock replacement is built into DB ops
        logger.info("[INVITE] Invite Tracker initialized.")

    # ---------------- DATABASE HELPERS ----------------
    async def _ensure_user(self, user_id: str):
        """Ensure user has a row in the DB."""
        await add_user(user_id)

    async def _get_invites(self, user_id: str) -> int:
        """Return number of invites for a user."""
        await self._ensure_user(user_id)
        row = await get_user(user_id)
        if row:
            _, _, _, _, _, invites = (*row, 0)  # add default if missing
            return invites
        return 0

    async def _set_invites(self, user_id: str, count: int):
        """Set invite count for a user."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
            await db.execute("UPDATE users SET invites = ? WHERE user_id = ?", (count, user_id))
            await db.commit()

    async def _add_invite(self, user_id: str, amount: int = 1):
        """Add invites to a user."""
        current = await self._get_invites(user_id)
        await self._set_invites(user_id, current + amount)
        return current + amount

    async def _reset_invites(self, user_id: Optional[str] = None):
        """Reset invites for a specific user or all users."""
        async with aiosqlite.connect(DB_PATH) as db:
            if user_id:
                await db.execute("UPDATE users SET invites = 0 WHERE user_id = ?", (user_id,))
            else:
                await db.execute("UPDATE users SET invites = 0")
            await db.commit()

    # ---------------- EVENTS ----------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Trigger when a new member joins."""
        inviter_id = None  # If you have a real invite tracking system, get inviter_id here

        # update DB
        if inviter_id:
            new_total = await self._add_invite(str(inviter_id))

        # send welcome message
        welcome_channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if welcome_channel:
            embed = discord.Embed(
                title="🎉 Welcome!",
                description=f"Welcome {member.mention} to {member.guild.name}!",
                color=discord.Color.green()
            )
            embed.add_field(name="Invited by", value=f"<@{inviter_id}>" if inviter_id else "Unknown", inline=True)
            embed.add_field(name="Total Invites", value=str(new_total) if inviter_id else "N/A", inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            await welcome_channel.send(embed=embed)

        # log event
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"[JOIN] {member.display_name} joined the server. Inviter: {inviter_id}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Trigger when a member leaves."""
        leave_channel = self.bot.get_channel(LEAVE_CHANNEL_ID)
        if leave_channel:
            embed = discord.Embed(
                title="👋 Member Left",
                description=f"{member.display_name} has left the server.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await leave_channel.send(embed=embed)

        # log event
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"[LEAVE] {member.display_name} left the server.")

    # ---------------- COMMANDS ----------------
    @commands.command(name="invitepreview")
    @commands.has_permissions(administrator=True)
    async def invite_preview(self, ctx: commands.Context, member: discord.Member = None, invites: int = 1):
        """Preview the welcome message for a new invite."""
        if member is None:
            member = ctx.author

        welcome_channel = self.bot.get_channel(WELCOME_CHANNEL_ID)

        embed = discord.Embed(
            title="🎉 New Invite!",
            description=f"{member.mention} has been credited with **{invites} invite(s)**!",
            color=discord.Color.green()
        )
        embed.add_field(name="Server", value=ctx.guild.name, inline=True)
        embed.add_field(name="Total Invites", value=str(invites), inline=True)
        embed.set_footer(text="Invite tracking preview")
        embed.set_thumbnail(url=member.display_avatar.url)

        await ctx.send("📩 This is a preview of the invite message:")
        await ctx.send(embed=embed)

    @commands.command(name="resetinvites")
    @commands.has_permissions(administrator=True)
    async def reset_invites_cmd(self, ctx: commands.Context, member: discord.Member = None):
        """Reset invites for a user or all users."""
        if member:
            await self._reset_invites(str(member.id))
            await ctx.send(f"✅ Reset invites for {member.display_name}.")
        else:
            await self._reset_invites()
            await ctx.send("✅ Reset invites for all users.")

    @commands.command(name="invitecount")
    async def invite_count_cmd(self, ctx: commands.Context, member: discord.Member = None):
        """Check total invites of a user."""
        if member is None:
            member = ctx.author
        total = await self._get_invites(str(member.id))
        await ctx.send(f"📊 {member.display_name} has **{total}** invite(s).")

    @commands.command(name="invitedebug")
    @commands.is_owner()
    async def invite_debug_cmd(self, ctx: commands.Context, member: discord.Member = None):
        """Owner-only debug for invites."""
        if member is None:
            member = ctx.author
        total = await self._get_invites(str(member.id))
        await ctx.send(f"[DEBUG] {member.display_name} has {total} invites in DB.")

# ---------------------------- COG SETUP ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(InviteTracker(bot))
    logger.info("[INVITE] Invite Tracker cog setup complete.")
