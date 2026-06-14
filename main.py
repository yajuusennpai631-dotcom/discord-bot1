print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1488795327069945970

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

guild = discord.Object(id=GUILD_ID)

@bot.event
async def on_ready():
    synced = await bot.tree.sync(guild=guild)
    print(f"{len(synced)} commands synced")
    print(f"Logged in as {bot.user}")

@bot.tree.command(
    name="hello",
    description="あいさつするコマンド",
    guild=guild
)
async def hello(interaction: discord.Interaction):
    # 原型を崩さず、メッセージ部分だけをユーザーのメンション付きに変更しました
    await interaction.response.send_message(f"{interaction.user.mention} さん、おはよう")

bot.run(TOKEN)