# cogs/aura.py
"""
Aura System — Samurai Edition (Polished, 300+ lines)
---------------------------------------------------
Theme: Samurai / Power
Steal Mode: CHAOS (dangerous; failed steals have harsh consequences)
Daily reward: Gambler style (1 - 500 random aura)
No "gold" references — aura only.
"""

from __future__ import annotations

import asyncio
import csv
import math
import random
import time
from datetime import datetime, timedelta
from io import StringIO
from typing import List, Optional, Tuple

import aiosqlite
import discord
from discord.ext import commands, tasks

# local project DB helpers
from database import add_user, get_user, update_user
from logger import logger

DB_PATH = "database.db"

# ----------------------------
# Samuari Theme Constants
# ----------------------------
THEME_ICON = "⚔️"
THEME_COLOR = 0xCC3333  # deep red
RANKS = [
    (0, "Ronin"),
    (500, "Ashigaru"),
    (2000, "Samurai"),
    (7000, "Hatamoto"),
    (15000, "Shogun"),
]

# ----------------------------
# Config: cooldowns, values
# ----------------------------
TRANSFER_COOLDOWN = 7       # seconds between transfers per user
STEAL_COOLDOWN = 300        # seconds per user for steal
REACTION_AURA_CHANCE = 0.08 # chance to get passive aura when reaction added to your message
REACTION_AURA_AMT = 2
DAILY_MIN = 1
DAILY_MAX = 500
DAILY_COOLDOWN_HOURS = 24
DAILY_JACKPOT_CHANCE = 0.002  # tiny chance to hit jackpot 2500 aura
AURA_LOG_TABLE = "aura_transactions"
DAILY_TABLE = "aura_daily_claims"
BADGES_TABLE = "aura_badges"
BOUNTY_TABLE = "aura_bounties"
CURSE_TABLE = "aura_curses"

# ----------------------------
# Helpers & data classes
# ----------------------------
def samurai_embed(title: str, description: str = "", color: int = THEME_COLOR) -> discord.Embed:
    e = discord.Embed(title=f"{THEME_ICON}  {title}", description=description, color=color)
    e.set_footer(text="Realm Royz — Aura (Samurai Path)")
    return e

def format_aura(amount: int) -> str:
    return f"**{amount}** aura"

def rank_for_aura(aura: int) -> str:
    name = RANKS[0][1]
    for thresh, title in RANKS:
        if aura >= thresh:
            name = title
    return name

# Transaction tuple: (id, timestamp, type, from_user, to_user, amount, reason)
class TRow:
    def __init__(self, id, ts, ttype, from_user, to_user, amount, reason):
        self.id = id
        self.ts = ts
        self.type = ttype
        self.from_user = from_user
        self.to_user = to_user
        self.amount = amount
        self.reason = reason

