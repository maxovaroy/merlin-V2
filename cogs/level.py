# cogs/level.py
import discord
from discord.ext import commands
import asyncio
import time
import math
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import aiosqlite
from PIL import Image, ImageDraw, ImageFont

from database import add_user, get_user, update_user, DB_PATH
from logger import logger

# ----------------- CONFIG -----------------
XP_PER_MESSAGE = 6                  # preserved from your original
MESSAGE_COOLDOWN = 3                # seconds cooldown per user to avoid spam XP
LEVEL_UP_CHANNEL_ID = 1305771250693705818  # user-provided specific channel ID
LEVEL_UP_GIF = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcWo5eTJ3bW5ocTM0YWZhZzVtbXdyNnJ0YjM1bHhmcXUzMWk1bzNsMyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/tMH2lSNTy0MX2AYCoz/giphy.gif"

# Daily streak settings (you chose "both" rewards; these defaults are chosen and are adjustable)
DAILY_RESET_TZ = ZoneInfo("Asia/Kolkata")  # you asked for server midnight reset in Asia/Kolkata
DAILY_BASE_AURA = 50
DAILY_BASE_XP = 100
DAILY_STREAK_MULTIPLIER = 0.10  # +10% per consecutive day (applied multiplicatively to base reward)
DAILY_MAX_STREAK_BONUS = 5.0    # safety cap (5x)

# Rank card visuals
CARD_WIDTH = 900
CARD_HEIGHT = 250
BAR_WIDTH = 520
BAR_HEIGHT = 28
FONT_PATH = None  # leave None to use default PIL font, or set path to a .ttf in your repo

# Profile cache config (to avoid regenerating images when nothing changed)
PROFILE_CACHE_TTL = 30  # seconds; small because single community

# DB access helper (short-lived aiosqlite connections are used in database.py)
# We don't maintain a persistent connection here because database.py currently opens per-call.
# If you change DB strategy, we can adapt.

# ----------------- HELPERS -----------------
def xp_to_level(xp: int) -> int:
    """Level formula: same as your DB (int(sqrt(xp//10)) + 1). Kept consistent."""
    try:
        return int(math.sqrt(xp // 10)) + 1
    except Exception:
        return 1

def level_to_min_xp(level: int) -> int:
    """Minimum XP required to be 'level' (reverse of formula)."""
    return ((level - 1) ** 2) * 10

def progress_fraction(xp: int, level: int) -> float:
    """Fraction (0..1) showing progress into current level."""
    min_xp = level_to_min_xp(level)
    next_min = level_to_min_xp(level + 1)
    if next_min == min_xp:
        return 0.0
    return max(0.0, min(1.0, (xp - min_xp) / (next_min - min_xp)))

def format_big(n: int) -> str:
    return f"{n:,}"

def now_epoch() -> int:
    return int(time.time())

def epoch_midnight_tz(dt: datetime, tz: ZoneInfo) -> int:
    """Return epoch seconds of midnight (00:00) of the given local date in tz."""
    local_midnight = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=tz)
    return int(local_midnight.timestamp())

# ----------------- RANK CARD GENERATOR -----------------
def generate_rank_card(username: str, avatar_bytes: Optional[bytes], level: int, xp: int,
                       aura: int, streak: int) -> bytes:
    """
    Returns PNG bytes for rank card.
    Simple, modern bold style (font fallback to default if FONT_PATH not set).
    """
    # Create base
    im = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (24, 26, 27, 255))  # dark background
    draw = ImageDraw.Draw(im)

    # Load fonts
    try:
        if FONT_PATH:
            font_bold = ImageFont.truetype(FONT_PATH, 40)
            font_regular = ImageFont.truetype(FONT_PATH, 22)
            font_small = ImageFont.truetype(FONT_PATH, 18)
        else:
            font_bold = ImageFont.load_default()
            font_regular = ImageFont.load_default()
            font_small = ImageFont.load_default()
    except Exception:
        font_bold = ImageFont.load_default()
        font_regular = ImageFont.load_default()
        font_small = ImageFont.load_default()

    padding = 24

    # Left side: avatar circle
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
            # Draw placeholder circle
            draw.ellipse((av_x, av_y, av_x+av_size, av_y+av_size), fill=(40,40,40))
    else:
        draw.ellipse((av_x, av_y, av_x+av_size, av_y+av_size), fill=(40,40,40))

    # Right side: text & bar
    text_x = av_x + av_size + padding
    text_y = av_y

    # Username
    draw.text((text_x, text_y), username, font=font_bold, fill=(255,255,255))
    # Level / aura / streak on same line to the right
    level_text = f"Level {level}"
    aura_text = f"Aura: {aura}"
    streak_text = f"Streak: {streak}d"
    # layout small top-right texts
    right_x = CARD_WIDTH - padding - draw.textsize(level_text, font=font_regular)[0]
    draw.text((right_x, text_y), level_text, font=font_regular, fill=(255,255,255))

    # XP progress bar and values
    frac = progress_fraction(xp, level)
    bar_y = text_y + 60
    bar_x = text_x
    # bar background
    draw.rounded_rectangle((bar_x, bar_y, bar_x + BAR_WIDTH, bar_y + BAR_HEIGHT), radius=12, fill=(50,50,50))
    # progress
    prog_w = int(BAR_WIDTH * frac)
    if prog_w > 0:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + prog_w, bar_y + BAR_HEIGHT), radius=12, fill=(30,215,96))
    # xp text
    xp_text = f"{format_big(xp)} XP"
    xp_text_w, _ = draw.textsize(xp_text, font=font_small)
    draw.text((bar_x + BAR_WIDTH - xp_text_w, bar_y - 24), xp_text, font=font_small, fill=(200,200,200))

    # small footer: Aura and Streak
    footer_y = bar_y + BAR_HEIGHT + 18
    draw.text((bar_x, footer_y), aura_text, font=font_regular, fill=(200,200,200))
    streak_w = draw.textsize(streak_text, font=font_regular)[0]
    draw.text((CARD_WIDTH - padding - streak_w, footer_y), streak_text, font=font_regular, fill=(200,200,200))

    # Export PNG bytes
    b = io.BytesIO()
    im.save(b, format="PNG")
    b.seek(0)
    return b.read()

