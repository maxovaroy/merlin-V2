# cogs/aura.py
"""
Aura System — Samurai Edition (Polished, 300+ lines)
---------------------------------------------------
Theme: Samurai / Power
Steal Mode: CHAOS (dangerous; failed steals have harsh consequences)
Gamble: random aura (1 - 500) daily
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
from typing import List, Optional

import aiosqlite
import discord
from discord.ext import commands, tasks

from database import add_user, get_user, update_user
from logger import logger

DB_PATH = "database.db"

# ----------------------------
# Samurai Theme Constants
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
GAMBLE_MIN = 1
GAMBLE_MAX = 500
GAMBLE_COOLDOWN_HOURS = 24
GAMBLE_JACKPOT_CHANCE = 0.002  # tiny chance to hit jackpot 2500 aura

AURA_LOG_TABLE = "aura_transactions"
GAMBLE_TABLE = "aura_gamble_claims"
BADGES_TABLE = "aura_badges"
BOUNTY_TABLE = "aura_bounties"
CURSE_TABLE = "aura_curses"

# ----------------------------
# Helpers
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

class TRow:
    """Transaction row tuple"""
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
        self._transfer_cooldowns: dict[str, float] = {}
        self._steal_cooldowns: dict[str, float] = {}
        self._bounty_cache: dict[str, int] = {}
        self._curse_cache: dict[str, float] = {}
        self._cleanup_task = tasks.loop(minutes=5)(self._cleanup_caches)
        self._cleanup_task.start()

    # ----------------------------
    # Lifecycle
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
    # DB Tables
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
                # daily/gamble claims
                await db.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {GAMBLE_TABLE} (
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
    # DB Helpers
    # ----------------------------
    async def _log(self, ttype: str, from_user: Optional[str], to_user: Optional[str], amount: int, reason: Optional[str] = None):
        ts = datetime.utcnow().isoformat()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    f"INSERT INTO {AURA_LOG_TABLE} (ts, type, from_user, to_user, amount, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, ttype, from_user, to_user, amount, reason)
                )
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] Failed to log transaction: %s", e)

    async def _ensure_user(self, user_id: str):
        await add_user(user_id)
        return await get_user(user_id)

    async def _set_aura(self, user_id: str, amount: int):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (max(0, amount), user_id))
                await db.commit()
        except Exception as e:
            logger.exception("[Aura] _set_aura failed for %s: %s", user_id, e)

    async def _get_curse(self, user_id: str) -> Optional[float]:
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

    async def _cleanup_caches(self):
        now = time.time()
        expired = [uid for uid, ts in self._curse_cache.items() if ts <= now]
        for uid in expired:
            self._curse_cache.pop(uid, None)
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(f"DELETE FROM {CURSE_TABLE} WHERE user_id = ?", (uid,))
                    await db.commit()
            except Exception:
                pass

    # ----------------------------
    # Commands
    # ----------------------------
    @commands.command(name="auraTransfer")
    @commands.cooldown(1, TRANSFER_COOLDOWN, commands.BucketType.user)
    async def aura_transfer(self, ctx: commands.Context, amount: int, member: discord.Member):
        giver_id = str(ctx.author.id)
        target_id = str(member.id)
        if giver_id == target_id:
            return await ctx.send("❌ You cannot modify aura for yourself.")

        giver_row = await self._ensure_user(giver_id)
        target_row = await self._ensure_user(target_id)
        giver_aura = int(giver_row[4]) if giver_row else 0
        target_aura = int(target_row[4]) if target_row else 0

        curse_exp = await self._get_curse(giver_id)
        if curse_exp and curse_exp > time.time():
            amount = max(1, amount // 2 if amount > 0 else -(abs(amount) // 2))

        if amount > 0:
            if giver_aura < amount:
                return await ctx.send(f"❌ You only have {format_aura(giver_aura)}.")
            await self._set_aura(giver_id, giver_aura - amount)
            await self._set_aura(target_id, target_aura + amount)
            await self._log("transfer", giver_id, target_id, amount, reason="user_transfer")
            await ctx.send(embed=samurai_embed("Transfer Complete", f"{ctx.author.mention} gave {format_aura(amount)} to {member.mention}"))
        else:
            amt = abs(amount)
            if target_aura <= 0:
                return await ctx.send("❌ Target has no aura to take.")
            stolen = min(amt, target_aura)
            await self._set_aura(giver_id, giver_aura + stolen)
            await self._set_aura(target_id, target_aura - stolen)
            await self._log("take", giver_id, target_id, stolen, reason="user_take")
            await ctx.send(embed=samurai_embed("Taken", f"{ctx.author.mention} took {format_aura(stolen)} from {member.mention}"))

    @commands.command(name="auraProfile")
    async def aura_profile(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        row = await self._ensure_user(str(target.id))
        aura = int(row[4]) if row else 0
        embed = samurai_embed(f"{target.display_name} — Aura Profile", f"Aura: {format_aura(aura)}\nRank: {rank_for_aura(aura)}")
        await ctx.send(embed=embed)

    @commands.command(name="gamble")  # renamed from daily
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def gamble_cmd(self, ctx: commands.Context):
        """Gamble daily aura (replaces 'daily' command to avoid conflicts)"""
        uid = str(ctx.author.id)
        now = datetime.utcnow()
        await self._ensure_user(uid)

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(f"SELECT last_claim, streak FROM {GAMBLE_TABLE} WHERE user_id = ?", (uid,))
                row = await cur.fetchone()
                last_claim = datetime.fromisoformat(row[0]) if row else None
                streak = row[1] if row else 0

                if last_claim and (now - last_claim) < timedelta(hours=GAMBLE_COOLDOWN_HOURS):
                    rem = last_claim + timedelta(hours=GAMBLE_COOLDOWN_HOURS) - now
                    return await ctx.send(f"⏳ You already gambled. Next in {rem.seconds//3600}h {(rem.seconds%3600)//60}m.")

                reward = random.randint(GAMBLE_MIN, GAMBLE_MAX)
                if random.random() < GAMBLE_JACKPOT_CHANCE:
                    reward += 2500
                    jack = True
                else:
                    jack = False

                curse_exp = await self._get_curse(uid)
                if curse_exp and curse_exp > time.time():
                    reward = max(1, reward // 2)

                curr_aura = int((await get_user(uid))[4])
                await self._set_aura(uid, curr_aura + reward)
                new_streak = streak + 1 if last_claim else 1
                await db.execute(f"INSERT OR REPLACE INTO {GAMBLE_TABLE} (user_id, last_claim, streak) VALUES (?, ?, ?)", (uid, now.isoformat(), new_streak))
                await db.commit()
                await self._log("gamble", None, uid, reward, reason=f"streak_{new_streak}")

                if jack:
                    await ctx.send(embed=samurai_embed("JACKPOT!", f"{ctx.author.mention} hit the jackpot and gained {format_aura(reward)}!"))
                else:
                    await ctx.send(embed=samurai_embed("Gamble Result", f"{ctx.author.mention} gained {format_aura(reward)} (streak {new_streak})"))

        except Exception as e:
            logger.exception("[Aura] gamble error: %s", e)
            await ctx.send("⚠ Gamble failed.")

    # ----------------------------
    # COMMAND: steal (CHAOS mode) — rewritten per requested rules
    # ----------------------------
    @commands.command(name="steal")
    @commands.cooldown(1, STEAL_COOLDOWN, commands.BucketType.user)
    async def steal_cmd(self, ctx: commands.Context, target: discord.Member, amount: int):
        """
        CHAOS steal:
        - Success chance scales with aura difference (richer victims easier)
        - On success: thief gets 5-20% of victim's aura (min 10)
        - On fail: thief loses DOUBLE the attempted amount; victim gains that full lost amount; bounty increases on thief
        """
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

        # Base chance and scaling by relative aura (richer victims easier)
        base_chance = 0.35
        # compute ratio influence
        # if victim has much more aura than thief, increase chance up to 0.80
        if victim_ratio := (target_aura / (thief_aura + 1)):
            # scale factor: logish to avoid runaway
            scale = math.log10(max(1, victim_ratio))  # 0.. 
            bonus = min(0.45, scale * 0.15)  # small bonus per order of magnitude
            chance = base_chance + bonus
        else:
            chance = base_chance

        # if thief has more aura, reduce chance down to min 0.20
        if thief_aura > target_aura and thief_aura > 0:
            adv = min(0.15, (thief_aura - target_aura) / (thief_aura + target_aura + 1) * 0.5)
            chance = max(0.20, chance - adv)

        # curse reduces chance
        curse_exp = await self._get_curse(thief_id)
        if curse_exp and curse_exp > time.time():
            chance *= 0.6

        roll = random.random()

        # success path
        if roll < chance:
            # steal percentage of victim aura (5-20%)
            pct = random.randint(5, 20) / 100.0
            stolen = max(10, int(target_aura * pct))
            stolen = min(stolen, target_aura)
            # apply transfer
            await self._set_aura(thief_id, thief_aura + stolen)
            await self._set_aura(target_id, target_aura - stolen)
            await self._log("steal_success", thief_id, target_id, stolen, reason="chaos_steal")
            await ctx.send(embed=samurai_embed("Steal Success", f"💥 {thief.mention} stealthily took {format_aura(stolen)} from {target.mention}!"))
            await self._maybe_award_badge(thief_id)
            return

        # failure path (CHAOS): thief loses double the attempted amount
        loss = min(thief_aura, amount * 2)
        # thief loses loss; victim gains full lost amount
        new_thief = max(0, thief_aura - loss)
        new_target = target_aura + loss
        await self._set_aura(thief_id, new_thief)
        await self._set_aura(target_id, new_target)
        # increase bounty on thief (1.5x of attempted amount, rounded)
        b_increase = int(max(1, amount * 1.5))
        current_bounty = await self._get_bounty(thief_id)
        new_bounty = current_bounty + b_increase
        await self._set_bounty(thief_id, new_bounty)
        await self._log("steal_fail", thief_id, target_id, loss, reason="chaos_fail_double_and_bounty")
        # dramatic chaotic message (C)
        await ctx.send(embed=samurai_embed("Catastrophic Failure!", f"🔥 {thief.mention} attempted to steal {format_aura(amount)} but failed catastrophically!\nThey **lost {format_aura(loss)}**, which {target.mention} gains — a bounty of {format_aura(new_bounty)} is placed on the thief!"))
        return

    # ----------------------------
    # COMMAND: auraRain (admin only)
    # ----------------------------
    @commands.command(name="auraRain")
    @commands.has_permissions(administrator=True)
    async def aura_rain(self, ctx: commands.Context, total: int, winners: int = 5):
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

    @gamble.error
    async def gamble_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send("⏳ Gamble is on cooldown, try again shortly.")
        else:
            logger.exception("[Aura] gamble error: %s", error)
            await ctx.send("⚠ Gamble error.")

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
    logger.info("[Aura] Samurai Aura cog loaded (chaos steal, gambler renamed to gamble).")
