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

def create_user_list_embed(allowed_users):
    """許可リストのEmbedを生成する共通ヘルパー関数"""
    embed = discord.Embed(
        title="👥 アドミンコマンド使用許可ユーザー一覧", 
        description="現在、以下のユーザーに管理者用コマンドの使用権限が与えられています。\n※サーバー管理者は登録なしで最初からすべてのコマンドを使用できます。",
        color=discord.Color.dark_blue()
    )
    if not allowed_users:
        embed.add_field(name="【登録ユーザー】", value="現在、登録されているユーザーはいません。", inline=False)
        embed.set_footer(text="現在の登録者数: 0名")
    else:
        user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
        embed.add_field(name="【登録ユーザー】", value="\n".join(user_mentions), inline=False)
        embed.set_footer(text=f"現在の登録者数: {len(allowed_users)}名")
    return embed


class UserManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="👤 許可ユーザーを追加する...", custom_id="manage_add_user")
    async def add_user_callback(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
            return

        target_user = select.values[0]
        guild_id_str = str(interaction.guild.id)
        all_data = load_data()
        guild_config = get_guild_config(all_data, guild_id_str)

        if target_user.id not in guild_config["allowed_users"]:
            guild_config["allowed_users"].append(target_user.id)
            save_data(all_data)
            
            updated_embed = create_user_list_embed(guild_config["allowed_users"])
            await interaction.response.edit_message(embed=updated_embed, view=self)
            await interaction.followup.send(f"✅ {target_user.mention} を許可リストに追加しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {target_user.mention} は既に登録されています。", ephemeral=True)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="❌ 許可ユーザーを削除する...", custom_id="manage_remove_user")
    async def remove_user_callback(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("この操作は管理者のみ実行できます。", ephemeral=True)
            return

        target_user = select.values[0]
        guild_id_str = str(interaction.guild.id)
        all_data = load_data()
        guild_config = get_guild_config(all_data, guild_id_str)

        if target_user.id in guild_config["allowed_users"]:
            guild_config["allowed_users"].remove(target_user.id)
            save_data(all_data)
            
            updated_embed = create_user_list_embed(guild_config["allowed_users"])
            await interaction.response.edit_message(embed=updated_embed, view=self)
            await interaction.followup.send(f"❌ {target_user.mention} を許可リストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {target_user.mention} は元々登録されていません。", ephemeral=True)


class DynamicRoleView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        
        styles = [
            discord.ButtonStyle.primary,
            discord.ButtonStyle.success,
            discord.ButtonStyle.secondary,
            discord.ButtonStyle.danger
        ]
        
        for i, role in enumerate(roles):
            style = styles[i % len(styles)]
            button = discord.ui.Button(
                label=role.name, 
                style=style, 
                custom_id=f"dynamic_role_{role.id}"
            )
            button.callback = self.create_callback(role.id)
            self.add_item(button)

    def create_callback(self, role_id):
        async def button_callback(interaction: discord.Interaction):
            guild = interaction.guild
            role = guild.get_role(role_id)
            
            if not role:
                await interaction.response.send_message("このロールはサーバー上に存在しません。", ephemeral=True)
                return

            if role in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(role)
                    await interaction.response.send_message(f"❌ {role.name} ロールを外しました。", ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message("ボットの権限が足りないためロールを削除できません。役職の順位を確認してください。", ephemeral=True)
            else:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(f"✅ {role.name} ロールを付与しました！", ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message("ボットの権限が足りないためロールを付与できません。ボットの役職を対象のロールより上に配置してください。", ephemeral=True)
        return button_callback


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
    
    all_data = load_data()
    for guild_id_str, config in all_data.items():
        panel_roles = config.get("panel_roles", [])
        if panel_roles:
            guild = bot.get_guild(int(guild_id_str))
            if guild:
                roles = [guild.get_role(rid) for rid in panel_roles if guild.get_role(rid)]
                if roles:
                    bot.add_view(DynamicRoleView(roles))

    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)} commands synced (Global)")
    except Exception as e:
        print(f"❌ コマンド同期エラー: {e}")
    print(f"🤖 Logged in as {bot.user}")


# 💡 【重要修正】メッセージ転送のサーバー間混線を完全に防止しました
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # メッセージが送信された「現在のサーバー」のIDをもとに設定を取得
    guild_id_str = str(message.guild.id)
    all_data = load_data()
    
    # 該当サーバーの設定が存在する場合のみ処理する（他サーバーの設定との混線を防ぐ）
    if guild_id_str in all_data:
        guild_config = all_data[guild_id_str]
        from_id = guild_config.get("from_channel")
        to_id = guild_config.get("to_channel")

        # 送信されたチャンネルが、そのサーバーで設定された転送元と一致するかチェック
        if from_id and to_id and message.channel.id == from_id:
            # 転送できるのはサーバーの管理者（Administrator）のみ
            if message.author.guild_permissions.administrator:
                # 転送先チャンネルを「現在のサーバー内」から取得
                to_channel = message.guild.get_channel(to_id)
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

@bot.tree.command(name="list_users", description="使用許可リストの確認、および追加・削除を画面上で行います")
@discord.app_commands.default_permissions(administrator=True)
async def list_users(interaction: discord.Interaction):
    guild_id_str = str(interaction.guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    allowed_users = guild_config.get("allowed_users", [])
    
    embed = create_user_list_embed(allowed_users)
    view = UserManageView()

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# 🔴 管理系コマンド（管理者のみ表示）

@bot.tree.command(name="create_channel", description="新しいテキストチャンネルを作成します")
@discord.app_commands.default_permissions(administrator=True)
async def create_channel(
    interaction: discord.Interaction, 
    name: str, 
    category: discord.CategoryChannel = None
):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    try:
        new_channel = await guild.create_text_channel(name=name, category=category)
        category_msg = f"（カテゴリー: {category.name}）" if category else ""
        await interaction.followup.send(f"✅ 新しいテキストチャンネル {new_channel.mention} を作成しました！{category_msg}", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ ボットの権限が足りないため、チャンネルを作成できませんでした。ボットに『チャンネルの管理』権限が与えられているか確認してください。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ チャンネルの作成中にエラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="create_role_panel", description="このチャンネルの権限を持つロールの選択パネルを自動生成します")
@discord.app_commands.default_permissions(administrator=True)
async def create_role_panel(
    interaction: discord.Interaction, 
    title: str = "🏷️ ロール役職の選択", 
    description: str = "下のボタンを押すことで、自由にロールを付け外しできます。\nもう一度押すと外すことができます。",
    image_file: discord.Attachment = None
):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    guild = interaction.guild
    
    detected_roles = []
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role) and target != guild.default_role:
            if overwrite.view_channel is True or overwrite.read_messages is True:
                detected_roles.append(target)
                
    detected_roles.sort(key=lambda r: r.position, reverse=True)

    if not detected_roles:
        await interaction.followup.send("⚠️ このチャンネルの『権限の追加』に個別登録されているロールが見つかりませんでした。先にチャンネル設定の「権限」から、選択肢にしたいロールを追加（閲覧を許可など）してください。", ephemeral=True)
        return

    if len(detected_roles) > 25:
        await interaction.followup.send("⚠️ Discordの仕様上、一度に設置できるボタンは25個までです。ロールの数を減らしてください。", ephemeral=True)
        return

    guild_id_str = str(guild.id)
    all_data = load_data()
    guild_config = get_guild_config(all_data, guild_id_str)
    guild_config["panel_roles"] = [r.id for r in detected_roles]
    save_data(all_data)

    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    if image_file:
        embed.set_image(url=image_file.url)
        
    view = DynamicRoleView(detected_roles)
    
    await channel.send(embed=embed, view=view)
    await interaction.followup.send(f"✅ {len(detected_roles)}個のロールを含むパネルを綺麗に作成しました！", ephemeral=True)


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
async def send_verify_button(
    interaction: discord.Interaction, 
    title: str = "サーバー認証", 
    description: str = "下のボタンを押すと認証が完了し、全てのチャンネルが閲覧可能になります。",
    image_file: discord.Attachment = None
):
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
    if image_file:
        embed.set_image(url=image_file.url)

    view = VerifyButtonView()
    
    try:
        await target_channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"{target_channel.mention} に認証ボタンを設置しました！", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"メッセージの送信に失敗しました。エラー: {e}", ephemeral=True)

try:
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("❌ エラー: Discordトークンが無効です。")
except Exception as e:
    print(f"❌ ボット起動中に予期せぬエラーが発生しました: {e}")