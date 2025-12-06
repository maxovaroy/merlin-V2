# cogs/giveaway.py

import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiosqlite
import random
import time

DB_PATH = "database.db"


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id, min_messages, min_level):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.min_messages = min_messages
        self.min_level = min_level

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.green)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT messages, level FROM users WHERE user_id=?", (interaction.user.id,))
            data = await cur.fetchone()
            if not data:
                return await interaction.response.send_message("You are not in database yet. Chat a bit first.", ephemeral=True)

            user_messages, user_level = data

            if user_messages < self.min_messages:
                return await interaction.response.send_message(
                    f"You need at least {self.min_messages} messages to join.",
                    ephemeral=True
                )

            if user_level < self.min_level:
                return await interaction.response.send_message(
                    f"You need at least Level {self.min_level} to join.",
                    ephemeral=True
                )

            await db.execute(
                "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                (self.giveaway_id, interaction.user.id)
            )
            await db.commit()

        await interaction.response.send_message("You have joined the giveaway.", ephemeral=True)


class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.init_db())
        self.check_active_giveaway.start()

    async def init_db(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS giveaway (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message_id INTEGER,
                    prize TEXT,
                    winner_count INTEGER,
                    min_messages INTEGER,
                    min_level INTEGER,
                    end_time INTEGER,
                    active INTEGER DEFAULT 1
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER,
                    user_id INTEGER,
                    UNIQUE(giveaway_id, user_id)
                )
            """)
            await db.commit()

    async def end_giveaway(self, giveaway_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT channel_id, prize, winner_count FROM giveaway WHERE id=? AND active=1",
                                   (giveaway_id,))
            g = await cur.fetchone()
            if not g:
                return

            channel_id, prize, winner_count = g

            cur = await db.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
            users = [u[0] for u in await cur.fetchall()]

            await db.execute("UPDATE giveaway SET active=0 WHERE id=?", (giveaway_id,))
            await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
            await db.commit()

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        if not users:
            return await channel.send("Giveaway ended. No participants.")

        winners = random.sample(users, min(len(users), winner_count))
        mentions = " ".join(f"<@{uid}>" for uid in winners)

        embed = discord.Embed(
            title="🎉 Giveaway Ended!",
            description=f"Winners: {mentions}\nPrize: **{prize}**",
            color=discord.Color.gold()
        )
        await channel.send(embed=embed)

    @app_commands.command(name="giveaway_start")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_start(self, interaction: discord.Interaction, prize: str,
                             winner_count: int = 1, min_messages: int = 0, min_level: int = 0,
                             duration_minutes: int = 60):

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM giveaway WHERE active=1")
            if await cur.fetchone():
                return await interaction.response.send_message("A giveaway is already active.", ephemeral=True)

        end_time = int(time.time()) + duration_minutes * 60

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("""
                INSERT INTO giveaway (channel_id, prize, winner_count, min_messages, min_level, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (interaction.channel.id, prize, winner_count, min_messages, min_level, end_time))
            g_id = cur.lastrowid
            await db.commit()

        embed = discord.Embed(
            title="🎉 Giveaway Started!",
            description=(
                f"Prize: **{prize}**\n"
                f"Winners: {winner_count}\n"
                f"Required Messages: {min_messages}\n"
                f"Required Level: {min_level}"
            ),
            color=discord.Color.blue()
        )
        embed.add_field(name="Ends in", value=f"{duration_minutes} minutes")

        view = GiveawayView(g_id, min_messages, min_level)
        message = await interaction.channel.send(embed=embed, view=view)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE giveaway SET message_id=? WHERE id=?", (message.id, g_id))
            await db.commit()

        await interaction.response.send_message(f"Giveaway started. ID: {g_id}", ephemeral=True)

    @app_commands.command(name="giveaway_end")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_end(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id FROM giveaway WHERE active=1")
            r = await cur.fetchone()
            if not r:
                return await interaction.response.send_message("No active giveaway.", ephemeral=True)

            g_id = r[0]

        await self.end_giveaway(g_id)
        await interaction.response.send_message("Giveaway ended.", ephemeral=True)

    @app_commands.command(name="giveaway_reroll")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_reroll(self, interaction: discord.Interaction, giveaway_id: int):
        await self.end_giveaway(giveaway_id)
        await interaction.response.send_message("Rerolled winners.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def check_active_giveaway(self):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, end_time FROM giveaway WHERE active=1")
            r = await cur.fetchone()

        if r:
            g_id, end_time = r
            if time.time() >= end_time:
                await self.end_giveaway(g_id)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
