print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys

TOKEN = os.getenv("DISCORD_TOKEN")

# 💡 トークンが空の場合に早期にログへ警告を出す
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

# 💡 JSONファイルの保存先パス（Volume対応）
if os.path.exists("/app/data"):
    JSON_FILE = "/app/data/allowed_users.json"
else:
    # 念のため、ローカル環境やVolume未作成時の対策
    JSON_FILE = "allowed_users.json"

# 💡 JSONからすべてのデータを読み込む関数（エラーハンドリング強化）
def load_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ JSON読み込みエラー: {e}")
            return {}
    return {}

# 💡 JSONにすべてのデータを保存する関数（フォルダ自動作成＆エラーハンドリング強化）
def save_data(data):
    try:
        # 保存先フォルダ（/app/data など）が存在しない場合は自動作成
        dir_name = os.path.dirname(JSON_FILE)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            print(f"📁 保存先フォルダを作成しました: {dir_name}")

        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ JSON保存エラー (権限やパスの問題): {e}")

# サーバーごとの初期設定テンプレートを作成するヘルパー関数
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

# 認証ボタンを処理するクラス
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

# 再起動の確認プロンプトを表示するクラス
class RestartConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="はい (再起動)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
            return
            
        await interaction.response.send_message("ボットを終了します。Railwayによる自動再起動をお待ちください...", ephemeral=True)
        await bot.close()
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
    guild_config["announce_role"] = None
    save_data(all_data)
    await interaction.response.send_message("お知らせ設定（チャンネル・ロール）をリセットしました。", ephemeral=True)

@bot.tree.command(name="send_announcement", description="設定されたチャンネルにロールメンション付きでお知らせを送信します")
@discord.app_commands.default_permissions(administrator=True)
async def send_announcement(interaction: discord.Interaction, message: str):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    channel_id = guild_config.get("announce_channel")
    role_id = guild_config.get("announce_role")
    if not channel_id or not role_id:
        await interaction.response.send_message("お知らせチャンネル、またはロールが設定されていません。先に `/set_announcement` を実行してください。", ephemeral=True)
        return
    target_channel = bot.get_channel(channel_id)
    target_role = interaction.guild.get_role(role_id)
    if target_channel and target_role:
        await target_channel.send(f"{target_role.mention}\n\n{message}")
        await interaction.response.send_message("お知らせを送信しました！", ephemeral=True)
    else:
        await interaction.response.send_message("チャンネルまたはロールが見つかりませんでした。再設定してください。", ephemeral=True)

@bot.tree.command(name="allow_user", description="コマンドの使用を許可するユーザーを追加します")
@discord.app_commands.default_permissions(administrator=True)
async def allow_user(interaction: discord.Interaction, user: discord.User):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    if user.id not in guild_config["allowed_users"]:
        guild_config["allowed_users"].append(user.id)
    save_data(all_data)
    await interaction.response.send_message(f"{user.mention} を許可リストに追加しました。", ephemeral=True)

@bot.tree.command(name="deny_user", description="コマンドの使用許可リストからユーザーを削除します")
@discord.app_commands.default_permissions(administrator=True)
async def deny_user(interaction: discord.Interaction, user: discord.User):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    if user.id in guild_config["allowed_users"]:
        guild_config["allowed_users"].remove(user.id)
        save_data(all_data)
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

@bot.tree.command(name="set_verify_role", description="認証ボタンを押したときに付与するロールを設定します")
@discord.app_commands.default_permissions(administrator=True)
async def set_verify_role(interaction: discord.Interaction, role: discord.Role):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["verify_role"] = role.id
    save_data(all_data)
    await interaction.response.send_message(f"認証用ロールを {role.mention} に設定しました！", ephemeral=True)

@bot.tree.command(name="set_verify_channel", description="認証パネル（ボタン）を送信するチャンネルを設定します")
@discord.app_commands.default_permissions(administrator=True)
async def set_verify_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["verify_channel"] = channel.id
    save_data(all_data)
    await interaction.response.send_message(f"認証チャンネルを {channel.mention} に設定しました！", ephemeral=True)

@bot.tree.command(name="send_verify_button", description="設定されたチャンネルに認証ボタン付きのパネルメッセージを送信します")
@discord.app_commands.default_permissions(administrator=True)
async def send_verify_button(interaction: discord.Interaction, title: str = "サーバー認証", description: str = "下のボタンを押すと認証が完了し、全てのチャンネルが閲覧可能になります。"):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    channel_id = guild_config.get("verify_channel")
    if not channel_id:
        await interaction.response.send_message("認証チャンネルが設定されていません。先に `/set_verify_channel` を実行してください。", ephemeral=True)
        return
        
    target_channel = bot.get_channel(channel_id)
    if not target_channel:
        await interaction.response.send_message("設定されたチャンネルが見つかりませんでした。再設定してください。", ephemeral=True)
        return

    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    view = VerifyButtonView()
    
    await target_channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"{target_channel.mention} に認証ボタンを設置しました！", ephemeral=True)

# 💡 bot.runをtry-exceptで囲み、Discordへの接続エラーを明確にキャッチする
try:
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("❌ エラー: Discordトークンが無効です。Developer Portalのトークンと一致しているか確認してください。")
except Exception as e:
    print(f"❌ ボット起動中に予期せぬエラーが発生しました: {e}")