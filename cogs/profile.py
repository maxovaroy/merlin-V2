import discord
from discord.ext import commands
from database import add_user, update_user, get_user
from logger import logger


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("[PROFILE] Profile cog initialized.")

    # ----------------------------------------------------
    # MESSAGE HANDLER
    # (Handles XP only for normal messages)
    # ----------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        
        # If it's a command - skip XP update
        if message.content.startswith("!"):
            logger.debug(f"[PROFILE] Skipped XP for command: {message.content}")
            return

        user_id = str(message.author.id)

        logger.debug(f"[PROFILE] Message received from {message.author} | Updating XP.")

        # Ensure user exists & update stats
        await add_user(user_id)
        await update_user(user_id)

        logger.debug(f"[PROFILE] Stats updated for: {user_id}")

    # ----------------------------------------------------
    # PROFILE COMMAND
    # ----------------------------------------------------
    @commands.command(
        name="profile",
        aliases=["rank", "stats"],
        usage="!profile",
        help="Shows your Realm Royz profile stats."
    )
    async def profile(self, ctx):
        user_id = str(ctx.author.id)
        logger.info(f"[PROFILE CMD] Used by {ctx.author} ({user_id})")

        # Fetch DB info
        user = await get_user(user_id)
        if not user:
            logger.error(f"[PROFILE CMD] User DB missing: {user_id}")
            return await ctx.send("⚠ No profile found! Try talking in chat first.")

        # DB row layout:
        # (user_id, xp, level, messages, aura)
        db_user_id, xp, level, messages, aura = user

        logger.debug(
            f"[PROFILE CMD] DB -> ID={db_user_id}, XP={xp}, LVL={level}, "
            f"MSG={messages}, AURA={aura}"
        )

        # ----------------------------------------------------
        # EMBED BUILD
        # ----------------------------------------------------
        embed = discord.Embed(
            title=f"🌌 {ctx.author.name}'s Realm Profile",
            description="🧿 **Your current known stats:**",
            color=discord.Color.teal()
        )

        # Display avatar but avoid errors
        try:
            embed.set_thumbnail(url=ctx.author.avatar.url)
        except Exception as e:
            logger.warning(f"[PROFILE CMD] Failed to load avatar: {e}")

        embed.add_field(name="⭐ Level", value=f"```{level}```", inline=True)
        embed.add_field(name="🔥 XP", value=f"```{xp}```", inline=True)
        embed.add_field(name="💬 Messages", value=f"```{messages}```", inline=True)
        embed.add_field(name="✨ Aura", value=f"```{aura}```", inline=True)

        embed.set_footer(text="🌙 Realm Royz Profile System")
        embed.set_author(name=self.bot.user.name)

        await ctx.send(embed=embed)
        logger.info(
            f"[PROFILE CMD] Embed sent for user {ctx.author} ({user_id}) successfully."
        )


# REQUIRED
async def setup(bot):
    await bot.add_cog(Profile(bot))
    logger.info("[PROFILE] Cog loaded successfully.")
