import discord
from discord.ext import commands
from database import add_user, update_user, get_user
from logger import logger

class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # Global Message Event
    # -------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        user_id = str(message.author.id)
        content = message.content

        logger.debug(
            f"[PROFILE COG] Message received from {message.author} "
            f"({user_id}): {content}"
        )

        # ensure user exists
        await add_user(user_id)
        logger.debug(f"[PROFILE COG] add_user processed: {user_id}")

        # give XP based on message
        await update_user(user_id)
        logger.debug(f"[PROFILE COG] Updated stats for: {user_id}")

        # continue commands
        await self.bot.process_commands(message)

    # -------------------------
    # PROFILE COMMAND
    # -------------------------
    @commands.command(
        name="profile",
        brief="Shows your RR profile",
        description="Displays your current level, XP, aura and message count."
    )
    async def profile(self, ctx):
        user_id = str(ctx.author.id)
        logger.info(
            f"[PROFILE CMD] Command used by: {ctx.author} ({user_id})"
        )

        user = await get_user(user_id)

        # no record (almost impossible now)
        if user is None:
            logger.error(f"[PROFILE CMD] User not found in DB: {user_id}")
            return await ctx.send("No profile found for you yet!")

        # unpack database row
        # DB ROW: (user_id, xp, level, messages, aura)
        db_user_id, xp, level, messages, aura = user

        logger.debug(
            f"[PROFILE CMD] Loaded DB Stats -> ID={db_user_id} | XP={xp} "
            f"| LVL={level} | MSG={messages} | AURA={aura}"
        )

        # -------------------------
        # Make Embed
        # -------------------------
        embed = discord.Embed(
            title=f"🌌 {ctx.author.name}'s Realm Profile",
            description="**Your known stats in Realm Royz:**",
            color=discord.Color.teal()
        )

        try:
            embed.set_thumbnail(url=ctx.author.avatar.url)
        except:
            pass

        embed.add_field(name="⭐ Level", value=f"```{level}```", inline=True)
        embed.add_field(name="🔥 XP", value=f"```{xp}```", inline=True)
        embed.add_field(name="💬 Messages", value=f"```{messages}```", inline=True)
        embed.add_field(name="✨ Aura", value=f"```{aura}```", inline=True)

        embed.set_footer(text="● Realm Royz Profile System")
        embed.set_author(name=self.bot.user.name)

        await ctx.send(embed=embed)
        logger.info(
            f"[PROFILE CMD] Profile sent successfully for {ctx.author} ({user_id})"
        )

# -------------------------------------------
# REQUIRED SETUP FUNCTION
# -------------------------------------------
async def setup(bot):
    await bot.add_cog(Profile(bot))
    logger.info("[LOAD] Profile cog loaded")
