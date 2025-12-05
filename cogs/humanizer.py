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
}

FILLERS = ["ngl", "lol", "idk", "fr", "no cap", "ong", "btw", "lmao", "hmmm"]

# Tone style presets
TONE_MOOD = {
    "friendly": {"prefix": "", "suffix": " 😄"},
    "neutral": {"prefix": "", "suffix": ""},
    "chaotic": {"prefix": "yo ", "suffix": " 💀"},
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
            return "chaotic"
        return "neutral"

    async def _generate_reply(self, msg):
        content = msg.content.strip()

        reply = self._apply_slang(content)

        if any(greet in content.lower() for greet in ["hi", "hello", "hey"]):
            return f"hey {msg.author.display_name}"

        if content.endswith("?"):
            return random.choice(["hmm good q 🤔", "idk fr", "lemme think abt that"])

        # Add typo/filler randomness
        if random.random() < 0.4:
            reply = self._typoify(reply)

        # Refer to past messages randomly
        if USE_MEMORY and message.author.id in self._memory and random.random() < 0.3:
            past = random.choice(self._memory[message.author.id])
            reply += f" (btw u said: '{past[:30]}...')"

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
    bot.logger.info("[HUMANIZER] Humanizer cog loaded.")
