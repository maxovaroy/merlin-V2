# cogs/humanizer.py

"""
Humanizer Cog — “Real-Text Bot” for Realm Royz
-------------------------------------------------
Purpose:
- Makes the bot reply to user messages in a more human-like chat style,
  using slang/shortforms, casual tone, contextual memory, and configurable personality.

Features:
- Toggleable human-style auto-replies
- Slang / shortform mapping (e.g. cuz → because, gn → goodnight, u → you, smol → small)
- Basic mood/personality influenced by user’s aura / level
- Reply probability / cooldown to avoid spam
- Simple short-term memory (last user message), extensible to longer history
- Admin commands to configure behavior per server or globally
- “Preview mode” to see how the bot would respond to a given input
- Safety: respects that other cogs/commands run first; ignores bot/commands
- Easy to extend: build conversation flows, empathy, reactions, jokes, etc.

Configure at the top section. Later sections are modular — you can enable/disable.

To Do (future):
- Per-user style preferences (formal / slangy / meme / chaotic)
- Language filter, blacklist, context memory DB
- Reaction to triggers, keyword-based replies, mini-games, “quote back” mode
"""

import asyncio
import random
import time
from typing import Optional, Dict

import discord
from discord.ext import commands

# --------------- CONFIG SECTION (Edit as you like) ---------------

# Enable / disable humanizer globally
ENABLE_HUMANIZER = True

# Probability that bot replies to a user message (0.0 — never, 1.0 — always)
REPLY_PROBABILITY = 0.12

# Minimum user level to get full “friendly bot” tone
MIN_LEVEL_FOR_FRIENDLY = 5

# Cooldown per user (seconds) — prevents bot replying too often to same user
USER_COOLDOWN = 60   # bot will reply max once per minute to same user

# Minimum message length (characters) before replying
MIN_MSG_LENGTH = 5

# Slang / shortform mapping — you can add more
SLANG_MAP = {
    " cuz ": " because ",
    "brb": "be right back",
    "u ": "you ",
    " ur ": " your ",
    "smol": "small",
    "gn": "goodnight",
    "idk": "I don't know",
    "lol": "haha",
    "omg": "oh my god",
    "wtf": "what the heck",
}

# Tone settings — how bot speaks depending on mood
# Example: neutral → casual, high_aura → supportive/friendly, low_aura → chaotic/sarcastic
TONE_MOOD = {
    "neutral": {
        "prefix": "",
        "suffix": "",
    },
    "friendly": {
        "prefix": "",
        "suffix": " 🙂",
    },
    "sarcastic": {
        "prefix": "",
        "suffix": " 😏",
    },
    "chaotic": {
        "prefix": "",
        "suffix": " 🤪",
    }
}

# Memory usage — whether bot should remember last message per user for context
USE_MEMORY = True

# --------------- End CONFIG ---------------

class Humanizer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # per-user cooldown tracker: user_id -> timestamp of last bot reply
        self._last_reply: Dict[int, float] = {}
        # simple memory store: user_id -> last message content
        self._memory: Dict[int, str] = {}
        self._lock = asyncio.Lock()

    # ---------------- Utility Methods ----------------

    def _should_reply(self, user: discord.Member) -> bool:
        if not ENABLE_HUMANIZER:
            return False
        now = time.time()
        last = self._last_reply.get(user.id, 0)
        if now - last < USER_COOLDOWN:
            return False
        return random.random() < REPLY_PROBABILITY

    def _apply_slang(self, text: str) -> str:
        lowered = text.lower()
        for slang, full in SLANG_MAP.items():
            if slang in lowered:
                # replace respecting case roughly (simple method)
                text = text.replace(slang, full)
        return text

    async def _get_user_stats(self, user: discord.Member):
        """
        Fetch user data: level, aura — to determine tone.
        Depends on your LevelCog + Aura cog existing.
        """
        level = 0
        aura = 0
        # Get level
        try:
            level_cog = self.bot.get_cog("LevelCog")
            if level_cog:
                xp, level = await level_cog.get_user_level_data(user.guild.id, user.id)
        except Exception:
            pass

        # Get aura
        try:
            aura_cog = self.bot.get_cog("Aura")
            if aura_cog:
                row = await aura_cog._get_user_row(str(user.id))
                aura = int(row[4]) if row and len(row) >= 5 else 0
        except Exception:
            pass

        return level, aura

    def _choose_tone(self, level: int, aura: int) -> str:
        """
        Picks a tone string based on level/aura.
        You can refine logic as you like.
        """
        if aura >= 1000 or level >= MIN_LEVEL_FOR_FRIENDLY:
            return "friendly"
        elif aura < 100 and level < 3:
            return "chaotic"
        else:
            return "neutral"

    async def _generate_reply(self, msg: discord.Message) -> Optional[str]:
        """
        Basic reply generator: uses simple transformations.
        Override / expand this for smarter behavior.
        """
        content = msg.content.strip()
        if len(content) < MIN_MSG_LENGTH:
            return None

        # Simple echo-based reply: send back with shortform translation
        reply = content

        # apply slang translation
        reply = self._apply_slang(reply)

        # maybe add a short remark or tweak
        # e.g. if user says hi/hello → reply hello
        lowered = reply.lower()
        if any(greet in lowered for greet in ["hello", "hi", "hey"]):
            reply = f"Hey {msg.author.display_name}!"
        elif lowered.endswith("?"):
            reply = "Hmm, good question... 🤔"
        else:
            # randomly chop off some words / add filler
            words = reply.split()
            if len(words) > 5 and random.random() < 0.4:
                reply = " ".join(words[:-1])  # drop last word
            # add casual filler
            if random.random() < 0.3:
                reply += random.choice(["", " lol", " haha", " 🙂", " 🤪"])

        return reply

    # ---------------- Bot Event Listeners ----------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Wait until other cogs process first
        await self.bot.process_commands(message)

        # Bot should not reply to itself or other bots
        if message.author.bot:
            return
        # Ignore commands (start with prefix)
        if message.content.startswith(self.bot.command_prefix):
            return

        # Decide if bot should reply
        if not self._should_reply(message.author):
            return

        # Lock to avoid concurrency issues
        async with self._lock:
            level, aura = await self._get_user_stats(message.author)
            tone = self._choose_tone(level, aura)

            reply = await self._generate_reply(message)
            if not reply:
                return

            # apply tone
            style = TONE_MOOD.get(tone, TONE_MOOD["neutral"])
            final_msg = f"{style['prefix']}{reply}{style['suffix']}"

            try:
                await message.channel.typing()
                # optional delay to simulate typing
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await message.reply(final_msg, mention_author=False)
                self._last_reply[message.author.id] = time.time()
            except Exception:
                pass

            # Optionally add XP for talking with bot (uncomment to enable)
            # try:
            #     level_cog = self.bot.get_cog("LevelCog")
            #     if level_cog:
            #         await level_cog.force_add_xp(message.guild.id, message.author.id, XP_PER_MESSAGE // 2)
            # except:
            #     pass

            # Save memory if enabled
            if USE_MEMORY:
                self._memory[message.author.id] = message.content.strip()

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
        tone = self._choose_tone(level, aura)
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
