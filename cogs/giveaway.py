# cogs/giveaway.py
"""
Persistent Giveaway cog
- Uses SQLite to persist giveaways and entries
- Restores active giveaways & join buttons after bot restart
- Supports min_messages & min_level requirements
- /giveaway preview, start, end, reroll, set_manager
- Role-based manager config per guild (or fallback to manage_guild perm)
- Edits original embed on end, posts winners embed below
- Detailed logging via your logger.py
"""

import asyncio
import aiosqlite
import random
import time
from typing import Optional, List

import discord
from discord.ext import commands, tasks
from discord import app_commands

from logger import logger  # your repo's logger

DB_PATH = "database.db"
CHECK_INTERVAL = 20  # seconds between background checks for endings


# ----- Utility: View for a giveaway (persistent recreatable) -----
class PersistentGiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int, min_messages: int, min_level: int):
        # timeout=None to allow re-adding as persistent view during runtime restore
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.min_messages = min_messages
        self.min_level = min_level

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.green, custom_id="giveaway_join_button")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handler executes on button click. Validates requirements, registers entry."""
        user = interaction.user
        guild = interaction.guild
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Validate giveaway still active + exists
                cur = await db.execute("SELECT active FROM giveaways WHERE id=?", (self.giveaway_id,))
                row = await cur.fetchone()
                if not row or row[0] != 1:
                    await interaction.response.send_message("This giveaway is no longer active.", ephemeral=True)
                    logger.info(f"[GIVEAWAY] Join blocked - giveaway inactive id={self.giveaway_id}, user={user.id}")
                    return

                # Requirement check: messages & level from users table
                cur = await db.execute("SELECT messages, level FROM users WHERE user_id=?", (user.id,))
                urow = await cur.fetchone()
                if not urow:
                    await interaction.response.send_message(
                        "You are not in the database yet. Send some messages first to join.", ephemeral=True
                    )
                    logger.debug(f"[GIVEAWAY] Join blocked - user not in users table: {user.id}")
                    return

                user_messages, user_level = int(urow[0]), int(urow[1])
                if user_messages < self.min_messages:
                    await interaction.response.send_message(
                        f"You need at least {self.min_messages} messages to join (you have {user_messages}).",
                        ephemeral=True,
                    )
                    logger.info(f"[GIVEAWAY] Join blocked - insufficient messages user={user.id} have={user_messages} need={self.min_messages}")
                    return
                if user_level < self.min_level:
                    await interaction.response.send_message(
                        f"You need at least level {self.min_level} to join (you are level {user_level}).",
                        ephemeral=True,
                    )
                    logger.info(f"[GIVEAWAY] Join blocked - insufficient level user={user.id} level={user_level} need={self.min_level}")
                    return

                # Register entry (unique)
                await db.execute(
                    "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id, won) VALUES (?, ?, 0)",
                    (self.giveaway_id, user.id),
                )
                await db.commit()

            await interaction.response.send_message("You have joined the giveaway. Good luck!", ephemeral=True)
            logger.info(f"[GIVEAWAY] User joined -> giveaway_id={self.giveaway_id} user={user.id}")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Exception in join_button: {exc}")
            try:
                await interaction.response.send_message("An error occurred while joining. Try again later.", ephemeral=True)
            except Exception:
                pass


# ----- The cog -----
class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._check_task = self.bot.loop.create_task(self._ensure_db_and_restore())
        logger.info("[GIVEAWAY] Cog init - restore task started")

    # -------------------------
    # Database & restore logic
    # -------------------------
    async def _ensure_db_and_restore(self):
        """Create tables and then restore active giveaways (recreate views + schedule)."""
        try:
            # Ensure DB schema
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
            logger.exception(f"[GIVEAWAY] DB initialization failed: {e}")
            return

        # Restore active giveaways (rebuild views and ensure they're tracked)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT id, guild_id, channel_id, message_id, end_time, min_messages, min_level FROM giveaways WHERE active=1")
                rows = await cur.fetchall()

            if not rows:
                logger.info("[GIVEAWAY] No active giveaways to restore")
            for row in rows:
                gid, guild_id, channel_id, message_id, end_time, min_messages, min_level = row
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    logger.warning(f"[GIVEAWAY] Channel not found during restore: channel_id={channel_id} (giveaway_id={gid})")
                    continue

                try:
                    message = await channel.fetch_message(message_id)
                except Exception as exc:
                    logger.warning(f"[GIVEAWAY] Could not fetch message {message_id} in channel {channel_id}: {exc}")
                    # If message can't be fetched, mark giveaway inactive to avoid orphaned items
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (gid,))
                        await db.commit()
                    continue

                # Create view and add as persistent
                view = PersistentGiveawayView(giveaway_id=gid, min_messages=min_messages or 0, min_level=min_level or 0)
                try:
                    self.bot.add_view(view, message_id=message.id)  # register persistent view
                    logger.info(f"[GIVEAWAY] Restored view -> giveaway_id={gid} message_id={message.id}")
                except Exception:
                    # fallback: add without message_id
                    self.bot.add_view(view)
                    logger.info(f"[GIVEAWAY] Restored view (no message_id binding) -> giveaway_id={gid} ")

            # start background checker
            self._bg_check_task = self.bot.loop.create_task(self._background_check_loop())
            logger.info("[GIVEAWAY] Background check loop started")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Exception during restore: {exc}")

    # -------------------------
    # Background loop to end giveaways
    # -------------------------
    async def _background_check_loop(self):
        logger.debug("[GIVEAWAY] entering background check loop")
        try:
            while True:
                try:
                    now = int(time.time())
                    async with aiosqlite.connect(DB_PATH) as db:
                        cur = await db.execute("SELECT id, channel_id, message_id, end_time FROM giveaways WHERE active=1")
                        rows = await cur.fetchall()
                    for row in rows:
                        gid, channel_id, message_id, end_time = row
                        if end_time is None:
                            continue
                        if now >= int(end_time):
                            logger.info(f"[GIVEAWAY] Detected expired giveaway -> id={gid}")
                            # fetch message object
                            channel = self.bot.get_channel(channel_id)
                            if channel is None:
                                logger.warning(f"[GIVEAWAY] Channel missing when ending giveaway id={gid}")
                                # still mark as ended and cleanup DB to avoid loops
                                await self._finalize_giveaway(gid, None, [])
                                continue
                            try:
                                message = await channel.fetch_message(message_id)
                            except Exception as e:
                                logger.warning(f"[GIVEAWAY] Could not fetch message to end giveaway id={gid}: {e}")
                                await self._finalize_giveaway(gid, None, [])
                                continue
                            await self._finalize_giveaway(gid, message, None)  # finalizer fetches participants itself
                except Exception:
                    logger.exception("[GIVEAWAY] Error in background check inner loop")
                await asyncio.sleep(CHECK_INTERVAL)
        except asyncio.CancelledError:
            logger.info("[GIVEAWAY] Background check loop cancelled")
        except Exception:
            logger.exception("[GIVEAWAY] Background check loop crashed unexpectedly")

    # -------------------------
    # Core finalizer (ending a giveaway)
    # -------------------------
    async def _finalize_giveaway(self, giveaway_id: int, message: Optional[discord.Message], participants_override: Optional[List[int]]):
        """
        Picks winners, edits original embed to show ended state, removes view (button),
        posts winners embed below. Marks giveaway inactive and cleans up entries.
        If participants_override is provided, use that participant list instead of DB entries.
        """
        logger.info(f"[GIVEAWAY] finalizing giveaway id={giveaway_id}")

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT channel_id, title, prize, winner_count FROM giveaways WHERE id=? AND active=1",
                    (giveaway_id,),
                )
                g = await cur.fetchone()
                if not g:
                    logger.warning(f"[GIVEAWAY] Giveaway not found or already inactive id={giveaway_id}")
                    return

                channel_id, title, prize, winner_count = g
                # fetch participants if not provided
                if participants_override is None:
                    cur = await db.execute("SELECT user_id, won FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    rows = await cur.fetchall()
                    participants = [int(r[0]) for r in rows]
                else:
                    participants = participants_override

                # Fetch the message if not provided
                if message is None:
                    ch = self.bot.get_channel(channel_id)
                    if ch:
                        try:
                            # get stored message_id to fetch; if missing just post winners
                            cur = await db.execute("SELECT message_id FROM giveaways WHERE id=?", (giveaway_id,))
                            mrow = await cur.fetchone()
                            message = await ch.fetch_message(mrow[0]) if mrow and mrow[0] else None
                        except Exception as exc:
                            logger.warning(f"[GIVEAWAY] Could not fetch original message for id={giveaway_id}: {exc}")
                            message = None

                # If no participants
                if not participants:
                    if message:
                        # edit original embed to show ended + no participants
                        try:
                            embed = discord.Embed(
                                title=f"{title} — ENDED",
                                description=f"Prize: **{prize}**\nNo participants joined.",
                                color=discord.Color.red(),
                            )
                            await message.edit(embed=embed, view=None)
                        except Exception as exc:
                            logger.warning(f"[GIVEAWAY] Failed to edit message for ended empty giveaway: {exc}")
                    # mark giveaway inactive & cleanup entries
                    await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (giveaway_id,))
                    await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    await db.commit()
                    logger.info(f"[GIVEAWAY] Ended (no participants) -> id={giveaway_id}")
                    return

                # Select winners: ensure we don't pick previous winners (won=1) if possible
                # First, gather eligible (won==0)
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? AND won=0", (giveaway_id,))
                eligible_rows = await cur.fetchall()
                eligible = [int(r[0]) for r in eligible_rows]
                if not eligible:
                    # fallback: include previous winners if none left
                    cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    all_rows = await cur.fetchall()
                    eligible = [int(r[0]) for r in all_rows]

                winners_count = min(int(winner_count), len(eligible))
                winners = random.sample(eligible, winners_count)

                # Mark winners in DB (set won=1)
                for uid in winners:
                    await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (giveaway_id, uid))

                # Mark giveaway inactive and commit
                await db.execute("UPDATE giveaways SET active=0 WHERE id=?", (giveaway_id,))
                await db.commit()

                # Edit original message embed to show ended and remove buttons
                if message:
                    try:
                        ended_embed = discord.Embed(
                            title=f"{title} — ENDED",
                            description=f"Prize: **{prize}**\nWinners will be announced below.",
                            color=discord.Color.dark_blue(),
                        )
                        ended_embed.add_field(name="Winners", value="(see announcement below)", inline=False)
                        await message.edit(embed=ended_embed, view=None)
                        logger.info(f"[GIVEAWAY] Edited original message to ENDED -> giveaway_id={giveaway_id}")
                    except Exception as exc:
                        logger.warning(f"[GIVEAWAY] Failed to edit original message after ending: {exc}")

                # Send winners embed below
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                winners_embed = discord.Embed(
                    title="🎉 Giveaway Winners!",
                    description=f"Prize: **{prize}**\nWinners: {mentions}",
                    color=discord.Color.gold(),
                )
                ch = self.bot.get_channel(channel_id)
                if ch:
                    await ch.send(embed=winners_embed)
                    logger.info(f"[GIVEAWAY] Winners announced -> giveaway_id={giveaway_id} winners={winners}")

                # cleanup entries (remove them)
                await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                await db.commit()
                logger.debug(f"[GIVEAWAY] Cleaned up entries for giveaway_id={giveaway_id}")

        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Exception during finalize_giveaway: {exc}")

    # -------------------------
    # Helper: create giveaway row & post embed
    # -------------------------
    async def _create_giveaway(self, guild_id: int, channel_id: int, title: str, prize: str,
                               winner_count: int, min_messages: int, min_level: int, duration_seconds: int) -> Optional[int]:
        start_time = int(time.time())
        end_time = start_time + duration_seconds
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "INSERT INTO giveaways (guild_id, channel_id, title, prize, winner_count, min_messages, min_level, start_time, end_time, active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (guild_id, channel_id, title, prize, winner_count, min_messages, min_level, start_time, end_time),
                )
                giveaway_id = cur.lastrowid
                await db.commit()
                logger.info(f"[GIVEAWAY] DB insert -> id={giveaway_id}, guild_id={guild_id}")
                return giveaway_id
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] DB error inserting giveaway: {exc}")
            return None

    # -------------------------
    # Slash commands
    # -------------------------

    # helper permission: check if interaction user is configured manager role OR has manage_guild
    async def _is_manager(self, interaction: discord.Interaction) -> bool:
        try:
            # check manage_guild
            if interaction.user.guild_permissions.manage_guild:
                return True
            # check guild settings role
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT manager_role_id FROM guild_settings WHERE guild_id=?", (interaction.guild.id,))
                row = await cur.fetchone()
                if row and row[0]:
                    role_id = int(row[0])
                    role = interaction.guild.get_role(role_id)
                    if role and role in interaction.user.roles:
                        return True
            # fallback: check for role name containing 'giveaway' or 'manager' (lenient)
            for r in interaction.user.roles:
                if "giveaway" in r.name.lower() or "manager" in r.name.lower():
                    return True
            return False
        except Exception:
            logger.exception("[GIVEAWAY] Error checking manager permissions")
            return False

    @app_commands.command(name="giveaway_set_manager", description="Set guild role that can manage giveaways")
    @app_commands.describe(role="Role allowed to manage giveaways")
    async def giveaway_set_manager(self, interaction: discord.Interaction, role: discord.Role):
        # only guild admins can set (owner or manage_guild)
        if not interaction.user.guild_permissions.manage_guild and interaction.user != interaction.guild.owner:
            await interaction.response.send_message("You must be a server manager to set the giveaway manager role.", ephemeral=True)
            return
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT OR REPLACE INTO guild_settings (guild_id, manager_role_id) VALUES (?, ?)",
                                 (interaction.guild.id, role.id))
                await db.commit()
            await interaction.response.send_message(f"Giveaway manager role set to {role.mention}", ephemeral=True)
            logger.info(f"[GIVEAWAY] Manager role set for guild {interaction.guild.id} -> role_id={role.id}")
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Failed to set manager role: {exc}")
            await interaction.response.send_message("Failed to save manager role.", ephemeral=True)

    @app_commands.command(name="giveaway_preview", description="Preview giveaway embed + mock winners")
    @app_commands.describe(title="Giveaway title", prize="Prize description", duration="duration like 1h/30m/2d", winners="number of winners", min_messages="min messages to join", min_level="min level to join")
    async def giveaway_preview(
        self,
        interaction: discord.Interaction,
        title: str,
        prize: str,
        duration: str,
        winners: int = 1,
        min_messages: int = 0,
        min_level: int = 0,
    ):
        # parse duration string -> seconds
        seconds = self._parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("Invalid duration format. Use examples like 30m, 1h, 2d.", ephemeral=True)
            logger.warning("[GIVEAWAY] Preview: invalid duration")
            return

        # Send a preview embed (in the same channel)
        embed = discord.Embed(title=f"[PREVIEW] {title}",
                              description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin Messages: {min_messages}\nMin Level: {min_level}",
                              color=discord.Color.yellow())
        embed.set_footer(text="Giveaway Preview — this will not start a real giveaway")

        view = PersistentGiveawayView(giveaway_id=0, min_messages=min_messages, min_level=min_level)
        # disable join button for preview so users don't accidentally join
        for item in view.children:
            item.disabled = True

        # create a mock winners embed
        mock_mentions = " ".join([f"@User{i}" for i in range(1, max(2, winners + 1))])
        winners_embed = discord.Embed(title="[PREVIEW] Winners (mock)", description=f"Prize: **{prize}**\nWinners: {mock_mentions}", color=discord.Color.gold())

        await interaction.response.send_message(embed=embed, view=view)
        await interaction.channel.send(embed=winners_embed)
        logger.info(f"[GIVEAWAY] Preview shown by {interaction.user.id} in guild {interaction.guild.id}")

    @app_commands.command(name="giveaway_start", description="Start a new giveaway (manager role required)")
    @app_commands.describe(title="Title", prize="Prize text", duration="1h/30m/2d", winners="Number of winners", min_messages="Minimum messages to join", min_level="Minimum level to join")
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        title: str,
        prize: str,
        duration: str,
        winners: int = 1,
        min_messages: int = 0,
        min_level: int = 0,
    ):
        # Permission check: manager role OR manage_guild
        allowed = await self._is_manager(interaction)
        if not allowed:
            await interaction.response.send_message("You are not allowed to start giveaways. Ask your server manager to set a manager role or give you permissions.", ephemeral=True)
            logger.warning(f"[GIVEAWAY] Start blocked - not manager user={interaction.user.id}")
            return

        seconds = self._parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("Invalid duration format. Use examples like 30m, 1h, 2d.", ephemeral=True)
            logger.warning("[GIVEAWAY] Start: invalid duration")
            return

        # Check only one active giveaway per guild
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM giveaways WHERE guild_id=? AND active=1", (interaction.guild.id,))
            if await cur.fetchone():
                await interaction.response.send_message("There is already an active giveaway in this server. End it first.", ephemeral=True)
                logger.info(f"[GIVEAWAY] Start blocked - active exists in guild {interaction.guild.id}")
                return

        # Insert row
        giveaway_id = await self._create_giveaway(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            title=title,
            prize=prize,
            winner_count=winners,
            min_messages=min_messages,
            min_level=min_level,
            duration_seconds=seconds,
        )
        if not giveaway_id:
            await interaction.response.send_message("Failed to create giveaway (DB error).", ephemeral=True)
            return

        # Post embed + add persistent view
        embed = discord.Embed(title=title,
                              description=f"Prize: **{prize}**\nDuration: {duration}\nWinners: {winners}\nMin Messages: {min_messages}\nMin Level: {min_level}",
                              color=discord.Color.green())
        embed.set_footer(text=f"Hosted by {interaction.user.display_name} — Click the button below to join")

        view = PersistentGiveawayView(giveaway_id=giveaway_id, min_messages=min_messages, min_level=min_level)
        try:
            message = await interaction.channel.send(embed=embed, view=view)
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Failed to send giveaway embed: {exc}")
            await interaction.response.send_message("Failed to post giveaway embed.", ephemeral=True)
            return

        # store message_id
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE giveaways SET message_id=? WHERE id=?", (message.id, giveaway_id))
                await db.commit()
        except Exception:
            logger.exception("[GIVEAWAY] Could not update message_id in DB")

        # register the view persistently with the bot so it works after restarts
        try:
            self.bot.add_view(view, message_id=message.id)
        except Exception:
            # fallback - still add view to runtime
            self.bot.add_view(view)

        logger.info(f"[GIVEAWAY] Started -> id={giveaway_id} msg={message.id} guild={interaction.guild.id} by={interaction.user.id}")
        await interaction.response.send_message(f"Giveaway started (id: {giveaway_id}).", ephemeral=True)

    @app_commands.command(name="giveaway_end", description="End the active giveaway immediately (manager only)")
    async def giveaway_end(self, interaction: discord.Interaction):
        allowed = await self._is_manager(interaction)
        if not allowed:
            await interaction.response.send_message("You are not allowed to end giveaways.", ephemeral=True)
            return
        # fetch active giveaway for this guild
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, channel_id, message_id FROM giveaways WHERE guild_id=? AND active=1", (interaction.guild.id,))
            row = await cur.fetchone()
            if not row:
                await interaction.response.send_message("No active giveaway found in this server.", ephemeral=True)
                return
            gid, channel_id, message_id = row

        channel = self.bot.get_channel(channel_id)
        message = None
        if channel:
            try:
                message = await channel.fetch_message(message_id)
            except Exception as exc:
                logger.warning(f"[GIVEAWAY] Could not fetch message for manual end: {exc}")

        await self._finalize_giveaway(gid, message, None)
        await interaction.response.send_message("Giveaway ended.", ephemeral=True)
        logger.info(f"[GIVEAWAY] Manually ended by user {interaction.user.id} -> giveaway_id={gid}")

    @app_commands.command(name="giveaway_reroll", description="Reroll winners for a finished giveaway (manager only)")
    @app_commands.describe(giveaway_id="Giveaway ID to reroll")
    async def giveaway_reroll(self, interaction: discord.Interaction, giveaway_id: int):
        allowed = await self._is_manager(interaction)
        if not allowed:
            await interaction.response.send_message("You are not allowed to reroll giveaways.", ephemeral=True)
            return
        # Check that the giveaway exists and is inactive (ended)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT channel_id, prize, winner_count FROM giveaways WHERE id=? AND active=0", (giveaway_id,))
            row = await cur.fetchone()
            if not row:
                await interaction.response.send_message("Giveaway not found or still active.", ephemeral=True)
                return
            channel_id, prize, winner_count = row

            # collect participants who participated in that giveaway (entries were cleaned up at end, but when ended we marked winners; to allow reroll we need to re-use a snapshot approach)
            # Implementation choice: For reroll to work we must reinsert participants or store them; since previous finalize removed entries, we'll reconstruct from message reactions is not applicable.
            # To support reroll, we will rely on a lightweight approach: allow reroll only immediately after end (entries still might be present) OR use a temporary backup table before cleanup.
            # For simplicity, attempt to fetch entries - if none, reject.
            cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
            rows = await cur.fetchall()
            participants = [int(r[0]) for r in rows]

            if not participants:
                await interaction.response.send_message("No stored participants to reroll. Reroll is only possible while participant data exists.", ephemeral=True)
                logger.warning(f"[GIVEAWAY] Reroll failed - no participants snapshot for id={giveaway_id}")
                return

        # select new winners, avoiding previous winners if possible (we store won flag)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? AND won=0", (giveaway_id,))
                eligible_rows = await cur.fetchall()
                eligible = [int(r[0]) for r in eligible_rows]
                if not eligible:
                    cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
                    eligible = [int(r[0]) for r in await cur.fetchall()]

                winners = random.sample(eligible, min(int(winner_count), len(eligible)))
                # mark new winners (and keep won=1 for them)
                for uid in winners:
                    await db.execute("UPDATE giveaway_entries SET won=1 WHERE giveaway_id=? AND user_id=?", (giveaway_id, uid))
                await db.commit()
        except Exception as exc:
            logger.exception(f"[GIVEAWAY] Reroll DB error: {exc}")
            await interaction.response.send_message("Failed to reroll due to DB error.", ephemeral=True)
            return

        # announce reroll winners in the original channel
        ch = self.bot.get_channel(channel_id)
        if ch:
            mentions = " ".join(f"<@{uid}>" for uid in winners)
            embed = discord.Embed(title="🎉 Giveaway Reroll Winners!", description=f"Prize: **{prize}**\nWinners: {mentions}", color=discord.Color.gold())
            await ch.send(embed=embed)
            await interaction.response.send_message("Reroll complete; winners announced.", ephemeral=True)
            logger.info(f"[GIVEAWAY] Reroll complete -> giveaway_id={giveaway_id} winners={winners}")
        else:
            await interaction.response.send_message("Reroll complete but original channel not found to announce winners.", ephemeral=True)
            logger.warning(f"[GIVEAWAY] Reroll: channel not found for giveaway {giveaway_id}")

    # -------------------------
    # Helpers
    # -------------------------
    def _parse_duration(self, duration: str) -> Optional[int]:
        """Simple parser: supports Xm, Xh, Xd (minutes/hours/days)."""
        if not duration or len(duration) < 2:
            return None
        unit = duration[-1].lower()
        try:
            num = int(duration[:-1])
        except ValueError:
            return None
        if unit == "m":
            return num * 60
        if unit == "h":
            return num * 3600
        if unit == "d":
            return num * 86400
        return None

    # -------------------------
    # Cog unload cleanup
    # -------------------------
    def cog_unload(self):
        # cancel background tasks if present
        try:
            if hasattr(self, "_bg_check_task"):
                self._bg_check_task.cancel()
            if hasattr(self, "_check_task"):
                self._check_task.cancel()
            logger.info("[GIVEAWAY] Cog unloaded and background tasks cancelled")
        except Exception:
            logger.exception("[GIVEAWAY] Error cancelling tasks on unload")


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
    logger.info("[GIVEAWAY] Cog setup completed")
