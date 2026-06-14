print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# 💡 特定のサーバーへの固定を解除しました（色々なサーバーで動かすため）
# GUILD_ID や guild = discord.Object(...) のコードは削除しています

# 💡 JSONファイルの保存先パス
JSON_FILE = "allowed_users.json"

# 💡 JSONからすべてのデータを読み込む関数（サーバーIDごとの構造に対応）
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
            "announce_role": None
        }
    return all_data[guild_id_str]

@bot.event
async def on_ready():
    # 💡 guild=guild の指定を外すことで、ボットが入っているすべてのサーバーでコマンドが使えるようになります（グローバル同期）
    # ⚠️ 反映までに最長で1時間ほどかかる場合があります
    synced = await bot.tree.sync()
    print(f"{len(synced)} commands synced (Global)")
    print(f"Logged in as {bot.user}")

# 💡 メッセージが送信されたときに自動で実行されるイベント
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # 💡 発言があったサーバーのIDを取得して、そのサーバーの設定を読み込む
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

# 💡 再起動の確認プロンプト（ボタン）を表示するクラス
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

# 💡 再起動コマンド（管理者のみ）
@bot.tree.command(
    name="restart",
    description="ボットを再起動します（確認プロンプトを表示）"
)
async def restart(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    view = RestartConfirmView()
    await interaction.response.send_message("本当にボットを再起動しますか？", view=view, ephemeral=True)

# 💡 転送元のチャンネルと転送先のチャンネルをコマンドで指定する（管理者のみ）
@bot.tree.command(
    name="set_forward",
    description="アドミンのメッセージ転送元と転送先のチャンネルを設定します"
)
async def set_forward(
    interaction: discord.Interaction, 
    from_channel: discord.TextChannel, 
    to_channel: discord.TextChannel
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    guild_config["from_channel"] = from_channel.id
    guild_config["to_channel"] = to_channel.id
    save_data(all_data)
    
    await interaction.response.send_message(
        f"転送設定を完了しました！\n"
        f"【転送元】{from_channel.mention}\n"
        f"【転送先】{to_channel.mention}", 
        ephemeral=True
    )

# 💡 転送設定を解除（リセット）するコマンド（管理者のみ）
@bot.tree.command(
    name="reset_forward",
    description="チャンネルの転送設定を解除します"
)
async def reset_forward(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    guild_config["from_channel"] = None
    guild_config["to_channel"] = None
    save_data(all_data)
    
    await interaction.response.send_message("チャンネルの転送設定をリセットしました。", ephemeral=True)

# 💡 お知らせチャンネルと通知対象のロールを設定する（管理者のみ）
@bot.tree.command(
    name="set_announcement",
    description="お知らせチャンネルと通知するロールを設定します"
)
async def set_announcement(
    interaction: discord.Interaction, 
    channel: discord.TextChannel, 
    role: discord.Role
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    guild_config["announce_channel"] = channel.id
    guild_config["announce_role"] = role.id
    save_data(all_data)
    
    await interaction.response.send_message(
        f"お知らせ設定を完了しました！\n"
        f"【送信先】{channel.mention}\n"
        f"【対象ロール】{role.mention}", 
        ephemeral=True
    )

# 💡 設定されたチャンネルにロールメンション付きでお知らせを送信する（管理者のみ）
@bot.tree.command(
    name="send_announcement",
    description="設定されたチャンネルにロールメンション付きでお知らせを送信します"
)
async def send_announcement(interaction: discord.Interaction, message: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
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
        announcement_text = f"{target_role.mention}\n\n{message}"
        await target_channel.send(announcement_text)
        await interaction.response.send_message("お知らせを送信しました！", ephemeral=True)
    else:
        await interaction.response.send_message("チャンネルまたはロールが見つかりませんでした。再設定してください。", ephemeral=True)

@bot.tree.command(
    name="hello",
    description="あいさつするコマンド"
)
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"{interaction.user.mention} さん、おはよう")

@bot.tree.command(
    name="say",
    description="ボットに匿名で発言させます"
)
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

@bot.tree.command(
    name="allow_user",
    description="コマンドの使用を許可するユーザーを追加します"
)
async def allow_user(interaction: discord.Interaction, user: discord.User):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    # リスト形式で保存されているので、重複を避けて追加
    if user.id not in guild_config["allowed_users"]:
        guild_config["allowed_users"].append(user.id)
    save_data(all_data)
    
    await interaction.response.send_message(f"{user.mention} を許可リストに追加しました。", ephemeral=True)

@bot.tree.command(
    name="deny_user",
    description="コマンドの使用許可リストからユーザーを削除します"
)
async def deny_user(interaction: discord.Interaction, user: discord.User):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    
    if user.id in guild_config["allowed_users"]:
        guild_config["allowed_users"].remove(user.id)
        save_data(all_data)
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

@bot.tree.command(
    name="list_users",
    description="コマンドの使用を許可されているユーザーの一覧を表示します"
)
async def list_users(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    allowed_users = guild_config.get("allowed_users", [])
        
    if not allowed_users:
        await interaction.response.send_message("現在、許可リストに登録されているユーザーはいません。\n※管理者は登録なしで使えます。", ephemeral=True)
        return
        
    user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
    list_text = "【コマンド使用許可ユーザー一覧】\n" + "\n".join(user_mentions)
    
    await interaction.response.send_message(list_text, ephemeral=True)

bot.run(TOKEN)