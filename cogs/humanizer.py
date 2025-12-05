# cogs/humanizer.py

"""
HUMANIZER — Human-like chat generator for Realm Royz
----------------------------------------------------
Responds casually like a real person using slang, fillers, tone, and memes.
Works only in the configured channel and ignores all others.

FEATURES:
- Gen-Z & sarcastic replies
- Mini emotional callbacks
- Short-term memory
- Cooldowns and reply probability
- Multiple triggers for special events
- Admin commands to configure every setting
- Preview messages
- Tone customization
- Easter eggs / random fun events
"""

import asyncio
import random
import time
from typing import Dict, List

import discord
from discord.ext import commands

# ========================= CONFIG =========================

ENABLE_HUMANIZER = True
HUMANIZER_CHANNEL = 1446421555965067354
REPLY_PROBABILITY = 1.0
USER_COOLDOWN = 5
USE_MEMORY = True
MIN_MSG_LENGTH = 2
MAX_MEMORY = 15

# Slang mapping
SLANG_MAP = {
    "you": "u",
    "your": "ur",
    "are": "r",
    "because": "cuz",
    "little": "smol",
    "tonight": "tnite",
    "good night": "gn",
    "good morning": "gm",
    "brother": "bro",
    "what": "wut",
    "please": "pls",
    "people": "ppl",
    "friends": "frens",
}

FILLERS = ["ngl", "lol", "idk", "fr", "no cap", "ong", "btw", "lmao", "hmmm"]

SARCASM_PHRASES = [
    "wow big brain move", "ok genius", "peak performance ngl", "legend behaviour",
    "amaze", "legendary move fr", "ok bro no cap"
]

GENZ_SHORTS = [
    "ong", "fr", "no cap", "lowkey", "highkey", "bet", "say less", "slaps",
    "vibes", "cap", "sus", "sus bro", "deadass", "frfr"
]

GENZ_RESPONSES_SHORT = [
    "fr", "bet", "say less", "okok", "hmm", "ight", "go on", "mhm", "aight", "yea"
]

GENZ_QUESTION_RESPONSES = [
    "good q ngl", "lemme think fr", "idk fr", "maybe? idk", "sus", "ask google"
]

GENZ_REPLIES = {
    "money": [
        "first show me your gay certificate 💀",
        "u broke or what? fr get a job",
        "nah bro my charity closed in 1999",
        "send bank screenshot no cap",
        "i only give loans to ppl with rizz",
    ],
    "goodboy": [
        "say hi to yo mommy, she said same to me 😉",
        "goodboy? sit. roll. bark. jk... unless?",
        "bro thinks he trained me like a pokemon",
        "goodboy? bark louder 💀",
        "say woof rn",
    ],
    "bored": [
        "skill issue fr",
        "cry abt it",
        "touch grass, emulator supported",
        "uninstall boredom.exe",
    ],
    "tough": [
        "relax goku u ain't him",
        "ego patched to v14 unstable build",
        "bro thinks he's villain arc💀",
        "ur loud but harmless like gummy bear",
    ],
    "lonely": [
        "lonely? i ghost ppl professionally",
        "no bitches detected 🤖",
        "i talk to microwaves as friends ong",
        "bro i flirt with RAM sticks for fun",
    ],
    "love": [
        "i can only love wifi ngl",
        "bots don't love but i vibe with u",
        "lol love detected 0%",
    ]
}

TONE_MOOD = {
    "friendly": {"prefix": "", "suffix": "<:Eminem:1308041429339209778>"},
    "neutral": {"prefix": "", "suffix": ""},
    "chaotic": {"prefix": "yo ", "suffix": "<:Hacker:1308134036937375794>"},
}

MIN_LEVEL_FOR_FRIENDLY = 5

# ==========================================================