# ----------------- COG -----------------
class LevelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cooldowns = {}  # user_id -> timestamp (for message XP)
        self._profile_cache = {}  # user_id -> (expiry_ts, bytes)

        logger.info("LevelCog (enhanced) loaded.")

    # ----------------- message listener -----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # do not award XP for bots or DMs
        if message.author.bot or not message.guild:
            return

        # ignore commands that start with '!' (your prefix)
        if message.content.startswith("!"):
            return

        user_id = str(message.author.id)
        now = time.time()
        last = self._cooldowns.get(user_id, 0)
        if now - last < MESSAGE_COOLDOWN:
            return
        self._cooldowns[user_id] = now

        # ensure user row exists
        await add_user(user_id)

        # fetch before-update level (no cache here; we want the authoritative DB value)
        before = await get_user(user_id)
        before_xp = int(before[1]) if before and before[1] is not None else 0
        before_level = int(before[2]) if before and before[2] is not None else xp_to_level(before_xp)

        # update xp -> update_user handles level calc & aura addition on level up
        await update_user(user_id, xp_gain=XP_PER_MESSAGE)

        # fetch after-update row and compare
        after = await get_user(user_id)
        after_xp = int(after[1]) if after and after[1] is not None else before_xp
        after_level = int(after[2]) if after and after[2] is not None else xp_to_level(after_xp)
        after_aura = int(after[4]) if after and len(after) > 4 and after[4] is not None else 0

        # clear profile cache so next profile shows updated values
        self._profile_cache.pop(user_id, None)

        # leveled?
        if after_level > before_level:
            # send level-up embed to configured channel only
            guild = message.guild
            channel = guild.get_channel(LEVEL_UP_CHANNEL_ID) if LEVEL_UP_CHANNEL_ID else None
            embed = discord.Embed(
                title="⚔ LEVEL UP!",
                description=f"<@{user_id}> has reached **Level {after_level}**!",
                color=discord.Color.gold()
            )
            embed.set_image(url=LEVEL_UP_GIF)
            embed.set_footer(text="Your journey continues...")
            try:
                if channel:
                    await channel.send(embed=embed)
                else:
                    # Fallback: send in same channel if no configured channel found
                    await message.channel.send(embed=embed)
            except Exception:
                logger.exception("Failed to send level-up embed.")

    # ----------------- daily command (streak) -----------------
    @commands.command(name="daily")
    async def daily(self, ctx: commands.Context):
        """
        Claim daily streak reward.
        Resets at midnight Asia/Kolkata.
        Awards both aura + XP. Streak broken if day missed.
        """
        user_id = str(ctx.author.id)
        now = datetime.now(tz=DAILY_RESET_TZ)
        today_midnight = epoch_midnight_tz(now, DAILY_RESET_TZ)
        # fetch user row
        row = await get_user(user_id)
        if not row:
            await add_user(user_id)
            row = await get_user(user_id)

        # row columns: user_id, xp, level, messages, aura, (streak), (last_daily_claim)
        streak = int(row[5]) if len(row) > 5 and row[5] is not None else 0
        last_claim = int(row[6]) if len(row) > 6 and row[6] is not None else 0

        # if last_claim is >= today's midnight, they've already claimed for today
        if last_claim >= today_midnight:
            await ctx.reply("You have already claimed today's reward. Come back tomorrow.")
            return

        # check if last_claim was yesterday's midnight (i.e., consecutive), else reset
        yesterday = (now - timedelta(days=1))
        yesterday_midnight = epoch_midnight_tz(yesterday, DAILY_RESET_TZ)
        if last_claim >= yesterday_midnight:
            streak = streak + 1
        else:
            streak = 1

        # calculate rewards (base * (1 + MULT * (streak-1))), capped
        multiplier = 1.0 + DAILY_STREAK_MULTIPLIER * (streak - 1)
        multiplier = min(multiplier, DAILY_MAX_STREAK_BONUS)
        give_aura = int(DAILY_BASE_AURA * multiplier)
        give_xp = int(DAILY_BASE_XP * multiplier)

        # persist: we will update xp & aura by running update_user + direct aura modify in DB
        # update_user only handles xp and level & aura increase on level up; we need to add aura manually
        # We'll do a simple SQL update here using aiosqlite (short lived).
        async with aiosqlite.connect(DB_PATH) as db:
            # ensure row exists
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
            # add xp and messages increment via existing update_user logic (so level calc remains centralized)
            await db.execute("UPDATE users SET xp = xp + ?, messages = messages + 1 WHERE user_id = ?",
                             (give_xp, user_id))
            # add aura
            await db.execute("UPDATE users SET aura = aura + ? WHERE user_id = ?", (give_aura, user_id))
            # set streak and last_daily_claim to today's midnight epoch (to align with midnight reset)
            await db.execute("UPDATE users SET streak = ?, last_daily_claim = ? WHERE user_id = ?",
                             (streak, today_midnight, user_id))
            await db.commit()

        # After DB changes, run sync to recalc level if xp warranted
        # We'll recalc level by reading xp and computing new level (and setting DB level).
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
            r = await cursor.fetchone()
            if r:
                xp_now = int(r[0])
                cur_level = int(r[1])
                new_level = xp_to_level(xp_now)
                if new_level > cur_level:
                    # award extra aura for level-up is handled in update_user originally, but here we set directly
                    await db.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, user_id))
                    logger.info(f"User {user_id} leveled up from daily claim: {cur_level} -> {new_level}")
            await db.commit()

        # Clear profile cache so next profile shows updated values
        self._profile_cache.pop(user_id, None)

        # Notify user
        await ctx.reply(f"✅ You claimed your daily reward!\nAura: +{give_aura} | XP: +{give_xp} | Streak: {streak} day(s)")

    # ----------------- profile (rank card) -----------------
    @commands.command(name="profile", aliases=["rank", "lvl"])
    async def profile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        user_id = str(member.id)

        # check cache
        cached = self._profile_cache.get(user_id)
        if cached and cached[0] > time.time():
            png_bytes = cached[1]
            file = discord.File(io.BytesIO(png_bytes), filename="rank.png")
            await ctx.reply(file=file)
            return

        row = await get_user(user_id)
        if not row:
            await ctx.reply("User not found. They should chat once to create a profile.")
            return

        xp = int(row[1]) if row[1] is not None else 0
        level = int(row[2]) if row[2] is not None else xp_to_level(xp)
        aura = int(row[4]) if len(row) > 4 and row[4] is not None else 0
        streak = int(row[5]) if len(row) > 5 and row[5] is not None else 0

        # fetch avatar bytes (small size)
        avatar_bytes = None
        try:
            avatar = member.avatar or member.default_avatar
            avatar_bytes = await avatar.read()
        except Exception:
            avatar_bytes = None

        png = generate_rank_card(member.display_name, avatar_bytes, level, xp, aura, streak)

        # cache result
        expiry = time.time() + PROFILE_CACHE_TTL
        self._profile_cache[user_id] = (expiry, png)

        file = discord.File(io.BytesIO(png), filename="rank.png")
        await ctx.reply(file=file)

    # ----------------- leaderboard (compact top 10) -----------------
    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        # compact, long-term friendly: top 10 by level then xp
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 10")
            rows = await cursor.fetchall()

        if not rows:
            return await ctx.reply("No data to show on leaderboard.")

        lines = []
        for i, r in enumerate(rows, start=1):
            try:
                uid = int(r[0])
            except Exception:
                uid = None
            lvl = int(r[2]) if r[2] is not None else xp_to_level(int(r[1] or 0))
            xp = int(r[1] or 0)
            member = ctx.guild.get_member(uid) if uid else None
            name = member.display_name if member else f"User {r[0]}"
            lines.append(f"**#{i}** {name} — Level {lvl} • {format_big(xp)} XP")

        await ctx.reply("\n".join(lines))

    # ----------------- admin utilities (setxp, setlevel, lvlup, exportcsv, synclevels) -----------------
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

    @commands.command(name="lvlup")
    @commands.has_permissions(administrator=True)
    async def lvlup(self, ctx: commands.Context, member: discord.Member, levels: int = 1):
        if levels < 1:
            return await ctx.reply("Please provide a positive number of levels.")
        user_id = str(member.id)
        row = await get_user(user_id)
        xp = int(row[1]) if row and row[1] is not None else 0
        level = int(row[2]) if row and row[2] is not None else xp_to_level(xp)
        target_level = level + levels
        target_xp = level_to_min_xp(target_level)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
            await db.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (target_xp, target_level, user_id))
            await db.commit()
        self._profile_cache.pop(user_id, None)

        # send level-up embed to configured channel only
        guild = ctx.guild
        channel = guild.get_channel(LEVEL_UP_CHANNEL_ID) if LEVEL_UP_CHANNEL_ID else None
        embed = discord.Embed(
            title=f"⚔ LEVEL UP!",
            description=f"<@{user_id}> has reached **Level {target_level}**!",
            color=discord.Color.gold()
        )
        embed.set_image(url=LEVEL_UP_GIF)
        embed.set_footer(text="Your journey continues...")
        try:
            if channel:
                await channel.send(embed=embed)
            else:
                await ctx.send(embed=embed)
        except Exception:
            logger.exception("Failed to send lvlup embed.")
        await ctx.reply(f"✅ {member.display_name} is now Level **{target_level}** ({target_xp} XP)!")

    @commands.command(name="synclevels")
    @commands.has_permissions(administrator=True)
    async def synclevels(self, ctx: commands.Context, limit: int = 5000):
        await ctx.reply("Starting level sync...")
        updated = 0
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id, xp FROM users LIMIT ?", (limit,))
            rows = await cursor.fetchall()
            for r in rows:
                uid = r[0]
                xp = int(r[1] or 0)
                new_level = xp_to_level(xp)
                await db.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, uid))
                updated += 1
            await db.commit()
        self._profile_cache.clear()
        await ctx.reply(f"Sync complete — updated {updated} users (limit {limit}).")

    @commands.command(name="exportcsv")
    @commands.has_permissions(administrator=True)
    async def exportcsv(self, ctx: commands.Context, limit: int = 1000):
        import csv
        from io import StringIO, BytesIO
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT user_id,xp,level,messages,aura,streak,last_daily_claim FROM users ORDER BY xp DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
        if not rows:
            return await ctx.reply("No data to export.")
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id","xp","level","messages","aura","streak","last_daily_claim"])
        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3] if len(r) > 3 else "", r[4] if len(r) > 4 else "", r[5] if len(r) > 5 else "", r[6] if len(r) > 6 else ""])
        data = BytesIO(buf.getvalue().encode("utf-8"))
        data.seek(0)
        file = discord.File(fp=data, filename="leaderboard.csv")
        await ctx.reply("CSV Export:", file=file)

    # ----------------- cleanup -----------------
    async def cog_unload(self):
        self._profile_cache.clear()
        self._cooldowns.clear()
        logger.info("LevelCog unloading — cleared caches.")


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
    logger.info("LevelCog loaded.")
