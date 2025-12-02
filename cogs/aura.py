import discord
from discord.ext import commands
from database import get_user, add_user, spend_aura
from logger import logger
import random

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        """Give or take aura points from another user. Usage: !aura <+/-amount> @user"""
        
        # Prevent using aura on self
        if member.id == ctx.author.id:
            await ctx.send("❌ You cannot give or take aura from yourself!")
            logger.warning(f"{ctx.author} tried to use aura on themselves.")
            return

        # Ensure both users exist in DB
        await add_user(str(ctx.author.id))
        await add_user(str(member.id))

        # Fetch recipient info
        user = await get_user(str(member.id))
        if user is None:
            await ctx.send("User not found in database.")
            logger.warning(f"Aura command: User {member.id} not found.")
            return

        user_id, xp, level, messages, aura, aura_pool = user

        # If taking aura (-amount), limit max to 100
        if amount < 0:
            amount = max(-100, amount)  # -100 is maximum
            amount = await spend_aura(str(ctx.author.id), -amount)  # Deduct from sender's aura pool
            if amount <= 0:
                await ctx.send(f"❌ You don't have enough aura to take from {member.mention}.")
                return
            new_aura = max(aura + amount, 0)
            async with add_user.__globals__['aiosqlite'].connect('database.db') as db:
                await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, str(member.id)))
                await db.commit()
            await ctx.send(f"✨ {ctx.author.mention} took {amount} aura from {member.mention}! They now have {new_aura} aura.")
            logger.info(f"{ctx.author} took {amount} aura from {member}. New aura: {new_aura}")
            return

        # If giving aura (+amount), use from sender's aura pool
        spent = await spend_aura(str(ctx.author.id), amount)
        if spent <= 0:
            await ctx.send("❌ You don't have enough aura in your pool to give.")
            return

        new_aura = aura + spent
        async with add_user.__globals__['aiosqlite'].connect('database.db') as db:
            await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, str(member.id)))
            await db.commit()

        await ctx.send(f"✨ {ctx.author.mention} gave {spent} aura to {member.mention}! They now have {new_aura} aura.")
        logger.info(f"{ctx.author} gave {spent} aura to {member}. New aura: {new_aura}")

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
