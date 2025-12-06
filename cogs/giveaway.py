# cogs/giveaway.py
"""
Giveaway v2 (prefix !)
- Persistent SQLite giveaways + entries (no long-term history)
- Restores views after restart and resumes countdowns
- Duration parser supports combined units (1d2h30m, 2h, 45m)
- Prevents bots from joining
- Status command shows remaining time & participants
- Winner announcement pings winners
- Commands: !giveaway preview|start|end|reroll|set_manager|status
"""

import asyncio
import aiosqlite
import random
import time
import re
import shlex
from typing import Optional, List

import discord
from discord.ext import commands

from logger import logger  # your repo logger

DB_PATH = "database.db"
CHECK_INTERVAL = 10  # seconds


class PersistentGiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int, min_messages: int, min_level: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.min_messages = int(min_messages or 0)
        self.min_level = int(min_level or 0)

    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, custom_id="giveaway_join_button")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user.bot:
            await interaction.response.send_message("Bots cannot join giveaways.", ephemeral=True)
            logger.debug(f"[GIVEAWAY] bot prevented from joining giveaway {self.giveaway_id} user={user.id}")
            return

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # verify active
                cur = await db.execute("SELECT active FROM giveaways WHERE id=?", (self.giveaway_id,))
                row = await cur.fetchone()
                if not row or row[0] != 1:
                    await interaction.response.send_message("This giveaway is no longer active.", ephemeral=True)
                    logger.info(f"[GIVEAWAY] join blocked - inactive giveaway {self.giveaway_id} by {user.id}")
                    return

                # fetch user stats
                cur = await db.execute("SELECT messages, level FROM users WHERE user_id=?", (user.id,))
                urow = await cur.fetchone()
                if not urow:
                    await interaction.response.send_message("You must chat a bit first to join giveaways.", ephemeral=True)
                    logger.debug(f"[GIVEAWAY] join blocked - user not in users table: {user.id}")
                    return

                user_messages, user_level = int(urow[0]), int(urow[1])
                if user_messages < self.min_messages:
                    await interaction.response.send_message(
                        f"You need at least {self.min_messages} messages to join (you have {user_messages}).",
                        ephemeral=True,
                    )
                    logger.info(f"[GIVEAWAY] join blocked - insufficient messages user={user.id} have={user_messages} need={self.min_messages}")
                    return
                if user_level < self.min_level:
                    await interaction.response.send_message(
                        f"You need to be level {self.min_level} to join (you are level {user_level}).",
                        ephemeral=True,
                    )
                    logger.info(f"[GIVEAWAY] join blocked - insufficient level user={user.id} level={user_level} need={self.min_level}")
                    return

                # register entry uniquely
                await db.execute(
                    "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id, won) VALUES (?, ?, 0)",
                    (self.giveaway_id, user.id),
                )
                await db.commit()

            await interaction.response.send_message("You joined the giveaway. Good luck!", ephemeral=True)
            logger.info(f"[GIVEAWAY] user joined giveaway {self.giveaway_id} user={user.id}")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Exception in join handler: {exc}")
            try:
                await interaction.response.send_message("Failed to join due to internal error.", ephemeral=True)
            except Exception:
                pass


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._restore_task = self.bot.loop.create_task(self._init_and_restore())
        logger.info("[GIVEAWAY] Cog init - restore task scheduled")

    # ----------------- DB init & restore -----------------
    async def _init_and_restore(self):
        # create schema
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS giveaways (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        channel_id INTEGER,
                        message_id INTEGER,
                        title TEXT,
                        prize TEXT,
                        winner_count INTEGER,
                        min_messages INTEGER DEFAULT 0,
                        min_level INTEGER DEFAULT 0,
                        start_time INTEGER,
                        end_time INTEGER,
                        active INTEGER DEFAULT 1
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS giveaway_entries (
                        giveaway_id INTEGER,
                        user_id INTEGER,
                        won INTEGER DEFAULT 0,
                        UNIQUE(giveaway_id, user_id)
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id INTEGER PRIMARY KEY,
                        manager_role_id INTEGER
                    )
                    """
                )
                await db.commit()
                logger.info("[GIVEAWAY] DB schema ensured")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] DB init error: {exc}")
            return

        # restore active giveaways
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, channel_id, message_id, end_time, min_messages, min_level FROM giveaways WHERE active=1")
                rows = await cur.fetchall()

            if not rows:
                logger.info("[GIVEAWAY] No active giveaways to restore")
            for r in rows:
                gid, channel_id, message_id, end_time, min_messages, min_level = r
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    logger.warning(f"[GIVEAWAY] restore: channel missing {channel_id} (giveaway {gid}) - marking inactive")
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
                        await db.commit()
                    continue

                try:
                    message = await channel.fetch_message(message_id)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] restore: cannot fetch message {message_id}: {exc}")
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
                        await db.commit()
                    continue

                view = PersistentGiveawayView(giveaway_id=gid, min_messages=min_messages or 0, min_level=min_level or 0)
                try:
                    self.bot.add_view(view, message_id=message.id)
                    logger.info(f"[GIVEAWAY] restored view for giveaway {gid} message {message.id}")
                except Exception:
                    self.bot.add_view(view)
                    logger.info(f"[GIVEAWAY] restored view (fallback) for giveaway {gid}")
            # start checker
            self._checker = self.bot.loop.create_task(self._checker_loop())
            logger.info("[GIVEAWAY] background checker started")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] restore failed: {exc}")

    async def _checker_loop(self):
        logger.debug("[GIVEAWAY] checker loop running")
        try:
            while True:
                try:
                    now = int(time.time())
                    async with aiosqlite.connect(DB_PATH) as db:
                        cur = await db.execute("SELECT id FROM giveaways WHERE active=1 AND end_time <= ?", (now,))
                        rows = await cur.fetchall()
                    for (gid,) in rows:
                        logger.info(f"[GIVEAWAY] time reached for giveaway {gid}")
                        await self._finalize_giveaway(gid)
                except Exception:
                    logger.exception("[GIVEAWAY] checker loop inner exception")
                await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[GIVEAWAY] checker loop cancelled")

    # ----------------- finalize giveaway -----------------
    async def _finalize_giveaway(self, giveaway_id: int):
        logger.info(f"[GIVEAWAY] finalizing giveaway {giveaway_id}")
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT channel_id, message_id, title, prize, winner_count FROM giveaways WHERE id=? AND active=1", (giveaway_id,))
                row = await cur.fetchone()
                if not row:
                    logger.warning(f"[GIVEAWAY] finalize: not found or already inactive {giveaway_id}")
                    return
                channel_id, message_id, title, prize, winner_count = row

                # fetch participants
                cur = await db.execute("SELECT user_id, won FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                rows = await cur.fetchall()
                participants = [int(r[0]) for r in rows]

                # mark inactive
                await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (giveaway_id,))
                await db.commit()

            channel = self.bot.get_channel(channel_id)
            message = None
            if channel and message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] finalize: failed to fetch original message {message_id}: {exc}")

            if not participants:
                if message:
                    try:
                        ended_embed = discord.Embed(title=f"{title} — ENDED", description=f"Prize: **{prize}**\nNo participants.", color=discord.Color.red())
                        await message.edit(embed=ended_embed, view=None)
                    except Exception as exc:
                        logger.warning(f"[GIVEAWAY] finalize: failed editing message: {exc}")
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    await db.commit()
                logger.info(f"[GIVEAWAY] finalize ended with no participants id={giveaway_id}")
                return

            # pick winners preferring those with won==0
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? AND won=0", (giveaway_id,))
                eligible_rows = await cur.fetchall()
                eligible = [int(r[0]) for r in eligible_rows]
                if not eligible:
                    cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    eligible = [int(r[0]) for r in await cur.fetchall()]

            winners_count = min(int(winner_count), len(eligible))
            winners = random.sample(eligible, winners_count)

            # mark winners
            async with aiosqlite.connect(DB_PATH) as db:
                for uid in winners:
                    await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (giveaway_id, uid))
                await db.commit()

            # edit original embed to ended + remove button
            if message:
                try:
                    ended_embed = discord.Embed(title=f"{title} — ENDED", description=f"Prize: **{prize}**\nWinners announced below.", color=discord.Color.dark_blue())
                    await message.edit(embed=ended_embed, view=None)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] finalize: failed to edit original message: {exc}")

            # announce winners with mentions (ping)
            mentions = " ".join(f"<@{uid}>" for uid in winners)
            winners_embed = discord.Embed(title="🎉 Giveaway Winners!", description=f"Prize: **{prize}**\nWinners: {mentions}", color=discord.Color.gold())
            if channel:
                try:
                    await channel.send(embed=winners_embed)
                except Exception:
                    logger.exception("[GIVEAWAY] finalize: failed to send winners embed")

            # cleanup entries (we remove them after finalization)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                await db.commit()

            logger.info(f"[GIVEAWAY] finalize completed id={giveaway_id} winners={winners}")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] finalize exception: {exc}")

    # ----------------- helpers -----------------
    def _parse_duration(self, s: str) -> Optional[int]:
        """
        Accepts combos like '1d2h30m', '2h', '45m', '1d', or '90m' etc.
        Returns seconds or None.
        """
        if not s or not isinstance(s, str):
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

    async def _is_manager(self, ctx: commands.Context) -> bool:
        try:
            if ctx.author.guild_permissions.manage_guild:
                return True
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT manager_role_id FROM guild_settings WHERE guild_id=?", (ctx.guild.id,))
                row = await cur.fetchone()
            if row and row[0]:
                role_id = int(row[0])
                role = ctx.guild.get_role(role_id)
                if role and role in ctx.author.roles:
                    return True
            # lenient role name check
            for r in ctx.author.roles:
                if "giveaway" in r.name.lower() or "manager" in r.name.lower():
                    return True
            return False
        except Exception:
            logger.exception("[GIVEAWAY] permission check failed")
            return False

    # ----------------- prefix command single endpoint -----------------
    @commands.command(name="giveaway")
    async def giveaway(self, ctx: commands.Context, *, raw: str = ""):
        """
        !giveaway preview "Title" "Prize" 1d2h30m winners min_messages min_level
        !giveaway start ...
        !giveaway end
        !giveaway reroll <id>
        !giveaway set_manager @Role
        !giveaway status
        """
        if not raw:
            return await ctx.send("Usage: !giveaway <preview|start|end|reroll|set_manager|status> ...")

        try:
            parts = shlex.split(raw)
        except Exception:
            parts = raw.split()
        sub = parts[0].lower()
        args = parts[1:]

        if sub == "preview":
            if len(args) < 3:
                return await ctx.send('Usage: !giveaway preview "Title" "Prize" <duration> <winners> [min_messages] [min_level]')
            title = args[0]
            prize = args[1]
            duration = args[2]
            winners = int(args[3]) if len(args) >= 4 else 1
            min_messages = int(args[4]) if len(args) >= 5 else 0
            min_level = int(args[5]) if len(args) >= 6 else 0

            seconds = self._parse_duration(duration)
            if seconds is None:
                return await ctx.send("Invalid duration format. Examples: 1d, 1h30m, 45m")

            embed = discord.Embed(title=f"[PREVIEW] {title}", description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin messages: {min_messages}\nMin level: {min_level}", color=discord.Color.yellow())
            embed.set_footer(text="Giveaway preview — not a real giveaway")
            view = PersistentGiveawayView(giveaway_id=0, min_messages=min_messages, min_level=min_level)
            for item in view.children:
                item.disabled = True
            await ctx.send(embed=embed, view=view)

            mock_mentions = " ".join([f"@User{i}" for i in range(1, max(2, winners + 1))])
            winners_embed = discord.Embed(title="[PREVIEW] Winners (mock)", description=f"Prize: **{prize}**\nWinners: {mock_mentions}", color=discord.Color.gold())
            await ctx.send(embed=winners_embed)
            logger.info(f"[GIVEAWAY] preview by {ctx.author.id} guild={ctx.guild.id}")

        elif sub == "start":
            allowed = await self._is_manager(ctx)
            if not allowed:
                await ctx.send("You are not allowed to start giveaways.")
                logger.warning(f"[GIVEAWAY] start blocked - not manager {ctx.author.id}")
                return

            if len(args) < 3:
                return await ctx.send('Usage: !giveaway start "Title" "Prize" <duration> <winners> [min_messages] [min_level]')

            title = args[0]
            prize = args[1]
            duration = args[2]
            winners = int(args[3]) if len(args) >= 4 else 1
            min_messages = int(args[4]) if len(args) >= 5 else 0
            min_level = int(args[5]) if len(args) >= 6 else 0

            seconds = self._parse_duration(duration)
            if seconds is None:
                return await ctx.send("Invalid duration format. Examples: 1d, 1h30m, 45m")

            # single active per guild
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                if await cur.fetchone():
                    await ctx.send("There is already an active giveaway in this server. End it first.")
                    logger.info(f"[GIVEAWAY] start blocked - active exists guild={ctx.guild.id}")
                    return

            start_time = int(time.time())
            end_time = start_time + seconds
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute(
                        "INSERT INTO giveaways (guild_id, channel_id, title, prize, winner_count, min_messages, min_level, start_time, end_time, active) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (ctx.guild.id, ctx.channel.id, title, prize, winners, min_messages, min_level, start_time, end_time),
                    )
                    gid = cur.lastrowid
                    await db.commit()

                embed = discord.Embed(title=title, description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin messages: {min_messages}\nMin level: {min_level}", color=discord.Color.green())
                embed.set_footer(text=f"Hosted by {ctx.author.display_name} — Click Join to enter")
                view = PersistentGiveawayView(giveaway_id=gid, min_messages=min_messages, min_level=min_level)
                msg = await ctx.send(embed=embed, view=view)

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
                    await db.commit()

                # register persistent view
                try:
                    self.bot.add_view(view, message_id=msg.id)
                except Exception:
                    self.bot.add_view(view)

                await ctx.send(f"Giveaway started (id: {gid}).")
                logger.info(f"[GIVEAWAY] started id={gid} guild={ctx.guild.id} by {ctx.author.id}")
            except Exception as exc:
                logger.exception(f"[GIVEAWAY] start failed: {exc}")
                await ctx.send("Failed to start giveaway due to internal error.")

        elif sub == "end":
            allowed = await self._is_manager(ctx)
            if not allowed:
                return await ctx.send("You are not allowed to end giveaways.")

            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                row = await cur.fetchone()
                if not row:
                    return await ctx.send("No active giveaway found.")
                gid = row[0]

            await self._finalize_giveaway(gid)
            await ctx.send("Giveaway ended.")
            logger.info(f"[GIVEAWAY] manually ended id={gid} by {ctx.author.id}")

        elif sub == "reroll":
            allowed = await self._is_manager(ctx)
            if not allowed:
                return await ctx.send("You are not allowed to reroll giveaways.")
            if len(args) < 1:
                return await ctx.send("Usage: !giveaway reroll <giveaway_id>")
            try:
                gid = int(args[0])
            except ValueError:
                return await ctx.send("Invalid giveaway id.")

            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT channel_id, prize, winner_count FROM giveaways WHERE id=? AND active=0", (gid,))
                row = await cur.fetchone()
                if not row:
                    return await ctx.send("Giveaway not found or still active.")
                channel_id, prize, winner_count = row

                # attempt to fetch participant snapshot in entries table
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,))
                rows = await cur.fetchall()
                participants = [int(r[0]) for r in rows]
                if not participants:
                    return await ctx.send("No participant snapshot available to reroll (entries were cleaned up).")

            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? AND won=0", (gid,))
                    eligible_rows = await cur.fetchall()
                    eligible = [int(r[0]) for r in eligible_rows]
                    if not eligible:
                        cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,))
                        eligible = [int(r[0]) for r in await cur.fetchall()]

                    winners_count = min(int(winner_count), len(eligible))
                    winners = random.sample(eligible, winners_count)
                    for uid in winners:
                        await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (gid, uid))
                    await db.commit()
            except Exception as exc:
                logger.exception(f"[GIVEAWAY] reroll DB error: {exc}")
                return await ctx.send("Reroll failed due to DB error.")

            channel = self.bot.get_channel(channel_id)
            if channel:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                embed = discord.Embed(title="🎉 Giveaway Reroll Winners!", description=f"Prize: **{prize}**\nWinners: {mentions}", color=discord.Color.gold())
                await channel.send(embed=embed)
                await ctx.send("Reroll complete; winners announced.")
                logger.info(f"[GIVEAWAY] reroll id={gid} winners={winners}")
            else:
                await ctx.send("Reroll complete but original channel not found.")
                logger.warning(f"[GIVEAWAY] reroll: channel missing for id={gid}")

        elif sub == "set_manager":
            if not ctx.author.guild_permissions.manage_guild and ctx.author != ctx.guild.owner:
                return await ctx.send("Only guild managers/owners can set manager role.")
            if not args:
                return await ctx.send("Usage: !giveaway set_manager @Role or role_id")
            role = None
            if ctx.message.role_mentions:
                role = ctx.message.role_mentions[0]
            else:
                try:
                    rid = int(args[0])
                    role = ctx.guild.get_role(rid)
                except Exception:
                    pass
            if not role:
                return await ctx.send("Role not found. Mention a role or pass role id.")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO guild_settings (guild_id, manager_role_id) VALUES (?, ?)", (ctx.guild.id, role.id))
                await db.commit()
            await ctx.send(f"Giveaway manager role set to {role.mention}")
            logger.info(f"[GIVEAWAY] set_manager guild={ctx.guild.id} role={role.id} by {ctx.author.id}")

        elif sub == "status":
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, title, prize, winner_count, end_time FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                row = await cur.fetchone()
                if not row:
                    return await ctx.send("No active giveaway in this server.")
                gid, title, prize, winner_count, end_time = row
                remaining = int(end_time) - int(time.time())
                if remaining < 0:
                    remaining = 0
                days = remaining // 86400
                hours = (remaining % 86400) // 3600
                mins = (remaining % 3600) // 60
                secs = remaining % 60
                cur = await db.execute("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (gid,))
                pcount_row = await cur.fetchone()
                pcount = pcount_row[0] if pcount_row else 0
                time_parts = []
                if days:
                    time_parts.append(f"{days}d")
                if hours:
                    time_parts.append(f"{hours}h")
                if mins:
                    time_parts.append(f"{mins}m")
                if not time_parts:
                    time_parts.append(f"{secs}s")
                await ctx.send(f"Active Giveaway {gid}: {title} | Prize: {prize} | Winners: {winner_count} | Participants: {pcount} | Time left: {' '.join(time_parts)}")
        else:
            await ctx.send("Unknown subcommand. Use preview|start|end|reroll|set_manager|status")

    # ----------------- unload -----------------
    def cog_unload(self):
        try:
            if hasattr(self, "_restore_task"):
                self._restore_task.cancel()
            if hasattr(self, "_checker"):
                self._checker.cancel()
            logger.info("[GIVEAWAY] Cog unloaded and tasks cancelled")
        except Exception:
            logger.exception("[GIVEAWAY] Cog unload exception")


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
    logger.info("[GIVEAWAY] Cog setup complete (v2)")
