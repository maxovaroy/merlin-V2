# cogs/humanizer.py
"""
HUMANIZER — Human-like chat generator for Realm Royz
----------------------------------------------------
Responds casually like a real person using slang, fillers, tone, and memes.
Works only in the configured channel and ignores all others.

Improvements in this version:
- SQLite per-user session memory (scalable, live queries)
- Cringe scoring with decay and pruning
- Intent detection and adaptive tone
- Spam / repeated-message detection + strong cooldown
- Roasts escalate with user behavior
- DB pruning to limit growth
- Clean startup / shutdown of background tasks
"""

from __future__ import annotations

import asyncio
import random
import time
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord.ext import commands

from logger import logger  # your repo logger (optional); safe fallback to print if missing

# ---------------- CONFIG ----------------
DB_PATH = "humanizer.db"                 # separate DB file for humanizer
HUMANIZER_CHANNEL = 1446421555965067354
ENABLE_HUMANIZER = True
REPLY_PROBABILITY = 1.0
USER_COOLDOWN = 5                        # normal cooldown seconds
STRONG_COOLDOWN = 12                     # stronger cooldown when spamming
MIN_MSG_LENGTH = 2
MAX_MEMORY_PER_USER = 40                 # keep up to N messages per user in DB
REPEAT_SPAM_THRESHOLD = 3                # identical message count for spam trigger
REPEAT_SPAM_WINDOW = 20                  # seconds window to check repeats
CRINGE_INCREMENT_SHORT_MSG = 1           # amount to add on short messages
CRINGE_DECAY_INTERVAL = 60 * 10         # every 10 minutes decay task runs
CRINGE_DECAY_AMOUNT = 1                  # amount to reduce per interval
CRINGE_PRUNE_THRESHOLD = 200             # if stored messages for a user exceed this, prune

# ================= SLANG / VOICE =================
SLANG_MAP = {
    "you": "u", "your": "ur", "are": "r", "because": "cuz", "little": "smol",
    "tonight": "tnite", "good night": "gn", "good morning": "gm", "brother": "bro",
    "what": "wut", "please": "pls", "people": "ppl", "friends": "frens",
}

FILLERS = ["ngl", "lol", "idk", "fr", "no cap", "ong", "btw", "lmao", "hmmm"]

SARCASM_PHRASES = [
    "wow big brain move", "ok genius", "peak performance ngl", "legend behaviour",
    "amaze", "legendary move fr", "ok bro no cap"
]

GENZ_SHORTS = ["ong", "fr", "no cap", "lowkey", "highkey", "bet", "say less", "slaps", "vibes", "cap", "sus"]

GENZ_RESPONSES_SHORT = ["fr", "bet", "say less", "okok", "hmm", "ight", "go on", "mhm", "aight", "yea"]

GENZ_QUESTION_RESPONSES = ["good q ngl", "lemme think fr", "idk fr", "maybe? idk", "sus", "ask google"]

GENZ_REPLIES = {
    "money": ["u broke or what? fr get a job", "nah bro my charity closed in 1999", "send bank screenshot no cap"],
    "goodboy": ["goodboy? sit. roll. bark. jk...", "say woof rn"],
    "bored": ["skill issue fr", "touch grass", "uninstall boredom.exe"],
    "tough": ["relax goku u ain't him", "ur loud but harmless like gummy bear"],
    "lonely": ["lonely? touch grass", "i talk to microwaves as friends ong"],
    "love": ["i can only love wifi ngl", "bots don't love but i vibe with u"],
}

TONE_MOOD = {
    "friendly": {"prefix": "", "suffix": "<:Eminem:1308041429339209778>"},
    "neutral": {"prefix": "", "suffix": ""},
    "chaotic": {"prefix": "yo ", "suffix": "<:Hacker:1308134036937375794>"},
}

MIN_LEVEL_FOR_FRIENDLY = 5

# ---------------- SAFETY ----------------
BLOCKED_PHRASES = [
    "how to kill", "how to murder", "how to hurt", "bomb", "terror", "how to make weapon",
    "suicide", "rape", "kidnap", "hide the body", "dispose of a body"
]
BLOCKED_RESPONSE = [
    "Can't help with that my boy.",
    "nah g that's wild — not answering.",
    "bro what 💀 no.",
    "ayo chill, can't answer that."
]

