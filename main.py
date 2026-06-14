print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json  # 💡 JSONを扱うためのライブラリを追加

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1488795327069945970

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

guild = discord.Object(id=GUILD_ID)

# 💡 JSONファイルの保存先パス
JSON_FILE = "allowed_users.json"

# 💡 JSONからユーザーIDを読み込む関数
def load_allowed_users():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data)  # リストをセットに変換して返す
        except Exception as e:
            print(f"JSON読み込みエラー: {e}")
            return set()
    return set()

# 💡 JSONにユーザーIDを保存する関数
def save_allowed_users():
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            # セットのままだとJSONに保存できないのでリストに変換
            json.dump(list(allowed_users), f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"JSON保存エラー: {e}")

# 💡 起動時にJSONからデータを読み込む
allowed_users = load_allowed_users()

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
    save_allowed_users()  # 💡 追加したらJSONに保存
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
        save_allowed_users()  # 💡 削除したらJSONに保存
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

bot.run(TOKEN)