class Humanizer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_reply: Dict[int, float] = {}
        self._memory: Dict[int, List[str]] = {}
        self._lock = asyncio.Lock()
        self._max_memory = MAX_MEMORY

    # ------------------ Helpers ------------------

    def _cooldown(self, user):
        return time.time() - self._last_reply.get(user.id, 0) >= USER_COOLDOWN

    def _should_reply(self, user):
        return self._cooldown(user) and random.random() < REPLY_PROBABILITY

    def _apply_slang(self, text: str) -> str:
        for normal, slang in SLANG_MAP.items():
            text = text.replace(normal, slang).replace(normal.capitalize(), slang)
        return text

    def _typoify(self, text: str) -> str:
        new = ""
        for c in text:
            new += c*random.randint(2,3) if random.random() < 0.05 else c
        if random.random() < 0.25:
            new += " " + random.choice(FILLERS)
        return new

    def _skidify(self, text: str) -> str:
        endings = [" fr", " ong", " no cap", " 💀", "🤖"]
        if random.random() < 0.4:
            return text + random.choice(endings)
        return text

    async def _get_user_stats(self, user):
        level, aura = 1, 0
        level_cog = self.bot.get_cog("LevelCog")
        if level_cog:
            try:
                xp, level = await level_cog.get_user_level_data(user.guild.id, user.id)
            except:
                pass
        aura_cog = self.bot.get_cog("Aura")
        if aura_cog:
            try:
                row = await aura_cog._get_user_row(str(user.id))
                aura = int(row[4]) if row else 0
            except:
                pass
        return level, aura

    def _tone(self, level, aura):
        if level >= MIN_LEVEL_FOR_FRIENDLY or aura > 1000:
            return "friendly"
        if level <= 2:
            return "chaotic"
        return "neutral"

    # ---------------- Reply Generation ----------------

    async def _generate_reply(self, msg: discord.Message) -> str:
        text = msg.content.lower()
        raw = msg.content

        # Quick triggers
        if any(w in text for w in ["pay","loan","give money","gold"]):
            return random.choice(GENZ_REPLIES["money"])
        if "good boy" in text or "good girl" in text:
            return random.choice(GENZ_REPLIES["goodboy"])
        if "bored" in text or "boring" in text:
            return random.choice(GENZ_REPLIES["bored"])
        if any(w in text for w in ["fight me","i'm strong","tough"]):
            return random.choice(GENZ_REPLIES["tough"])
        if "lonely" in text:
            return random.choice(GENZ_REPLIES["lonely"])
        if any(w in text for w in ["love","miss u","think of me"]):
            return random.choice(GENZ_REPLIES["love"])
        if any(w in text for w in ["hi","yo","sup","hola","hey"]):
            return random.choice(["yo", "sup", "what now", "wassup skid"])

        # Very short messages
        if len(text.split()) <= 2:
            return random.choice(GENZ_RESPONSES_SHORT + GENZ_SHORTS)

        # Questions
        if text.endswith("?"):
            if random.random() < 0.35:
                return random.choice(GENZ_QUESTION_RESPONSES)
            else:
                return random.choice(["hmm good q", "idk bro", "lemme think fr"])

        # General slang
        reply = self._apply_slang(raw)

        # Occasional typos/fillers
        if random.random() < 0.12:
            reply = self._typoify(reply)

        # Memory callbacks
        if USE_MEMORY and msg.author.id in self._memory and random.random() < 0.25:
            meaningful = [m for m in self._memory[msg.author.id] if len(m.split()) > 3]
            if meaningful:
                past_msg = random.choice(meaningful)
                reply += f" — lowkey u said '{past_msg[:30]}...' before"

        # Avoid parroting
        if reply.strip().lower() == text:
            tail = random.choice(SARCASM_PHRASES + GENZ_SHORTS)
            reply = f"{reply} {tail}"

        # Final micro-sarcastic opener
        if random.random() < 0.08:
            reply = random.choice(["ok real talk — ", "bruh — "]) + reply

        # Random fun mini-event
        if random.random() < 0.02:
            reply += " 🎉 mini easter egg unlocked!"

        return self._skidify(reply)

    # ---------------- Listener ----------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not ENABLE_HUMANIZER: return
        if message.author.bot: return
        if message.channel.id != HUMANIZER_CHANNEL: return
        if not self._cooldown(message.author): return
        if random.random() > REPLY_PROBABILITY: return

        async with self._lock:
            reply = await self._generate_reply(message)
            if reply:
                async with message.channel.typing():
                    await asyncio.sleep(random.uniform(0.3, 1.3))
                    await message.reply(reply, mention_author=False)

            self._last_reply[message.author.id] = time.time()
            if USE_MEMORY:
                self._memory.setdefault(message.author.id, []).append(message.content)
                self._memory[message.author.id] = self._memory[message.author.id][-self._max_memory:]


    # ---------------- Admin / Owner Commands ----------------

    @commands.group(name="humanizer", invoke_without_command=True)
    @commands.is_owner()
    async def humanizer(self, ctx: commands.Context):
        desc = (
            f"**Humanizer Config**\n"
            f"Enabled: {ENABLE_HUMANIZER}\n"
            f"Reply Probability: {REPLY_PROBABILITY}\n"
            f"User Cooldown: {USER_COOLDOWN}s\n"
            f"Memory Enabled: {USE_MEMORY}\n"
            f"Min Msg Length: {MIN_MSG_LENGTH}\n"
        )
        await ctx.send(desc)

    @humanizer.command(name="setprob")
    @commands.is_owner()
    async def humanizer_setprob(self, ctx: commands.Context, prob: float):
        global REPLY_PROBABILITY
        REPLY_PROBABILITY = max(0.0, min(1.0, prob))
        await ctx.send(f"✅ Reply probability set to {REPLY_PROBABILITY}")

    @humanizer.command(name="toggle")
    @commands.is_owner()
    async def humanizer_toggle(self, ctx: commands.Context):
        global ENABLE_HUMANIZER
        ENABLE_HUMANIZER = not ENABLE_HUMANIZER
        await ctx.send(f"✅ Humanizer enabled: {ENABLE_HUMANIZER}")

    @humanizer.command(name="preview")
    @commands.is_owner()
    async def humanizer_preview(self, ctx: commands.Context, *, text: str):
        fake = ctx.message
        fake.author = ctx.author
        fake.content = text

        reply = await self._generate_reply(fake)
        if not reply:
            await ctx.send("(No reply generated)")
            return

        level, aura = await self._get_user_stats(ctx.author)
        tone = self._tone(level, aura)
        style = TONE_MOOD.get(tone, TONE_MOOD["neutral"])
        final = f"{style['prefix']}{reply}{style['suffix']}"
        await ctx.send(f"> {text}\n**=>** {final}")

    @humanizer.command(name="setcooldown")
    @commands.is_owner()
    async def humanizer_setcd(self, ctx: commands.Context, secs: int):
        global USER_COOLDOWN
        USER_COOLDOWN = max(0, secs)
        await ctx.send(f"✅ User cooldown set to {USER_COOLDOWN}s")

    @humanizer.command(name="memtoggle")
    @commands.is_owner()
    async def humanizer_memtoggle(self, ctx: commands.Context):
        global USE_MEMORY
        USE_MEMORY = not USE_MEMORY
        await ctx.send(f"🧠 Memory mode = {USE_MEMORY}")


# ---------------- Cog Setup ----------------

async def setup(bot: commands.Bot):
    await bot.add_cog(Humanizer(bot))
    print("[HUMANIZER] Humanizer cog loaded.")
