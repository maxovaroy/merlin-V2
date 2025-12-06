# cogs/giveaway.py
"""
Giveaway cog (prefix commands, !giveaway)
- Persisted in SQLite (giveaways, giveaway_entries, guild_settings)
- Restores active giveaways + views after bot restart
- Join via button only (entries added when user clicks)
- Validates min_messages and min_level using users table
- Commands: start, preview, end, reroll, set_manager, status
"""

import asyncio
import aiosqlite
import random
import time
import shlex
from typing import Optional, List

import discord
from discord.ext import commands

from logger import logger  # your repo logger

DB_PATH = "database.db"
CHECK_INTERVAL = 15  # seconds between background checks


class PersistentGiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int, min_messages: int, min_level: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.min_messages = int(min_messages or 0)
        self.min_level = int(min_level or 0)

    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, custom_id="giveaway_join_button")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # verify giveaway still active
                cur = await db.execute("SELECT active FROM giveaways WHERE id=?", (self.giveaway_id,))
                row = await cur.fetchone()
                if not row or row[0] != 1:
                    await interaction.response.send_message("This giveaway is no longer active.", ephemeral=True)
                    logger.info(f"[GIVEAWAY] join blocked - inactive giveaway {self.giveaway_id} by {user.id}")
                    return

                # fetch user stats from users table
                cur = await db.execute("SELECT messages, level FROM users WHERE user_id=?", (user.id,))
                urow = await cur.fetchone()
                if not urow:
                    await interaction.response.send_message("You must chat a bit first before joining giveaways.", ephemeral=True)
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

                # register entry
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
        self._bg_task = self.bot.loop.create_task(self._init_and_restore())
        logger.info("[GIVEAWAY] Cog loaded and restore task started")

    # ---------------- DB initialization & restore ----------------
    async def _init_and_restore(self):
        # ensure DB and tables exist
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
        except Exception as e:
            logger.exception(f"[GIVEAWAY] DB init failed: {e}")
            return

        # Restore active giveaways: re-add view and continue background checking
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
                    logger.warning(f"[GIVEAWAY] restore: channel not found {channel_id} (giveaway {gid})")
                    # mark inactive to avoid loops
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
                        await db.commit()
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] restore: failed to fetch message {message_id}: {exc}")
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
                        await db.commit()
                    continue

                view = PersistentGiveawayView(giveaway_id=gid, min_messages=min_messages or 0, min_level=min_level or 0)
                try:
                    # register view bound to message so button works after restart
                    self.bot.add_view(view, message_id=message.id)
                    logger.info(f"[GIVEAWAY] restored view for giveaway {gid} message {message.id}")
                except Exception:
                    self.bot.add_view(view)
                    logger.info(f"[GIVEAWAY] restored view (fallback) for giveaway {gid}")

            # start checker loop
            self._checker_task = self.bot.loop.create_task(self._checker_loop())
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
                        logger.info(f"[GIVEAWAY] scheduled end found -> id={gid}")
                        # finalize (message fetch inside finalizer)
                        await self._finalize_giveaway(gid)
                except Exception:
                    logger.exception("[GIVEAWAY] Error in checker loop")
                await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[GIVEAWAY] checker loop cancelled")

    # ---------------- core finalizer ----------------
    async def _finalize_giveaway(self, giveaway_id: int):
        """Pick winners, edit original embed to ended version (remove button), post winners embed, cleanup entries."""
        logger.info(f"[GIVEAWAY] finalizing giveaway {giveaway_id}")
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT channel_id, message_id, title, prize, winner_count FROM giveaways WHERE id=? AND active=1", (giveaway_id,))
                row = await cur.fetchone()
                if not row:
                    logger.warning(f"[GIVEAWAY] finalize: giveaway not found or already inactive {giveaway_id}")
                    return
                channel_id, message_id, title, prize, winner_count = row

                # fetch participants
                cur = await db.execute("SELECT user_id, won FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                parts = await cur.fetchall()
                participants = [int(p[0]) for p in parts]

                # Mark giveaway inactive and remove entries after selection
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
                # edit message to ended/no participants and remove view
                if message:
                    try:
                        ended = discord.Embed(title=f"{title} — ENDED", description=f"Prize: **{prize}**\nNo participants.", color=discord.Color.red())
                        await message.edit(embed=ended, view=None)
                    except Exception as exc:
                        logger.warning(f"[GIVEAWAY] finalize: failed edit message on no participants: {exc}")
                # cleanup entries (none) and leave
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    await db.commit()
                logger.info(f"[GIVEAWAY] finalize: ended with no participants -> id={giveaway_id}")
                return

            # pick winners; prefer entries where won==0 (fresh winners)
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? AND won=0", (giveaway_id,))
                eligible_rows = await cur.fetchall()
                eligible = [int(r[0]) for r in eligible_rows]
                if not eligible:
                    # fallback to anyone who entered
                    cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    eligible = [int(r[0]) for r in await cur.fetchall()]

            winners_count = min(int(winner_count), len(eligible))
            winners = random.sample(eligible, winners_count)

            # mark winners in DB
            async with aiosqlite.connect(DB_PATH) as db:
                for uid in winners:
                    await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (giveaway_id, uid))
                await db.commit()

            # edit original embed to ended and remove view
            if message:
                try:
                    ended = discord.Embed(title=f"{title} — ENDED", description=f"Prize: **{prize}**\nWinners announced below.", color=discord.Color.dark_blue())
                    await message.edit(embed=ended, view=None)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] finalize: failed to edit original message ended: {exc}")

            # announce winners
            mentions = " ".join(f"<@{uid}>" for uid in winners)
            winners_embed = discord.Embed(title="🎉 Giveaway Winners!", description=f"Prize: **{prize}**\nWinners: {mentions}", color=discord.Color.gold())
            if channel:
                try:
                    await channel.send(embed=winners_embed)
                except Exception:
                    logger.exception("[GIVEAWAY] finalize: failed to send winners embed")

            # cleanup entries (delete)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                await db.commit()

            logger.info(f"[GIVEAWAY] finalize completed id={giveaway_id} winners={winners}")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] finalize exception: {exc}")

    # ---------------- helpers ----------------
    def _parse_duration(self, s: str) -> Optional[int]:
        if not s or len(s) < 2:
            return None
        unit = s[-1].lower()
        try:
            num = int(s[:-1])
        except ValueError:
            return None
        if unit == "m":
            return num * 60
        if unit == "h":
            return num * 3600
        if unit == "d":
            return num * 86400
        return None

    async def _is_manager(self, ctx: commands.Context) -> bool:
        # allow server owners or manage_guild permission
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
            # lenient check for roles containing 'giveaway' or 'manager'
            for r in ctx.author.roles:
                if "giveaway" in r.name.lower() or "manager" in r.name.lower():
                    return True
            return False
        except Exception:
            logger.exception("[GIVEAWAY] permission check failed")
            return False

    # ---------------- Command: single entry point ----------------
    @commands.command(name="giveaway")
    async def giveaway(self, ctx: commands.Context, *, raw: str = ""):
        """
        Single prefix command endpoint:
        Usage:
          !giveaway start "Title" "Prize" 1h 1 min_messages min_level
          !giveaway preview "Title" "Prize" 10m 1 min_messages min_level
          !giveaway end
          !giveaway reroll <id>
          !giveaway set_manager @Role
          !giveaway status
        """
        if not raw:
            await ctx.send("Usage: !giveaway <start|preview|end|reroll|set_manager|status> ...")
            return

        # simple tokenization using shlex to support quoted strings
        try:
            parts = shlex.split(raw)
        except Exception:
            parts = raw.split()
        sub = parts[0].lower()
        args = parts[1:]

        if sub == "preview":
            # preview "title" "prize" duration winners min_messages min_level
            if len(args) < 3:
                return await ctx.send("Usage: !giveaway preview \"Title\" \"Prize\" <duration> <winners> [min_messages] [min_level]")
            title = args[0]
            prize = args[1]
            duration = args[2]
            winners = int(args[3]) if len(args) >= 4 else 1
            min_messages = int(args[4]) if len(args) >= 5 else 0
            min_level = int(args[5]) if len(args) >= 6 else 0

            seconds = self._parse_duration(duration)
            if seconds is None:
                return await ctx.send("Invalid duration format. Use examples: 30m, 1h, 2d")

            embed = discord.Embed(title=f"[PREVIEW] {title}", description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin messages: {min_messages}\nMin level: {min_level}", color=discord.Color.yellow())
            embed.set_footer(text="Giveaway preview — this does not start a giveaway")

            view = PersistentGiveawayView(giveaway_id=0, min_messages=min_messages, min_level=min_level)
            # disable button on preview
            for item in view.children:
                item.disabled = True

            await ctx.send(embed=embed, view=view)
            # mock winners
            mock_mentions = " ".join([f"@User{i}" for i in range(1, max(2, winners + 1))])
            winners_embed = discord.Embed(title="[PREVIEW] Winners (mock)", description=f"Prize: **{prize}**\nWinners: {mock_mentions}", color=discord.Color.gold())
            await ctx.send(embed=winners_embed)
            logger.info(f"[GIVEAWAY] preview shown by {ctx.author} in {ctx.guild.id}")

        elif sub == "start":
            # start "title" "prize" duration winners min_messages min_level
            # permission check
            allowed = await self._is_manager(ctx)
            if not allowed:
                await ctx.send("You are not allowed to start giveaways.")
                logger.warning(f"[GIVEAWAY] start blocked - user {ctx.author.id} not manager")
                return

            if len(args) < 3:
                return await ctx.send("Usage: !giveaway start \"Title\" \"Prize\" <duration> <winners> [min_messages] [min_level]")

            title = args[0]
            prize = args[1]
            duration = args[2]
            winners = int(args[3]) if len(args) >= 4 else 1
            min_messages = int(args[4]) if len(args) >= 5 else 0
            min_level = int(args[5]) if len(args) >= 6 else 0

            seconds = self._parse_duration(duration)
            if seconds is None:
                return await ctx.send("Invalid duration format. Use examples: 30m, 1h, 2d")

            # ensure only one active giveaway per guild
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                if await cur.fetchone():
                    await ctx.send("There is already an active giveaway in this server. End it first.")
                    logger.info(f"[GIVEAWAY] start blocked - active exists in guild {ctx.guild.id}")
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
                # post embed
                embed = discord.Embed(title=title, description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin messages: {min_messages}\nMin level: {min_level}", color=discord.Color.green())
                embed.set_footer(text=f"Hosted by {ctx.author.display_name} — Click Join to enter")
                view = PersistentGiveawayView(giveaway_id=gid, min_messages=min_messages, min_level=min_level)
                msg = await ctx.send(embed=embed, view=view)
                # update message_id
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (msg.id, gid))
                    await db.commit()
                # register view so button persists after restart
                try:
                    self.bot.add_view(view, message_id=msg.id)
                except Exception:
                    self.bot.add_view(view)
                await ctx.send(f"Giveaway started (id: {gid}).")
                logger.info(f"[GIVEAWAY] started id={gid} guild={ctx.guild.id} by={ctx.author.id}")
            except Exception as exc:
                logger.exception(f"[GIVEAWAY] start failed: {exc}")
                await ctx.send("Failed to start giveaway due to internal error.")

        elif sub == "end":
            allowed = await self._is_manager(ctx)
            if not allowed:
                await ctx.send("You are not allowed to end giveaways.")
                return
            # find active giveaway
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                row = await cur.fetchone()
                if not row:
                    await ctx.send("No active giveaway found.")
                    return
                gid = row[0]
            await self._finalize_giveaway(gid)
            await ctx.send("Giveaway ended.")
            logger.info(f"[GIVEAWAY] manually ended id={gid} by {ctx.author.id}")

        elif sub == "reroll":
            allowed = await self._is_manager(ctx)
            if not allowed:
                await ctx.send("You are not allowed to reroll giveaways.")
                return
            if len(args) < 1:
                return await ctx.send("Usage: !giveaway reroll <giveaway_id>")
            try:
                gid = int(args[0])
            except ValueError:
                return await ctx.send("Invalid giveaway id.")
            # Ensure giveaway exists and is inactive
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT channel_id, prize, winner_count FROM giveaways WHERE id=? AND active=0", (gid,))
                row = await cur.fetchone()
                if not row:
                    await ctx.send("Giveaway not found or still active.")
                    return
                channel_id, prize, winner_count = row
                # fetch participants snapshot - note: we delete entries at finalize; if none found, reroll isn't possible
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,))
                rows = await cur.fetchall()
                participants = [int(r[0]) for r in rows]
                if not participants:
                    await ctx.send("No participant snapshot available to reroll.")
                    logger.warning(f"[GIVEAWAY] reroll failed - no participants for id={gid}")
                    return

            # choose new winners excluding already won where possible
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
                    # mark winners
                    for uid in winners:
                        await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (gid, uid))
                    await db.commit()
            except Exception as exc:
                logger.exception(f"[GIVEAWAY] reroll failed: {exc}")
                return await ctx.send("Reroll failed due to DB error.")

            channel = self.bot.get_channel(channel_id)
            if channel:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                embed = discord.Embed(title="🎉 Giveaway Reroll Winners!", description=f"Prize: **{prize}**\nWinners: {mentions}", color=discord.Color.gold())
                await channel.send(embed=embed)
                await ctx.send("Reroll complete; winners announced.")
                logger.info(f"[GIVEAWAY] reroll id={gid} winners={winners}")
            else:
                await ctx.send("Reroll complete but original channel not found for announcement.")
                logger.warning(f"[GIVEAWAY] reroll channel missing for id={gid}")

        elif sub == "set_manager":
            # usage: !giveaway set_manager @Role
            if not ctx.author.guild_permissions.manage_guild and ctx.author != ctx.guild.owner:
                return await ctx.send("Only guild managers/owners can set the manager role.")
            if not args:
                return await ctx.send("Usage: !giveaway set_manager @Role or role_id")
            # attempt to parse role
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
            # show active giveaway in the guild
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, title, prize, winner_count, end_time FROM giveaways WHERE guild_id=? AND active=1", (ctx.guild.id,))
                row = await cur.fetchone()
                if not row:
                    return await ctx.send("No active giveaway found in this server.")
                gid, title, prize, winner_count, end_time = row
                remaining = int(end_time) - int(time.time())
                mins = remaining // 60
                secs = remaining % 60
                # participants count
                cur = await db.execute("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (gid,))
                pcount_row = await cur.fetchone()
                pcount = pcount_row[0] if pcount_row else 0
                await ctx.send(f"Active Giveaway {gid}: {title} | Prize: {prize} | Winners: {winner_count} | Participants: {pcount} | Time left: {mins}m {secs}s")
        else:
            await ctx.send("Unknown subcommand. Use start|preview|end|reroll|set_manager|status")

    def cog_unload(self):
        # Cancel background tasks on unload
        try:
            if hasattr(self, "_bg_task"):
                self._bg_task.cancel()
            if hasattr(self, "_checker_task"):
                self._checker_task.cancel()
            logger.info("[GIVEAWAY] Cog unloaded")
        except Exception:
            logger.exception("[GIVEAWAY] Cog unload error")


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
    logger.info("[GIVEAWAY] Cog setup complete")
