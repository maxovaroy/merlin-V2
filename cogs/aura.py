import discord
from discord.ext import commands
from database import get_user, add_user, update_user
from logger import logger

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="aura")
    @commands.has_permissions(administrator=False)  # Only admins can change aura
    async def aura(self, ctx, amount: int, member: discord.Member):
        """Give or take aura points from a user. Usage: !aura <+/-amount> @user"""
        # Ensure the member exists in DB
        await add_user(str(member.id))

        # Fetch current user info
        user = await get_user(str(member.id))
        if user is None:
            logger.warning(f"Aura command: User {member.id} not found in DB")
            return await ctx.send("User not found in database.")

        user_id, xp, level, messages, aura = user

        # Update aura
        new_aura = max(aura + amount, 0)  # prevent negative aura
        async with update_user.__globals__['aiosqlite'].connect('database.db') as db:
            await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, str(member.id)))
            await db.commit()

        logger.info(f"{ctx.author} changed aura for {member} by {amount}. New aura: {new_aura}")
        await ctx.send(f"✨ {member.mention}'s aura is now **{new_aura}**!")

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
