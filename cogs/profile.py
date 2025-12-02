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

        uid = str(message.author.id)
        db.add_message(uid)

    @commands.command()
    async def profile(self, ctx):
        uid = str(ctx.author.id)
        result = db.get_user(uid)

        user_id, xp, lvl, messages, aura = result

        embed = discord.Embed(
            title=f"{ctx.author.name}'s Profile",
            description="Your Merlin stats",
            color=0x00ff99
        )
        embed.add_field(name="Level", value=lvl)
        embed.add_field(name="XP", value=xp)
        embed.add_field(name="Messages", value=messages)
        embed.add_field(name="Aura", value=aura)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Profile(bot))
