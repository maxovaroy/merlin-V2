# cogs/aura.py
"""
Samurai Aura Cog — Chaos Mode
-----------------------------
Handles Aura-based gambling, transfer, and stealing.
Aura is only displayed through the profile command.
"""

import discord
from discord.ext import commands
import aiosqlite
import random

DB_PATH = "database.db"

class Aura(commands.Cog):
    """Aura system: gamble, transfer, and chaos steal."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------
    # Helper: ensure user exists in DB
    # -----------------------------
    async def ensure_user(self, user_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO aura (user_id, aura) 
                VALUES (?, 0) 
                ON CONFLICT(user_id) DO NOTHING
            """, (user_id,))
            await db.commit()

    # -----------------------------
    # Transfer aura
    # -----------------------------
    @commands.command(name="auraTransfer")
    async def aura_transfer(self, ctx, member: discord.Member, amount: int):
        if member.id == ctx.author.id:
            return await ctx.send("You can't transfer Aura to yourself!")
        if amount <= 0:
            return await ctx.send("Transfer amount must be positive.")

        await self.ensure_user(ctx.author.id)
        await self.ensure_user(member.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT aura FROM aura WHERE user_id = ?", (ctx.author.id,)) as cursor:
                sender_aura = (await cursor.fetchone())[0]

            if sender_aura < amount:
                return await ctx.send("You don't have enough Aura to transfer!")

            await db.execute("UPDATE aura SET aura = aura - ? WHERE user_id = ?", (amount, ctx.author.id))
            await db.execute("""
                INSERT INTO aura (user_id, aura) 
                VALUES (?, ?) 
                ON CONFLICT(user_id) DO UPDATE SET aura = aura + excluded.aura
            """, (member.id, amount))
            await db.commit()

        await ctx.send(f"💠 {ctx.author.display_name} transferred {amount} Aura to {member.display_name}.")

    # -----------------------------
    # Gamble aura
    # -----------------------------
    @commands.command(name="auraGamble")
    async def aura_gamble(self, ctx, amount: int):
        if amount <= 0:
            return await ctx.send("You must gamble a positive amount.")

        await self.ensure_user(ctx.author.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT aura FROM aura WHERE user_id = ?", (ctx.author.id,)) as cursor:
                aura_amount = (await cursor.fetchone())[0]

            if aura_amount < amount:
                return await ctx.send("You don't have enough Aura to gamble!")

            win = random.choice([True, False])
            delta = amount if win else -amount

            await db.execute("""
                INSERT INTO aura (user_id, aura)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET aura = aura + ?
            """, (ctx.author.id, delta, delta))
            await db.commit()

        outcome = "won" if win else "lost"
        await ctx.send(f"🎲 You {outcome} {amount} Aura!")

    # -----------------------------
    # Chaos steal aura
    # -----------------------------
    @commands.command(name="auraSteal")
    async def aura_steal(self, ctx, member: discord.Member):
        if member.id == ctx.author.id:
            return await ctx.send("You can't steal from yourself!")

        await self.ensure_user(ctx.author.id)
        await self.ensure_user(member.id)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT aura FROM aura WHERE user_id = ?", (member.id,)) as cursor:
                target_aura = (await cursor.fetchone())[0]

            if target_aura <= 0:
                return await ctx.send(f"{member.display_name} has no Aura to steal!")

            # 50% success chance
            success = random.choice([True, False])
            steal_amount = random.randint(1, max(1, target_aura // 2))

            if success:
                await db.execute("UPDATE aura SET aura = aura - ? WHERE user_id = ?", (steal_amount, member.id))
                await db.execute("""
                    INSERT INTO aura (user_id, aura)
                    VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET aura = aura + excluded.aura
                """, (ctx.author.id, steal_amount))
                await db.commit()
                await ctx.send(f"⚔️ {ctx.author.display_name} successfully stole {steal_amount} Aura from {member.display_name}!")
            else:
                await ctx.send(f"❌ {ctx.author.display_name} failed to steal from {member.display_name}!")

async def setup(bot: commands.Bot):
    await bot.add_cog(Aura(bot))
