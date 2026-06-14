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

# 💡 コマンドの使用を許可されたユーザーのIDを管理するリスト（初期状態では空）
allowed_users = set()

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

@bot.tree.command(
    name="say",
    description="ボットに匿名で発言させます",
    guild=guild
)
async def say(interaction: discord.Interaction, message: str):
    # 💡 実行したユーザーが許可リスト、またはサーバーの管理者(Administrator)であるかチェック
    if interaction.user.id not in allowed_users and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return

    # 1. コマンドを入力したチャンネルに、ボットとしてメッセージを送信
    await interaction.channel.send(message)
    # 2. 実行した本人にだけ「送信しました」と隠しメッセージを表示（これで匿名性が保たれます）
    await interaction.response.send_message("メッセージを匿名で送信しました。", ephemeral=True)

# 💡 許可されたユーザーを追加するコマンド（サーバーの管理者のみ実行可能）
@bot.tree.command(
    name="allow_user",
    description="コマンドの使用を許可するユーザーを追加します",
    guild=guild
)
async def allow_user(interaction: discord.Interaction, user: discord.User):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    allowed_users.add(user.id)
    await interaction.response.send_message(f"{user.mention} を許可リストに追加しました。", ephemeral=True)

# 💡 許可されたユーザーを削除するコマンド（サーバーの管理者のみ実行可能）
@bot.tree.command(
    name="deny_user",
    description="コマンドの使用許可リストからユーザーを削除します",
    guild=guild
)
async def deny_user(interaction: discord.Interaction, user: discord.User):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    if user.id in allowed_users:
        allowed_users.remove(user.id)
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

bot.run(TOKEN)