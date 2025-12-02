import discord
from discord.ext import commands
import config

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print("🤖 Merlin is now online!")

async def load():
    await bot.load_extension("cogs.profile")

import asyncio
asyncio.run(load())

bot.run(config.TOKEN)
