print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ エラー: 環境変数 'DISCORD_TOKEN' が設定されていないか、読み込めていません！")
    sys.exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

if os.path.exists("/app/data"):
    JSON_FILE = "/app/data/allowed_users.json"
else:
    JSON_FILE = "allowed_users.json"

def load_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ JSON読み込みエラー: {e}")
            return {}
    return {}

def save_data(data):
    try:
        dir_name = os.path.dirname(JSON_FILE)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            print(f"📁 保存先フォルダを作成しました: {dir_name}")

        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ JSON保存エラー (権限やパスの問題): {e}")

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
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commands synced (Global)")
    except Exception as e:
        print(f"❌ コマンド同期エラー: {e}")
    print(f"🤖 Logged in as {bot.user}")

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

class RestartConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="はい (再起動)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
            return
            
        await interaction.response.send_message("ボットを安全に終了します。再起動をお待ちください...", ephemeral=True)
        await bot.close()
        await asyncio.sleep(1)
        sys.exit(0)

    @discord.ui.button(label="いいえ (キャンセル)", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("再起動をキャンセルしました。", ephemeral=True)
        self.stop()


# ==================== コマンドの定義 ====================

@bot.tree.command(name="hello", description="あいさつするコマンド")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"{interaction.user.mention} さん、おはよう")

@bot.tree.command(name="list_users", description="アドミンコマンドの使用を許可されているユーザーの一覧を表示します")
async def list_users(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    allowed_users = guild_config.get("allowed_users", [])
    if not allowed_users:
        await interaction.response.send_message("現在、アドミンコマンドの使用許可リストに登録されているユーザーはいません。\n※サーバー管理者は登録なしで使えます。", ephemeral=True)
        return
    user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
    await interaction.response.send_message("【アドミンコマンド使用許可ユーザー一覧】\n" + "\n".join(user_mentions), ephemeral=True)


# 🔴 管理系コマンド（管理者のみ表示）

@bot.tree.command(name="say", description="ボットに匿名で発言させます")
@discord.app_commands.default_permissions(administrator=True)
async def say(interaction: discord.Interaction, message: str):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    allowed_users = guild_config.get("allowed_users", [])
    if interaction.user.id not in allowed_users and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
        return
    await interaction.channel.send(message)
    await interaction.response.send_message("メッセージを匿名で送信しました。", ephemeral=True)

@bot.tree.command(name="restart", description="ボットを再起動します（確認プロンプトを表示）")
@discord.app_commands.default_permissions(administrator=True)
async def restart(interaction: discord.Interaction):
    view = RestartConfirmView()
    await interaction.response.send_message("本当にボットを再起動しますか？", view=view, ephemeral=True)

@bot.tree.command(name="set_forward", description="アドミンのメッセージ転送元と転送先のチャンネルを設定します")
@discord.app_commands.default_permissions(administrator=True)
async def set_forward(interaction: discord.Interaction, from_channel: discord.TextChannel, to_channel: discord.TextChannel):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["from_channel"] = from_channel.id
    guild_config["to_channel"] = to_channel.id
    save_data(all_data)
    await interaction.response.send_message(f"転送設定を完了しました！\n【転送元】{from_channel.mention}\n【転送先】{to_channel.mention}", ephemeral=True)

@bot.tree.command(name="reset_forward", description="チャンネルの転送設定を解除します")
@discord.app_commands.default_permissions(administrator=True)
async def reset_forward(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["from_channel"] = None
    guild_config["to_channel"] = None
    save_data(all_data)
    await interaction.response.send_message("チャンネルの転送設定をリセットしました。", ephemeral=True)

@bot.tree.command(name="set_announcement", description="お知らせチャンネルと通知するロールを設定します")
@discord.app_commands.default_permissions(administrator=True)
async def set_announcement(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["announce_channel"] = channel.id
    guild_config["announce_role"] = role.id
    save_data(all_data)
    await interaction.response.send_message(f"お知らせ設定を完了しました！\n【送信先】{channel.mention}\n【対象ロール】{role.mention}", ephemeral=True)

@bot.tree.command(name="reset_announcement", description="お知らせチャンネルとロールの設定を解除します")
@discord.app_commands.default_permissions(administrator=True)
async def reset_announcement(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["announce_channel"] = None
    guild_config