# cogs/moderation.py
"""
Moderation Cog v2 — lightweight, polished
Features implemented:
- kick / ban / unban (safety checks)
- mute / unmute (timed mute with persistence)
- warn / warnings / delwarn (DB-backed)
- clear (purge)
- logs sent as embed to LOG_CHANNEL_ID
- permission & hierarchy safety hardening
- persistent DB (warnings, mutes, guild_settings)
- background task to auto-unmute on expiry
"""

import asyncio
import time
import re
from typing import Optional, List, Tuple

import aiosqlite
import discord
from discord.ext import commands

from logger import logger

# ---------------- CONFIG ----------------
DB_PATH = "database.db"
LOG_CHANNEL_ID = 1177896378085679145  # <- set to your desired mod-log channel ID
MUTE_ROLE_NAME = "Muted"
MUTE_CHECK_INTERVAL = 10  # seconds between checks for expired mutes

# ---------------- Cog ----------------
class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._bg_task = self.bot.loop.create_task(self._init_db_and_restore())
        logger.info("[MOD] Moderation cog initializing...")

    # ---------------- DB setup & restore ----------------
    async def _init_db_and_restore(self):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS warnings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        moderator_id INTEGER,
                        reason TEXT,
                        timestamp INTEGER
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mutes (
                        guild_id INTEGER,
                        user_id INTEGER,
                        end_time INTEGER,
                        reason TEXT,
                        PRIMARY KEY (guild_id, user_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id INTEGER PRIMARY KEY,
                        mute_role_id INTEGER
                    )
                    """
                )
                await db.commit()
            logger.info("[MOD] DB initialized")

            # Start background mute monitor
            self._mute_task = self.bot.loop.create_task(self._mute_monitor_loop())
            logger.info("[MOD] Mute monitor task started")
        except Exception as e:
            logger.exception(f"[MOD] DB/init error: {e}")

    # ---------------- Utility helpers ----------------
    def _parse_duration(self, s: Optional[str]) -> Optional[int]:
        """
        Parse durations like '1d2h30m', '2h', '45m'. Return seconds or None.
        """
        if not s:
            return None
        s = s.replace(" ", "").lower()
        pattern = r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$"
        m = re.match(pattern, s)
        if not m:
            return None
        days = int(m.group(1)) if m.group(1) else 0
        hours = int(m.group(2)) if m.group(2) else 0
        mins = int(m.group(3)) if m.group(3) else 0
        total = days * 86400 + hours * 3600 + mins * 60
        return total if total > 0 else None

    async def _get_mute_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        """
        Return the Muted role for the guild, creating/configuring it if missing.
        Stores role id in guild_settings table for future.
        """
        try:
            # First check stored role id
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT mute_role_id FROM guild_settings WHERE guild_id=?", (guild.id,))
                row = await cur.fetchone()
            if row and row[0]:
                role = guild.get_role(int(row[0]))
                if role:
                    return role

            # Fallback: try to find role by name
            role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
            if role:
                # persist it
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT OR REPLACE INTO guild_settings (guild_id, mute_role_id) VALUES (?, ?)",
                                     (guild.id, role.id))
                    await db.commit()
                return role

            # Create Muted role
            perms = discord.Permissions(send_messages=False, speak=False, add_reactions=False)
            try:
                role = await guild.create_role(name=MUTE_ROLE_NAME, permissions=discord.Permissions.none(), reason="Create muted role for moderation cog")
            except discord.Forbidden:
                logger.warning("[MOD] Missing permission to create Muted role in guild %s", guild.id)
                return None
            except Exception as exc:
                logger.exception("[MOD] Failed to create Muted role: %s", exc)
                return None

            # Set channel overrides to disallow send_messages & speak
            for channel in guild.channels:
                try:
                    await channel.set_permissions(role, send_messages=False, speak=False, add_reactions=False)
                except Exception:
                    # ignore channels we can't set
                    continue

            # Persist role id
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO guild_settings (guild_id, mute_role_id) VALUES (?, ?)", (guild.id, role.id))
                await db.commit()

            return role
        except Exception as e:
            logger.exception("[MOD] _get_mute_role error: %s", e)
            return None

    async def _log_embed(self, guild: discord.Guild, title: str, description: str, fields: Optional[List[Tuple[str, str, bool]]] = None):
        """
        Send an embed to the configured LOG_CHANNEL_ID. If not available, log locally.
        """
        embed = discord.Embed(title=title, description=description, color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text=f"Server: {guild.name} • ID: {guild.id}")
        try:
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(embed=embed)
            else:
                logger.warning("[MOD] LOG_CHANNEL_ID not found or bot cannot access it")
                # fallback to logger
                logger.info("[MOD LOG] %s: %s", title, description)
        except Exception as e:
            logger.exception("[MOD] Failed to send log embed: %s", e)

    # ---------------- Timed mute monitor ----------------
    async def _mute_monitor_loop(self):
        """
        Background loop: checks mutes table for expired mutes, unmute them.
        """
        try:
            while True:
                try:
                    now = int(time.time())
                    async with aiosqlite.connect(DB_PATH) as db:
                        cur = await db.execute("SELECT guild_id, user_id FROM mutes WHERE end_time IS NOT NULL AND end_time <= ?", (now,))
                        rows = await cur.fetchall()

                    for guild_id, user_id in rows:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            # remove mute row to avoid stale data
                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
                                await db.commit()
                            continue

                        member = guild.get_member(user_id)
                        # attempt unmute
                        role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
                        if member and role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="Mute expired (auto unmute)")
                                await self._log_embed(guild, "Auto Unmute", f"Automatically unmuted <@{user_id}> (mute expired).")
                                logger.info("[MOD] Auto-unmuted %s in guild %s", user_id, guild_id)
                            except Exception as exc:
                                logger.exception("[MOD] Auto-unmute failed: %s", exc)
                        # cleanup database
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
                            await db.commit()
                except Exception:
                    logger.exception("[MOD] Error in mute monitor loop")
                await asyncio.sleep(MUTE_CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[MOD] Mute monitor loop cancelled")

    # ---------------- Safety checks ----------------
    def _can_act_on(self, moderator: discord.Member, target: discord.Member) -> Tuple[bool, Optional[str]]:
        """
        Verify the moderator can act on the target. Return (allowed, reason).
        """
        if moderator.id == target.id:
            return False, "You cannot perform this action on yourself."
        if target == self.bot.user:
            return False, "You cannot perform this action on the bot."
        if target.top_role >= moderator.top_role and moderator.guild.owner_id != moderator.id:
            return False, "You cannot act on a member with equal or higher role."
        if target.top_role >= self.bot.user.top_role:
            return False, "The bot cannot act on this member due to role hierarchy."
        return True, None

    # ---------------- Commands ----------------

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed:
            return await ctx.send(f"⚠ {msg}")
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ Kicked {member.mention} | Reason: {reason}")
            await self._log_embed(ctx.guild, "Member Kicked", f"{ctx.author.mention} kicked {member.mention}.", fields=[("Reason", reason, False)])
            logger.info("[MOD] Kicked %s in guild %s by %s", member.id, ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to kick this user.")
            logger.warning("[MOD] Kick forbidden: %s", member.id)
        except Exception as e:
            await ctx.send("⚠ Failed to kick user.")
            logger.exception("[MOD] Kick failed: %s", e)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed:
            return await ctx.send(f"⚠ {msg}")
        try:
            await member.ban(reason=reason)
            await ctx.send(f"✅ Banned {member.mention} | Reason: {reason}")
            await self._log_embed(ctx.guild, "Member Banned", f"{ctx.author.mention} banned {member.mention}.", fields=[("Reason", reason, False)])
            logger.info("[MOD] Banned %s in guild %s by %s", member.id, ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to ban this user.")
            logger.warning("[MOD] Ban forbidden: %s", member.id)
        except Exception as e:
            await ctx.send("⚠ Failed to ban user.")
            logger.exception("[MOD] Ban failed: %s", e)

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(f"✅ Unbanned {user.mention}")
            await self._log_embed(ctx.guild, "Member Unbanned", f"{ctx.author.mention} unbanned {user.mention}.")
            logger.info("[MOD] Unbanned %s in guild %s by %s", user_id, ctx.guild.id, ctx.author.id)
        except discord.NotFound:
            await ctx.send("⚠ User not found in ban list.")
            logger.warning("[MOD] Unban not found: %s", user_id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to unban.")
            logger.warning("[MOD] Unban forbidden")
        except Exception as e:
            await ctx.send("⚠ Failed to unban user.")
            logger.exception("[MOD] Unban failed: %s", e)

    @commands.command(name="mute")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: Optional[str] = None, *, reason: str = "No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed:
            return await ctx.send(f"⚠ {msg}")

        # parse duration
        seconds = None
        end_time = None
        if duration:
            seconds = self._parse_duration(duration)
            if seconds is None:
                # If the duration token was actually part of the reason (e.g., no duration provided), treat it as reason
                # but we require explicit duration for timed mute; if invalid, inform user.
                await ctx.send("⚠ Invalid duration format. Examples: 1d, 2h30m, 45m. Use no duration for permanent mute.")
                return
            end_time = int(time.time()) + seconds

        try:
            role = await self._get_mute_role(ctx.guild)
            if not role:
                return await ctx.send("⚠ Could not create or find a Muted role. Check bot permissions.")
            # check role hierarchy
            if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
                # if moderator cannot assign this role
                await ctx.send("⚠ You cannot assign the Muted role due to role hierarchy.")
                return
            await member.add_roles(role, reason=reason)
            # persist mute (end_time may be None for permanent mute)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO mutes (guild_id, user_id, end_time, reason) VALUES (?, ?, ?, ?)",
                                 (ctx.guild.id, member.id, end_time, reason))
                await db.commit()
            # log
            if end_time:
                human = f"Muted until <t:{end_time}:F> ({duration})"
            else:
                human = "Muted permanently"
            await ctx.send(f"🔇 {member.mention} {human} | Reason: {reason}")
            await self._log_embed(ctx.guild, "Member Muted", f"{ctx.author.mention} muted {member.mention}.", fields=[("Duration", human, False), ("Reason", reason, False)])
            logger.info("[MOD] Muted %s in guild %s by %s (duration=%s)", member.id, ctx.guild.id, ctx.author.id, duration)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to add roles.")
            logger.warning("[MOD] Mute forbidden for %s", member.id)
        except Exception as e:
            await ctx.send("⚠ Failed to mute user.")
            logger.exception("[MOD] Mute failed: %s", e)

    @commands.command(name="unmute")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed:
            # allow moderators to unmute themselves? no — keep safety
            return await ctx.send(f"⚠ {msg}")
        try:
            role = discord.utils.get(ctx.guild.roles, name=MUTE_ROLE_NAME)
            if not role:
                return await ctx.send("⚠ No Muted role found.")
            if role not in member.roles:
                return await ctx.send("⚠ Member is not muted.")
            await member.remove_roles(role, reason=f"Unmuted by {ctx.author}")
            # remove DB entry
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
                await db.commit()
            await ctx.send(f"🔊 Unmuted {member.mention}")
            await self._log_embed(ctx.guild, "Member Unmuted", f"{ctx.author.mention} unmuted {member.mention}.")
            logger.info("[MOD] Unmuted %s in guild %s by %s", member.id, ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to remove roles.")
            logger.warning("[MOD] Unmute forbidden for %s", member.id)
        except Exception as e:
            await ctx.send("⚠ Failed to unmute user.")
            logger.exception("[MOD] Unmute failed: %s", e)

    @commands.command(name="clear", aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int = 5):
        if amount < 1:
            return await ctx.send("⚠ Provide a number > 0.")
        # limit to avoid accidental mass deletes
        amount = min(amount, 1000)
        try:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=6)
            await self._log_embed(ctx.guild, "Messages Purged", f"{ctx.author.mention} purged {len(deleted)} messages in {ctx.channel.mention}.")
            logger.info("[MOD] Purged %s messages in guild %s by %s", len(deleted), ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to delete messages.")
            logger.warning("[MOD] Purge forbidden in guild %s", ctx.guild.id)
        except Exception as e:
            await ctx.send("⚠ Failed to purge messages.")
            logger.exception("[MOD] Purge failed: %s", e)

    # ---------------- Warn system ----------------
    @commands.command(name="warn")
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed:
            return await ctx.send(f"⚠ {msg}")
        try:
            ts = int(time.time())
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                                       (ctx.guild.id, member.id, ctx.author.id, reason, ts))
                warn_id = cur.lastrowid
                await db.commit()
            await ctx.send(f"⚠ Warned {member.mention} (case #{warn_id}) | Reason: {reason}")
            await self._log_embed(ctx.guild, "User Warned", f"{ctx.author.mention} warned {member.mention}.", fields=[("Case", str(warn_id), True), ("Reason", reason, False)])
            logger.info("[MOD] Warned %s in guild %s by %s (case %s)", member.id, ctx.guild.id, ctx.author.id, warn_id)
        except Exception as e:
            await ctx.send("⚠ Failed to issue warning.")
            logger.exception("[MOD] Warn failed: %s", e)

    @commands.command(name="warnings")
    @commands.has_permissions(kick_members=True)
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, moderator_id, reason, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC", (ctx.guild.id, member.id))
                rows = await cur.fetchall()
            if not rows:
                return await ctx.send(f"No warnings found for {member.mention}.")
            embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
            for wid, mod_id, reason, ts in rows:
                t = discord.utils.format_dt(discord.utils.snowflake_time(1) if False else discord.utils.utcfromtimestamp(ts))
                try:
                    mod = await self.bot.fetch_user(mod_id)
                    mod_repr = f"{mod} ({mod_id})"
                except Exception:
                    mod_repr = str(mod_id)
                embed.add_field(name=f"Case #{wid}", value=f"By: {mod_repr}\nReason: {reason}\nAt: <t:{ts}:F>", inline=False)
            await ctx.send(embed=embed)
            logger.info("[MOD] Fetched warnings for %s in guild %s", member.id, ctx.guild.id)
        except Exception as e:
            await ctx.send("⚠ Failed to fetch warnings.")
            logger.exception("[MOD] Fetch warnings failed: %s", e)

    @commands.command(name="delwarn")
    @commands.has_permissions(kick_members=True)
    async def delwarn(self, ctx: commands.Context, case_id: int):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, guild_id FROM warnings WHERE id=?", (case_id,))
                row = await cur.fetchone()
                if not row:
                    return await ctx.send("Case not found.")
                if row[1] != ctx.guild.id:
                    return await ctx.send("Case ID not in this server.")
                await db.execute("DELETE FROM warnings WHERE id=?", (case_id,))
                await db.commit()
            await ctx.send(f"✅ Deleted warning case #{case_id}.")
            await self._log_embed(ctx.guild, "Warning Removed", f"{ctx.author.mention} removed warning case #{case_id}.")
            logger.info("[MOD] Deleted warn case %s in guild %s by %s", case_id, ctx.guild.id, ctx.author.id)
        except Exception as e:
            await ctx.send("⚠ Failed to delete warning.")
            logger.exception("[MOD] Delwarn failed: %s", e)

    # ---------------- Cog lifecycle ----------------
    async def cog_load(self):
        logger.info("[MOD] Moderation cog loaded.")

    async def cog_unload(self):
        # cancel background tasks
        try:
            if hasattr(self, "_mute_task"):
                self._mute_task.cancel()
            if hasattr(self, "_restore_task"):
                self._restore_task.cancel()
            logger.info("[MOD] Moderation cog unloaded.")
        except Exception:
            logger.exception("[MOD] Error unloading moderation cog")

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    logger.info("[MOD] Moderation cog setup complete.")
