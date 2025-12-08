# cogs/level.py
import discord
from discord.ext import commands
import time
import math
import io
import aiosqlite
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from database import DB_PATH, add_user, get_user, update_user
from logger import logger

# ---------------- CONFIG ----------------
COMMAND_PREFIX = "!"
XP_PER_MESSAGE = 6
MESSAGE_COOLDOWN = 3  # seconds
LEVEL_UP_CHANNEL_ID = 1305771250693705818
LEVEL_UP_GIF = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWo5eTJ3bW5ocTM0YWZhZzVtbXdyNnJ0YjM1bHhmcXUzMWk1bzNsMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tMH2lSNTy0MX2AYCoz/giphy.gif"

# Rank card visuals (simple)
CARD_WIDTH = 900
CARD_HEIGHT = 250
BAR_WIDTH = 520
BAR_HEIGHT = 28
FONT_PATH = None  # set to a .ttf path if you have one
PROFILE_CACHE_TTL = 30  # seconds

# DAILY formulas
def compute_daily_xp(streak: int) -> int:
    return 50 * max(1, streak)

def compute_daily_aura(streak: int) -> int:
    if streak <= 7:
        return 10 * streak
    if streak <= 30:
        return 15 * streak
    return 25 * streak

# ---------------- util functions ----------------
def xp_to_level(xp: int) -> int:
    try:
        return int(math.sqrt(xp // 10)) + 1
    except Exception:
        return 1

def level_to_min_xp(level: int) -> int:
    return ((level - 1) ** 2) * 10

def progress_fraction(xp: int, level: int) -> float:
    min_xp = level_to_min_xp(level)
    next_min = level_to_min_xp(level + 1)
    if next_min == min_xp:
        return 0.0
    return max(0.0, min(1.0, (xp - min_xp) / (next_min - min_xp)))

def format_big(n: int) -> str:
    return f"{n:,}"

# ---------------- rank card ----------------
def generate_rank_card(username: str, avatar_bytes: Optional[bytes], level: int, xp: int, aura: int, streak: int) -> bytes:
    im = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (24, 26, 27, 255))
    draw = ImageDraw.Draw(im)

    try:
        if FONT_PATH:
            font_bold = ImageFont.truetype(FONT_PATH, 40)
            font_reg = ImageFont.truetype(FONT_PATH, 20)
            font_small = ImageFont.truetype(FONT_PATH, 16)
        else:
            font_bold = ImageFont.load_default()
            font_reg = ImageFont.load_default()
            font_small = ImageFont.load_default()
    except Exception:
        font_bold = ImageFont.load_default()
        font_reg = ImageFont.load_default()
        font_small = ImageFont.load_default()

    padding = 24
    av_size = 180
    av_x = padding
    av_y = (CARD_HEIGHT - av_size) // 2
    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((av_size, av_size))
            mask = Image.new("L", (av_size, av_size), 0)
            mdraw = ImageDraw.Draw(mask)
            mdraw.ellipse((0, 0, av_size, av_size), fill=255)
            im.paste(av, (av_x, av_y), mask)
        except Exception:
            draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(40, 40, 40))
    else:
        draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(40, 40, 40))

    text_x = av_x + av_size + padding
    text_y = av_y

    draw.text((text_x, text_y), username, font=font_bold, fill=(255, 255, 255))

    level_text = f"Level {level}"
    draw.text((CARD_WIDTH - padding - draw.textsize(level_text, font=font_reg)[0], text_y),
              level_text, font=font_reg, fill=(255, 255, 255))

    frac = progress_fraction(xp, level)
    bar_y = text_y + 60
    bar_x = text_x
    draw.rounded_rectangle((bar_x, bar_y, bar_x + BAR_WIDTH, bar_y + BAR_HEIGHT), radius=12, fill=(50, 50, 50))
    prog_w = int(BAR_WIDTH * frac)
    if prog_w > 0:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + prog_w, bar_y + BAR_HEIGHT), radius=12, fill=(30, 215, 96))

    xp_text = f"{format_big(xp)} XP"
    xp_text_w, _ = draw.textsize(xp_text, font=font_small)
    draw.text((bar_x + BAR_WIDTH - xp_text_w, bar_y - 24), xp_text, font=font_small, fill=(200, 200, 200))

    footer_y = bar_y + BAR_HEIGHT + 18
    aura_text = f"Aura: {aura}"
    streak_text = f"Streak: {streak}d"
    draw.text((bar_x, footer_y), aura_text, font=font_reg, fill=(200, 200, 200))
    draw.text((CARD_WIDTH - padding - draw.textsize(streak_text, font=font_reg)[0], footer_y),
              streak_text, font=font_reg, fill=(200, 200, 200))

    b = io.BytesIO()
    im.save(b, format="PNG")
    b.seek(0)
    return b.read()

