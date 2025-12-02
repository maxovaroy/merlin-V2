import discord
from discord.ext import commands
import config
from database import db

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await init_db()
    print("🤖 Merlin is online!")

async def main():
    await db.connect()
    await bot.load_extension("cogs.profile")
    await bot.start(config.TOKEN)

import asyncio
asyncio.run(main())
