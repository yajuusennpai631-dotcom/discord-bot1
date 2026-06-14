print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # ロール付与のためにメンバー管理のインテンツを有効化

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# 💡 JSONファイルの保存先パスを自動切り替え（Volume対応）
if os.path.exists("/app/data"):
    JSON_FILE = "/app/data/allowed_users.json"
else:
    JSON_FILE = "allowed_users.json"

# 💡 JSONからすべてのデータを読み込む関数
def load_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"JSON読み込みエラー: {e}")
            return {}
    return {}

# 💡 JSONにすべてのデータを保存する関数
def save_data(data):
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"JSON保存エラー: {e}")

# 💡 サーバーごとの初期設定テンプレートを作成するヘルパー関数
def get_guild_config(all_data, guild_id_str):
    if guild_id_str not in all_data:
        all_data[guild_id_str] = {
            "allowed_users": [],
            "from_channel": None,
            "to_channel": None,
            "announce_channel": None,
            "announce_role": None,
            "verify_channel": None,
            "verify_role": None
        }
    return all_data[guild_id_str]

# 💡 認証ボタンを処理するクラス
class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.success, custom_id="persistent_verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id_str = str(interaction.guild.id)
        all_data = load_data()
        guild_config = get_guild_config(all_data, guild_id_str)
        
        role_id = guild_config.get("verify_role")
        if not role_id:
            await interaction.response.send_message("サーバー側で認証用ロールが設定されていません。管理者に連絡してください。", ephemeral=True)
            return

        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("設定されている認証ロールが見つかりませんでした。管理者に連絡してください。", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("あなたはすでに認証されています！", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"認証に成功しました！ {role.mention} ロールを付与しました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("ボットの権限が足りないためロールを付与できませんでした。ボットの役職を付与したいロールより上に配置してください。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)

@bot.event
async def on_ready():
    bot.add_view(VerifyButtonView())
    synced = await bot.tree.sync()
    print(f"{len(synced)} commands synced (Global)")
    print(f"Logged in as {bot.user}")

# 💡 メッセージ送信時に実行されるイベント
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    guild_id_str = str(message.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)

    from_id = guild_config.get("from_channel")
    to_id = guild_config.get("to_channel")

    if from_id and to_id and message.channel.id == from_id and message.author.guild_permissions.administrator:
        to_channel = bot.get_channel(to_id)
        if to_channel:
            await to_channel.send(message.content)

    await bot.process_commands(message)

# 💡 再起動の確認プロンプトを表示するクラス
class RestartConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="はい (再起動)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction