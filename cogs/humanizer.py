# cogs/humanizer.py

"""
HUMANIZER — Human-like chat generator for Realm Royz
----------------------------------------------------
Responds casually like a real person using slang, fillers & tone.
Works **only in the configured channel** and ignores all others.

You can customize the tone, slang style, probabilities & memory easily.
"""

import asyncio
import random
import time
from typing import Dict, Optional

import discord
from discord.ext import commands

# ========================= CONFIG =========================

ENABLE_HUMANIZER = True

# "Humanizer will only reply in THIS channel"
HUMANIZER_CHANNEL = 1446421555965067354     # <--- your channel

# Probability of replying to a message
REPLY_PROBABILITY = 1.0       # 1.0 = always reply, 0.3 = 30% chance

# Cooldown per user between replies (seconds)
USER_COOLDOWN = 5

# Use short memory to remember last message per user
USE_MEMORY = True
MIN_MSG_LENGTH = 2

# Slang mapping (more natural)
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
    "tf": "tf",   # keep as-is  
    "n u": "and u",  
    "u": "u",  
    "ur": "ur",  
    "k": "k",  
    "yea": "yea",  
    "lol": "lol"

}

FILLERS = ["ngl", "lol", "idk", "fr", "no cap", "ong", "btw", "lmao", "hmmm"]

# === new style phrases for Sarcastic + Gen-Z mix ===
SARCASM_PHRASES = [
    "wow big brain move", "oh absolutely (not)", "legend behaviour", 
    "ok genius", "amaze", "peak performance ngl"
]

GENZ_SHORTS = [
    "ong", "fr", "no cap", "lowkey", "highkey", "bet", "say less", "slaps"
]

GENZ_RESPONSES_SHORT = [
    "fr", "bet", "say less", "okok", "hmm", "ight", "go on", "mhm"
]

GENZ_QUESTION_RESPONSES = [
    "good q ngl", "lemme think fr", "idk fr", "maybe? idk", "sus"
]

# Tone style presets
TONE_MOOD = {
    "friendly": {"prefix": "", "suffix": "<:Eminem:1308041429339209778>"},
    "neutral": {"prefix": "", "suffix": ""},
    "chaotic": {"prefix": "yo ", "suffix": "<:Hacker:1308134036937375794>"},
}

MIN_LEVEL_FOR_FRIENDLY = 5  # Change as you like

# ==========================================================


