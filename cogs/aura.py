import discord
from discord.ext import commands
import random
from database import get_user, add_user, update_user
from logger import logger

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Track temporary aura pool for each user (in-memory)
        self.user_aura_pool = {}

    async def add_aura_on_levelup(self, user_id: str, level: int):
        """Add random aura points to user pool when leveling up"""
        if level <= 10:
            points = random.randint(1, 100)
        elif 11 <= level <= 20:
            points = random.randint(101, 300)
        elif 21 <= level <= 30:
            points = random.randint(301, 500)
        else:
            points = random.randint(501, 1000)

        self.user_aura_pool[user_id] = self.user_aura_pool.get(user_id, 0) + points
        logger.info(f"User {user_id} leveled up! Added {points} aura points to their pool. Total pool: {self.user_aura_pool[user_id]}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Ensure the user exists
        await add_user(str(message.author.id))

        # Fetch old level
        user = await get_user(str(message.author.id))
        if user is None:
            return
        _, _, old_level, _, _ = user

        # Update XP/messages
        await update_user(str(message.author.id))

        # Fetch new level
        user = await get_user(str(message.author.id))
        _, _, new_level, _, _ = user

        # Check if leveled up
        if new_level > old_level:
            await self.add_aura_on_levelup(str(message.author.id), new_level)

    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        """Give or take aura points from a user. Usage: !aura <+/-amount> @user"""
        giver_id = str(ctx.author.id)
        receiver_id = str(member.id)

        if giver_id == receiver_id:
            return await ctx.send("❌ You cannot give or take aura from yourself.")

        # Ensure both users exist
        await add_user(giver_id)
        await add_user(receiver_id)

        # Fetch giver pool
        pool = self.user_aura_pool.get(giver_id, 0)

        if amount > 0:
            # Giving aura
            if pool < amount:
                return await ctx.send(f"❌ You only have {pool} aura points to give.")
        elif amount < 0:
            # Taking aura, limit to 100
            if abs(amount) > 100:
                amount = -100

        # Fetch receiver info
        user = await get_user(receiver_id)
        if user is None:
            return await ctx.send("Receiver not found in database.")
        _, _, _, _, current_aura = user

        # Update receiver aura
        new_aura = max(current_aura + amount, 0)
        async with update_user.__globals__['aiosqlite'].connect('database.db') as db:
            await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, receiver_id))
            await db.commit()

        # Deduct from giver pool if giving
        if amount > 0:
            self.user_aura_pool[giver_id] -= amount

        logger.info(f"{ctx.author} changed aura for {member} by {amount}. Receiver new aura: {new_aura}, Giver pool left: {self.user_aura_pool.get(giver_id,0)}")
        await ctx.send(f"✨ {member.mention}'s aura is now **{new_aura}**! You have **{self.user_aura_pool.get(giver_id,0)}** aura left to give.")

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
