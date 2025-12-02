import discord
from discord.ext import commands
import config
from database import init_db
from logger import logger
import asyncio
import os
import sys
import traceback

# ==========================================================
# Discord Intents Setup
# ==========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

COMMAND_PREFIX = "!"
COG_FOLDER = "cogs"

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# ==========================================================
# Helper: dynamically load all cogs from /cogs folder
# ==========================================================
async def load_all_cogs():
    loaded = 0
    failed = 0

    for file in os.listdir(COG_FOLDER):
        if file.endswith(".py"):
            ext = f"{COG_FOLDER}.{file[:-3]}"
            try:
                # load_extension on discord.py v2+ expects a module that exports async setup
                await bot.load_extension(ext)
                logger.info(f"[COG LOADED] {ext}")
                loaded += 1
            except Exception as e:
                logger.error(f"[COG FAILED] {ext} - {e}")
                failed += 1
    logger.info(f"Cogs loaded: {loaded}, Failed: {failed}")

# ==========================================================
# BOT READY EVENT
# ==========================================================
@bot.event
async def on_ready():
    logger.info(f"Bot is online as {bot.user}")
    # Ensure database structures are present (your database.init_db should create required tables)
    try:
        await init_db()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"init_db() failed: {e}")

    # Load all cogs
    await load_all_cogs()

    # Change bot presence
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game("Realm Royz Simulation RPG")
    )

    logger.info("Bot is fully initialized and running.")

# ==========================================================
# Unified on_message handler
#
# Central place to award XP and still allow commands to work.
# It looks for a loaded cog called "LevelCog" and calls its
# add_xp method. This prevents stray on_message definitions
# from stopping commands from running.
# ==========================================================
@bot.event
async def on_message(message):
    # ignore bots
    if message.author.bot:
        return

    # award xp only in guilds (not DMs)
    if message.guild:
        # try to get the LevelCog; if loaded, call its add_xp
        level_cog = bot.get_cog("LevelCog")
        if level_cog:
            try:
                # add a small xp amount; the cog decides the actual behavior and rate-limits
                await level_cog.add_xp(message.guild.id, message.author.id, 5, message=message)
            except Exception as e:
                # don't crash the bot if XP awarding fails
                logger.error(f"Error while awarding xp: {e}")

    # always process commands (important!)
    try:
        await bot.process_commands(message)
    except Exception as e:
        # If a command raises, let on_command_error handle it
        logger.error(f"process_commands error: {e}")

# ==========================================================
# Error Logging
# ==========================================================
@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return await ctx.send("❌ Unknown command.")

    elif isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ Missing arguments.")

    elif isinstance(error, commands.MissingPermissions):
        return await ctx.send("❌ You don't have permission.")

    else:
        # full traceback printed
        logger.error("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        try:
            await ctx.send("⚠️ An unexpected error happened.")
        except Exception:
            # channel may not allow sending
            pass

    logger.error(f"Command error by {getattr(ctx,'author', 'unknown')} in {getattr(ctx,'channel', 'unknown')}: {error}")

# ==========================================================
# Command Logging
# ==========================================================
@bot.event
async def on_command(ctx):
    logger.info(
        f"[COMMAND] {ctx.command} used by {ctx.author} in "
        f"{ctx.guild}/{ctx.channel}"
    )

# ==========================================================
# Graceful Shutdown Handler
# ==========================================================
async def shutdown():
    logger.warning("Bot shutting down gracefully...")
    await bot.close()

# ==========================================================
# Reload All Cogs Command (Owner only)
# ==========================================================
@bot.command()
@commands.is_owner()
async def reload(ctx):
    """Hot reload all cogs without restarting bot"""
    reloaded = 0
    failed = []
    for ext in list(bot.extensions.keys()):
        try:
            await bot.unload_extension(ext)
            await bot.load_extension(ext)
            reloaded += 1
        except Exception as e:
            failed.append((ext, str(e)))

    msg = f"🔄 Reloaded {reloaded} cogs."
    if failed:
        msg += "\n❌ Failed to reload:\n" + "\n".join(f"- {ext}: {err}" for ext, err in failed)
    await ctx.send(msg)
    logger.info("All cogs hot reloaded (attempted).")

# ==========================================================
# MAIN RUNNER
# ==========================================================
async def main():
    logger.info("Launching bot...")

    try:
        await bot.start(config.TOKEN)
    except KeyboardInterrupt:
        await shutdown()
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