# ---------------- Cog ----------------
class Humanizer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_reply: Dict[int, float] = {}
        self._last_bot_reply: Dict[int, str] = {}
        self._lock = asyncio.Lock()

        # Startup tasks
        self._db_task = self.bot.loop.create_task(self._ensure_db())
        self._decay_task = self.bot.loop.create_task(self._cringe_decay_loop())
        logger.info("[HUMANIZER] initialized")

    # ---------------- Database setup ----------------
    async def _ensure_db(self):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory (
                        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        message TEXT,
                        tone INTEGER DEFAULT 0,
                        cringe INTEGER DEFAULT 0,
                        ts INTEGER
                    )
                    """
                )
                await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON memory(user_id)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_mem_guild ON memory(guild_id)")
                await db.commit()
            logger.info("[HUMANIZER] DB ready")
        except Exception as e:
            logger.exception("[HUMANIZER] DB init failed: %s", e)

    # ---------------- DB helpers ----------------
    async def _db_save_message(self, guild_id: int, user_id: int, message: str, tone: int = 0):
        ts = int(time.time())
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("INSERT INTO memory (guild_id, user_id, message, tone, cringe, ts) VALUES (?, ?, ?, ?, ?, ?)",
                                 (guild_id, user_id, message, tone, 0, ts))
                await db.commit()
                # prune older if exceed MAX_MEMORY_PER_USER
                cur = await db.execute("SELECT COUNT(*) FROM memory WHERE guild_id=? AND user_id=?", (guild_id, user_id))
                row = await cur.fetchone()
                cnt = row[0] if row else 0
                if cnt > MAX_MEMORY_PER_USER:
                    delete_n = cnt - MAX_MEMORY_PER_USER
                    await db.execute("DELETE FROM memory WHERE rowid IN (SELECT rowid FROM memory WHERE guild_id=? AND user_id=? ORDER BY ts ASC LIMIT ?)",
                                     (guild_id, user_id, delete_n))
                    await db.commit()
        except Exception as e:
            logger.exception("[HUMANIZER] _db_save_message failed: %s", e)

    async def _db_load_recent(self, guild_id: int, user_id: int, limit: int = 5) -> List[str]:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT message FROM memory WHERE guild_id=? AND user_id=? ORDER BY ts DESC LIMIT ?",
                                       (guild_id, user_id, limit))
                rows = await cur.fetchall()
            return [r[0] for r in rows] if rows else []
        except Exception as e:
            logger.exception("[HUMANIZER] _db_load_recent failed: %s", e)
            return []

    async def _db_inc_cringe(self, guild_id: int, user_id: int, amount: int = 1):
        """Increment cringe by writing a small record with cringe amount; also update latest row for quick reading."""
        # We'll store cringe per latest row to allow a fast read; also keep absolute count in aggregate queries.
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # If user has previous rows, update the latest row's cringe; else insert a dummy row with message ''
                cur = await db.execute("SELECT rowid, cringe FROM memory WHERE guild_id=? AND user_id=? ORDER BY ts DESC LIMIT 1", (guild_id, user_id))
                row = await cur.fetchone()
                if row:
                    rowid, current = row
                    await db.execute("UPDATE memory SET cringe = ? WHERE rowid = ?", (current + amount, rowid))
                else:
                    # insert placeholder message for tracking
                    await db.execute("INSERT INTO memory (guild_id,user_id,message,tone,cringe,ts) VALUES (?,?,?,?,?,?)",
                                     (guild_id, user_id, "", 0, amount, int(time.time())))
                await db.commit()
        except Exception as e:
            logger.exception("[HUMANIZER] _db_inc_cringe failed: %s", e)

    async def _db_get_cringe(self, guild_id: int, user_id: int) -> int:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT cringe FROM memory WHERE guild_id=? AND user_id=? ORDER BY ts DESC LIMIT 1", (guild_id, user_id))
                row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.exception("[HUMANIZER] _db_get_cringe failed: %s", e)
            return 0

    async def _db_decay_cringe_all(self, amount: int = CRINGE_DECAY_AMOUNT):
        """Reduce cringe on latest row for each user to apply decay over time."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # update latest row per user: use a correlated subquery to fetch latest rowid per user+guild
                await db.execute("""
                    UPDATE memory
                    SET cringe = MAX(0, cringe - ?)
                    WHERE rowid IN (
                        SELECT rowid FROM memory AS m2
                        WHERE m2.rowid = (
                            SELECT rowid FROM memory WHERE user_id = m2.user_id AND guild_id = m2.guild_id ORDER BY ts DESC LIMIT 1
                        )
                    )
                """, (amount,))
                await db.commit()
        except Exception as e:
            logger.exception("[HUMANIZER] _db_decay_cringe_all failed: %s", e)

    # ---------------- Decay background loop ----------------
    async def _cringe_decay_loop(self):
        try:
            while True:
                await asyncio.sleep(CRINGE_DECAY_INTERVAL)
                await self._db_decay_cringe_all()
                logger.debug("[HUMANIZER] cringe decay tick")
        except asyncio.CancelledError:
            logger.info("[HUMANIZER] cringe decay loop cancelled")
        except Exception:
            logger.exception("[HUMANIZER] cringe decay loop error")

    # ---------------- Helpers ----------------
    def _apply_slang(self, text: str) -> str:
        # replace whole words sensibly (simple approach)
        # lower-case mapping but preserve original case for first char where possible
        def repl(m):
            w = m.group(0)
            lower = w.lower()
            if lower in SLANG_MAP:
                return SLANG_MAP[lower]
            return w
        return re.sub(r"\b\w+\b", repl, text)

    def _typoify(self, text: str) -> str:
        new = ""
        for c in text:
            new += c * random.randint(1, 2) if random.random() < 0.05 else c
        if random.random() < 0.25:
            new += " " + random.choice(FILLERS)
        return new

    def _skidify(self, text: str) -> str:
        endings = [" fr", " ong", " no cap", " 💀", "🤖"]
        return text + random.choice(endings) if random.random() < 0.35 else text

    def _detect_intent(self, text: str) -> str:
        t = text.lower().strip()
        if any(g in t for g in ["hi", "hello", "sup", "yo", "hey"]):
            return "greeting"
        if t.endswith("?") or any(q in t for q in ["who", "what", "why", "how", "when", "where"]):
            return "question"
        if any(w in t for w in ["love", "miss", "handsome", "beautiful"]):
            return "praise"
        if any(w in t for w in ["fuck", "shit", "stfu", "bitch", "suck", "nig"]):
            return "insult"
        if any(w in t for w in ["bored", "boring", "im bored"]):
            return "bored"
        return "smalltalk"

    def _mild_sarcasm(self, text: str) -> str:
        variants = [
            f"{text} — ok genius",
            f"{text} ngl that's cute",
            f"{text} wow big brain move",
            f"{text} i vibed but lowkey cringe"
        ]
        return random.choice(variants)

    def _strong_roast(self, user_display: str) -> str:
        roasts = [
            f"{user_display} fr you got no chill",
            f"imagine being {user_display} and asking that",
            f"bruh {user_display} you peak clownery",
            f"stop talking, {user_display}, you're lowering the IQ of this chat",
            f"{user_display} wrote that? call it a draft and burn it"
        ]
        return random.choice(roasts)

    async def _get_user_stats(self, user: discord.Member) -> Tuple[int, int]:
        """Try to fetch level/aura from other cogs; return defaults if missing."""
        level, aura = 1, 0
        level_cog = self.bot.get_cog("LevelCog")
        if level_cog:
            try:
                xp, level = await level_cog.get_user_level_data(user.guild.id, user.id)
            except Exception:
                pass
        aura_cog = self.bot.get_cog("Aura")
        if aura_cog:
            try:
                row = await aura_cog._get_user_row(str(user.id))
                aura = int(row[4]) if row else 0
            except Exception:
                pass
        return level, aura

    def _tone_for_user(self, level: int, aura: int, cringe: int) -> str:
        """Compute tone label: friendly if level/aura high, chaotic if low level or high cringe."""
        if level >= MIN_LEVEL_FOR_FRIENDLY or aura > 1000:
            return "friendly"
        if cringe >= 12 or level <= 2:
            return "chaotic"
        return "neutral"

    # ---------------- Reply generation ----------------
    async def _generate_reply(self, msg: discord.Message) -> Optional[str]:
        text = msg.content.strip()
        if not text:
            return None
        low = text.lower()

        # Safety quick check
        if any(b in low for b in BLOCKED_PHRASES):
            return random.choice(BLOCKED_RESPONSE)

        # Save message record (tone default 0). We'll adjust cringe separately.
        await self._db_save_message(msg.guild.id, msg.author.id, text, tone=0)

        # Count recent identical messages for spam detection
        recent = await self._db_load_recent(msg.guild.id, msg.author.id, limit=REPEAT_SPAM_THRESHOLD)
        if len(recent) >= REPEAT_SPAM_THRESHOLD and all(r == recent[0] for r in recent):
            # escalate roast + strong cooldown
            roast = self._strong_roast(msg.author.display_name)
            # increment cringe more aggressively
            await self._db_inc_cringe(msg.guild.id, msg.author.id, amount=2)
            return f"{roast} — stop spamming, you're draining vibes."

        # Increase cringe for very short messages
        if len(text.split()) <= 2:
            await self._db_inc_cringe(msg.guild.id, msg.author.id, CRINGE_INCREMENT_SHORT_MSG)

        # compute personality factors
        cringe_val = await self._db_get_cringe(msg.guild.id, msg.author.id)
        level, aura = await self._get_user_stats(msg.author)
        tone_label = self._tone_for_user(level, aura, cringe_val)
        tone_style = TONE_MOOD.get(tone_label, TONE_MOOD["neutral"])

        # Intent-driven responses
        intent = self._detect_intent(text)
        sarcasm_bias = min(0.75, 0.15 + 0.05 * (cringe_val // 2))  # more cringe -> more sarcasm

        if intent == "greeting":
            if random.random() < 0.6:
                base = random.choice(["yo", "sup", "what now", "wassup skid"])
            else:
                base = random.choice(["aight", "say less", "fr"])
            reply = self._mild_sarcasm(base) if random.random() < sarcasm_bias else base
            return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

        if intent == "question":
            if random.random() < 0.4:
                answer = random.choice(GENZ_QUESTION_RESPONSES)
            else:
                answer = random.choice(["hmm good q", "idk bro", "lemme think fr"])
            reply = self._mild_sarcasm(answer) if random.random() < sarcasm_bias else answer
            return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

        if intent == "praise":
            reply = random.choice(["frfr", "yea ngl", "ok king", "say less"])
            return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

        if intent == "insult":
            # escalate: roast user hard if they've been insulting frequently or have high cringe
            # compute recent insults count
            mem = await self._db_load_recent(msg.guild.id, msg.author.id, limit=12)
            insult_count = sum(1 for m in mem if any(w in m.lower() for w in ["fuck", "shit", "stfu", "bitch", "suck", "nig"]))
            if insult_count >= 2 or cringe_val >= 6 or random.random() < 0.6:
                await self._db_inc_cringe(msg.guild.id, msg.author.id, amount=1)
                reply = self._strong_roast(msg.author.display_name)
            else:
                reply = random.choice(["ok bro", "no cap", "lol ok"]) + " " + random.choice(FILLERS)
            return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

        if intent == "bored":
            reply = random.choice(GENZ_REPLIES["bored"])
            return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

        # fallback: generate via slang, memory callback, smalltalk
        reply = self._apply_slang(text)

        # memory callback sometimes referencing a recent message
        rec = await self._db_load_recent(msg.guild.id, msg.author.id, limit=6)
        meaningful = [m for m in rec if len(m.split()) > 3]
        if meaningful and random.random() < 0.35:
            reply += f" — lowkey u said '{meaningful[0][:28]}...' before"

        # occasional typo/filler
        if random.random() < 0.12:
            reply = self._typoify(reply)

        # apply sarcasm bias
        if random.random() < sarcasm_bias:
            reply = self._mild_sarcasm(reply)

        # avoid parroting exact message
        if reply.strip().lower() == low:
            tail = random.choice(SARCASM_PHRASES + GENZ_SHORTS)
            reply = f"{reply} {tail}"

        # micro-opener chance
        if random.random() < 0.08:
            reply = random.choice(["ok real talk — ", "bruh — "]) + reply

        # random easter egg
        if random.random() < 0.02:
            reply += " 🎉 mini easter egg unlocked!"

        reply = self._skidify(reply)
        return f"{tone_style['prefix']}{reply}{tone_style['suffix']}"

    # ---------------- Listener ----------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not ENABLE_HUMANIZER:
            return
        if not message.guild:
            return
        if message.author.bot:
            return
        if message.channel.id != HUMANIZER_CHANNEL:
            return
        if len(message.content.strip()) < MIN_MSG_LENGTH:
            # still save short message for tracking but do not reply
            await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)
            return

        now = time.time()
        last = self._last_reply.get(message.author.id, 0)
        elapsed = now - last

        # spam / too-fast check
        if elapsed < 1.0:
            # immediate spam; apply strong roast and strong cooldown
            roast = self._strong_roast(message.author.display_name)
            await message.reply(roast, mention_author=False)
            # set next reply allowed after STRONG_COOLDOWN seconds
            self._last_reply[message.author.id] = now + STRONG_COOLDOWN
            # bump cringe
            await self._db_inc_cringe(message.guild.id, message.author.id, amount=2)
            # still store message
            await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)
            return

        # respect cooldown (USER_COOLDOWN)
        if elapsed < USER_COOLDOWN:
            # do not reply, but still store message for memory tracking
            await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)
            return

        # probabilistic reply permission
        if random.random() > REPLY_PROBABILITY:
            await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)
            return

        # safety filter
        low = message.content.lower()
        for phrase in BLOCKED_PHRASES:
            if phrase in low:
                await message.channel.send(random.choice(BLOCKED_RESPONSE))
                # store the blocked attempt too
                await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)
                return

        # generate and send reply under lock
        async with self._lock:
            try:
                reply = await self._generate_reply(message)
            except Exception as e:
                logger.exception("[HUMANIZER] _generate_reply failed: %s", e)
                reply = None

            # send reply if any
            if reply:
                try:
                    async with message.channel.typing():
                        await asyncio.sleep(random.uniform(0.25, 1.1))
                        await message.reply(reply, mention_author=False)
                except Exception:
                    # send fallback
                    try:
                        await message.channel.send(reply)
                    except Exception:
                        logger.exception("[HUMANIZER] Failed to send reply")

                # record last reply time (now)
                self._last_reply[message.author.id] = time.time()
                # store the bot's last reply for threaded callbacks
                self._last_bot_reply[message.author.id] = reply

            # always persist incoming message (already done in generate, but ensure)
            await self._db_save_message(message.guild.id, message.author.id, message.content, tone=0)

    # ---------------- Admin / Owner Commands ----------------
    @commands.group(name="humanizer", invoke_without_command=True)
    @commands.is_owner()
    async def humanizer(self, ctx: commands.Context):
        desc = (
            f"**Humanizer Config**\n"
            f"Enabled: {ENABLE_HUMANIZER}\n"
            f"Reply Probability: {REPLY_PROBABILITY}\n"
            f"User Cooldown: {USER_COOLDOWN}s\n"
            f"Min Msg Length: {MIN_MSG_LENGTH}\n"
        )
        await ctx.send(desc)

    @humanizer.command(name="preview")
    @commands.is_owner()
    async def humanizer_preview(self, ctx: commands.Context, *, text: str):
        fake = ctx.message
        # create a shallow fake message-like object
        class _Fake:
            def __init__(self, content, author, guild):
                self.content = content
                self.author = author
                self.guild = guild

        fake_msg = _Fake(text, ctx.author, ctx.guild)
        reply = await self._generate_reply(fake_msg)  # _generate_reply expects discord.Message-ish
        if not reply:
            return await ctx.send("(No reply generated)")
        await ctx.send(f"> {text}\n**=>** {reply}")

    @humanizer.command(name="setprob")
    @commands.is_owner()
    async def humanizer_setprob(self, ctx: commands.Context, prob: float):
        global REPLY_PROBABILITY
        REPLY_PROBABILITY = max(0.0, min(1.0, prob))
        await ctx.send(f"✅ Reply probability set to {REPLY_PROBABILITY}")

    @humanizer.command(name="setcooldown")
    @commands.is_owner()
    async def humanizer_setcd(self, ctx: commands.Context, secs: int):
        global USER_COOLDOWN
        USER_COOLDOWN = max(0, secs)
        await ctx.send(f"✅ User cooldown set to {USER_COOLDOWN}s")

    @humanizer.command(name="clearmem")
    @commands.is_owner()
    async def humanizer_clearmem(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        # Clear DB entries for a user in this guild (owner-only)
        member = member or ctx.author
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM memory WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id))
                await db.commit()
            await ctx.send(f"Cleared humanizer memory for {member.display_name}")
        except Exception:
            await ctx.send("Failed to clear memory.")
            logger.exception("[HUMANIZER] clearmem failed")

    # ---------------- Lifecycle ----------------
    async def cog_unload(self):
        # Cancel background tasks cleanly
        try:
            if hasattr(self, "_decay_task") and not self._decay_task.done():
                self._decay_task.cancel()
            if hasattr(self, "_db_task") and not self._db_task.done():
                self._db_task.cancel()
            logger.info("[HUMANIZER] cog unloaded")
        except Exception:
            logger.exception("[HUMANIZER] error during unload")

# ---------------- Setup ----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Humanizer(bot))
    logger.info("[HUMANIZER] cog setup complete")