# ---------------- COG ----------------
class LevelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._msg_cd = {}          # message XP cooldowns
        self._profile_cache = {}   # used for Profile cog
        logger.info("LevelCog loaded.")

    # ---- award XP per message ----
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(COMMAND_PREFIX):
            return

        uid = str(message.author.id)
        now = time.time()
        last = self._msg_cd.get(uid, 0)
        if now - last < MESSAGE_COOLDOWN:
            return
        self._msg_cd[uid] = now

        await add_user(uid)
        before = await get_user(uid)
        before_xp = int(before[1]) if before and before[1] is not None else 0
        before_level = int(before[2]) if before and before[2] is not None else xp_to_level(before_xp)

        await update_user(uid, xp_gain=XP_PER_MESSAGE)

        after = await get_user(uid)
        after_xp = int(after[1]) if after and after[1] is not None else before_xp
        after_level = int(after[2]) if after and after[2] is not None else xp_to_level(after_xp)

        # clear profile cache
        self._profile_cache.pop(uid, None)

        if after_level > before_level:
            guild = message.guild
            channel = guild.get_channel(LEVEL_UP_CHANNEL_ID) if LEVEL_UP_CHANNEL_ID else None
            embed = discord.Embed(title="⚔ LEVEL UP!",
                                  description=f"<@{uid}> has reached **Level {after_level}**!",
                                  color=discord.Color.gold())
            embed.set_image(url=LEVEL_UP_GIF)
            embed.set_footer(text="Your journey continues...")
            try:
                if channel:
                    await channel.send(embed=embed)
                else:
                    await message.channel.send(embed=embed)
            except Exception:
                logger.exception("Failed to send level-up embed.")

    # ---- daily command ----
    @commands.command(name="daily")
    async def daily(self, ctx: commands.Context):
        uid = str(ctx.author.id)
        now = int(time.time())

        await add_user(uid)
        row = await get_user(uid)
        streak = int(row[5]) if len(row) > 5 and row[5] is not None else 0
        last_claim = int(row[6]) if len(row) > 6 and row[6] is not None else 0

        if now - last_claim < 86400:
            remaining = 86400 - (now - last_claim)
            hrs = remaining // 3600
            mins = (remaining % 3600) // 60
            await ctx.reply(f"🈲 The samurai's blessing has already been taken today. Return in {hrs}h {mins}m to continue your streak.")
            return

        streak = streak + 1 if last_claim != 0 and (now - last_claim) <= 172800 else 1
        xp_reward = compute_daily_xp(streak)
        aura_reward = compute_daily_aura(streak)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
            await db.execute("UPDATE users SET xp = xp + ?, aura = aura + ?, streak_count = ?, last_streak_claim = ? WHERE user_id = ?",
                             (xp_reward, aura_reward, streak, now, uid))
            await db.commit()

        self._profile_cache.pop(uid, None)

        embed = discord.Embed(title="Daily Claim — Samurai's Blessing", color=discord.Color.orange())
        embed.add_field(name="User", value=ctx.author.mention, inline=True)
        embed.add_field(name="XP Gained", value=f"+{format_big(xp_reward)} XP", inline=True)
        embed.add_field(name="Aura Gained", value=f"+{format_big(aura_reward)} Aura", inline=True)
        embed.add_field(name="Streak", value=f"{streak} day(s)", inline=True)
        embed.set_footer(text="Claimed once per 24 hours. Keep your streak alive!")
        await ctx.reply(embed=embed)

    # ---- leaderboard ----
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT user_id, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 10")
            rows = await cur.fetchall()
        if not rows:
            return await ctx.reply("No leaderboard data.")
        lines = []
        for i, r in enumerate(rows, start=1):
            try:
                uid = int(r[0])
            except Exception:
                uid = None
            lvl = int(r[2] or 0)
            xp = int(r[1] or 0)
            member = ctx.guild.get_member(uid) if uid else None
            name = member.display_name if member else f"User {r[0]}"
            lines.append(f"**#{i}** {name} — Level {lvl} • {format_big(xp)} XP")
        await ctx.reply("\n".join(lines))

    # ---- admin commands (setxp, setlevel, lvlup, synclevels, exportcsv) ----
    @commands.command(name="setxp")
    @commands.has_permissions(administrator=True)
    async def setxp(self, ctx: commands.Context, member: discord.Member, xp: int):
        if xp < 0:
            return await ctx.reply("XP must be >= 0.")
        level = xp_to_level(xp)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (str(member.id),))
            await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, str(member.id)))
            await db.commit()
        self._profile_cache.pop(str(member.id), None)
        await ctx.reply(f"Set {member.display_name}'s XP to {xp} (Level {level}).")

    @commands.command(name="setlevel")
    @commands.has_permissions(administrator=True)
    async def setlevel(self, ctx: commands.Context, member: discord.Member, level: int):
        if level < 1:
            return await ctx.reply("Level must be >= 1.")
        xp = level_to_min_xp(level)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (str(member.id),))
            await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, str(member.id)))
            await db.commit()
        self._profile_cache.pop(str(member.id), None)
        await ctx.reply(f"Set {member.display_name}'s Level to {level} ({xp} XP).")

    async def cog_unload(self):
        self._profile_cache.clear()
        self._msg_cd.clear()
        logger.info("LevelCog unloading — cleared caches.")

async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
    logger.info("LevelCog loaded.")
