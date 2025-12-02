import discord
from discord.ext import commands
from database import get_user, add_user, update_user
from logger import logger
import random
import aiosqlite

DB_PATH = "database.db"

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Aura command
    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        """Give or take aura points from a user. Usage: !aura <+/-amount> @user"""
        giver_id = str(ctx.author.id)
        receiver_id = str(member.id)

        if giver_id == receiver_id:
            return await ctx.send("❌ You cannot give or take aura to yourself!")

        # Ensure both users exist in DB
        await add_user(giver_id)
        await add_user(receiver_id)

        giver = await get_user(giver_id)
        receiver = await get_user(receiver_id)

        if giver is None or receiver is None:
            return await ctx.send("❌ One of the users is not found in the database!")

        _, _, giver_level, _, _, aura_pool = giver
        _, _, _, _, receiver_aura, _ = receiver

        if amount > 0:  # Giving aura
            if amount > aura_pool:
                return await ctx.send(f"❌ You do not have enough aura in your pool! You have {aura_pool}.")
            new_aura_pool = aura_pool - amount
            new_receiver_aura = receiver_aura + amount
        else:  # Taking aura
            amount = abs(amount)
            if amount > 100:
                amount = 100  # Max 100 aura can be taken
            new_receiver_aura = max(receiver_aura - amount, 0)
            new_aura_pool = aura_pool  # giver's pool doesn't change when taking aura

        # Update the database
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_receiver_aura, receiver_id))
            await db.execute("UPDATE users SET aura_pool = ? WHERE user_id = ?", (new_aura_pool, giver_id))
            await db.commit()

        action = "gave" if amount > 0 else "took"
        await ctx.send(f"✨ {ctx.author.mention} {action} {amount} aura {'to' if amount > 0 else 'from'} {member.mention}!")
        logger.info(f"{ctx.author} {action} {amount} aura {'to' if amount > 0 else 'from'} {member}. Giver pool: {new_aura_pool}, Receiver aura: {new_receiver_aura}")

    # Method to add aura on level up (to be called from update_user)
    @staticmethod
    async def award_levelup_aura(user_id: str, level: int):
        if level <= 10:
            aura_award = random.randint(1, 100)
        elif 11 <= level <= 20:
            aura_award = random.randint(101, 300)
        elif 21 <= level <= 30:
            aura_award = random.randint(301, 600)
        else:
            aura_award = random.randint(601, 1000)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            current_pool = row[0] if row else 0
            new_pool = current_pool + aura_award
            await db.execute("UPDATE users SET aura_pool = ? WHERE user_id = ?", (new_pool, user_id))
            await db.commit()
        logger.info(f"User {user_id} leveled up and received {aura_award} aura. New aura_pool: {new_pool}")

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
