print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys  # 💡 システム終了を呼び出すために追加

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

# 💡 JSONからすべてのデータを読み込む関数
def load_data():
    default_data = {
        "allowed_users": [], 
        "from_channel": None, 
        "to_channel": None,
        "announce_channel": None,
        "announce_role": None
    }
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
                "to_channel": forward_config["to_channel"],
                "announce_channel": announce_config["announce_channel"],
                "announce_role": announce_config["announce_role"]
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
announce_config = {
    "announce_channel": saved_data.get("announce_channel"),
    "announce_role": saved_data.get("announce_role")
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

    from_id = forward_config["from_channel"]
    to_id = forward_config["to_channel"]

    if from_id and to_id and message.channel.id == from_id and message.author.guild_permissions.administrator:
        to_channel = bot.get_channel(to_id)
        if to_channel:
            await to_channel.send(message.content)

    await bot.process_commands(message)

# 💡 【新機能】再起動の確認プロンプト（ボタン）を表示するクラス
class RestartConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60) # 60秒間操作がなければ無効化

    @discord.ui.button(label="はい (再起動)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
            return
            
        await interaction.response.send_message("ボットを終了します。Railwayによる自動再起動をお待ちください...", ephemeral=True)
        # ボットをログアウトさせてプログラムを終了する
        await bot.close()
        sys.exit(0)

    @discord.ui.button(label="いいえ (キャンセル)", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("再起動をキャンセルしました。", ephemeral=True)
        self.stop()

# 💡 【新機能】再起動コマンド（管理者のみ）
@bot.tree.command(
    name="restart",
    description="ボットを再起動します（確認プロンプトを表示）",
    guild=guild
)
async def restart(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    # 確認ボタン付きのプロンプトを送信
    view = RestartConfirmView()
    await interaction.response.send_message("本当にボットを再起動しますか？", view=view, ephemeral=True)

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
        
    forward_config["from_channel"] = from_channel.id
    forward_config["to_channel"] = to_channel.id
    save_data()
    
    await interaction.response.send_message(
        f"転送設定を完了しました！\n"
        f"【転送元】{from_channel.mention}\n"
        f"【転送先】{to_channel.mention}", 
        ephemeral=True
    )

# 💡 転送設定を解除（リセット）するコマンド（管理者のみ）
@bot.tree.command(
    name="reset_forward",
    description="チャンネルの転送設定を解除します",
    guild=guild
)
async def reset_forward(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    forward_config["from_channel"] = None
    forward_config["to_channel"] = None
    save_data()
    
    await interaction.response.send_message("チャンネルの転送設定をリセットしました。", ephemeral=True)

# 💡 お知らせチャンネルと通知対象のロールを設定する（管理者のみ）
@bot.tree.command(
    name="set_announcement",
    description="お知らせチャンネルと通知するロールを設定します",
    guild=guild
)
async def set_announcement(
    interaction: discord.Interaction, 
    channel: discord.TextChannel, 
    role: discord.Role
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    announce_config["announce_channel"] = channel.id
    announce_config["announce_role"] = role.id
    save_data()
    
    await interaction.response.send_message(
        f"お知らせ設定を完了しました！\n"
        f"【送信先】{channel.mention}\n"
        f"【対象ロール】{role.mention}", 
        ephemeral=True
    )

# 💡 設定されたチャンネルにロールメンション付きでお知らせを送信する（管理者のみ）
@bot.tree.command(
    name="send_announcement",
    description="設定されたチャンネルにロールメンション付きでお知らせを送信します",
    guild=guild
)
async def send_announcement(interaction: discord.Interaction, message: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("この管理コマンドはサーバーの管理者のみ実行できます。", ephemeral=True)
        return
        
    channel_id = announce_config["announce_channel"]
    role_id = announce_config["announce_role"]
    
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
    save_data()
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
        save_data()
        await interaction.response.send_message(f"{user.mention} を許可リストから削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} は元々許可リストに登録されていません。", ephemeral=True)

bot.run(TOKEN)