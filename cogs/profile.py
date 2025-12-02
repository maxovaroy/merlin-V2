import discord
from discord.ext import commands
from database import db

class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        await db.add_message(str(message.author.id))

    @commands.command()
    async def profile(self, ctx):
        user = await db.get_user(str(ctx.author.id))
        user_id, xp, level, messages, aura = user

        embed = discord.Embed(
            title=f"{ctx.author.name}'s Profile",
            color=0x00ff99
        )
        embed.add_field(name="Level", value=level)
        embed.add_field(name="XP", value=xp)
        embed.add_field(name="Messages", value=messages)
        embed.add_field(name="Aura", value=aura)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Profile(bot))
