print("=== WINDOWS_TEST_0614_FULL_SECURE ===")

import os
import discord
from discord.ext import commands
import json
import sys
import asyncio
import urllib.request
import urllib.parse
import base64
import requests

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
            "verify_role": None,
            "panel_roles": []
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
        embed.set_footer(text=f"登録者数: 0名")
    else:
        user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
        embed.add_field(name="登録ユーザー", value="\n".join(user_mentions), inline=False)
        embed.set_footer(text=f"登録者数: {len(allowed_users)}名")
    return embed


async def is_owner_check(interaction: discord.Interaction) -> bool:
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id
    
    if interaction.user.id != interaction.client.owner_id:
        await interaction.response.send_message("このコマンドはアプリの所有者専用です。", ephemeral=True)
        return False
    return True

async def is_admin_or_allowed(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    if interaction.user.id in cfg.get("allowed_users", []):
        return True
        
    await interaction.response.send_message("このコマンドを実行する権限がありません（管理者または許可ユーザー専用）。", ephemeral=True)
    return False


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
        if not interaction.guild: return
        if not interaction.user.guild_permissions.administrator: return

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
        if not interaction.guild: return
        if not interaction.user.guild_permissions.administrator: return

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
        for role in roles:
            button = discord.ui.Button(
                label=f"{role.name} を受け取る / 外す", 
                style=discord.ButtonStyle.secondary, 
                custom_id=f"dynamic_role_{role.id}"
            )
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
                    await interaction.response.send_message("Botの権限が不足しているためロールを外せませんでした。ロールの順序を確認してください。", ephemeral=True)
            else:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(f"{role.name} ロールを付与しました！", ephemeral=True)
                except discord.Forbidden:
                    await interaction.response.send_message("Botの権限が不足しているためロールを付与できませんでした。ボットのロールを対象ロールより上に移動してください。", ephemeral=True)
        return button_callback


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.primary, custom_id="persistent_verify_button")
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
    
    print("--- 起動完了: 現在のサーバー設定一覧 ---")
    for guild_id_str, config in all_data.items():
        if guild_id_str == "user_apps":
            continue
        
        guild = bot.get_guild(int(guild_id_str))
        guild_name = guild.name if guild else "不明なサーバー"
        print(f"サーバー: {guild_name} (ID: {guild_id_str})")
        print(f"  > Message転送: {'有効' if config.get('from_channel') else '未設定'}")
        print(f"  > サーバー認証: {'有効' if config.get('verify_channel') else '未設定'}")
        print(f"  > 配信お知らせ: {'有効' if config.get('announce_channel') else '未設定'}")
        
        panel_roles = config.get("panel_roles", [])
        if panel_roles and guild:
            roles = [guild.get_role(rid) for rid in panel_roles if guild.get_role(rid)]
            if roles: 
                bot.add_view(DynamicRoleView(roles))
                print(f"  > ロールパネル: {len(roles)}個の取得ボタンを再活性化しました")
    print("---------------------------------------")
    print(f"ログインユーザー: {bot.user.name} (ID: {bot.user.id})")
    print("スラッシュコマンドを更新したい場合は、サーバー上で '!sync' と発言してください。")


@bot.command(name="sync")
async def sync_command(ctx):
    if ctx.author.guild_permissions.administrator:
        await ctx.send("Discordにスラッシュコマンドを同期中... 少々お待ちください。")
        try:
            await bot.tree.sync()
            await ctx.send("スラッシュコマンドの同期が完了しました。Discordアプリを再起動して確認してください。")
        except discord.errors.HTTPException as e:
            await ctx.send(f"Discord側で一時的な制限がかかっています。5〜10分ほど置いて再度試してください。\nエラー内容: `{e}`")


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


# ==================== 【一般ユーザー・プレイヤー向け機能】 ====================

@bot.tree.command(name="hello", description="Botが挨拶を返します")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"こんにちは、{interaction.user.mention}さん！")