# ----------------------------
# Aura Cog
# ----------------------------
class Aura(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # in-memory cooldown maps
        self._transfer_cooldowns: dict[str, float] = {}
        self._steal_cooldowns: dict[str, float] = {}
        # bounties and curses are persisted, but we keep quick caches to reduce DB reads
        self._bounty_cache: dict[str, int] = {}
        self._curse_cache: dict[str, float] = {}  # user_id -> curse_expire_ts
        # background task to cleanup expired curses
        self._cleanup_task = tasks.loop(minutes=5)(self._cleanup_caches)
        self._cleanup_task.start()

    # ----------------------------
    # Cog lifecycle
    # ----------------------------
    async def cog_load(self):
        logger.info("[Aura] loading and ensuring DB tables")
        await self._ensure_tables()

    async def cog_unload(self):
        try:
            self._cleanup_task.cancel()
        except Exception:
            pass

    # ----------------------------
    # DB table creation (safe)
    # ----------------------------
    async def _ensure_tables(self):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # transactions
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {AURA_LOG_TABLE} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        type TEXT NOT NULL,
                        from_user TEXT,
                        to_user TEXT,
                        amount INTEGER NOT NULL,
                        reason TEXT
                    )
                    """
                )
                # daily claims
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {DAILY_TABLE} (
                        user_id TEXT PRIMARY KEY,
                        last_claim TEXT,
                        streak INTEGER DEFAULT 0
                    )
                    """
                )
                # badges
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {BADGES_TABLE} (
                        user_id TEXT,
                        badge TEXT,
                        granted_at TEXT,
                        PRIMARY KEY (user_id, badge)
                    )
                    """
                )
                # bounties
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {BOUNTY_TABLE} (
                        target_id TEXT PRIMARY KEY,
                        amount INTEGER
                    )
                    """
                )
                # curses
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {CURSE_TABLE} (
                        user_id TEXT PRIMARY KEY,
                        expires_at TEXT
                    )
                    """
                )
                await db.commit()
            logger.info("[Aura] DB tables ensured.")
        except Exception as e:
            logger.exception("[Aura] Error ensuring tables: %s", e)

    # ----------------------------
    # Low-level DB helpers
    # ----------------------------
    async def _log(self, ttype: str, from_user: Optional[str], to_user: Optional[str], amount: int, reason: Optional[str] = None):
        ts = datetime.utcnow().isoformat()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(f"INSERT INTO {AURA_LOG_TABLE} (ts, type, from_user, to_user, amount, reason) VALUES (?, ?, ?, ?, ?, ?)",
                                 (ts, ttype, from_user, to_user, amount, reason))
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] Failed to log transaction: %s", e)

    async def _set_aura(self, user_id: str, amount: int):
        """Write aura to users table. Keep non-negative."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (max(0, int(amount)), user_id))
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] _set_aura failed for %s: %s", user_id, e)

    async def _get_user_row(self, user_id: str):
        try:
            return await get_user(user_id)
        except Exception as e:
            logger.exception("[Aura] _get_user_row failed for %s: %s", user_id, e)
            return None

    async def _ensure_user(self, user_id: str):
        try:
            await add_user(user_id)
        except Exception:
            pass
        return await self._get_user_row(user_id)

    async def _get_transactions(self, limit: int = 200) -> List[TRow]:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT id, ts, type, from_user, to_user, amount, reason FROM {AURA_LOG_TABLE} ORDER BY id DESC LIMIT ?", (limit,))
                rows = await cur.fetchall()
            return [TRow(*r) for r in rows]
        except Exception as e:
            logger.exception("[Aura] read tx failed: %s", e)
            return []

    # ----------------------------
    # Cache helpers for bounties and curses
    # ----------------------------
    async def _get_bounty(self, user_id: str) -> int:
        if user_id in self._bounty_cache:
            return self._bounty_cache[user_id]
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT amount FROM {BOUNTY_TABLE} WHERE target_id = ?", (user_id,))
                row = await cur.fetchone()
                val = int(row[0]) if row else 0
                self._bounty_cache[user_id] = val
                return val
        except Exception as e:
            logger.exception("[Aura] get_bounty failed: %s", e)
            return 0

    async def _set_bounty(self, user_id: str, amount: int):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                if amount <= 0:
                    await db.execute(f"DELETE FROM {BOUNTY_TABLE} WHERE target_id = ?", (user_id,))
                    self._bounty_cache.pop(user_id, None)
                else:
                    await db.execute(f"INSERT OR REPLACE INTO {BOUNTY_TABLE} (target_id, amount) VALUES (?, ?)", (user_id, amount))
                    self._bounty_cache[user_id] = amount
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] set_bounty failed: %s", e)

    async def _get_curse(self, user_id: str) -> Optional[float]:
        # returns expiry timestamp if cursed
        if user_id in self._curse_cache:
            return self._curse_cache[user_id]
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT expires_at FROM {CURSE_TABLE} WHERE user_id = ?", (user_id,))
                row = await cur.fetchone()
                if not row:
                    return None
                exp = float(row[0])
                self._curse_cache[user_id] = exp
                return exp
        except Exception as e:
            logger.exception("[Aura] get_curse failed: %s", e)
            return None

    async def _set_curse(self, user_id: str, expiry_ts: float):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(f"INSERT OR REPLACE INTO {CURSE_TABLE} (user_id, expires_at) VALUES (?, ?)", (user_id, str(expiry_ts)))
                await db.commit()
            self._curse_cache[user_id] = expiry_ts
        except Exception as e:
            logger.exception("[Aura] set_curse failed: %s", e)

    async def _clear_curse(self, user_id: str):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(f"DELETE FROM {CURSE_TABLE} WHERE user_id = ?", (user_id,))
                await db.commit()
            self._curse_cache.pop(user_id, None)
        except Exception as e:
            logger.exception("[Aura] clear_curse failed: %s", e)

    async def _get_badges(self, user_id: str) -> List[str]:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT badge FROM {BADGES_TABLE} WHERE user_id = ?", (user_id,))
                rows = await cur.fetchall()
                return [r[0] for r in rows]
        except Exception as e:
            logger.exception("[Aura] get_badges failed: %s", e)
            return []

    async def _grant_badge(self, user_id: str, badge: str):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(f"INSERT OR IGNORE INTO {BADGES_TABLE} (user_id, badge, granted_at) VALUES (?, ?, ?)", (user_id, badge, datetime.utcnow().isoformat()))
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] grant_badge failed: %s", e)

    # ----------------------------
    # Background cleanup
    # ----------------------------
    async def _cleanup_caches(self):
        # purge expired curses from cache
        now = time.time()
        expired = [uid for uid, ts in self._curse_cache.items() if ts <= now]
        for uid in expired:
            self._curse_cache.pop(uid, None)
            # also ensure DB row removed
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(f"DELETE FROM {CURSE_TABLE} WHERE user_id = ?", (uid,))
                    await db.commit()
            except Exception:
                pass

    # ----------------------------
    # COMMAND: Transfer aura (give/take)
    # ----------------------------
    @commands.command(name="aura")
    @commands.cooldown(1, TRANSFER_COOLDOWN, commands.BucketType.user)
    async def aura_cmd(self, ctx: commands.Context, amount: int, member: discord.Member):
        """
        !aura <+/-amount> @user
        Give or take aura from another user.
        """
        giver = ctx.author
        giver_id = str(giver.id)
        target = member
        target_id = str(target.id)

        if giver_id == target_id:
            return await ctx.send("❌ You cannot modify aura for yourself.")

        # ensure users exist
        await self._ensure_user(giver_id)
        await self._ensure_user(target_id)

        giver_row = await self._get_user_row(giver_id)
        target_row = await self._get_user_row(target_id)
        giver_aura = int(giver_row[4]) if giver_row else 0
        target_aura = int(target_row[4]) if target_row else 0

        # If cursed, transfers are halved
        curse_exp = await self._get_curse(giver_id)
        if curse_exp and curse_exp > time.time():
            await ctx.send("⚠ You are cursed — your aura effectiveness is weakened.")
            amount = max(1, amount // 2 if amount > 0 else -(abs(amount) // 2))

        if amount > 0:
            # give aura
            if giver_aura < amount:
                return await ctx.send(f"❌ You only have {format_aura(giver_aura)}.")
            new_giver = giver_aura - amount
            new_target = target_aura + amount
            await self._set_aura(giver_id, new_giver)
            await self._set_aura(target_id, new_target)
            await self._log("transfer", giver_id, target_id, amount, reason="user_transfer")
            await ctx.send(embed=samurai_embed("Transfer Complete", f"{giver.mention} gave {format_aura(amount)} to {target.mention}"))
            # check badges
            await self._maybe_award_badge(target_id)
        else:
            # negative amount => take from target
            amt = abs(amount)
            if target_aura <= 0:
                return await ctx.send("❌ Target has no aura to take.")
            taken = min(amt, target_aura)
            new_giver = giver_aura + taken
            new_target = target_aura - taken
            await self._set_aura(giver_id, new_giver)
            await self._set_aura(target_id, new_target)
            await self._log("take", giver_id, target_id, taken, reason="user_take")
            await ctx.send(embed=samurai_embed("Taken", f"{giver.mention} took {format_aura(taken)} from {target.mention}"))

    # ----------------------------
    # COMMAND: myAura (profile short)
    # ----------------------------
    @commands.command(name="myAura")
    async def my_aura_cmd(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        await self._ensure_user(str(target.id))
        row = await self._get_user_row(str(target.id))
        aura = int(row[4]) if row else 0
        rank = rank_for_aura(aura)
        badges = await self._get_badges(str(target.id))

        embed = samurai_embed(f"{target.display_name} — Aura", "")
        embed.add_field(name="Aura", value=format_aura(aura), inline=True)
        embed.add_field(name="Rank", value=f"{rank}", inline=True)
        embed.add_field(name="Badges", value=", ".join(badges) if badges else "None", inline=False)
        try:
            embed.set_thumbnail(url=target.display_avatar.url)
        except Exception:
            pass
        await ctx.send(embed=embed)

    # ----------------------------
    # COMMAND: auraProfile (detailed)
    # ----------------------------
    @commands.command(name="auraProfile")
    async def aura_profile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        await self._ensure_user(str(target.id))
        row = await self._get_user_row(str(target.id))
        aura = int(row[4]) if row else 0
        badges = await self._get_badges(str(target.id))
        bounty = await self._get_bounty(str(target.id))
        curse_exp = await self._get_curse(str(target.id))
        cursed = False
        if curse_exp and curse_exp > time.time():
            cursed = True

        embed = samurai_embed(f"{target.display_name} — Full Aura Profile")
        embed.set_thumbnail(url=target.display_avatar.url if hasattr(target, "display_avatar") else None)
        embed.add_field(name="Aura", value=format_aura(aura), inline=True)
        embed.add_field(name="Rank", value=rank_for_aura(aura), inline=True)
        embed.add_field(name="Bounty", value=format_aura(bounty) if bounty > 0 else "None", inline=True)
        embed.add_field(name="Cursed", value="Yes (active)" if cursed else "No", inline=True)
        embed.add_field(name="Badges", value=", ".join(badges) if badges else "None", inline=False)
        await ctx.send(embed=embed)

    # ----------------------------
    # COMMAND: auraTop (paginated)
    # ----------------------------
    @commands.command(name="auraTop")
    async def aura_top(self, ctx: commands.Context, page: int = 1):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT user_id, aura FROM users ORDER BY aura DESC")
                rows = await cur.fetchall()
        except Exception as e:
            logger.exception("[Aura] auraTop read failed: %s", e)
            return await ctx.send("⚠ Failed to load leaderboard.")

        if not rows:
            return await ctx.send("No aura data yet.")

        page = max(1, page)
        page_size = 10
        total_pages = math.ceil(len(rows) / page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        slice_rows = rows[start:start + page_size]

        desc_lines = []
        for idx, (uid, aura) in enumerate(slice_rows, start=start + 1):
            member = ctx.guild.get_member(int(uid)) if ctx.guild else None
            name = member.display_name if member else f"User {uid}"
            desc_lines.append(f"#{idx} • {name} — {format_aura(int(aura))}")

        embed = samurai_embed(f"Top Aura — Page {page}/{total_pages}", "\n".join(desc_lines))
        await ctx.send(embed=embed)

    # ----------------------------
    # COMMAND: daily (gambler-style)
    # ----------------------------
    @commands.command(name="daily")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def daily_cmd(self, ctx: commands.Context):
        uid = str(ctx.author.id)
        now = datetime.utcnow()

        await self._ensure_user(uid)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT last_claim, streak FROM {DAILY_TABLE} WHERE user_id = ?", (uid,))
                row = await cur.fetchone()
                if row:
                    last_claim_s, streak = row
                    last_claim = datetime.fromisoformat(last_claim_s) if last_claim_s else None
                else:
                    last_claim = None
                    streak = 0

                can_claim = False
                if last_claim is None:
                    can_claim = True
                    new_streak = 1
                else:
                    delta = now - last_claim
                    if delta >= timedelta(hours=DAILY_COOLDOWN_HOURS):
                        # reset if 48+ hours
                        if delta >= timedelta(hours=DAILY_COOLDOWN_HOURS * 2):
                            new_streak = 1
                        else:
                            new_streak = streak + 1
                        can_claim = True
                    else:
                        can_claim = False
                        new_streak = streak

                if not can_claim:
                    next_time = last_claim + timedelta(hours=DAILY_COOLDOWN_HOURS)
                    rem = next_time - now
                    return await ctx.send(f"⏳ You already claimed. Next claim in {rem.seconds//3600}h {(rem.seconds%3600)//60}m.")

                # Gambler style: random between DAILY_MIN..DAILY_MAX, with tiny jackpot
                reward = random.randint(DAILY_MIN, DAILY_MAX)
                if random.random() < DAILY_JACKPOT_CHANCE:
                    reward += 2500  # jackpot
                    jack = True
                else:
                    jack = False

                # streak slightly increases small chance
                if new_streak >= 7:
                    reward += random.randint(0, 100)

                # apply curse penalty if cursed
                curse_exp = await self._get_curse(uid)
                if curse_exp and curse_exp > time.time():
                    reward = max(1, reward // 2)

                # write new aura
                row_main = await self._get_user_row(uid)
                curr = int(row_main[4]) if row_main else 0
                new_total = curr + reward
                await self._set_aura(uid, new_total)

                # upsert daily table
                await db.execute(f"INSERT OR REPLACE INTO {DAILY_TABLE} (user_id, last_claim, streak) VALUES (?, ?, ?)", (uid, now.isoformat(), new_streak))
                await db.commit()

                await self._log("daily", None, uid, reward, reason=f"streak_{new_streak}")
                if jack:
                    await ctx.send(embed=samurai_embed("JACKPOT!", f"🎉 {ctx.author.mention} hit the jackpot and gained {format_aura(reward)} (streak {new_streak})"))
                else:
                    await ctx.send(embed=samurai_embed("Daily Claim", f"{ctx.author.mention} claimed {format_aura(reward)} (streak {new_streak})"))
                # badge check
                await self._maybe_award_badge(uid)
        except Exception as e:
            logger.exception("[Aura] daily error: %s", e)
            await ctx.send("⚠ Daily claim failed.")

    # ----------------------------
    # COMMAND: steal (CHAOS mode)
    # ----------------------------
    @commands.command(name="steal")
    @commands.cooldown(1, STEAL_COOLDOWN, commands.BucketType.user)
    async def steal_cmd(self, ctx: commands.Context, target: discord.Member, amount: int):
        thief = ctx.author
        thief_id = str(thief.id)
        target_id = str(target.id)

        if thief_id == target_id:
            return await ctx.send("❌ Can't steal from yourself.")

        if amount <= 0:
            return await ctx.send("❌ Amount must be positive.")

        await self._ensure_user(thief_id)
        await self._ensure_user(target_id)

        thief_row = await self._get_user_row(thief_id)
        target_row = await self._get_user_row(target_id)
        thief_aura = int(thief_row[4]) if thief_row else 0
        target_aura = int(target_row[4]) if target_row else 0

        if target_aura <= 0:
            return await ctx.send("❌ Target has no aura to steal.")

        # chaos mechanics:
        # success chance base on relative auras and randomness
        base_chance = 0.35
        if thief_aura < target_aura:
            base_chance += 0.15
        else:
            base_chance -= 0.1
        base_chance = max(0.05, min(0.9, base_chance))
        roll = random.random()

        # If cursed, reduce chance further
        curse_exp = await self._get_curse(thief_id)
        if curse_exp and curse_exp > time.time():
            base_chance *= 0.6

        if roll < base_chance:
            # success: steal min(amount, target_aura)
            stolen = min(int(amount), target_aura)
            await self._set_aura(thief_id, thief_aura + stolen)
            await self._set_aura(target_id, target_aura - stolen)
            await self._log("steal_success", thief_id, target_id, stolen, reason="chaos_steal")
            await ctx.send(embed=samurai_embed("Steal Success", f"💥 {thief.mention} stole {format_aura(stolen)} from {target.mention}!"))
            await self._maybe_award_badge(thief_id)
        else:
            # failure: CHAOS punishments (random among several)
            punishment = random.choice(["lose_equal", "double_loss", "bounty", "curse"])
            if punishment == "lose_equal":
                lost = min(thief_aura, amount)
                await self._set_aura(thief_id, thief_aura - lost)
                await self._log("steal_fail_lose", thief_id, target_id, lost, reason="chaos_fail")
                await ctx.send(embed=samurai_embed("Steal Failed", f"❌ {thief.mention} failed and lost {format_aura(lost)} as penalty."))
            elif punishment == "double_loss":
                lost = min(thief_aura, amount * 2)
                await self._set_aura(thief_id, thief_aura - lost)
                await self._log("steal_fail_double", thief_id, target_id, lost, reason="chaos_fail_double")
                await ctx.send(embed=samurai_embed("Disaster!", f"🔥 {thief.mention} failed catastrophically and lost {format_aura(lost)}!"))
            elif punishment == "bounty":
                # increase bounty on thief (so others may get incentive)
                current_bounty = await self._get_bounty(thief_id)
                new_bounty = current_bounty + max(1, amount // 2)
                await self._set_bounty(thief_id, new_bounty)
                await self._log("steal_fail_bounty", thief_id, target_id, new_bounty, reason="chaos_bounty")
                await ctx.send(embed=samurai_embed("Marked!", f"⚠ {thief.mention} failed and now has a bounty of {format_aura(new_bounty)} on their head!"))
            else:  # curse
                # apply a temporary curse which halves daily rewards for 6 hours
                expiry = time.time() + 6 * 3600
                await self._set_curse(thief_id, expiry)
                await self._log("steal_fail_curse", thief_id, target_id, 0, reason="chaos_curse")
                await ctx.send(embed=samurai_embed("Cursed!", f"🕯 {thief.mention} failed and was cursed — daily rewards and gains are weakened for 6 hours."))

    # ----------------------------
    # COMMAND: auraRain (admin only)
    # ----------------------------
    @commands.command(name="auraRain")
    @commands.has_permissions(administrator=True)
    async def aura_rain(self, ctx: commands.Context, total: int, winners: int = 5):
        """
        !auraRain <total> [winners] — distribute 'total' aura randomly among online members (winners count)
        """
        if total <= 0 or winners <= 0:
            return await ctx.send("❌ Invalid parameters.")

        online_members = [m for m in ctx.guild.members if not m.bot and m.status != discord.Status.offline]
        if not online_members:
            return await ctx.send("No online members to rain upon.")

        winners = min(winners, len(online_members))
        chosen = random.sample(online_members, k=winners)
        per = total // winners
        remainder = total - (per * winners)

        for i, mem in enumerate(chosen):
            amt = per + (1 if i == 0 and remainder > 0 else 0)
            await self._ensure_user(str(mem.id))
            row = await self._get_user_row(str(mem.id))
            curr = int(row[4]) if row else 0
            await self._set_aura(str(mem.id), curr + amt)
            await self._log("rain", None, str(mem.id), amt, reason="admin_rain")
        await ctx.send(embed=samurai_embed("Aura Rain", f"🌧 Distributed {total} aura among {winners} warriors."))

    # ----------------------------
    # COMMAND: auraHistory
    # ----------------------------
    @commands.command(name="auraHistory")
    async def aura_history(self, ctx: commands.Context, limit: int = 20):
        rows = await self._get_transactions(limit)
        if not rows:
            return await ctx.send("No aura transactions found.")
        lines = []
        for r in rows[:limit]:
            lines.append(f"{r.id}. [{r.ts}] {r.type} {r.amount} ({r.from_user or '-'} -> {r.to_user or '-'})")
        await ctx.send("```\n" + "\n".join(lines) + "\n```")

    # ----------------------------
    # COMMAND: auraExport (admin)
    # ----------------------------
    @commands.command(name="auraExport")
    @commands.has_permissions(administrator=True)
    async def aura_export(self, ctx: commands.Context, limit: int = 500):
        rows = await self._get_transactions(limit)
        if not rows:
            return await ctx.send("No transactions to export.")
        buf = StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "timestamp", "type", "from_user", "to_user", "amount", "reason"])
        for r in rows:
            w.writerow([r.id, r.ts, r.type, r.from_user, r.to_user, r.amount, r.reason])
        buf.seek(0)
        file = discord.File(fp=StringIO(buf.read()), filename="aura_transactions.csv")
        await ctx.send("📥 Aura transactions export:", file=file)

    # ----------------------------
    # Admin commands: set/reset aura & badge management
    # ----------------------------
    @commands.command(name="setaura")
    @commands.has_permissions(administrator=True)
    async def set_aura(self, ctx: commands.Context, member: discord.Member, amount: int):
        await self._ensure_user(str(member.id))
        await self._set_aura(str(member.id), amount)
        await self._log("admin_set", None, str(member.id), amount, reason="admin_set")
        await ctx.send(embed=samurai_embed("Admin Action", f"✅ Set {member.display_name}'s aura to {format_aura(amount)}"))

    @commands.command(name="resetAura")
    @commands.has_permissions(administrator=True)
    async def reset_aura(self, ctx: commands.Context, member: discord.Member):
        await self._ensure_user(str(member.id))
        await self._set_aura(str(member.id), 0)
        await self._log("admin_reset", None, str(member.id), 0, reason="admin_reset")
        await ctx.send(embed=samurai_embed("Admin Action", f"✅ Reset {member.display_name}'s aura to zero."))

    @commands.command(name="grantbadge")
    @commands.has_permissions(administrator=True)
    async def grant_badge(self, ctx: commands.Context, member: discord.Member, badge: str):
        await self._grant_badge(str(member.id), badge)
        await ctx.send(embed=samurai_embed("Badge Granted", f"🏅 Granted badge `{badge}` to {member.display_name}"))

    @commands.command(name="revokebadge")
    @commands.has_permissions(administrator=True)
    async def revoke_badge(self, ctx: commands.Context, member: discord.Member, badge: str):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(f"DELETE FROM {BADGES_TABLE} WHERE user_id = ? AND badge = ?", (str(member.id), badge))
                await db.commit()
            await ctx.send(embed=samurai_embed("Badge Revoked", f"🗑 Revoked badge `{badge}` from {member.display_name}"))
        except Exception as e:
            logger.exception("[Aura] revoke_badge failed: %s", e)
            await ctx.send("⚠ Failed to revoke badge.")

    # ----------------------------
    # Reaction listener (passive aura)
    # ----------------------------
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        # give small chance to grant passive aura to owner of reacted message
        try:
            if user.bot:
                return
            if random.random() < REACTION_AURA_CHANCE:
                owner = reaction.message.author
                if owner and not owner.bot:
                    uid = str(owner.id)
                    await self._ensure_user(uid)
                    row = await self._get_user_row(uid)
                    curr = int(row[4]) if row else 0
                    await self._set_aura(uid, curr + REACTION_AURA_AMT)
                    await self._log("reaction_bonus", None, uid, REACTION_AURA_AMT, reason="reaction")
        except Exception as e:
            logger.exception("[Aura] reaction handler error: %s", e)

    # ----------------------------
    # Utility: maybe award badges based on aura thresholds
    # ----------------------------
    async def _maybe_award_badge(self, user_id: str):
        row = await self._get_user_row(user_id)
        if not row:
            return
        aura = int(row[4])
        # example thresholds -> badges
        try:
            if aura >= 500 and "warrior" not in await self._get_badges(user_id):
                await self._grant_badge(user_id, "warrior")
            if aura >= 2000 and "veteran" not in await self._get_badges(user_id):
                await self._grant_badge(user_id, "veteran")
            if aura >= 10000 and "legend" not in await self._get_badges(user_id):
                await self._grant_badge(user_id, "legend")
        except Exception:
            pass

    # ----------------------------
    # Cooldown / admin reset helpers
    # ----------------------------
    @commands.command(name="resetAuraCooldowns")
    @commands.has_permissions(administrator=True)
    async def reset_cooldowns(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if member:
            uid = str(member.id)
            self._transfer_cooldowns.pop(uid, None)
            self._steal_cooldowns.pop(uid, None)
            await ctx.send(f"✅ Cleared aura cooldowns for {member.display_name}.")
        else:
            self._transfer_cooldowns.clear()
            self._steal_cooldowns.clear()
            await ctx.send("✅ Cleared all aura cooldowns.")

    # ----------------------------
    # Command error handlers (cooldowns)
    # ----------------------------
    @aura_cmd.error
    async def aura_cmd_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Transfer cooldown: try again in {error.retry_after:.1f}s")
        else:
            logger.exception("[Aura] aura_cmd error: %s", error)
            await ctx.send("⚠ An error occurred.")

    @daily_cmd.error
    async def daily_cmd_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send("⏳ Daily is on cooldown, try again shortly.")
        else:
            logger.exception("[Aura] daily error: %s", error)
            await ctx.send("⚠ Daily error.")

    @steal_cmd.error
    async def steal_cmd_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Steal cooldown: try again in {error.retry_after:.1f}s")
        else:
            logger.exception("[Aura] steal error: %s", error)
            await ctx.send("⚠ Steal error.")

    # ----------------------------
    # Clean shutdown
    # ----------------------------
    async def cog_before_invoke(self, ctx: commands.Context):
        # ensure our DB tables are in place before handling commands
        await self._ensure_tables()

# ----------------------------
# Setup
# ----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Aura(bot))
    logger.info("[Aura] Samurai Aura cog loaded (chaos steal, gambler daily).")
