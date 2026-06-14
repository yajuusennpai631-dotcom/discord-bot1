print("=== WINDOWS_TEST_0614 ===")

import os
import discord
from discord.ext import commands
import json
import sys
import asyncio
import urllib.request
import urllib.parse

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("エラー: 環境変数 'DISCORD_TOKEN' が見つかりません。")
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
            print(f"JSON読み込みエラー: {e}")
            return {}
    return {}

def save_data(data):
    try:
        dir_name = os.path.dirname(JSON_FILE)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            print(f"保存先フォルダを作成しました: {dir_name}")

        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"JSON保存エラー: {e}")

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

def get_user_app_data(all_data, user_id_str):
    if "user_apps" not in all_data:
        all_data["user_apps"] = {}
    if user_id_str not in all_data["user_apps"]:
        all_data["user_apps"][user_id_str] = {
            "memos": [],
            "bookmarks": []
        }
    return all_data["user_apps"][user_id_str]

def create_user_list_embed(allowed_users):
    embed = discord.Embed(
        title="コマンド使用許可ユーザー一覧", 
        description="現在、以下のユーザーに権限が与えられています。\n※サーバー管理者は登録なしですべてのコマンドを使用できます。",
        color=discord.Color.blue()
    )
    if not allowed_users:
        embed.add_field(name="登録ユーザー", value="現在登録されているユーザーはいません。", inline=False)
        embed.set_footer(text="登録者数: 0名")
    else:
        user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
        embed.add_field(name="登録ユーザー", value="\n".join(user_mentions), inline=False)
        embed.set_footer(text=f"登録者数: {len(allowed_users)}名")
    return embed

user_app_config = {
    "contexts": discord.app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    "integration_types": [discord.app_commands.AppInstallationType.guild, discord.app_commands.AppInstallationType.user]
}

async def is_owner_check(interaction: discord.Interaction) -> bool:
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id
    
    if interaction.user.id != interaction.client.owner_id:
        await interaction.response.send_message("このコマンドはアプリの所有者専用です。", ephemeral=True)
        return False
    return True


# --- メモ削除用のコンポーネント ---
class MemoDeleteSelect(discord.ui.Select):
    def __init__(self, memos):
        options = []
        for i, memo in enumerate(memos):
            short_memo = memo if len(memo) <= 50 else memo[:47] + "..."
            options.append(discord.SelectOption(label=f"{i+1}. {short_memo}", value=str(i)))
            if i >= 24:
                break
        super().__init__(placeholder="削除するメモを選択してください", options=options)

    async def callback(self, interaction: discord.Interaction):
        all_data = load_data()
        user_data = get_user_app_data(all_data, str(interaction.user.id))
        memos = user_data.get("memos", [])
        
        idx = int(self.values[0])
        if idx < len(memos):
            removed_memo = memos.pop(idx)
            save_data(all_data)
            await interaction.response.send_message(f"メモを削除しました:\n`{removed_memo}`", ephemeral=True)
        else:
            await interaction.response.send_message("エラー: メモの削除に失敗しました。", ephemeral=True)

class MemoDeleteView(discord.ui.View):
    def __init__(self, memos):
        super().__init__(timeout=180)
        self.add_item(MemoDeleteSelect(memos))


class UserManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="許可ユーザーを追加...", custom_id="manage_add_user")
    async def add_user_callback(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not interaction.guild:
            await interaction.response.send_message("この操作はサーバー内でのみ実行できます。", ephemeral=True)
            return
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
            await interaction.followup.send(f"{target_user.mention} を許可リストに追加しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"{target_user.mention} は既に登録されています。", ephemeral=True)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="許可ユーザーを削除...", custom_id="manage_remove_user")
    async def remove_user_callback(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not interaction.guild:
            await interaction.response.send_message("この操作はサーバー内でのみ実行できます。", ephemeral=True)
            return
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
            await interaction.followup.send(f"{target_user.mention} を許可リストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"{target_user.mention} は登録されていません。", ephemeral=True)


class DynamicRoleView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        styles = [discord.ButtonStyle.primary, discord.ButtonStyle.success, discord.ButtonStyle.secondary, discord.ButtonStyle.danger]
        for i, role in enumerate(roles):
            button = discord.ui.Button(label=role.name, style=styles[i % len(styles)], custom_id=f"dynamic_role_{role.id}")
            button.callback = self.create_callback(role.id)
            self.add_item(button)

    def create_callback(self, role_id):
        async def button_callback(interaction: discord.Interaction):
            guild = interaction.guild
            if not guild: return
            role = guild.get_role(role_id)
            if not role: return

            if role in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(role)
                    await interaction.response.send_message(f"{role.name} ロールを外しました。", ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message("Botの権限が不足しています。", ephemeral=True)
            else:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(f"{role.name} ロールを付与しました。", ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message("Botの権限が不足しています。", ephemeral=True)
        return button_callback


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.success, custom_id="persistent_verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild: return
        guild_id_str = str(interaction.guild.id)
        all_data = load_data()
        guild_config = get_guild_config(all_data, guild_id_str)
        role_id = guild_config.get("verify_role")
        if not role_id: return
        role = interaction.guild.get_role(role_id)
        if not role: return

        if role in interaction.user.roles:
            await interaction.response.send_message("すでに認証されています。", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message("認証が完了しました！", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)


@bot.event
async def on_ready():
    bot.add_view(VerifyButtonView())
    all_data = load_data()
    for guild_id_str, config in all_data.items():
        if guild_id_str == "user_apps": continue
        panel_roles = config.get("panel_roles", [])
        if panel_roles:
            guild = bot.get_guild(int(guild_id_str))
            if guild:
                roles = [guild.get_role(rid) for rid in panel_roles if guild.get_role(rid)]
                if roles: bot.add_view(DynamicRoleView(roles))
    try:
        await bot.tree.sync()
        print("全コマンドの同期が完了しました。")
    except Exception as e:
        print(f"同期エラー: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild: return
    guild_id_str = str(message.guild.id)
    all_data = load_data()
    if guild_id_str in all_data:
        guild_config = all_data[guild_id_str]
        from_id = guild_config.get("from_channel")
        to_id = guild_config.get("to_channel")
        if from_id and to_id and message.channel.id == from_id:
            if message.author.guild_permissions.administrator:
                to_channel = message.guild.get_channel(to_id)
                if to_channel: await to_channel.send(message.content)
    await bot.process_commands(message)


class RestartConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="はい (再起動)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Botを終了します...", ephemeral=True)
        await bot.close()
        sys.exit(0)

    @discord.ui.button(label="いいえ", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("キャンセルしました。", ephemeral=True)
        self.stop()


# ==================== 【マイアプリ専用機能】 ====================

@bot.tree.command(name="my_memo", description="【マイアプリ専用】メモの追加・一覧表示・削除", **user_app_config)
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="メモを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="選択して削除する", value="delete"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_memo(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    if not await is_owner_check(interaction): return
    all_data = load_data()
    user_data = get_user_app_data(all_data, str(interaction.user.id))
    act = action.value

    if act == "add":
        if not content:
            await interaction.response.send_message("保存する内容を入力してください。", ephemeral=True)
            return
        user_data["memos"].append(content)
        save_data(all_data)
        await interaction.response.send_message(f"メモを保存しました:\n`{content}`", ephemeral=True)

    elif act == "list":
        memos = user_data.get("memos", [])
        embed = discord.Embed(title="専用メモ一覧", color=discord.Color.gold())
        embed.description = "\n".join([f"**{i+1}.** {m}" for i, m in enumerate(memos)]) if memos else "保存されているメモはありません。"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif act == "delete":
        memos = user_data.get("memos", [])
        if not memos:
            await interaction.response.send_message("削除できるメモがありません。", ephemeral=True)
            return
        view = MemoDeleteView(memos)
        await interaction.response.send_message("削除したいメモをメニューから選んでください：", view=view, ephemeral=True)

    elif act == "clear":
        user_data["memos"] = []
        save_data(all_data)
        await interaction.response.send_message("全てのメモを消去しました。", ephemeral=True)


@bot.tree.command(name="my_search", description="【マイアプリ専用】各種検索サイトやWikipediaで検索", **user_app_config)
@discord.app_commands.choices(engine=[
    discord.app_commands.Choice(name="Google", value="google"),
    discord.app_commands.Choice(name="YouTube", value="youtube"),
    discord.app_commands.Choice(name="GitHub", value="github"),
    discord.app_commands.Choice(name="X (Twitter)", value="x"),
    discord.app_commands.Choice(name="Wikipedia", value="wiki")
])
async def my_search(interaction: discord.Interaction, engine: discord.app_commands.Choice[str], query: str):
    if not await is_owner_check(interaction): return
    eng = engine.value

    if eng == "wiki":
        await interaction.response.defer(ephemeral=True)
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://ja.wikipedia.org/api/rest_v1/page/summary/{encoded_query}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                embed = discord.Embed(title=f"Wiki検索結果: {data.get('title', query)}", description=data.get('extract', '概要なし'), color=discord.Color.blue())
                if "content_urls" in data: embed.url = data["content_urls"]["desktop"]["page"]
                if "thumbnail" in data: embed.set_thumbnail(url=data["thumbnail"]["source"])
                await interaction.followup.send(embed=embed, ephemeral=True)
        except:
            await interaction.followup.send(f"Wikipediaで「{query}」が見つかりませんでした。", ephemeral=True)
    
    else:
        encoded_query = urllib.parse.quote_plus(query)
        urls = {
            "google": f"https://www.google.com/search?q={encoded_query}",
            "youtube": f"https://www.youtube.com/results?search_query={encoded_query}",
            "github": f"https://github.com/search?q={encoded_query}",
            "x": f"https://x.com/search?q={encoded_query}"
        }
        embed = discord.Embed(
            title=f"検索リンク生成 ({engine.name})",
            description=f"「**{query}**」の検索リンクを作成しました。\n\n🔗 **[ここをクリックして検索結果を開く]({urls[eng]})**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="my_clip", description="【マイアプリ専用】テキストやリンクをクリップして保存", **user_app_config)
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="クリップを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_clip(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    if not await is_owner_check(interaction): return
    all_data = load_data()
    user_data = get_user_app_data(all_data, str(interaction.user.id))
    act = action.value

    if act == "add":
        if not content:
            await interaction.response.send_message("内容を入力してください。", ephemeral=True)
            return
        user_data["bookmarks"].append(content)
        save_data(all_data)
        await interaction.response.send_message("クリップに保存しました。", ephemeral=True)
    elif act == "list":
        bks = user_data.get("bookmarks", [])
        embed = discord.Embed(title="クリップ一覧", color=discord.Color.magenta())
        embed.description = "\n".join([f"• {b}" for b in bks]) if bks else "保存されているクリップはありません。"
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif act == "clear":
        user_data["bookmarks"] = []
        save_data(all_data)
        await interaction.response.send_message("全て消去しました。", ephemeral=True)


@bot.tree.command(name="my_scan", description="【マイアプリ専用】サーバー情報、または指定ユーザーの情報を確認", **user_app_config)
async def my_scan(interaction: discord.Interaction, target_user: discord.User = None):
    if not await is_owner_check(interaction): return
    embed = discord.Embed(title="データスキャン結果", color=discord.Color.teal())

    if target_user:
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="ユーザー名", value=f"{target_user.name} ({target_user.mention})", inline=True)
        embed.add_field(name="ID", value=f"`{target_user.id}`", inline=True)
        embed.add_field(name="アカウント作成日", value=discord.utils.format_dt(target_user.created_at, style="F"), inline=False)
        if interaction.guild:
            m = interaction.guild.get_member(target_user.id)
            if m and m.joined_at:
                embed.add_field(name="サーバー参加日", value=discord.utils.format_dt(m.joined_at, style="F"), inline=False)
                roles = [r.mention for r in m.roles if r != interaction.guild.default_role]
                embed.add_field(name="所持ロール", value=" ".join(roles) if roles else "なし", inline=False)
    else:
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        g = interaction.guild
        if g.icon: embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="サーバー名", value=g.name, inline=True)
        embed.add_field(name="サーバーID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="オーナー", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="メンバー数", value=f"{g.member_count} 人", inline=True)
        embed.add_field(name="ブースト状況", value=f"Level {g.premium_tier} ({g.premium_subscription_count}回)", inline=True)
        embed.add_field(name="サーバー作成日", value=discord.utils.format_dt(g.created_at, style="F"), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="my_scan_channels", description="【マイアプリ専用】サーバーのチャンネル構造とカスタム権限をスキャン", **user_app_config)
async def my_scan_channels(interaction: discord.Interaction):
    if not await is_owner_check(interaction): return
    if not interaction.guild:
        await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    g = interaction.guild
    report = [f"**{g.name} チャンネルレポート**", f"カテゴリー: {len(g.categories)} | テキスト: {len(g.text_channels)} | ボイス: {len(g.voice_channels)}\n", "個別権限が設定されているチャンネル:"]
    
    count = 0
    for ch in g.channels:
        if isinstance(ch, discord.CategoryChannel): continue
        if ch.overwrites:
            roles = []
            for target, ow in ch.overwrites.items():
                if isinstance(target, discord.Role):
                    if ow.view_channel is False or ow.read_messages is False: roles.append(f"⛔ {target.name}")
                    elif ow.view_channel is True or ow.read_messages is True: roles.append(f"👁️ {target.name}")
            if roles:
                count += 1
                report.append(f"• {ch.mention} ➜ {', '.join(roles[:3])}")
    if count == 0: report.append("個別設定されたチャンネルはありません。")
    full_rep = "\n".join(report)
    await interaction.followup.send(embed=discord.Embed(title="フルスキャン結果", description=full_rep[:1950], color=discord.Color.red()), ephemeral=True)


# ==================== 【サーバー管理コマンド】 ====================

@bot.tree.command(name="server_list_users", description="【管理】使用許可リストの確認・編集を行います", **user_app_config)
async def server_list_users(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
        return
    g_id = str(interaction.guild.id)
    all_data = load_data()
    config = get_guild_config(all_data, g_id)
    embed = create_user_list_embed(config.get("allowed_users", []))
    await interaction.response.send_message(embed=embed, view=UserManageView(), ephemeral=True)

@bot.tree.command(name="server_create_channel", description="【管理】新しいテキストチャンネルを作成します", **user_app_config)
async def server_create_channel(interaction: discord.Interaction, name: str, category: discord.CategoryChannel = None):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        new_ch = await interaction.guild.create_text_channel(name=name, category=category)
        await interaction.followup.send(f"チャンネル {new_ch.mention} を作成しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"作成失敗: {e}", ephemeral=True)

@bot.tree.command(name="server_role_panel", description="【管理】チャンネル閲覧権限を持つロールの選択パネルを生成します", **user_app_config)
async def server_role_panel(interaction: discord.Interaction, title: str = "ロールの選択", description: str = "ボタンを押すことで、ロールを付け外しできます。", image_file: discord.Attachment = None):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ch = interaction.channel
    g = interaction.guild
    roles = [t for t, ow in ch.overwrites.items() if isinstance(t, discord.Role) and t != g.default_role and (ow.view_channel is True or ow.read_messages is True)]
    roles.sort(key=lambda r: r.position, reverse=True)
    if not roles:
        await interaction.followup.send("対象のロールが見つかりません。", ephemeral=True)
        return
    all_data = load_data()
    get_guild_config(all_data, str(g.id))["panel_roles"] = [r.id for r in roles]
    save_data(all_data)
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    if image_file: embed.set_image(url=image_file.url)
    await ch.send(embed=embed, view=DynamicRoleView(roles))
    await interaction.followup.send("パネルを設置しました。", ephemeral=True)

@bot.tree.command(name="server_forward_setup", description="【管理】メッセージ転送元のチャンネルと転送先を設定します", **user_app_config)
async def server_forward_setup(interaction: discord.Interaction, from_channel: discord.TextChannel, to_channel: discord.TextChannel):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = from_channel.id, to_channel.id
    save_data(all_data)
    await interaction.response.send_message("転送設定を保存しました。", ephemeral=True)

@bot.tree.command(name="server_forward_reset", description="【管理】チャンネルの転送設定を解除します", **user_app_config)
async def server_forward_reset(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = None, None
    save_data(all_data)
    await interaction.response.send_message("転送設定を解除しました。", ephemeral=True)

@bot.tree.command(name="server_announce_setup", description="【管理】お知らせ用のチャンネルとロールを設定します", **user_app_config)
async def server_announce_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["announce_channel"], cfg["announce_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("お知らせ設定を保存しました。", ephemeral=True)

@bot.tree.command(name="server_announce_send", description="【管理】設定されたチャンネルにロールメンション付きでお知らせを送信します", **user_app_config)
async def server_announce_send(interaction: discord.Interaction, message: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ch, r = bot.get_channel(cfg.get("announce_channel")), interaction.guild.get_role(cfg.get("announce_role", 0))
    if ch and r:
        await ch.send(f"{r.mention}\n\n{message}")
        await interaction.response.send_message("お知らせを送信しました。", ephemeral=True)
    else:
        await interaction.response.send_message("設定が不完全です。", ephemeral=True)

@bot.tree.command(name="server_verify_setup", description="【管理】認証用ロールと送信チャンネルを設定します", **user_app_config)
async def server_verify_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["verify_channel"], cfg["verify_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("認証設定を保存しました。", ephemeral=True)

@bot.tree.command(name="server_verify_btn", description="【管理】設定されたチャンネルに認証用ボタンパネルを送信します", **user_app_config)
async def server_verify_btn(interaction: discord.Interaction, title: str = "サーバー認証", description: str = "ボタンを押すと認証が完了します。", image_file: discord.Attachment = None):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ch = bot.get_channel(cfg.get("verify_channel"))
    if not ch:
        await interaction.response.send_message("先に設定を完了してください。", ephemeral=True)
        return
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    if image_file: embed.set_image(url=image_file.url)
    await ch.send(embed=embed, view=VerifyButtonView())
    await interaction.response.send_message("認証パネルを設置しました。", ephemeral=True)

@bot.tree.command(name="server_say", description="ボットに匿名で発言させます", **user_app_config)
async def server_say(interaction: discord.Interaction, message: str):
    if not interaction.guild: return
    all_data = load_data()
    allowed = get_guild_config(all_data, str(interaction.guild.id)).get("allowed_users", [])
    if interaction.user.id not in allowed and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    await interaction.channel.send(message)
    await interaction.response.send_message("送信しました。", ephemeral=True)

@bot.tree.command(name="server_restart", description="ボットを再起動します（管理者用）", **user_app_config)
async def server_restart(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.send_message("本当に再起動しますか？", view=RestartConfirmView(), ephemeral=True)

try:
    bot.run(TOKEN)
except Exception as e:
    print(f"エラーが発生しました: {e}")