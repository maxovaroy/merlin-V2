import discord
from discord.ext import commands
import config
from database import init_db
from logger import logger
import asyncio

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Bot ready event
@bot.event
async def on_ready():
    await init_db()
    logger.info(f"Bot is online as {bot.user}!")
    # Load all cogs
    try:
        await bot.load_extension("cogs.profile")
        logger.info("Cog 'profile' loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load cog 'profile': {e}")

# Log command usage
@bot.event
async def on_command(ctx):
    logger.info(f"Command used: {ctx.command} by {ctx.author} in {ctx.guild}/{ctx.channel}")

# Log command errors
@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Error in command {ctx.command} by {ctx.author}: {error}")

# Main runner
async def main():
    try:
        logger.info("Starting bot...")
        await bot.start(config.TOKEN)
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")

if __name__ == "__main__":
    asyncio.run(main())
