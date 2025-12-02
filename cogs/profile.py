# cogs/profile.py
"""
Profile Cog for Merlin Realm Royz
Features:
- Display a user's profile stats: Level, XP, Messages, Aura
- Integrates with LevelSystem cog for accurate XP/Level display
- Displays progress bar towards next level
- Shows avatar and polished embeds
- Handles database safely
- Owner/admin debug options included
"""

import discord
from discord.ext import commands
from logger import logger
from database import add_user, get_user

# Emoji progress bar characters
PROGRESS_FILLED = "█"
PROGRESS_EMPTY = "░"

# Width of XP progress bar
PROGRESS_WIDTH = 18

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("[PROFILE] Profile cog initialized.")

    # ----------------------------
    # Helper functions
    # ----------------------------
    async def _get_level_data(self, user_id: str, guild_id: int):
        """
        Returns XP, level, fraction progress towards next level
        by querying the LevelSystem cog
        """
        level_cog = self.bot.get_cog("LevelSystem")
        if level_cog:
            xp, level = await level_cog.get_user_level_data(guild_id, int(user_id))
        else:
            # fallback if LevelSystem is missing
            user_row = await get_user(user_id)
            if user_row:
                _, xp, level, *_ = user_row
            else:
                xp, level = 0, 1

        # progress calculation
        current_xp_in_level = xp - ((level - 1) * 100)
        xp_needed = 100
        frac = min(max(current_xp_in_level / xp_needed, 0), 1)
        return xp, level, current_xp_in_level, xp_needed, frac

    def _progress_bar(self, frac: float) -> str:
        filled = int(round(frac * PROGRESS_WIDTH))
        empty = PROGRESS_WIDTH - filled
        return f"{PROGRESS_FILLED * filled}{PROGRESS_EMPTY * empty} {int(frac * 100)}%"

    async def _ensure_user_exists(self, user_id: str):
        """Ensure the user has a row in the database."""
        try:
            await add_user(user_id)
        except Exception as e:
            logger.exception("[PROFILE] Failed to ensure user exists: %s", e)

    # ----------------------------
    # Commands
    # ----------------------------
    @commands.command(
        name="profile",
        aliases=["rank", "stats", "xpinfo"],
        usage="!profile [@member]",
        help="Displays your Realm Royz profile stats."
    )
    async def profile(self, ctx: commands.Context, member: discord.Member = None):
        """
        Show a profile of yourself or another member
        """
        try:
            if member is None:
                member = ctx.author

            user_id = str(member.id)
            guild_id = ctx.guild.id if ctx.guild else 0

            await self._ensure_user_exists(user_id)

            # get DB row
            user_row = await get_user(user_id)
            if not user_row:
                return await ctx.send("⚠ No profile found! Try chatting first to create a profile.")

            _, db_xp, db_level, messages, aura = user_row

            # get progress via LevelSystem cog
            xp, level, current_in_level, xp_needed, frac = await self._get_level_data(user_id, guild_id)

            progress_bar = self._progress_bar(frac)

            # ----------------------------
            # Embed
            # ----------------------------
            embed = discord.Embed(
                title=f"🌌 {member.display_name}'s Realm Profile",
                description=f"🧿 **Current Stats**",
                color=discord.Color.teal()
            )

            try:
                embed.set_thumbnail(url=member.display_avatar.url)
            except Exception as e:
                logger.warning("[PROFILE] Failed to load avatar: %s", e)

            # Main stats
            embed.add_field(name="⭐ Level", value=f"```{level}```", inline=True)
            embed.add_field(name="🔥 XP", value=f"```{xp} ({current_in_level}/{xp_needed})```", inline=True)
            embed.add_field(name="💬 Messages", value=f"```{messages}```", inline=True)
            embed.add_field(name="✨ Aura", value=f"```{aura}```", inline=True)

            # Progress bar
            embed.add_field(name="Progress to next level", value=progress_bar, inline=False)

            # Footer + author
            embed.set_footer(text=f"🌙 Realm Royz Profile System | ID: {user_id}")
            embed.set_author(name=self.bot.user.name, icon_url=getattr(self.bot.user.avatar, "url", None))

            await ctx.send(embed=embed)
            logger.info("[PROFILE CMD] Sent profile for %s (%s)", member.display_name, user_id)

        except Exception as e:
            logger.exception("[PROFILE] Error in profile command: %s", e)
            await ctx.send("⚠ An error occurred while fetching your profile.")

    # ----------------------------
    # Debug / Admin commands
    # ----------------------------
    @commands.command(name="profiledebug")
    @commands.is_owner()
    async def profiledebug(self, ctx: commands.Context, member: discord.Member = None):
        """Owner-only debug: shows DB row and LevelSystem info"""
        if member is None:
            member = ctx.author

        try:
            user_id = str(member.id)
            guild_id = ctx.guild.id if ctx.guild else 0

            user_row = await get_user(user_id)
            xp, level, current_in_level, xp_needed, frac = await self._get_level_data(user_id, guild_id)

            await ctx.send(
                f"**Debug for {member.display_name}**\n"
                f"DB Row: {user_row}\n"
                f"LevelSystem XP/Level: {xp}/{level}\n"
                f"Current in level: {current_in_level}/{xp_needed} ({int(frac*100)}%)"
            )

        except Exception as e:
            logger.exception("[PROFILE DEBUG] Failed for member %s: %s", member.display_name, e)
            await ctx.send("Debug failed.")

    # ----------------------------
    # Lifecycle
    # ----------------------------
    async def cog_load(self):
        logger.info("[PROFILE] Cog loaded successfully.")

    async def cog_unload(self):
        logger.info("[PROFILE] Cog unloaded.")


# ----------------------------
# Setup
# ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    logger.info("[PROFILE] Profile cog setup complete.")