class Humanizer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_reply: Dict[int, float] = {}
        self._memory: Dict[int, list] = {}  # store last 10 messages per user
        MAX_MEMORY = 10  # maximum messages to remember
        self.lock = asyncio.Lock()


    def _typoify(self, text: str) -> str:
        """Add human-like typos, letter repetition, and fillers"""
        new_text = ""
        for ch in text:
            if random.random() < 0.05:
                new_text += ch*random.randint(2,3)  # repeat letter randomly
            else:
                new_text += ch
        if random.random() < 0.2:
            new_text += " " + random.choice(FILLERS)
        return new_text


    # ---------------- Helper functions ----------------

    def _should_reply(self, user):
        now = time.time()
        last = self._last_reply.get(user.id, 0)
        if now - last < USER_COOLDOWN:
            return False
        return random.random() < REPLY_PROBABILITY

    def _apply_slang(self, text: str) -> str:
        for normal, slang in SLANG_MAP.items():
            text = text.replace(normal, slang).replace(normal.capitalize(), slang)
        return text

    async def _get_user_stats(self, user):
        """Level & aura influence tone (optional integration)"""
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
            return "chaotic"   # chaotic will lean sarcastic in replies via the lists above
        return "neutral"


    async def _generate_reply(self, msg):
        # Work on a cleaned lowercase copy for decisions, but keep original for replies
        raw = msg.content.strip()
        content = raw.lower()
    
        # ---- GREETINGS (use short gen-z replies) ----
        # Expanded greetings
        if any(w in content for w in ["hi", "hello", "hola", "wassup", "yo", "sup", "hey", "hui", "ola"]):
            greet = [
                "yo",
                "sup",
                "hola",
                "hey",
                "wassup",
                "hi?",
                "hello there",
                "yoo what's good",
                "sup chat",
                "hey hey"
            ]
            return random.choice(greet)
            
        # ---------------- Personal / Emotional Questions ----------------
        if any(word in content for word in ["lonely", "alone", "sad", "love u", "miss u", "think of me"]):
            emotional_replies = [
                "lonely? nah, i got u here chatting rn",
                "sometimes ig, but convos like this make it better ngl",
                "who knows, maybe a lil… but i manage 😅",
                "bruh i'm a bot, my only emotion is lag",
                "lonely? only when nobody tags me fr",
                "nope, i vibe. u lonely tho?"
            ]
            return random.choice(emotional_replies)

        # ---- VERY SHORT / FRAGMENTED MESSAGES ----
        # keep short, snappy, gen-z style or sarcastic micro-roasts
        if len(content.split()) <= 2:
            # small chance for sarcastic micro-roast if user is rude
            if any(w in content for w in ["wtf", "shut", "stfu", "fuck", "madafaka", "baka"]) and random.random() < 0.35:
                return random.choice(["ok calm down", "chill fr", random.choice(SARCASM_PHRASES)])
            # otherwise gen-z short reply
            return random.choice(GENZ_RESPONSES_SHORT + GENZ_SHORTS)
    
        # ---- QUESTIONS ----
        if content.endswith("?"):
            # mix gen-z and sarcastic answers for questions
            if random.random() < 0.35:
                return random.choice(GENZ_QUESTION_RESPONSES)
            else:
                return random.choice(["hmm good q", "idk bro", "lemme think fr"])
    
        # ---- Longer messages: apply slang and maybe memory ----
        reply = self._apply_slang(raw)  # preserve original casing for flavor
    
        # Light typo/filler occasionally (but less for sarcasm)
        if random.random() < 0.12:
            reply = self._typoify(reply)
    
        # Reference past messages only if meaningful (3+ words)
        if USE_MEMORY and msg.author.id in self._memory and random.random() < 0.25:
            meaningful = [m for m in self._memory[msg.author.id] if len(m.split()) > 3]
            if meaningful:
                past_msg = random.choice(meaningful)
                # Prefer a sarcastic callback sometimes
                if random.random() < 0.4:
                    reply += f" — lol remember when u said '{past_msg[:30]}...'?"
                else:
                    reply += f" — lowkey u said '{past_msg[:30]}...' before"
    
        # If the reply equals the message (avoid parroting), tweak it
        if reply.strip().lower() == content:
            # Add a sarcastic/gen-z tail
            tail = random.choice(SARCASM_PHRASES + GENZ_SHORTS)
            reply = f"{reply} {tail}"
    
        # Final small chance to add a micro-sarcastic opener (not always)
        if random.random() < 0.08:
            reply = random.choice(["ok real talk — ", "bruh — "]) + reply
    
        return reply


    # ---------------- Listener ----------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        if not ENABLE_HUMANIZER:
            return
        
        # Must be inside the dedicated channel
        if message.channel.id != HUMANIZER_CHANNEL:
            return
        
        if message.author.bot:
            return
        
        if message.content.startswith(self.bot.command_prefix):
            return

        if not self._should_reply(message.author):
            return

        async with self.lock:

            level, aura = await self._get_user_stats(message.author)
            tone = TONE_MOOD[self._tone(level, aura)]

            reply = await self._generate_reply(message)
            if not reply:
                return

            final = f"{tone['prefix']}{reply}{tone['suffix']}"

            async with message.channel.typing():
                await asyncio.sleep(random.uniform(0.4, 1.3))
                await message.reply(final, mention_author=False)
                self._last_reply[message.author.id] = time.time()

            # Save memory
            if USE_MEMORY:
                mem = self._memory.setdefault(message.author.id, [])
                mem.append(message.content)
                if len(mem) > MAX_MEMORY:
                    mem.pop(0)  # remove oldest message, keep only last 5

    # ---------------- Admin / Owner Commands ----------------

    @commands.group(name="humanizer", invoke_without_command=True)
    @commands.is_owner()
    async def humanizer(self, ctx: commands.Context):
        """Humanizer settings: show current config values."""
        desc = (
            f"**Humanizer Config**\n"
            f"Enabled: {ENABLE_HUMANIZER}\n"
            f"Reply Probability: {REPLY_PROBABILITY}\n"
            f"User Cooldown: {USER_COOLDOWN}s\n"
            f"Memory: {USE_MEMORY}\n"
            f"Min Msg Length: {MIN_MSG_LENGTH}\n"
        )
        await ctx.send(desc)

    @humanizer.command(name="setprob")
    @commands.is_owner()
    async def humanizer_setprob(self, ctx: commands.Context, prob: float):
        """Set new reply probability (0.0 – 1.0)"""
        global REPLY_PROBABILITY
        REPLY_PROBABILITY = max(0.0, min(1.0, prob))
        await ctx.send(f"✅ Reply probability set to {REPLY_PROBABILITY}")

    @humanizer.command(name="toggle")
    @commands.is_owner()
    async def humanizer_toggle(self, ctx: commands.Context):
        """Toggle humanizer on/off"""
        global ENABLE_HUMANIZER
        ENABLE_HUMANIZER = not ENABLE_HUMANIZER
        await ctx.send(f"✅ Humanizer enabled: {ENABLE_HUMANIZER}")

    @humanizer.command(name="preview")
    @commands.is_owner()
    async def humanizer_preview(self, ctx: commands.Context, *, text: str):
        """Show how bot would reply to given text"""
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
        """Set user cooldown (in seconds) between bot replies"""
        global USER_COOLDOWN
        USER_COOLDOWN = max(0, secs)
        await ctx.send(f"✅ User cooldown set to {USER_COOLDOWN}s")

    @humanizer.command(name="memtoggle")
    @commands.is_owner()
    async def humanizer_memtoggle(self, ctx: commands.Context):
        """Toggle short-term memory on/off"""
        global USE_MEMORY
        USE_MEMORY = not USE_MEMORY
        await ctx.send(f"✅ Memory usage: {USE_MEMORY}")

    # More commands can be added: tone adjust, slang list edit, etc.

async def setup(bot: commands.Bot):
    await bot.add_cog(Humanizer(bot))
    print("[HUMANIZER] Humanizer cog loaded.")
