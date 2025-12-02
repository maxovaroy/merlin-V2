import discord
from discord.ext import commands
from database import add_user, update_user, get_user
from logger import logger  # Added logger import

class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        logger.debug(f"Message received from {message.author} ({message.author.id}): {message.content}")

        # Ensure the user exists
        await add_user(str(message.author.id))
        logger.debug(f"Ensured user {message.author.id} exists in DB.")

        # Update XP and message count
        await update_user(str(message.author.id))
        logger.debug(f"Updated XP/messages/aura for user {message.author.id}")

        # Allow commands to work
        await self.bot.process_commands(message)

    @commands.command()
    async def profile(self, ctx):
        user = await get_user(str(ctx.author.id))
        if user is None:
            logger.warning(f"Profile command: User {ctx.author.id} not found in DB")
            return await ctx.send("User not found in database.")

        user_id, xp, level, messages, aura = user
        logger.info(f"Profile command used by {ctx.author} ({ctx.author.id}) - Level: {level}, XP: {xp}, Messages: {messages}, Aura: {aura}")

        embed = discord.Embed(
            title=f"{ctx.author.name}'s Profile",
            description="Your stats so far:",
            color=0x00ff99
        )

        embed.set_thumbnail(url=ctx.author.avatar.url)
        embed.add_field(name="⭐ Level", value=level, inline=True)
        embed.add_field(name="🔥 XP", value=xp, inline=True)
        embed.add_field(name="💬 Messages", value=messages, inline=True)
        embed.add_field(name="✨ Aura", value=aura, inline=True)
        embed.set_footer(text="Realm Royz Profile System")

        await ctx.send(embed=embed)
        logger.debug(f"Sent profile embed to {ctx.author.id}")

async def setup(bot):
    await bot.add_cog(Profile(bot))
    logger.info("Profile cog loaded successfully")
