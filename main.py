print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json

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

# 💡 JSONからすべてのデータ（ユーザーリストとチャンネル設定）を読み込む関数
def load_data():
    default_data = {"allowed_users": [], "from_channel": None, "to_channel": None}
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"JSON読み込みエラー: {e}")
            return default_data
    return default_data

# 💡 JSONにすべてのデータを保存する関数
def save_data():
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            data = {
                "allowed_users": list(allowed_users),
                "from_channel": forward_config["from_channel"],
                "to_channel": forward_config["to_channel"]
            }
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"JSON保存エラー: {e}")

# 💡 起動時にJSONからデータを読み込んでセットアップ
saved_data = load_data()
allowed_users = set(saved_data.get("allowed_users", []))
forward_config = {
    "from_channel": saved_data.get("from_channel"),
    "to_channel": saved_data.get("to_channel")
}

@bot.event
async def on_ready():
    synced = await bot.tree.sync(guild=guild)
    print(f"{len(synced)} commands synced")
    print(f"Logged in as {bot.user}")

# 💡 メッセージが送信されたときに自動で実行されるイベント
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # 設定された転送元・転送先チャンネルのIDがあるか確認
    from_id = forward_config["from_channel"]
    to_id = forward_config["to_channel"]

    # 1. 設定された「転送元チャンネル」での発言かどうかチェック
    # 2. 発言したユーザーが「管理者（Administrator）」かどうかチェック
    if from_id and to_id and message.channel.id == from_id and message.author.guild_permissions.administrator:
        to_channel = bot.get_channel(to_id)
        if to_channel:
            await to_channel.send(message.content)

    await bot.process_commands(message)

# 💡 転送元のチャンネルと転送先のチャンネルをコマンドで指定する（管理者のみ）
@bot.tree.command(
    name="set_forward",
    description="アドミンのメッセージ転送元と転送先のチャンネルを設定します",
    guild=guild
)
async def set_forward(
    interaction: discord.Interaction, 
    from_channel: discord.TextChannel, 
    to_channel: discord.TextChannel
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    # 設定を更新してJSONに保存
    forward_config["from_channel"] = from_channel.id
    forward_config["to_channel"] = to_channel.id
    save_data()
    
    await interaction.response.send_message(
        f"転送設定を完了しました！\n"
        f"【転送元】{from_channel.mention}\n"
        f"【転送先】{to_channel.mention}", 
        ephemeral=True
    )

@bot.tree.command(
    name="hello",
    description="あいさつするコマンド",
    guild=guild
)
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"{interaction.user.mention} さん、おはよう")

@bot.tree.command(
    name="say",
    description="ボットに匿名で発言させます",
    guild=guild
)
async def say(interaction: discord.Interaction, message: str):
    if interaction.user.id not in allowed_users and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return

    await interaction.channel.send(message)
    await interaction.response.send_message("メッセージを匿名で送信しました。", ephemeral=True)

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
    save_data()  # 共通の保存関数に変更
    await interaction.response.send_message(f"{user.mention} を許可リストに追加しました。", ephemeral=True)

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
        save_data()  # 共通の保存関数に変更
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

bot.run(TOKEN)