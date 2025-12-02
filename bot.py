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
    await init_db()
    logger.info("Database initialized.")

    await load_all_cogs()

    # Change bot presence
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game("Realm Royz Simulation RPG")
    )

    logger.info("Bot is fully initialized and running.")


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
        await ctx.send("⚠️ An unexpected error happened.")
    
    logger.error(f"Command error by {ctx.author} in {ctx.channel}: {error}")


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
    for ext in list(bot.extensions.keys()):
        try:
            await bot.unload_extension(ext)
            await bot.load_extension(ext)
        except Exception as e:
            return await ctx.send(f"❌ Failed to reload: {ext}\n{e}")

    await ctx.send("🔄 All cogs reloaded successfully.")
    logger.info("All cogs hot reloaded.")


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
