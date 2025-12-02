import discord
from discord.ext import commands
from database import get_user, add_user, update_user
from logger import logger
import random
import aiosqlite

DB_PATH = "database.db"

# =======================================================
#                     AURA SYSTEM COG
# =======================================================
# This system handles:
# - Giving aura
# - Taking aura
# - Transferring aura
# - Aura leaderboard
# - Aura check command
#
# NO MORE AURA POOL ANYWHERE
# =======================================================


class Aura(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ===================================================
    # Helper DB functions
    # ===================================================

    async def get_aura(self, user_id: str):
        """Returns aura for a specific user"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT aura FROM users WHERE user_id = ?", 
                (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def set_aura(self, user_id: str, new_amount: int):
        """Updates aura of a user"""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET aura = ? WHERE user_id = ?", 
                (new_amount, user_id)
            )
            await db.commit()

    async def ensure_users(self, *members):
        for m in members:
            await add_user(m)

    # ===================================================
    # Main aura modifying logic
    # ===================================================

    @commands.command(name="aura")
    async def aura(self, ctx, amount: int, member: discord.Member):
        """
        !aura <+/-amount> @user
        Give or take aura.
        """
        giver_id = str(ctx.author.id)
        receiver_id = str(member.id)

        if giver_id == receiver_id:
            return await ctx.send("❌ You cannot modify aura with yourself.")

        await self.ensure_users(giver_id, receiver_id)

        giver_data = await get_user(giver_id)
        receiver_data = await get_user(receiver_id)

        if giver_data is None or receiver_data is None:
            return await ctx.send("❌ Failed to find users in database.")

        giver_aura = giver_data[4]
        receiver_aura = receiver_data[4]

        # ------------------ Giving Aura -------------------
        if amount > 0:
            if giver_aura < amount:
                return await ctx.send(f"❌ You only have {giver_aura} aura.")
            
            giver_aura -= amount
            receiver_aura += amount
            verb = "gave"

        # ---------------- Taking Aura ----------------------
        else:
            amount = abs(amount)

            if receiver_aura < amount:
                amount = receiver_aura  # steal max possible

            giver_aura += amount
            receiver_aura -= amount
            verb = "took"

        # ----------------- Update DB -----------------------
        await self.set_aura(giver_id, giver_aura)
        await self.set_aura(receiver_id, receiver_aura)

        await ctx.send(
            f"✨ {ctx.author.mention} **{verb} {amount} aura** "
            f"{'to' if verb == 'gave' else 'from'} {member.mention}!"
        )

        logger.info(
            f"[AURA ACTION] {ctx.author} {verb} {amount} aura {'to' if verb=='gave' else 'from'} {member}."
            f"New aura giver={giver_aura}, receiver={receiver_aura}"
        )

    # ===================================================
    # 🎯 Aura check command
    # ===================================================
    @commands.command(name="myAura")
    async def my_aura(self, ctx):
        await add_user(str(ctx.author.id))
        aura = await self.get_aura(str(ctx.author.id))

        embed = discord.Embed(
            title=f"🌟 Aura Status for {ctx.author.name}",
            color=0x00ffdd
        )
        embed.add_field(name="✨ Aura:", value=str(aura))
        embed.set_thumbnail(url=ctx.author.avatar.url)

        await ctx.send(embed=embed)

    # ===================================================
    # 🏆 Leaderboard
    # ===================================================
    @commands.command(name="auraTop")
    async def aura_top(self, ctx):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT user_id, aura FROM users ORDER BY aura DESC LIMIT 10"
            )
            rows = await cursor.fetchall()

        if not rows:
            return await ctx.send("Nobody has aura yet 😭")

        embed = discord.Embed(
            title="🏆 Top Aura Users",
            color=0xFFD700
        )

        for i, (uid, aura) in enumerate(rows, start=1):
            member = ctx.guild.get_member(int(uid))
            name = member.name if member else uid
            embed.add_field(
                name=f"{i}. {name}",
                value=f"✨ {aura} aura",
                inline=False
            )

        await ctx.send(embed=embed)

    # ===================================================
    # Aura random reward function (call from update_user)
    # ===================================================
    @staticmethod
    async def award_levelup_aura(user_id: str, lvl: int):
        reward = random.randint(10, 50)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT aura FROM users WHERE user_id = ?", 
                (user_id,)
            )
            row = await cursor.fetchone()
            current = row[0] if row else 0
            new = current + reward

            await db.execute(
                "UPDATE users SET aura = ? WHERE user_id = ?", 
                (new, user_id)
            )
            await db.commit()

        logger.info(
            f"[LEVEL UP] User {user_id} gained {reward} aura. New total: {new}"
        )


# Required setup for discord.py extensions
async def setup(bot):
    await bot.add_cog(Aura(bot))
    logger.info("Aura cog fully loaded.")
