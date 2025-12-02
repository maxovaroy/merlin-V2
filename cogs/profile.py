import discord
from discord.ext import commands
from database import add_user, update_user, get_user

class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Ensure the user exists
        await add_user(str(message.author.id))

        # Update XP and message count
        await update_user(str(message.author.id))

        # Allow commands to work
        await self.bot.process_commands(message)

    @commands.command()
    async def profile(self, ctx):
        user = await get_user(str(ctx.author.id))
        if user is None:
            return await ctx.send("User not found in database.")

        user_id, xp, level, messages, aura = user

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

async def setup(bot):
    await bot.add_cog(Profile(bot))
