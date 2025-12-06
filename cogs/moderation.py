# cogs/moderation.py
"""
Moderation Cog v3 — lightweight, polished
Features:
- Kick / Ban / Unban (safety checks)
- Timeout-based Mute / Unmute (with optional duration)
- Warn / Warnings / Delwarn (DB-backed)
- Clear / Purge messages
- Logs as embeds to LOG_CHANNEL_ID
- Permission & hierarchy safety
- Persistent DB for mutes & warnings
- Background task auto-unmutes on expiry
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
LOG_CHANNEL_ID = 1177896378085679145  # Mod-log channel ID
MUTE_CHECK_INTERVAL = 10  # Seconds

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
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS warnings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        moderator_id INTEGER,
                        reason TEXT,
                        timestamp INTEGER
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS mutes (
                        guild_id INTEGER,
                        user_id INTEGER,
                        end_time INTEGER,
                        reason TEXT,
                        PRIMARY KEY (guild_id, user_id)
                    )
                """)
                await db.commit()
            logger.info("[MOD] DB initialized")

            # Start background mute monitor
            self._mute_task = self.bot.loop.create_task(self._mute_monitor_loop())
            logger.info("[MOD] Mute monitor task started")
        except Exception as e:
            logger.exception("[MOD] DB/init error: %s", e)

    # ---------------- Utility ----------------
    def _parse_duration(self, s: Optional[str]) -> Optional[int]:
        """Parse durations like '1d2h30m', '2h', '45m' → return seconds"""
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
        total = days*86400 + hours*3600 + mins*60
        return total if total > 0 else None

    async def _log_embed(self, guild: discord.Guild, title: str, description: str, fields: Optional[List[Tuple[str,str,bool]]] = None):
        """Send an embed to LOG_CHANNEL_ID"""
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
                logger.warning("[MOD] LOG_CHANNEL_ID not found or inaccessible")
                logger.info("[MOD LOG] %s: %s", title, description)
        except Exception as e:
            logger.exception("[MOD] Failed to send log embed: %s", e)

    # ---------------- Background mute monitor ----------------
    async def _mute_monitor_loop(self):
        """Auto-unmute expired mutes"""
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
                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
                                await db.commit()
                            continue
                        member = guild.get_member(user_id)
                        if member:
                            try:
                                await member.edit(timeout=None)
                                await self._log_embed(guild, "Auto Unmute", f"Automatically unmuted {member.mention} (mute expired).")
                                logger.info("[MOD] Auto-unmuted %s in guild %s", user_id, guild_id)
                            except Exception as exc:
                                logger.exception("[MOD] Auto-unmute failed: %s", exc)
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (guild_id, user_id))
                            await db.commit()
                except Exception:
                    logger.exception("[MOD] Error in mute monitor loop")
                await asyncio.sleep(MUTE_CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[MOD] Mute monitor loop cancelled")

    # ---------------- Safety ----------------
    def _can_act_on(self, moderator: discord.Member, target: discord.Member) -> Tuple[bool,str]:
        """Return (allowed, reason)"""
        bot_member = moderator.guild.me
        if target == moderator:
            return False, "You cannot act on yourself."
        if target.top_role >= moderator.top_role:
            return False, "Cannot act on someone with equal/higher role."
        if target.top_role >= bot_member.top_role:
            return False, "Cannot act due to bot role hierarchy."
        return True, ""

    # ---------------- Commands ----------------
    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str="No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed: return await ctx.send(f"⚠ {msg}")
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ Kicked {member.mention} | Reason: {reason}")
            await self._log_embed(ctx.guild, "Member Kicked", f"{ctx.author.mention} kicked {member.mention}.", [("Reason", reason, False)])
            logger.info("[MOD] Kicked %s in guild %s by %s", member.id, ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission.")
        except Exception as e:
            await ctx.send("⚠ Kick failed.")
            logger.exception("Kick failed: %s", e)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str="No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed: return await ctx.send(f"⚠ {msg}")
        try:
            await member.ban(reason=reason)
            await ctx.send(f"✅ Banned {member.mention} | Reason: {reason}")
            await self._log_embed(ctx.guild, "Member Banned", f"{ctx.author.mention} banned {member.mention}.", [("Reason", reason, False)])
            logger.info("[MOD] Banned %s in guild %s by %s", member.id, ctx.guild.id, ctx.author.id)
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission.")
        except Exception as e:
            await ctx.send("⚠ Ban failed.")
            logger.exception("Ban failed: %s", e)

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.send(f"✅ Unbanned {user.mention}")
            await self._log_embed(ctx.guild, "Member Unbanned", f"{ctx.author.mention} unbanned {user.mention}.")
        except discord.NotFound:
            await ctx.send("⚠ User not found in ban list.")
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission.")
        except Exception as e:
            await ctx.send("⚠ Unban failed.")
            logger.exception("Unban failed: %s", e)

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: Optional[str]=None, *, reason: str="No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed: return await ctx.send(f"⚠ {msg}")
        seconds = self._parse_duration(duration) if duration else None
        end_time = int(time.time()) + seconds if seconds else None
        try:
            await member.edit(timeout=seconds)
            human = f"Muted until <t:{end_time}:F> ({duration})" if seconds else "Muted permanently"
            await ctx.send(f"🔇 {member.mention} {human} | Reason: {reason}")
            # persist mute
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO mutes (guild_id,user_id,end_time,reason) VALUES(?,?,?,?)",
                                 (ctx.guild.id, member.id, end_time, reason))
                await db.commit()
            await self._log_embed(ctx.guild, "Member Muted", f"{ctx.author.mention} muted {member.mention}.", [("Duration", human, False), ("Reason", reason, False)])
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to mute.")
        except Exception as e:
            await ctx.send("⚠ Mute failed.")
            logger.exception("Mute failed: %s", e)

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed: return await ctx.send(f"⚠ {msg}")
        try:
            await member.edit(timeout=None)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
                await db.commit()
            await ctx.send(f"🔊 Unmuted {member.mention}")
            await self._log_embed(ctx.guild, "Member Unmuted", f"{ctx.author.mention} unmuted {member.mention}.")
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission.")
        except Exception as e:
            await ctx.send("⚠ Unmute failed.")
            logger.exception("Unmute failed: %s", e)

    @commands.command(name="clear", aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int=5):
        if amount < 1: return await ctx.send("⚠ Provide a number > 0.")
        amount = min(amount, 1000)
        try:
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(f"🧹 Deleted {len(deleted)} messages.", delete_after=6)
            await self._log_embed(ctx.guild, "Messages Purged", f"{ctx.author.mention} purged {len(deleted)} messages in {ctx.channel.mention}.")
        except discord.Forbidden:
            await ctx.send("⚠ Bot lacks permission to delete messages.")
        except Exception as e:
            await ctx.send("⚠ Purge failed.")
            logger.exception("Purge failed: %s", e)

    @commands.command(name="warn")
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str="No reason provided"):
        allowed, msg = self._can_act_on(ctx.author, member)
        if not allowed: return await ctx.send(f"⚠ {msg}")
        ts = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("INSERT INTO warnings (guild_id,user_id,moderator_id,reason,timestamp) VALUES(?,?,?,?,?)",
                                   (ctx.guild.id, member.id, ctx.author.id, reason, ts))
            warn_id = cur.lastrowid
            await db.commit()
        await ctx.send(f"⚠ Warned {member.mention} (case #{warn_id}) | Reason: {reason}")
        await self._log_embed(ctx.guild, "User Warned", f"{ctx.author.mention} warned {member.mention}.", [("Case", str(warn_id), True), ("Reason", reason, False)])

    @commands.command(name="warnings")
    @commands.has_permissions(kick_members=True)
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, moderator_id, reason, timestamp FROM warnings WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC", (ctx.guild.id, member.id))
            rows = await cur.fetchall()
        if not rows: return await ctx.send(f"No warnings for {member.mention}.")
        embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
        for wid, mod_id, reason, ts in rows:
            try: mod = await self.bot.fetch_user(mod_id); mod_repr = f"{mod} ({mod_id})"
            except: mod_repr = str(mod_id)
            embed.add_field(name=f"Case #{wid}", value=f"By: {mod_repr}\nReason: {reason}\nAt: <t:{ts}:F>", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="delwarn")
    @commands.has_permissions(kick_members=True)
    async def delwarn(self, ctx: commands.Context, case_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,guild_id FROM warnings WHERE id=?", (case_id,))
            row = await cur.fetchone()
            if not row: return await ctx.send("Case not found.")
            if row[1] != ctx.guild.id: return await ctx.send("Case not in this server.")
            await db.execute("DELETE FROM warnings WHERE id=?", (case_id,))
            await db.commit()
        await ctx.send(f"✅ Deleted warning case #{case_id}.")
        await self._log_embed(ctx.guild, "Warning Removed", f"{ctx.author.mention} removed warning case #{case_id}.")

    # ---------------- Lifecycle ----------------
    async def cog_load(self):
        logger.info("[MOD] Moderation cog loaded.")

    async def cog_unload(self):
        try:
            if hasattr(self, "_mute_task"):
                self._mute_task.cancel()
            logger.info("[MOD] Moderation cog unloaded.")
        except Exception:
            logger.exception("[MOD] Error unloading moderation cog")

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    logger.info("[MOD] Moderation cog setup complete.")
