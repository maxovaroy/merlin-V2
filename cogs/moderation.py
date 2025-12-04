# cogs/moderation.py
"""
Moderation Cog for Merlin Realm Royz
Features:
- Kick, Ban, Unban
- Mute / Unmute
- Clear messages (purge)
- Optional logging for moderation actions
- Owner/admin only commands where appropriate
- Safe handling of errors
"""

import discord
from discord.ext import commands
from logger import logger
from typing import Optional

# ---------------- CONFIG ----------------
LOG_CHANNEL_ID = None  # Optional: set your mod-log channel ID here

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("[MOD] Moderation cog initialized.")

    # ----------------------------
    # Helper: log actions
    # ----------------------------
    async def _log_action(self, guild: discord.Guild, message: str):
        logger.info("[MOD ACTION] %s", message)
        if LOG_CHANNEL_ID:
            channel = guild.get_channel(LOG_CHANNEL_ID)
            if channel:
                await channel.send(f"🛡 {message}")

    # ----------------------------
    # Kick command
    # ----------------------------
    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ Kicked {member.mention} | Reason: {reason}")
            await self._log_action(ctx.guild, f"{ctx.author} kicked {member} | Reason: {reason}")
        except Exception as e:
            await ctx.send(f"⚠ Failed to kick {member.mention}")
            logger.exception("Kick failed: %s", e)

    # ----------------------------
    # Ban command
    # ----------------------------
    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        try:
            await member.ban(reason=reason)
            await ctx.send(f"✅ Banned {member.mention} | Reason: {reason}")
            await self._log_action(ctx.guild, f"{ctx.author} banned {member} | Reason: {reason}")
        except Exception as e:
            await ctx.send(f"⚠ Failed to ban {member.mention}")
            logger.exception("Ban failed: %s", e)

    # ----------------------------
    # Unban command
    # ----------------------------
    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(f"✅ Unbanned {user.mention}")
            await self._log_action(ctx.guild, f"{ctx.author} unbanned {user}")
        except Exception as e:
            await ctx.send(f"⚠ Failed to unban user ID {user_id}")
            logger.exception("Unban failed: %s", e)

    # ----------------------------
    # Mute command (role-based)
    # ----------------------------
    @commands.command(name="mute")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not role:
            role = await ctx.guild.create_role(name="Muted")
            for channel in ctx.guild.channels:
                await channel.set_permissions(role, send_messages=False, speak=False)
        try:
            await member.add_roles(role, reason=reason)
            await ctx.send(f"🔇 Muted {member.mention} | Reason: {reason}")
            await self._log_action(ctx.guild, f"{ctx.author} muted {member} | Reason: {reason}")
        except Exception as e:
            await ctx.send(f"⚠ Failed to mute {member.mention}")
            logger.exception("Mute failed: %s", e)

    # ----------------------------
    # Unmute command
    # ----------------------------
    @commands.command(name="unmute")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not role:
            return await ctx.send("⚠ No mute role found.")
        try:
            await member.remove_roles(role)
            await ctx.send(f"🔊 Unmuted {member.mention}")
            await self._log_action(ctx.guild, f"{ctx.author} unmuted {member}")
        except Exception as e:
            await ctx.send(f"⚠ Failed to unmute {member.mention}")
            logger.exception("Unmute failed: %s", e)

    # ----------------------------
    # Clear / Purge messages
    # ----------------------------
    @commands.command(name="clear", aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int = 5):
        if amount < 1:
            return await ctx.send("⚠ Provide a number of messages to delete (>0).")
        try:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=5)
            await self._log_action(ctx.guild, f"{ctx.author} purged {len(deleted)} messages in {ctx.channel.name}")
        except Exception as e:
            await ctx.send("⚠ Failed to delete messages.")
            logger.exception("Purge failed: %s", e)

    # ----------------------------
    # Lifecycle
    # ----------------------------
    async def cog_load(self):
        logger.info("[MOD] Moderation cog loaded.")

    async def cog_unload(self):
        logger.info("[MOD] Moderation cog unloaded.")


# ----------------------------
# Setup
# ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    logger.info("[MOD] Moderation cog setup complete.")