@bot.tree.command(name="say", description="Botに指定したメッセージを発言させます")
async def say(interaction: discord.Interaction, message: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return
    await interaction.response.send_message("メッセージを送信しました。", ephemeral=True)
    await interaction.channel.send(message)


@bot.tree.command(name="search", description="各種検索サイトやWikipediaの検索リンクを生成します")
@discord.app_commands.choices(engine=[
    discord.app_commands.Choice(name="Google (ウェブ検索)", value="google"),
    discord.app_commands.Choice(name="YouTube (動画検索)", value="youtube"),
    discord.app_commands.Choice(name="GitHub (コード検索)", value="github"),
    discord.app_commands.Choice(name="X /旧Twitter", value="x"),
    discord.app_commands.Choice(name="Wikipedia (百科事典)", value="wiki")
])
async def search(interaction: discord.Interaction, engine: discord.app_commands.Choice[str], query: str):
    eng = engine.value

    if eng == "wiki":
        await interaction.response.defer(ephemeral=False)
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://ja.wikipedia.org/api/rest_v1/page/summary/{encoded_query}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                embed = discord.Embed(title=f"Wiki検索結果: {data.get('title', query)}", description=data.get('extract', '概要なし'), color=discord.Color.blue())
                if "content_urls" in data: embed.url = data["content_urls"]["desktop"]["page"]
                if "thumbnail" in data: embed.set_thumbnail(url=data["thumbnail"]["source"])
                await interaction.followup.send(embed=embed)
        except:
            await interaction.followup.send(f"Wikipediaで「{query}」が見つかりませんでした。")
    
    else:
        encoded_query = urllib.parse.quote_plus(query)
        urls = {
            "google": f"https://www.google.com/search?q={encoded_query}",
            "youtube": f"https://www.youtube.com/results?search_query={encoded_query}",
            "github": f"https://github.com/search?q={encoded_query}",
            "x": f"https://x.com/search?q={encoded_query}"
        }
        embed = discord.Embed(
            title=f"検索リンク ({engine.name})",
            description=f"「{query}」の検索用リンクを作成しました。\n\n[ここをクリックして検索結果を開く]({urls[eng]})",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)


@bot.tree.command(name="my_scan", description="サーバー情報、または指定ユーザーの情報を確認（一般ユーザーはサーバー情報のみ）")
async def my_scan(interaction: discord.Interaction, target_user: discord.User = None):
    if target_user:
        if not await is_admin_or_allowed(interaction): return
        
        embed = discord.Embed(title="ユーザーデータスキャン結果", color=discord.Color.teal())
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
        embed = discord.Embed(title=f"{g.name} サーバー情報", color=discord.Color.teal())
        if g.icon: embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="サーバー名", value=g.name, inline=True)
        embed.add_field(name="サーバーID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="オーナー", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="メンバー数", value=f"{g.member_count} 人", inline=True)
        embed.add_field(name="ブースト状況", value=f"Level {g.premium_tier} ({g.premium_subscription_count}回)", inline=True)
        embed.add_field(name="サーバー作成日", value=discord.utils.format_dt(g.created_at, style="F"), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== 【個人用プライベート機能（誰でも自由に使用可能）】 ====================

@bot.tree.command(name="my_memo", description="あなた専用の個人メモを追加・一覧表示・削除します（他の人には見えません）")
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="メモを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="選択して削除する", value="delete"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_memo(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    all_data = load_data()
    user_data = get_user_app_data(all_data, str(interaction.user.id))
    act = action.value

    if act == "add":
        if not content:
            await interaction.response.send_message("保存する内容を入力してください。", ephemeral=True)
            return
        user_data["memos"].append(content)
        save_data(all_data)
        await interaction.response.send_message(f"個人メモを保存しました:\n`{content}`", ephemeral=True)

    elif act == "list":
        memos = user_data.get("memos", [])
        embed = discord.Embed(title="あなた専用の個人メモ一覧", color=discord.Color.gold())
        embed.description = "\n".join([f"**{i+1}.** {m}" for i, m in enumerate(memos)]) if memos else "保存されているメモはありません。"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif act == "delete":
        memos = user_data.get("memos", [])
        if not memos:
            await interaction.response.send_message("削除できるメモがありません。", ephemeral=True)
            return
        view = MemoDeleteView(memos)
        await interaction.response.send_message("削除したい個人メモをメニューから選んでください：", view=view, ephemeral=True)

    elif act == "clear":
        user_data["memos"] = []
        save_data(all_data)
        await interaction.response.send_message("全ての個人メモを消去しました。", ephemeral=True)


@bot.tree.command(name="my_clip", description="あなた専用のクリップ（テキストやリンク）を保存・管理します（他の人には見えません）")
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="クリップを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_clip(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    all_data = load_data()
    user_data = get_user_app_data(all_data, str(interaction.user.id))
    act = action.value

    if act == "add":
        if not content:
            await interaction.response.send_message("内容を入力してください。", ephemeral=True)
            return
        user_data["bookmarks"].append(content)
        save_data(all_data)
        await interaction.response.send_message("個人クリップに保存しました。", ephemeral=True)
    elif act == "list":
        bks = user_data.get("bookmarks", [])
        embed = discord.Embed(title="あなた専用のクリップ一覧", color=discord.Color.magenta())
        embed.description = "\n".join([f"• {b}" for b in bks]) if bks else "保存されているクリップはありません。"
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif act == "clear":
        user_data["bookmarks"] = []
        save_data(all_data)
        await interaction.response.send_message("全ての個人クリップを消去しました。", ephemeral=True)


# ==================== 【管理者・許可ユーザー専用コマンド】 ====================

@bot.tree.command(name="my_scan_channels", description="サーバーのチャンネル構造とカスタム権限をスキャン")
async def my_scan_channels(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return
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
                    if ow.view_channel is False or ow.read_messages is False: roles.append(f"制限あり: {target.name}")
                    elif ow.view_channel is True or ow.read_messages is True: roles.append(f"閲覧可: {target.name}")
            if roles:
                count += 1
                report.append(f"• {ch.mention} -> {', '.join(roles[:3])}")
    if count == 0: report.append("個別設定されたチャンネルはありません。")
    full_rep = "\n".join(report)
    await interaction.followup.send(embed=discord.Embed(title="フルスキャン結果", description=full_rep[:1950], color=discord.Color.red()), ephemeral=True)


@bot.tree.command(name="my_audit_perms", description="@everyoneの権限設定をスキャン")
async def my_audit_perms(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return
        
    # ephemeral=False に変更し、全体に見えるように公開
    await interaction.response.defer(ephemeral=False)
    
    report = []
    for channel in interaction.guild.text_channels:
        everyone_perms = channel.permissions_for(interaction.guild.default_role)
        issues = []
        if everyone_perms.view_channel:
            issues.append("閲覧")
        if everyone_perms.send_messages:
            issues.append("送信")
            
        if issues:
            report.append(f"注意 {channel.mention} : @everyone に「{', '.join(issues)}」権限があります")
            
    if not report:
        await interaction.followup.send("チェック完了: @everyone に不適切な権限はありません。", ephemeral=False)
    else:
        embed = discord.Embed(
            title="権限スキャン結果", 
            description="以下のチャンネルの設定を確認してください：\n\n" + "\n".join(report), 
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=False)


@bot.tree.command(name="my_check_url", description="URLの安全性をVirusTotalでチェック")
async def my_check_url(interaction: discord.Interaction, url: str):
    if not await is_admin_or_allowed(interaction): return
    await interaction.response.defer(ephemeral=True)
    
    api_key = os.getenv("VT_API_KEY")
    if not api_key:
        await interaction.followup.send("エラー: 環境変数 'VT_API_KEY' が設定されていません。", ephemeral=True)
        return

    url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    headers = {"x-apikey": api_key}
    
    try:
        response = requests.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers=headers)
        data = response.json()
        if "error" in data:
            await interaction.followup.send("このURLのスキャンデータが見つかりませんでした。誰もスキャンしたことがない未知のURLの可能性があります。", ephemeral=True)
            return
        stats = data["data"]["attributes"]["last_analysis_stats"]
        malicious = stats["malicious"]
        suspicious = stats["suspicious"]
        color = discord.Color.green() if (malicious + suspicious) == 0 else discord.Color.red()
        msg = f"判定結果:\n危険なエンジン: {malicious}件\n怪しいエンジン: {suspicious}件"
        embed = discord.Embed(title="URLスキャン結果", description=msg, color=color)
        embed.add_field(name="対象URL", value=url[:100], inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)


# ==================== 【サーバー管理者専用コマンド】 ====================

@bot.tree.command(name="server_status", description="現在のサーバー設定状況を確認します")
async def server_status(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
        return
        
    g = interaction.guild
    g_id_str = str(g.id)
    all_data = load_data()
    cfg = get_guild_config(all_data, g_id_str)

    embed = discord.Embed(
        title=f"{g.name} - 設定状況", 
        description="このサーバーで有効化されている設定一覧です。",
        color=discord.Color.blue()
    )
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)

    from_ch = g.get_channel(cfg.get("from_channel")) if cfg.get("from_channel") else None
    to_ch = g.get_channel(cfg.get("to_channel")) if cfg.get("to_channel") else None
    forward_status = f"有効\n・転送元: {from_ch.mention if from_ch else '削除済のチャンネル'}\n・転送先: {to_ch.mention if to_ch else '削除済のチャンネル'}" if (from_ch or to_ch) else "未設定"
    embed.add_field(name="メッセージ転送設定", value=forward_status, inline=False)

    v_ch = g.get_channel(cfg.get("verify_channel")) if cfg.get("verify_channel") else None
    v_role = g.get_role(cfg.get("verify_role")) if cfg.get("verify_role") else None
    verify_status = f"有効\n・設置ch: {v_ch.mention if v_ch else '削除済のチャンネル'}\n・付与ロール: {v_role.mention if v_role else '削除済のロール'}" if (v_ch or v_role) else "未設定"
    embed.add_field(name="サーバー認証設定", value=verify_status, inline=False)

    a_ch = g.get_channel(cfg.get("announce_channel")) if cfg.get("announce_channel") else None
    a_role = g.get_role(cfg.get("announce_role")) if cfg.get("announce_role") else None
    announce_status = f"有効\n・お知らせch: {a_ch.mention if a_ch else '削除済のチャンネル'}\n・メンション対象: {a_role.mention if a_role else '削除済のロール'}" if (a_ch or a_role) else "未設定"
    embed.add_field(name="配信・お知らせ設定", value=announce_status, inline=False)

    panel_roles_ids = cfg.get("panel_roles", [])
    valid_panel_roles = [g.get_role(rid).name for rid in panel_roles_ids if g.get_role(rid)]
    panel_status = f"紐付け済み ({len(valid_panel_roles)}個)\n`{', '.join(valid_panel_roles)}`" if valid_panel_roles else "パネル未登録"
    embed.add_field(name="ロールパネル対象", value=panel_status, inline=False)

    allowed_users = cfg.get("allowed_users", [])
    allowed_status = ", ".join([f"<@{uid}>" for uid in allowed_users]) if allowed_users else "なし（管理者のみ使用可能）"
    embed.add_field(name="コマンド使用許可ユーザー", value=allowed_status, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="server_list_users", description="使用許可リストの確認・編集を行います")
async def server_list_users(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
        return
    g_id = str(interaction.guild.id)
    all_data = load_data()
    config = get_guild_config(all_data, g_id)
    embed = create_user_list_embed(config.get("allowed_users", []))
    await interaction.response.send_message(embed=embed, view=UserManageView(), ephemeral=True)


@bot.tree.command(name="server_create_channel", description="新しいテキストチャンネルを作成します")
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


@bot.tree.command(name="server_role_panel", description="指定したロール（最大5つ）を取得できるボタン付きパネルを送信します")
async def server_role_panel(
    interaction: discord.Interaction, 
    title: str, 
    description: str,
    role1: discord.Role,
    role2: discord.Role = None,
    role3: discord.Role = None,
    role4: discord.Role = None,
    role5: discord.Role = None
):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    ch = interaction.channel
    g = interaction.guild
    
    raw_roles = [role1, role2, role3, role4, role5]
    roles = [r for r in raw_roles if r is not None]
    
    all_data = load_data()
    get_guild_config(all_data, str(g.id))["panel_roles"] = [r.id for r in roles]
    save_data(all_data)
    
    embed = discord.Embed(
        title=title, 
        description=description, 
        color=discord.Color.blue()
    )
    
    role_mentions = [f"{r.mention}" for r in roles]
    embed.add_field(name="対象ロール一覧", value="\n\n".join(role_mentions), inline=False)
    
    await ch.send(embed=embed, view=DynamicRoleView(roles))
    await interaction.followup.send("ロールパネルを設置しました。", ephemeral=True)


@bot.tree.command(name="server_forward_setup", description="メッセージ転送元のチャンネルと転送先を設定します")
async def server_forward_setup(interaction: discord.Interaction, from_channel: discord.TextChannel, to_channel: discord.TextChannel):
    if not interaction.guild or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = from_channel.id, to_channel.id
    save_data(all_data)
    await interaction.response.send_message("転送設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_forward_reset", description="チャンネルの転送設定を解除します")
async def server_forward_reset(interaction: discord.Interaction):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = None, None
    save_data(all_data)
    await interaction.response.send_message("転送設定を解除しました。", ephemeral=True)


@bot.tree.command(name="server_announce_setup", description="お知らせ用のチャンネルとロールを設定します")
async def server_announce_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["announce_channel"], cfg["announce_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("お知らせ設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_announce_send", description="設定されたチャンネルにロールメンション付きでお知らせを送信します")
async def server_announce_send(interaction: discord.Interaction, message: str):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ch = bot.get_channel(cfg.get("announce_channel"))
    r = interaction.guild.get_role(cfg.get("announce_role", 0))
    if ch and r:
        await ch.send(f"{r.mention}\n\n{message}")
        await interaction.response.send_message("お知らせを送信しました。", ephemeral=True)
    else:
        await interaction.response.send_message("設定が不完全です。", ephemeral=True)


@bot.tree.command(name="server_verify_setup", description="認証用ロールと送信チャンネルを設定します")
async def server_verify_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["verify_channel"], cfg["verify_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("認証設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_verify_btn", description="設定されたチャンネルに認証用ボタンパネルを送信します")
async def server_verify_btn(interaction: discord.Interaction, title: str = "サーバー認証", description: str = "ボタンを押すと認証が完了します。", image_file: discord.Attachment = None):
    if not interaction.guild or not interaction.user.guild_permissions.administrator: return
    await interaction.response.defer(ephemeral=True)
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ch_id = cfg.get("verify_channel")
    if not ch_id:
        await interaction.followup.send("認証チャンネルが設定されていません。", ephemeral=True)
        return
    ch = interaction.guild.get_channel(ch_id)
    if not ch:
        await interaction.followup.send("設定されたチャンネルが見つかりません。", ephemeral=True)
        return
        
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    if image_file:
        file_data = await image_file.to_file()
        embed.set_image(url=f"attachment://{image_file.filename}")
        await ch.send(embed=embed, file=file_data, view=VerifyButtonView())
    else:
        await ch.send(embed=embed, view=VerifyButtonView())
    await interaction.followup.send("認証パネルを送信しました。", ephemeral=True)


bot.run(TOKEN)