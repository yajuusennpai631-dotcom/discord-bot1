import os
import discord
from discord.ext import commands
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1488795327069945970

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
guild = discord.Object(id=GUILD_ID)

@bot.tree.command(
    name="hello",
    description="あいさつするコマンド",
    guild=guild
)
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message("おはよう")

async def main():
    async with bot:
        await bot.tree.sync(guild=guild)
        print("コマンド同期完了！")
        await bot.start(TOKEN)

asyncio.run(main())