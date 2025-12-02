import discord
from discord.ext import commands
import random
from database import get_user, add_user, update_user, update_aura_pool, get_aura_pool
from logger import logger

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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

        await update_aura_pool(user_id, points)
        pool = await get_aura_pool(user_id)
        logger.info(f"User {user_id} leveled up! Added {points} aura points to their pool. Total pool: {pool}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        await add_user(str(message.author.id))

        user = await get_user(str(message.author.id))
        if user is None:
            return
        _, _, old_level, _, _, _ = user

        await update_user(str(message.author.id))

        user = await get_user(str(message.author.id))
        _, _, new_level, _, _, _ = user

        if new_level > old_level:
            await self.add_aura_on_levelup(str(message.author.id), new_level)

    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        giver_id = str(ctx.author.id)
        receiver_id = str(member.id)

        if giver_id == receiver_id:
            return await ctx.send("❌ You cannot give or take aura from yourself.")

        await add_user(giver_id)
        await add_user(receiver_id)

        giver_pool = await get_aura_pool(giver_id)

        if amount > 0:
            if giver_pool < amount:
                return await ctx.send(f"❌ You only have {giver_pool} aura points to give.")
        elif amount < 0:
            if abs(amount) > 100:
                amount = -100

        user = await get_user(receiver_id)
        if user is None:
            return await ctx.send("Receiver not found in database.")
        _, _, _, _, current_aura, _ = user

        new_aura = max(current_aura + amount, 0)
        async with update_user.__globals__['aiosqlite'].connect('database.db') as db:
            await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, receiver_id))
            await db.commit()

        if amount > 0:
            await update_aura_pool(giver_id, -amount)

        giver_pool = await get_aura_pool(giver_id)
        logger.info(f"{ctx.author} changed aura for {member} by {amount}. Receiver new aura: {new_aura}, Giver pool left: {giver_pool}")
        await ctx.send(f"✨ {member.mention}'s aura is now **{new_aura}**! You have **{giver_pool}** aura left to give.")

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
