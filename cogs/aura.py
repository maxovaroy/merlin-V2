import discord
from discord.ext import commands
from database import add_user, get_user, spend_aura
from logger import logger

class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        """Give aura points to another user. Usage: !aura <amount> @user"""
        sender = ctx.author

        if sender.id == member.id:
            return await ctx.send("❌ You cannot give aura to yourself!")

        if amount <= 0:
            return await ctx.send("❌ Amount must be greater than 0!")

        if amount > 100:
            amount = 100  # Cap at 100 per use

        # Ensure both users exist in DB
        await add_user(str(sender.id))
        await add_user(str(member.id))

        # Attempt to spend aura
        success = await spend_aura(str(sender.id), str(member.id), amount)
        if not success:
            return await ctx.send("❌ You don't have enough aura in your pool!")

        # Fetch updated aura pool
        sender_user = await get_user(str(sender.id))
        member_user = await get_user(str(member.id))
        aura_pool = sender_user[5]  # aura_pool column

        logger.info(f"{sender} gave {amount} aura to {member}. Remaining pool: {aura_pool}")
        await ctx.send(
            f"✨ {sender.mention} gave {amount} aura to {member.mention}!\n"
            f"Your remaining aura pool: **{aura_pool}**"
        )

async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog loaded successfully")
