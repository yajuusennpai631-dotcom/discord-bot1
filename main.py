import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1488795327069945970

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

guild = discord.Object(id=GUILD_ID)

@bot.event
async def on_ready():
    synced = await bot.tree.sync(guild=guild)
    print(f"{len(synced)}個のコマンドを同期しました")
    print(f"{bot.user} is now online!")

@bot.tree.command(
    name="hello",
    description="あいさつするコマンド",
    guild=guild
)
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message("おはよう")

bot.run(TOKEN)
