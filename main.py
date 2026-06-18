print("=== WINDOWS_TEST_0615_AUTO_STATUS_AND_PERMS ===")

import os
import discord
from discord.ext import commands
from discord import app_commands
import json
import sys
import asyncio
import urllib.request
import urllib.parse
import base64
import requests
import io
import datetime

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("エラー: 環境変数 'DISCORD_TOKEN' が見つかりません。")
    sys.exit(1)

# ◆ 追加: 申請パネルを送信する固定チャンネル名（BOTオーナーが事前に指定）
APPROVAL_PANEL_CHANNEL_NAME = os.getenv("APPROVAL_PANEL_CHANNEL_NAME", "bot-許可申請")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ◆ 注意: bot インスタンスは load_data() など下記の関数定義後にまとめて生成する
# （ApprovalCommandTree が load_data / get_guild_config を参照するため）

if os.path.exists("/app/data"):
    JSON_FILE = "/app/data/allowed_users.json"
else:
    JSON_FILE = "allowed_users.json"

# グローバルで手動ステータスを保持する変数（Noneならサーバー数表示）
current_custom_status = None

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
            "panel_roles": [],
            "mention_trigger_channel": None,  
            "mention_target_role": None,       
            "mention_custom_message": None,
            "approval_status": "pending",
            "approval_panel_channel_id": None
        }
    if "approval_status" not in all_data[guild_id_str]:
        all_data[guild_id_str]["approval_status"] = "pending"
    return all_data[guild_id_str]


def is_guild_approved(all_data, guild_id_str: str) -> bool:
    """サーバーがBOTオーナーから利用許可されているかを確認する"""
    cfg = get_guild_config(all_data, guild_id_str)
    return cfg.get("approval_status") == "approved"


# ◆ 修正: interaction_check は CommandTree をサブクラス化してオーバーライドする必要がある。
#    （@bot.tree.interaction_check というデコレータ形式は実際には機能しないため）
class ApprovalCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # DM等、ギルド外でのインタラクションはここでは制限しない
        if not interaction.guild:
            return True

        client = interaction.client

        # オーナーIDを確定
        if client.owner_id is None:
            try:
                app_info = await client.application_info()
                client.owner_id = app_info.owner.id
            except Exception:
                pass

        # BOTオーナーは未許可サーバーでも常に操作可能（許可申請の確認や設定変更のため）
        if interaction.user.id == client.owner_id:
            return True

        all_data = load_data()
        if not is_guild_approved(all_data, str(interaction.guild.id)):
            await interaction.response.send_message(
                "🔒 エラー: BOT所有者の認証がまだです。\n"
                "このサーバーはBOT所有者の利用許可を受けていないため、コマンドは無効化されています。\n"
                "サーバー管理者に申請パネルからの許可申請をご依頼ください。",
                ephemeral=True
            )
            return False

        return True


bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    tree_cls=ApprovalCommandTree
)

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
        embed.add_field(name="登録ユーザー", value="開示できるユーザーはいません。", inline=False)
        embed.set_footer(text=f"登録者数: 0名")
    else:
        user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
        embed.add_field(name="登録ユーザー", value="\n".join(user_mentions), inline=False)
        embed.set_footer(text=f"登録者数: {len(allowed_users)}名")
    return embed


# 視聴中・オンラインステータスを更新する共通関数
async def update_bot_status(client, text=None):
    global current_custom_status
    if text:
        current_custom_status = text
    
    status_text = current_custom_status if current_custom_status else f"{len(client.guilds)}個のサーバー"
    activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
    await client.change_presence(status=discord.Status.online, activity=activity)
    print(f"[ステータス更新] {status_text} を視聴中 (Online)")


async def is_owner_check(interaction: discord.Interaction) -> bool:
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id
    
    if interaction.user.id != interaction.client.owner_id:
        await interaction.response.send_message("このコマンドはアプリの所有者専用です。", ephemeral=True)
        return False
    return True


# ◆ 修正: オーナーを最初にチェックし、全サーバーで管理者権限相当を付与
async def is_admin_or_allowed(interaction: discord.Interaction) -> bool:
    # オーナーIDを確定
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

    # オーナーは全サーバーで無条件に通す
    if interaction.user.id == interaction.client.owner_id:
        return True

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


# ◆ 修正: サーバー管理者専用コマンド用チェック（オーナーも通す）
async def is_guild_admin(interaction: discord.Interaction) -> bool:
    """サーバー管理者 または BOTオーナーのみ通すチェック関数"""
    # オーナーIDを確定
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

    # オーナーは全サーバーで無条件に通す
    if interaction.user.id == interaction.client.owner_id:
        return True

    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return False

    if interaction.user.guild_permissions.administrator:
        return True

    await interaction.response.send_message("このコマンドはサーバー管理者専用です。", ephemeral=True)
    return False


# ==================== 【BOTオーナー許可制: 導入時承認フロー】 ====================

def find_approval_panel_channel(guild: discord.Guild):
    """固定チャンネル名から申請パネル送信先を検索する"""
    channel = discord.utils.find(
        lambda c: c.name == APPROVAL_PANEL_CHANNEL_NAME and isinstance(c, discord.TextChannel),
        guild.text_channels
    )
    if channel:
        return channel
    # フォールバック: Botが送信可能な最初のテキストチャンネル
    for ch in guild.text_channels:
        perms = ch.permissions_for(guild.me)
        if perms.send_messages:
            return ch
    return None


def build_approval_request_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🔒 このBOTの導入にはBOT所有者の許可が必要です",
        description=(
            "このBOTを継続して利用するには、**BOT所有者の承認**が必要です。\n\n"
            "下のボタンを押すと、BOT所有者に参加許可申請のDMが送信されます。\n"
            "所有者が**許可**すればBotが利用可能になります。\n"
            "所有者が**拒否**した場合、Botは自動的にサーバーから退出します。"
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="🏠 このサーバー", value=guild.name, inline=True)
    embed.add_field(name="👤 メンバー数", value=f"{guild.member_count}人", inline=True)
    embed.set_footer(text="サーバー管理者がボタンを押して申請してください")
    return embed


class ApprovalRequestView(discord.ui.View):
    """サーバー側に表示する『許可申請を送る』ボタン"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="📩 BOT所有者に許可申請を送る", style=discord.ButtonStyle.primary, custom_id="send_approval_request")
    async def send_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("このボタンはサーバー管理者のみ使用できます。", ephemeral=True)
            return

        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))

        if cfg.get("approval_status") == "approved":
            await interaction.response.send_message("このサーバーは既に許可されています。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        client = interaction.client
        if client.owner_id is None:
            app_info = await client.application_info()
            client.owner_id = app_info.owner.id

        try:
            owner = client.get_user(client.owner_id) or await client.fetch_user(client.owner_id)
        except Exception:
            owner = None

        if not owner:
            await interaction.followup.send("BOT所有者の情報を取得できませんでした。時間をおいて再試行してください。", ephemeral=True)
            return

        request_embed = discord.Embed(
            title="📨 新しいサーバー導入の許可申請",
            description="以下のサーバーからBotの利用許可申請が届きました。",
            color=discord.Color.gold()
        )
        if interaction.guild.icon:
            request_embed.set_thumbnail(url=interaction.guild.icon.url)

        owner_text = f"<@{interaction.guild.owner_id}>" if interaction.guild.owner_id else "不明"
        request_embed.add_field(name="サーバー名", value=interaction.guild.name, inline=True)
        request_embed.add_field(name="サーバーID", value=f"`{interaction.guild.id}`", inline=True)
        request_embed.add_field(name="サーバーオーナー", value=owner_text, inline=True)
        request_embed.add_field(name="メンバー数", value=f"{interaction.guild.member_count}人", inline=True)
        request_embed.add_field(name="申請者", value=f"{interaction.user} ({interaction.user.mention})", inline=False)
        request_embed.timestamp = discord.utils.utcnow()

        try:
            await owner.send(
                embed=request_embed,
                view=ApprovalDecisionView(
                    guild_id=interaction.guild.id,
                    panel_channel_id=interaction.channel.id
                )
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "BOT所有者へのDM送信に失敗しました（DM拒否設定の可能性があります）。所有者に直接ご連絡ください。",
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(f"申請送信中にエラーが発生しました: {e}", ephemeral=True)
            return

        cfg["approval_status"] = "pending_review"
        save_data(all_data)

        button.disabled = True
        button.label = "申請送信済み（所有者の確認待ち）"
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        await interaction.followup.send("BOT所有者に許可申請を送信しました。承認結果をお待ちください。", ephemeral=True)


class ApprovalDecisionView(discord.ui.View):
    """BOTオーナーのDMに表示する『許可/拒否』ボタン"""
    def __init__(self, guild_id: int, panel_channel_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.panel_channel_id = panel_channel_id

    async def _notify_panel_channel(self, client, text: str):
        guild = client.get_guild(self.guild_id)
        if not guild:
            return
        channel = guild.get_channel(self.panel_channel_id)
        if channel:
            try:
                await channel.send(text)
            except Exception:
                pass

    @discord.ui.button(label="✅ 許可する", style=discord.ButtonStyle.success, custom_id="approve_guild")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        client = interaction.client
        if client.owner_id is None:
            app_info = await client.application_info()
            client.owner_id = app_info.owner.id
        if interaction.user.id != client.owner_id:
            await interaction.response.send_message("このボタンはBOT所有者専用です。", ephemeral=True)
            return

        guild = client.get_guild(self.guild_id)
        guild_name = guild.name if guild else f"ID:{self.guild_id}"

        all_data = load_data()
        cfg = get_guild_config(all_data, str(self.guild_id))
        cfg["approval_status"] = "approved"
        save_data(all_data)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ **{guild_name}** の利用を許可しました。",
            embed=None,
            view=self
        )

        await self._notify_panel_channel(
            client,
            f"✅ BOT所有者がこのサーバーでの利用を**許可**しました。全機能が利用可能になりました。"
        )

    @discord.ui.button(label="❌ 拒否する", style=discord.ButtonStyle.danger, custom_id="reject_guild")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        client = interaction.client
        if client.owner_id is None:
            app_info = await client.application_info()
            client.owner_id = app_info.owner.id
        if interaction.user.id != client.owner_id:
            await interaction.response.send_message("このボタンはBOT所有者専用です。", ephemeral=True)
            return

        guild = client.get_guild(self.guild_id)
        guild_name = guild.name if guild else f"ID:{self.guild_id}"

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"❌ **{guild_name}** への導入を拒否しました。サーバーから退出します。",
            embed=None,
            view=self
        )

        if guild:
            try:
                await guild.leave()
                print(f"[許可拒否] {guild_name} (ID: {self.guild_id}) から自動退出しました。")
            except Exception as e:
                print(f"[許可拒否エラー] 退出処理に失敗しました: {e}")

        all_data = load_data()
        if str(self.guild_id) in all_data:
            del all_data[str(self.guild_id)]
            save_data(all_data)


async def send_approval_panel(guild: discord.Guild):
    """サーバー参加時に許可申請パネルを送信する"""
    channel = find_approval_panel_channel(guild)
    if not channel:
        print(f"[許可パネル] {guild.name} に送信可能なチャンネルが見つかりませんでした。")
        return

    embed = build_approval_request_embed(guild)
    view = ApprovalRequestView(guild_id=guild.id)
    try:
        await channel.send(embed=embed, view=view)
        print(f"[許可パネル] {guild.name} (#{channel.name}) に申請パネルを送信しました。")

        all_data = load_data()
        cfg = get_guild_config(all_data, str(guild.id))
        cfg["approval_panel_channel_id"] = channel.id
        save_data(all_data)
    except discord.Forbidden:
        print(f"[許可パネルエラー] {guild.name} で送信権限がありません。")
    except Exception as e:
        print(f"[許可パネルエラー] {guild.name} で予期しないエラー: {e}")


# ==================== 【オーナー専用: サーバー一覧 UI】 ====================

GUILDS_PER_PAGE = 5

def build_guild_list_embed(guilds: list, page: int) -> discord.Embed:
    """サーバー一覧ページのEmbedを生成する"""
    total_pages = max(1, (len(guilds) + GUILDS_PER_PAGE - 1) // GUILDS_PER_PAGE)
    start = page * GUILDS_PER_PAGE
    end = start + GUILDS_PER_PAGE
    page_guilds = guilds[start:end]

    embed = discord.Embed(
        title="導入中サーバー一覧",
        description=f"現在 **{len(guilds)}個** のサーバーに導入されています。",
        color=discord.Color.blurple()
    )

    for i, g in enumerate(page_guilds, start=start + 1):
        owner_text = f"<@{g.owner_id}>" if g.owner_id else "不明"
        embed.add_field(
            name=f"{i}. {g.name}",
            value=(
                f"ID: `{g.id}`\n"
                f"メンバー: **{g.member_count}人** | "
                f"オーナー: {owner_text}"
            ),
            inline=False
        )

    embed.set_footer(text=f"ページ {page + 1} / {total_pages}")
    return embed


class GuildLeaveConfirmView(discord.ui.View):
    """脱退確認用ビュー（2段階確認）"""
    def __init__(self, guild: discord.Guild, original_view: "GuildListView"):
        super().__init__(timeout=60)
        self.guild = guild
        self.original_view = original_view

    @discord.ui.button(label="本当に脱退する", style=discord.ButtonStyle.danger)
    async def confirm_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_name = self.guild.name
        try:
            await self.guild.leave()
            await interaction.response.edit_message(
                content=f"■ **{guild_name}** から脱退しました。",
                embed=None,
                view=None
            )
        except discord.HTTPException as e:
            await interaction.response.edit_message(
                content=f"■ 脱退に失敗しました: `{e}`",
                embed=None,
                view=None
            )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        guilds = list(interaction.client.guilds)
        embed = build_guild_list_embed(guilds, self.original_view.page)
        self.original_view.update_buttons(guilds)
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self.original_view
        )


class GuildSelectForLeave(discord.ui.Select):
    """現在ページのサーバーから脱退対象を選択するセレクトメニュー"""
    def __init__(self, guilds: list, page: int):
        start = page * GUILDS_PER_PAGE
        end = start + GUILDS_PER_PAGE
        page_guilds = guilds[start:end]

        options = [
            discord.SelectOption(
                label=g.name[:100],
                description=f"ID: {g.id} | メンバー: {g.member_count}人",
                value=str(g.id)
            )
            for g in page_guilds
        ]
        super().__init__(
            placeholder="脱退するサーバーを選択...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        guild = interaction.client.get_guild(guild_id)

        if not guild:
            await interaction.response.send_message("サーバーが見つかりませんでした。", ephemeral=True)
            return

        confirm_embed = discord.Embed(
            title="サーバー脱退の確認",
            description=(
                f"以下のサーバーから本当に脱退しますか？\n\n"
                f"**サーバー名:** {guild.name}\n"
                f"**サーバーID:** `{guild.id}`\n"
                f"**メンバー数:** {guild.member_count}人\n\n"
                f"※この操作は **取り消せません。**"
            ),
            color=discord.Color.red()
        )
        if guild.icon:
            confirm_embed.set_thumbnail(url=guild.icon.url)

        parent_view = self.view
        await interaction.response.edit_message(
            embed=confirm_embed,
            view=GuildLeaveConfirmView(guild, parent_view)
        )


class GuildListView(discord.ui.View):
    """サーバー一覧のページネーション + 脱退選択ビュー"""
    def __init__(self, guilds: list, page: int = 0):
        super().__init__(timeout=300)
        self.page = page
        self.guilds = guilds
        self._rebuild_select()
        self.update_buttons(guilds)

    def _rebuild_select(self):
        items_to_remove = [item for item in self.children if isinstance(item, GuildSelectForLeave)]
        for item in items_to_remove:
            self.remove_item(item)
        if self.guilds:
            self.add_item(GuildSelectForLeave(self.guilds, self.page))

    def update_buttons(self, guilds: list):
        total_pages = max(1, (len(guilds) + GUILDS_PER_PAGE - 1) // GUILDS_PER_PAGE)
        self.prev_button.disabled = (self.page <= 0)
        self.next_button.disabled = (self.page >= total_pages - 1)

    @discord.ui.button(label="◀ 前へ", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self.update_buttons(self.guilds)
        embed = build_guild_list_embed(self.guilds, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="次へ ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self.update_buttons(self.guilds)
        embed = build_guild_list_embed(self.guilds, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="更新", style=discord.ButtonStyle.primary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self.update_buttons(self.guilds)
        embed = build_guild_list_embed(self.guilds, self.page)
        await interaction.response.edit_message(embed=embed, view=self)


# ==================== 【その他UI部品】 ====================

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
                    await interaction.response.send_message(f"{role.name} ロールを付与しました。", ephemeral=True)
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
            await interaction.response.send_message("認証が完了しました。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)


@bot.event
async def on_ready():
    bot.add_view(VerifyButtonView())
    all_data = load_data()

    # ◆ 追加: 再起動後も許可申請パネル/承認DMのボタンを有効化する
    for guild_id_str, config in all_data.items():
        if guild_id_str == "user_apps":
            continue
        if config.get("approval_status") in ("pending", "pending_review"):
            bot.add_view(ApprovalRequestView(guild_id=int(guild_id_str)))
            panel_ch_id = config.get("approval_panel_channel_id") or 0
            bot.add_view(ApprovalDecisionView(guild_id=int(guild_id_str), panel_channel_id=panel_ch_id))

    if bot.owner_id is None:
        try:
            app_info = await bot.application_info()
            bot.owner_id = app_info.owner.id
            print(f"[システム] オーナーIDを確定しました: {bot.owner_id}")
        except Exception as e:
            print(f"[警告] オーナー情報の取得に失敗しました: {e}")

    try:
        await update_bot_status(bot)
    except Exception as e:
        print(f"初期ステータス設定エラー: {e}")
    
    print("--- 起動完了: 現在のサーバー設定一覧 ---")
    for guild_id_str, config in all_data.items():
        if guild_id_str == "user_apps":
            continue
        
        guild = bot.get_guild(int(guild_id_str))
        guild_name = guild.name if guild else "不明なサーバー"
        print(f"サーバー: {guild_name} (ID: {guild_id_str})")
        print(f"  > 承認状態: {config.get('approval_status', 'pending')}")
        print(f"  > Message転送: {'有効' if config.get('from_channel') else '未設定'}")
        print(f"  > サーバー認証: {'有効' if config.get('verify_channel') else '未設定'}")
        print(f"  > 配信お知らせ: {'有効' if config.get('announce_channel') else '未設定'}")
        print(f"  > 自動メンション: {'有効' if config.get('mention_trigger_channel') else '未設定'}")
        
        panel_roles = config.get("panel_roles", [])
        if panel_roles and guild:
            roles = [guild.get_role(rid) for rid in panel_roles if guild.get_role(rid)]
            if roles: 
                bot.add_view(DynamicRoleView(roles))
                print(f"  > ロールパネル: {len(roles)}個の取得ボタンを再活性化しました")
    print("---------------------------------------")
    print(f"ログインユーザー: {bot.user.name} (ID: {bot.user.id})")
    print("スラッシュコマンドを更新したい場合は、サーバー上で '!sync' と発言してください。")


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[サーバー参加] {guild.name} (ID: {guild.id}) に導入されました。")
    await update_bot_status(bot)

    # ◆ 追加: 未許可状態として登録し、許可申請パネルを送信する
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))
    cfg["approval_status"] = "pending"
    save_data(all_data)

    await send_approval_panel(guild)

@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[サーバー脱退] {guild.name} (ID: {guild.id}) から削除されました。")
    await update_bot_status(bot)


@bot.command(name="sync")
@commands.is_owner()
async def sync_command(ctx):
    """
    使い方:
      !sync       → このサーバーに即時同期（数秒で反映・テスト用）
      !sync global → 全サーバーにグローバル同期（反映まで最大1時間）
      !sync clear  → このサーバーのギルドコマンドをクリア（グローバルのみに戻す）
    """
    arg = ctx.message.content.replace("!sync", "").strip().lower()

    if arg == "global":
        await ctx.send("全サーバーへグローバル同期中... 反映まで最大1時間かかります。")
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"■ グローバル同期完了: {len(synced)}個のコマンドを同期しました。")
        except discord.errors.HTTPException as e:
            await ctx.send(f"■ Discord側で制限がかかっています。5〜10分後に再試行してください。\n`{e}`")

    elif arg == "clear":
        if not ctx.guild:
            await ctx.send("このコマンドはサーバー内で実行してください。")
            return
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"このサーバーのギルドコマンドをクリアしました。グローバルコマンドのみが有効です。")

    else:
        if not ctx.guild:
            await ctx.send("サーバー内で実行してください。グローバル同期は `!sync global` を使用してください。")
            return
        await ctx.send("このサーバーへ即時同期中...")
        try:
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                f"■ このサーバーへの即時同期が完了しました（{len(synced)}個）。\n"
                f"すぐに `/` で確認できます。\n"
                f"※全サーバーへ反映したい場合は `!sync global` を実行してください（最大1時間）。"
            )
        except discord.errors.HTTPException as e:
            await ctx.send(f"■ 同期に失敗しました。\n`{e}`")

@sync_command.error
async def sync_command_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("このコマンドはBotの所有者（オーナー）のみ実行できます。")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild: return
    guild_id_str = str(message.guild.id)
    all_data = load_data()
    
    if guild_id_str in all_data:
        guild_config = all_data[guild_id_str]

        # ◆ 追加: 未許可サーバーでは自動応答系の機能も停止する
        if guild_config.get("approval_status") != "approved":
            await bot.process_commands(message)
            return
        
        # 1. 管理者のメッセージ自動転送
        from_id = guild_config.get("from_channel")
        to_id = guild_config.get("to_channel")
        if from_id and to_id and message.channel.id == from_id:
            if message.author.guild_permissions.administrator:
                to_channel = message.guild.get_channel(to_id)
                if to_channel: await to_channel.send(message.content)
                
        # 2. 自動返信＋投稿された文章を通知する機能
        trigger_ch_id = guild_config.get("mention_trigger_channel")
        target_role_id = guild_config.get("mention_target_role")
        custom_msg = guild_config.get("mention_custom_message", "新しい書き込みがありました！")
        
        if trigger_ch_id and target_role_id and message.channel.id == trigger_ch_id:
            role = message.guild.get_role(target_role_id)
            if role:
                content_text = f"\n>>> {message.content}" if message.content else ""
                full_reply_text = f"{role.mention} {custom_msg}{content_text}"
                
                if len(full_reply_text) > 2000:
                    full_reply_text = full_reply_text[:1997] + "..."
                
                try:
                    await message.reply(
                        full_reply_text, 
                        allowed_mentions=discord.AllowedMentions(roles=[role])
                    )
                    print(f"[自動返信成功] ch: #{message.channel.name} でロール @{role.name} 宛てに送信しました。")
                except discord.Forbidden:
                    try:
                        await message.channel.send(
                            full_reply_text,
                            allowed_mentions=discord.AllowedMentions(roles=[role])
                        )
                        print(f"[自動返信フォールバック] 返信権限がないため、通常メッセージとして送信しました。")
                    except Exception as e:
                        print(f"[自動返信エラー] 通常送信も失敗しました。Botの権限を確認してください: {e}")
                except Exception as e:
                    print(f"[自動返信エラー] 不明なエラーが発生しました: {e}")

    await bot.process_commands(message)


# ==================== 【一般ユーザー・プレイヤー向け機能】 ====================

@bot.tree.command(name="help", description="あなたが利用可能なコマンド一覧をカテゴリ別に表示します")
async def help_command(interaction: discord.Interaction):
    is_owner = False
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id
    if interaction.user.id == interaction.client.owner_id:
        is_owner = True

    is_admin = False
    is_allowed = False
    if interaction.guild:
        if interaction.user.guild_permissions.administrator:
            is_admin = True
        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        if interaction.user.id in cfg.get("allowed_users", []):
            is_allowed = True

    embed = discord.Embed(
        title="マクマクBOT コマンド一覧",
        description="あなたがこのサーバーで利用できるスラッシュコマンドの一覧です。",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="▼ 一般ユーザー向け機能",
        value=(
            "`/help` : このコマンド一覧をあなただけに表示します\n"
            "`/hello` : Botが挨拶を返します\n"
            "`/search` : 各種検索サイトやWikipediaのリンク・概要を生成します\n"
            "`/my_scan` : サーバー情報、または指定ユーザーの基本情報を確認します"
        ),
        inline=False
    )
    
    embed.add_field(
        name="▼ 個人用プライベート機能 (他の人には見えません)",
        value=(
            "`/my_memo` : あなた専用の個人メモを追加・一覧表示・削除・全消去します\n"
            "`/my_clip` : あなた専用のクリップ（テキストやリンク）を保存・管理します"
        ),
        inline=False
    )
    
    if is_admin or is_allowed or is_owner:
        embed.add_field(
            name="▼ 管理者・許可ユーザー専用コマンド",
            value=(
                "`/my_scan_channels` : サーバーのチャンネル構造とカスタム権限をスキャンします\n"
                "`/my_audit_perms` : @everyone の不適切な権限をスキャンします\n"
                "`/my_check_url` : URLの安全性をVirusTotalでチェックします\n"
                "`/server_bot_check` : サーバーに導入されているBOTの権限を一括スキャンします\n"
                "`/backup_create` : サーバー構造（ロール/カテゴリ/チャンネル/権限）をJSONバックアップします\n"
                "`/say` : Botに指定したメッセージを代わりに発言させます"
            ),
            inline=False
        )
    
    if is_admin or is_owner:
        embed.add_field(
            name="▼ サーバー管理者専用コマンド",
            value=(
                "`/server_status` : 現在の各種機能の設定状況を確認します\n"
                "`/server_list_users` : コマンド使用許可リストの確認・編集を行います\n"
                "`/server_create_channel` : 新しいテキストチャンネルを作成します\n"
                "`/server_role_panel` : 指定ロールを取得できるボタン付きパネルを設置します\n"
                "`/server_forward_setup` / `reset` : メッセージ自動転送の設定を行います\n"
                "`/server_announce_setup` / `send` : 配信お知らせ機能の設定と送信を行います\n"
                "`/server_verify_setup` / `btn` : メンバー認証用パネルを設置します\n"
                "`/server_mention_setup` / `reset` : 自動返信ロールメンションの設定と解除を行います"
            ),
            inline=False
        )
    
    if is_owner:
        embed.add_field(
            name="▼ BOT所有者専用コマンド",
            value=(
                "`!sync` : スラッシュコマンドをDiscord側へ即時同期します (通常チャット形式)\n"
                "`/owner_status` : Botの視聴中ステータス文字をリアルタイムで変更します\n"
                "`/owner_guilds` : 導入中のサーバー一覧を確認し、任意のサーバーから脱退できます\n"
                "`/owner_guild_detail` : サーバーの詳細情報（ch数・ロール数・Bot設定状況）と招待リンクを取得します\n"
                "`/owner_broadcast` : 指定サーバーにEmbedでお知らせを一斉送信します"
            ),
            inline=False
        )
    
    embed.set_footer(text="※セキュリティのため、このヘルプは実行したあなたにのみ見えています。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="hello", description="Botが挨拶を返します")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"こんにちは、{interaction.user.mention}さん。")


@bot.tree.command(name="say", description="Botに指定したメッセージを発言させます")
async def say(interaction: discord.Interaction, message: str):
    if not await is_admin_or_allowed(interaction): return
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


@bot.tree.command(name="my_scan", description="サーバー情報、または指定ユーザーの基本情報を確認します")
async def my_scan(interaction: discord.Interaction, target_user: discord.User = None):
    if target_user:
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

    await interaction.response.send_message(embed=embed, ephemeral=False)


# ==================== 【個人用プライベート機能】 ====================

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
        embed.description = "\n".join([f"・{b}" for b in bks]) if bks else "保存されているクリップはありません。"
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif act == "clear":
        user_data["bookmarks"] = []
        save_data(all_data)
        await interaction.response.send_message("全ての個人クリップを消去しました。", ephemeral=True)


# ==================== 【BOT所有者（オーナー）専用コマンド】 ====================

@bot.tree.command(name="owner_status", description="【オーナー限定】Botの視聴中ステータスの文字をリアルタイムで変更します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_status(interaction: discord.Interaction, text: str):
    if not await is_owner_check(interaction): return
    try:
        if text.lower() == "reset":
            global current_custom_status
            current_custom_status = None
            await update_bot_status(bot)
            await interaction.response.send_message("ステータスをデフォルト（サーバー数カウント）にリセットしました。", ephemeral=True)
        else:
            await update_bot_status(bot, text)
            await interaction.response.send_message(f"Botのステータスを「{text} を視聴中」に変更しました。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"ステータスの変更中にエラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="owner_guilds", description="【オーナー限定】導入中のサーバー一覧を表示し、任意のサーバーから脱退できます")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_guilds(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return

    guilds = list(interaction.client.guilds)

    if not guilds:
        await interaction.response.send_message("現在、どのサーバーにも導入されていません。", ephemeral=True)
        return

    embed = build_guild_list_embed(guilds, page=0)
    view = GuildListView(guilds, page=0)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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
                report.append(f"・{ch.mention} -> {', '.join(roles[:3])}")
    if count == 0: report.append("個別設定されたチャンネルはありません。")
    full_rep = "\n".join(report)
    await interaction.followup.send(embed=discord.Embed(title="フルスキャン結果", description=full_rep[:1950], color=discord.Color.red()), ephemeral=True)


@bot.tree.command(name="my_audit_perms", description="@everyoneの権限設定をスキャン")
async def my_audit_perms(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return
    await interaction.response.defer(ephemeral=False)
    
    report = []
    for channel in interaction.guild.text_channels:
        everyone_perms = channel.permissions_for(interaction.guild.default_role)
        issues = []
        if everyone_perms.view_channel: issues.append("閲覧")
        if everyone_perms.send_messages: issues.append("送信")
        if issues: report.append(f"注意 {channel.mention} : @everyone に「{', '.join(issues)}」権限があります")
            
    if not report:
        await interaction.followup.send("チェック完了: @everyone に不適切な権限はありません。", ephemeral=False)
    else:
        embed = discord.Embed(title="権限スキャン結果", description="以下のチャンネルの設定を確認してください：\n\n" + "\n".join(report), color=discord.Color.red())
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


# Bot権限スキャンで「注意すべき権限」として表示する項目
# (属性名, 表示名)
WATCHED_BOT_PERMISSIONS = [
    ("administrator", "管理者"),
    ("ban_members", "メンバーBAN"),
    ("kick_members", "メンバーキック"),
    ("manage_guild", "サーバー管理"),
    ("manage_roles", "ロール管理"),
    ("manage_channels", "チャンネル管理"),
    ("manage_webhooks", "Webhook管理"),
    ("manage_messages", "メッセージ管理"),
    ("mention_everyone", "全員メンション"),
]


@bot.tree.command(name="server_bot_check", description="サーバーに導入されているBOTの権限をスキャンします")
async def server_bot_check(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)
    g = interaction.guild

    bot_members = [m for m in g.members if m.bot]

    if not bot_members:
        await interaction.followup.send("このサーバーにはBOTが導入されていません。", ephemeral=True)
        return

    danger_lines = []
    normal_lines = []

    for m in bot_members:
        perms = m.guild_permissions
        watched = [label for attr, label in WATCHED_BOT_PERMISSIONS if getattr(perms, attr, False)]

        if perms.administrator:
            # 管理者権限を持つBOTは危険として強調
            danger_lines.append(f"🔴 **{m.mention}** (`{m.name}`)\n　└ 危険: **管理者権限を保有**（全権限と同等）")
        elif watched:
            normal_lines.append(f"🟡 {m.mention} (`{m.name}`)\n　└ 権限: {', '.join(watched)}")
        else:
            normal_lines.append(f"🟢 {m.mention} (`{m.name}`)\n　└ 注意すべき権限はありません")

    embed = discord.Embed(
        title=f"{g.name} - BOT権限スキャン結果",
        description=f"導入されているBOT: **{len(bot_members)}体**",
        color=discord.Color.red() if danger_lines else discord.Color.green()
    )

    if danger_lines:
        embed.add_field(
            name=f"⚠️ 管理者権限を持つBOT ({len(danger_lines)}体)",
            value="\n".join(danger_lines)[:1024],
            inline=False
        )

    if normal_lines:
        # Discord embedのフィールド上限(1024文字)を考慮して分割
        chunk = []
        chunk_len = 0
        chunk_idx = 1
        for line in normal_lines:
            if chunk_len + len(line) + 1 > 1024:
                embed.add_field(name=f"その他のBOT ({chunk_idx})", value="\n".join(chunk), inline=False)
                chunk = []
                chunk_len = 0
                chunk_idx += 1
            chunk.append(line)
            chunk_len += len(line) + 1
        if chunk:
            embed.add_field(name=f"その他のBOT ({chunk_idx})" if chunk_idx > 1 else "その他のBOT", value="\n".join(chunk), inline=False)

    if danger_lines:
        embed.set_footer(text="管理者権限を持つBOTは、サーバー設定の改変やチャンネル削除など全操作が可能です。信頼できないBOTであれば権限の見直しを推奨します。")

    await interaction.followup.send(embed=embed, ephemeral=True)


# ==================== 【サーバー管理者専用コマンド (要・管理者権限)】 ====================
# ◆ @app_commands.default_permissions(administrator=True) を全て削除し、
#    is_guild_admin() による手動チェックに統一。
#    これによりBOTオーナーもサーバー管理者権限なしで全コマンドを利用可能。

@bot.tree.command(name="server_status", description="現在のサーバー設定状況を確認します")
async def server_status(interaction: discord.Interaction):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    g = interaction.guild
    g_id_str = str(g.id)
    all_data = load_data()
    cfg = get_guild_config(all_data, g_id_str)

    embed = discord.Embed(title=f"{g.name} - 設定状況", description="このサーバーで有効化されている設定一覧です。", color=discord.Color.blue())
    if g.icon: embed.set_thumbnail(url=g.icon.url)

    approval_status = cfg.get("approval_status", "pending")
    approval_label = {"approved": "✅ 許可済み", "pending_review": "⏳ 所有者確認待ち", "pending": "🔒 未申請"}.get(approval_status, approval_status)
    embed.add_field(name="BOT利用許可状態", value=approval_label, inline=False)

    from_ch = g.get_channel(cfg.get("from_channel")) if cfg.get("from_channel") else None
    to_ch = g.get_channel(cfg.get("to_channel")) if cfg.get("to_channel") else None
    forward_status = f"有効\n・転送元: {from_ch.mention if from_ch else '削除済'}\n・転送先: {to_ch.mention if to_ch else '削除済'}" if (from_ch or to_ch) else "未設定"
    embed.add_field(name="メッセージ転送設定", value=forward_status, inline=False)

    v_ch = g.get_channel(cfg.get("verify_channel")) if cfg.get("verify_channel") else None
    v_role = g.get_role(cfg.get("verify_role")) if cfg.get("verify_role") else None
    verify_status = f"有効\n・設置ch: {v_ch.mention if v_ch else '削除済'}\n・付与ロール: {v_role.mention if v_role else '削除済'}" if (v_ch or v_role) else "未設定"
    embed.add_field(name="サーバー認証設定", value=verify_status, inline=False)

    a_ch = g.get_channel(cfg.get("announce_channel")) if cfg.get("announce_channel") else None
    a_role = g.get_role(cfg.get("announce_role")) if cfg.get("announce_role") else None
    announce_status = f"有効\n・お知らせch: {a_ch.mention if a_ch else '削除済'}\n・メンション対象: {a_role.mention if a_role else '削除済'}" if (a_ch or a_role) else "未設定"
    embed.add_field(name="配信・お知らせ設定", value=announce_status, inline=False)

    m_ch = g.get_channel(cfg.get("mention_trigger_channel")) if cfg.get("mention_trigger_channel") else None
    m_role = g.get_role(cfg.get("mention_target_role")) if cfg.get("mention_target_role") else None
    m_msg = cfg.get("mention_custom_message", "未設定（デフォルト文章）")
    mention_status = f"有効\n・監視ch: {m_ch.mention if m_ch else '削除済'}\n・通知ロール: {m_role.mention if m_role else '削除済'}\n・返信テキスト: `{m_msg}`" if (m_ch or m_role) else "未設定"
    embed.add_field(name="自動返信ロールメンション設定", value=mention_status, inline=False)

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
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    g_id = str(interaction.guild.id)
    all_data = load_data()
    config = get_guild_config(all_data, g_id)
    embed = create_user_list_embed(config.get("allowed_users", []))
    await interaction.response.send_message(embed=embed, view=UserManageView(), ephemeral=True)


@bot.tree.command(name="server_create_channel", description="新しいテキストチャンネルを作成します")
async def server_create_channel(interaction: discord.Interaction, name: str, category: discord.CategoryChannel = None):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)
    try:
        new_ch = await interaction.guild.create_text_channel(name=name, category=category)
        await interaction.followup.send(f"チャンネル {new_ch.mention} を作成しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"作成失敗: {e}", ephemeral=True)


@bot.tree.command(name="server_role_panel", description="指定したロール（最大5つ）を取得できるボタン付きパネルを送信します")
async def server_role_panel(
    interaction: discord.Interaction, title: str, description: str,
    role1: discord.Role, role2: discord.Role = None, role3: discord.Role = None, role4: discord.Role = None, role5: discord.Role = None
):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)
    ch = interaction.channel
    g = interaction.guild
    
    raw_roles = [role1, role2, role3, role4, role5]
    roles = [r for r in raw_roles if r is not None]
    
    all_data = load_data()
    get_guild_config(all_data, str(g.id))["panel_roles"] = [r.id for r in roles]
    save_data(all_data)
    
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    role_mentions = [f"{r.mention}" for r in roles]
    embed.add_field(name="対象ロール一覧", value="\n\n".join(role_mentions), inline=False)
    
    await ch.send(embed=embed, view=DynamicRoleView(roles))
    await interaction.followup.send("ロールパネルを設置しました。", ephemeral=True)


@bot.tree.command(name="server_forward_setup", description="メッセージ転送元のチャンネルと転送先を設定します")
async def server_forward_setup(interaction: discord.Interaction, from_channel: discord.TextChannel, to_channel: discord.TextChannel):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = from_channel.id, to_channel.id
    save_data(all_data)
    await interaction.response.send_message("転送設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_forward_reset", description="チャンネルの転送設定を解除します")
async def server_forward_reset(interaction: discord.Interaction):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = None, None
    save_data(all_data)
    await interaction.response.send_message("転送設定を解除しました。", ephemeral=True)


@bot.tree.command(name="server_announce_setup", description="お知らせ用のチャンネルとロールを設定します")
async def server_announce_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["announce_channel"], cfg["announce_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("お知らせ設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_announce_send", description="設定されたチャンネルにロールメンション付きでお知らせを送信します")
async def server_announce_send(interaction: discord.Interaction, message: str):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

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
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["verify_channel"], cfg["verify_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("認証設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_verify_btn", description="設定されたチャンネルに認証用ボタンパネルを送信します")
async def server_verify_btn(interaction: discord.Interaction, title: str = "サーバー認証", description: str = "ボタンを押すと認証が完了します。", image_file: discord.Attachment = None):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

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


# ==================== 【自動返信ロールメンション設定コマンド】 ====================

@bot.tree.command(name="server_mention_setup", description="指定chへの投稿時、指定メッセージ＆指定ロールで元の文章を含めて返信（Reply）します")
async def server_mention_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role, text: str):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)

    try:
        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        cfg["mention_trigger_channel"] = channel.id
        cfg["mention_target_role"] = role.id
        cfg["mention_custom_message"] = text  
        save_data(all_data)
        
        await interaction.followup.send(
            f"自動返信ロールメンション（本文引用付き）を構築しました！\n"
            f"・監視チャンネル: {channel.mention}\n"
            f"・通知するロール: {role.mention}\n"
            f"・返信するテキスト: `{text}`\n"
            f"※誰かが書き込むと、Botがメッセージを引用しながらロールメンションを付けてインライン返信します。", 
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="server_mention_reset", description="自動ロールメンションの監視・返信設定を解除します")
async def server_mention_reset(interaction: discord.Interaction):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)

    try:
        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        cfg["mention_trigger_channel"] = None
        cfg["mention_target_role"] = None
        cfg["mention_custom_message"] = None
        save_data(all_data)
        
        await interaction.followup.send("自動返信ロールメンションの設定を解除しました。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"設定の解除中にエラーが発生しました: {e}", ephemeral=True)


# ==================== 【荒らし対策: サーバー構造バックアップ】 ====================

def _overwrites_to_dict(overwrites: dict) -> list:
    """チャンネル/カテゴリのpermission_overwritesをJSON化可能な形に変換する"""
    result = []
    for target, ow in overwrites.items():
        allow, deny = ow.pair()
        entry = {
            "target_type": "role" if isinstance(target, discord.Role) else "member",
            "target_id": target.id,
            "target_name": target.name if isinstance(target, discord.Role) else str(target),
            "allow": [perm for perm, value in allow if value],
            "deny": [perm for perm, value in deny if value],
        }
        result.append(entry)
    return result


def build_guild_backup_data(g: discord.Guild) -> dict:
    """サーバーのロール・カテゴリ・チャンネル・権限構造をdictにまとめる"""
    backup = {
        "backup_version": 1,
        "created_at": discord.utils.utcnow().isoformat(),
        "guild_id": g.id,
        "guild_name": g.name,
    }

    # ロール（@everyoneも含む。position順）
    roles_data = []
    for r in sorted(g.roles, key=lambda r: r.position):
        roles_data.append({
            "id": r.id,
            "name": r.name,
            "color": r.color.value,
            "hoist": r.hoist,
            "mentionable": r.mentionable,
            "permissions": r.permissions.value,
            "position": r.position,
            "is_default": r.is_default(),
        })
    backup["roles"] = roles_data

    # カテゴリ
    categories_data = []
    for cat in sorted(g.categories, key=lambda c: c.position):
        categories_data.append({
            "id": cat.id,
            "name": cat.name,
            "position": cat.position,
            "overwrites": _overwrites_to_dict(cat.overwrites),
        })
    backup["categories"] = categories_data

    # テキストチャンネル
    text_channels_data = []
    for ch in sorted(g.text_channels, key=lambda c: c.position):
        text_channels_data.append({
            "id": ch.id,
            "name": ch.name,
            "category_id": ch.category_id,
            "position": ch.position,
            "topic": ch.topic,
            "nsfw": ch.nsfw,
            "slowmode_delay": ch.slowmode_delay,
            "overwrites": _overwrites_to_dict(ch.overwrites),
        })
    backup["text_channels"] = text_channels_data

    # ボイスチャンネル
    voice_channels_data = []
    for ch in sorted(g.voice_channels, key=lambda c: c.position):
        voice_channels_data.append({
            "id": ch.id,
            "name": ch.name,
            "category_id": ch.category_id,
            "position": ch.position,
            "bitrate": ch.bitrate,
            "user_limit": ch.user_limit,
            "overwrites": _overwrites_to_dict(ch.overwrites),
        })
    backup["voice_channels"] = voice_channels_data

    # フォーラム/Stage等その他のチャンネル種別も念のため記録
    other_channels_data = []
    known_ids = {c["id"] for c in text_channels_data} | {c["id"] for c in voice_channels_data} | {c["id"] for c in categories_data}
    for ch in g.channels:
        if ch.id in known_ids:
            continue
        other_channels_data.append({
            "id": ch.id,
            "name": ch.name,
            "type": str(ch.type),
            "category_id": ch.category_id,
            "position": getattr(ch, "position", None),
            "overwrites": _overwrites_to_dict(ch.overwrites) if hasattr(ch, "overwrites") else [],
        })
    backup["other_channels"] = other_channels_data

    backup["summary"] = {
        "role_count": len(roles_data),
        "category_count": len(categories_data),
        "text_channel_count": len(text_channels_data),
        "voice_channel_count": len(voice_channels_data),
        "other_channel_count": len(other_channels_data),
    }

    return backup


@bot.tree.command(name="backup_create", description="サーバーのロール・カテゴリ・チャンネル・権限構造をJSONバックアップします（nuke荒らし対策）")
async def backup_create(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)
    g = interaction.guild

    try:
        backup_data = build_guild_backup_data(g)
    except Exception as e:
        await interaction.followup.send(f"バックアップの作成中にエラーが発生しました: {e}", ephemeral=True)
        return

    json_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    file_buffer = io.BytesIO(json_bytes)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{g.id}_{timestamp}.json"
    discord_file = discord.File(fp=file_buffer, filename=filename)

    summary = backup_data["summary"]
    embed = discord.Embed(
        title=f"{g.name} - バックアップ作成完了",
        description="サーバー構造（ロール・カテゴリ・チャンネル・権限）をJSONに保存しました。\n荒らし被害時の復旧用に、安全な場所に保管してください。",
        color=discord.Color.green()
    )
    embed.add_field(name="ロール数", value=f"{summary['role_count']}個", inline=True)
    embed.add_field(name="カテゴリ数", value=f"{summary['category_count']}個", inline=True)
    embed.add_field(name="テキストCh", value=f"{summary['text_channel_count']}個", inline=True)
    embed.add_field(name="ボイスCh", value=f"{summary['voice_channel_count']}個", inline=True)
    if summary["other_channel_count"]:
        embed.add_field(name="その他Ch", value=f"{summary['other_channel_count']}個", inline=True)
    embed.add_field(name="作成日時", value=discord.utils.format_dt(discord.utils.utcnow(), style="F"), inline=False)
    embed.set_footer(text="※このバックアップには権限の構造のみが含まれます。メッセージ内容や絵文字・スタンプは含まれません。")

    await interaction.followup.send(embed=embed, file=discord_file, ephemeral=True)


# ==================== 【オーナー専用: サーバー詳細情報 & 招待リンク取得】 ====================

class GuildDetailSelect(discord.ui.Select):
    """詳細確認するサーバーを選択するセレクトメニュー"""
    def __init__(self, guilds: list):
        options = [
            discord.SelectOption(
                label=g.name[:100],
                description=f"メンバー: {g.member_count}人 | ID: {g.id}",
                value=str(g.id)
            )
            for g in guilds[:25]
        ]
        super().__init__(
            placeholder="詳細を確認するサーバーを選択...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        guild = interaction.client.get_guild(guild_id)

        if not guild:
            await interaction.response.send_message("サーバーが見つかりませんでした。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        invite_url = "取得失敗（権限不足またはチャンネルなし）"
        try:
            for ch in guild.text_channels:
                perms = ch.permissions_for(guild.me)
                if perms.create_instant_invite:
                    invite = await ch.create_invite(
                        max_age=3600,
                        max_uses=1,
                        unique=True,
                        reason="オーナーによる招待リンク取得"
                    )
                    invite_url = invite.url
                    break
        except discord.Forbidden:
            invite_url = "権限不足のため取得できませんでした"
        except Exception as e:
            invite_url = f"エラー: {e}"

        embed = discord.Embed(
            title=f"{guild.name} の詳細情報",
            color=discord.Color.blurple()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        owner = guild.owner
        owner_text = f"{owner} (`{owner.id}`)" if owner else f"<@{guild.owner_id}>"
        embed.add_field(name="サーバー名", value=guild.name, inline=True)
        embed.add_field(name="サーバーID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="オーナー", value=owner_text, inline=True)

        total = guild.member_count
        bots = sum(1 for m in guild.members if m.bot)
        humans = total - bots
        embed.add_field(
            name="メンバー数",
            value=f"総計: **{total}人**\n└ 人間: {humans}人 / Bot: {bots}体",
            inline=True
        )

        text_ch = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        category_ch = len(guild.categories)
        embed.add_field(
            name="チャンネル数",
            value=f"テキスト: {text_ch} / ボイス: {voice_ch}\nカテゴリー: {category_ch}",
            inline=True
        )

        role_count = len(guild.roles) - 1
        embed.add_field(name="ロール数", value=f"{role_count}個", inline=True)

        embed.add_field(
            name="ブースト状況",
            value=f"Lv.{guild.premium_tier} ({guild.premium_subscription_count}回)",
            inline=True
        )

        embed.add_field(
            name="サーバー作成日",
            value=discord.utils.format_dt(guild.created_at, style="F"),
            inline=False
        )

        all_data = load_data()
        cfg = get_guild_config(all_data, str(guild.id))
        approval_status = cfg.get("approval_status", "pending")
        approval_label = {"approved": "✅ 許可済み", "pending_review": "⏳ 確認待ち", "pending": "🔒 未申請"}.get(approval_status, approval_status)
        settings = [
            f"利用許可: {approval_label}",
            f"{'■' if cfg.get('from_channel') else '―'} メッセージ転送",
            f"{'■' if cfg.get('verify_channel') else '―'} サーバー認証",
            f"{'■' if cfg.get('announce_channel') else '―'} 配信お知らせ",
            f"{'■' if cfg.get('mention_trigger_channel') else '―'} 自動返信メンション",
            f"{'■' if cfg.get('panel_roles') else '―'} ロールパネル",
            f"許可ユーザー: {len(cfg.get('allowed_users', []))}人",
        ]
        embed.add_field(name="Bot設定状況", value="\n".join(settings), inline=False)

        embed.add_field(
            name="招待リンク（1時間有効・1回限り）",
            value=invite_url,
            inline=False
        )
        embed.set_footer(text="取得日時")
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=True)


class GuildDetailView(discord.ui.View):
    """サーバー詳細確認用ビュー（ページネーション付き）"""
    def __init__(self, guilds: list, page: int = 0):
        super().__init__(timeout=300)
        self.guilds = guilds
        self.page = page
        self._rebuild_select()
        self._update_buttons()

    def _get_page_guilds(self):
        start = self.page * 25
        return self.guilds[start:start + 25]

    def _rebuild_select(self):
        for item in [i for i in self.children if isinstance(i, GuildDetailSelect)]:
            self.remove_item(item)
        page_guilds = self._get_page_guilds()
        if page_guilds:
            self.add_item(GuildDetailSelect(page_guilds))

    def _update_buttons(self):
        total_pages = max(1, (len(self.guilds) + 24) // 25)
        self.prev_btn.disabled = (self.page <= 0)
        self.next_btn.disabled = (self.page >= total_pages - 1)

    @discord.ui.button(label="◀ 前へ", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self._update_buttons()
        total_pages = max(1, (len(self.guilds) + 24) // 25)
        await interaction.response.edit_message(
            content=f"サーバーを選択してください（ページ {self.page + 1}/{total_pages}）",
            view=self
        )

    @discord.ui.button(label="次へ ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self._update_buttons()
        total_pages = max(1, (len(self.guilds) + 24) // 25)
        await interaction.response.edit_message(
            content=f"サーバーを選択してください（ページ {self.page + 1}/{total_pages}）",
            view=self
        )


@bot.tree.command(name="owner_guild_detail", description="【オーナー限定】サーバーの詳細情報と招待リンクを取得します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_guild_detail(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return

    guilds = list(interaction.client.guilds)
    if not guilds:
        await interaction.response.send_message("現在、どのサーバーにも導入されていません。", ephemeral=True)
        return

    total_pages = max(1, (len(guilds) + 24) // 25)
    view = GuildDetailView(guilds, page=0)
    await interaction.response.send_message(
        f"詳細を確認したいサーバーを選択してください（ページ 1/{total_pages}）",
        view=view,
        ephemeral=True
    )


# ==================== 【オーナー専用: 全サーバー一括お知らせ送信】 ====================

BROADCAST_COLORS = {
    "ブルー":   discord.Color.blue(),
    "グリーン": discord.Color.green(),
    "レッド":   discord.Color.red(),
    "ゴールド": discord.Color.gold(),
    "パープル": discord.Color.purple(),
    "グレー":   discord.Color.greyple(),
}


class BroadcastEmbedModal(discord.ui.Modal, title="お知らせ内容を入力"):
    embed_title = discord.ui.TextInput(
        label="タイトル",
        placeholder="例: 【重要】メンテナンスのお知らせ",
        max_length=256,
        required=True
    )
    embed_body = discord.ui.TextInput(
        label="本文",
        style=discord.TextStyle.paragraph,
        placeholder="お知らせの本文を入力してください...",
        max_length=2000,
        required=True
    )

    def __init__(self, target_guilds: list, channel_map: dict, color: discord.Color):
        super().__init__()
        self.target_guilds = target_guilds
        self.channel_map = channel_map
        self.color = color

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        title_text = self.embed_title.value
        body_text = self.embed_body.value

        preview_embed = discord.Embed(
            title=f"送信プレビュー（{len(self.target_guilds)}サーバー）",
            color=discord.Color.greyple()
        )
        preview_embed.add_field(name="送信対象サーバー数", value=f"{len(self.target_guilds)}サーバー", inline=False)
        preview_embed.add_field(name="Embedタイトル", value=title_text, inline=False)
        preview_embed.add_field(name="Embed本文", value=body_text[:500] + ("..." if len(body_text) > 500 else ""), inline=False)

        confirm_view = BroadcastConfirmView(
            target_guilds=self.target_guilds,
            channel_map=self.channel_map,
            color=self.color,
            title_text=title_text,
            body_text=body_text
        )
        await interaction.followup.send(
            "以下の内容で送信します。確認してください。",
            embed=preview_embed,
            view=confirm_view,
            ephemeral=True
        )


class BroadcastConfirmView(discord.ui.View):
    def __init__(self, target_guilds, channel_map, color, title_text, body_text):
        super().__init__(timeout=120)
        self.target_guilds = target_guilds
        self.channel_map = channel_map
        self.color = color
        self.title_text = title_text
        self.body_text = body_text

    @discord.ui.button(label="送信する", style=discord.ButtonStyle.success)
    async def confirm_send(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        broadcast_embed = discord.Embed(
            title=self.title_text,
            description=self.body_text,
            color=self.color
        )
        broadcast_embed.set_footer(text=f"送信者: {interaction.user.name}")
        broadcast_embed.timestamp = discord.utils.utcnow()

        success_list, fail_list = [], []

        for guild in self.target_guilds:
            ch_id = self.channel_map.get(guild.id)
            ch = guild.get_channel(ch_id) if ch_id else None
            if not ch:
                fail_list.append(f"× {guild.name}（チャンネルが見つかりません）")
                continue
            try:
                await ch.send(embed=broadcast_embed)
                success_list.append(f"○ {guild.name} → #{ch.name}")
            except discord.Forbidden:
                fail_list.append(f"× {guild.name} → #{ch.name}（送信権限なし）")
            except Exception as e:
                fail_list.append(f"× {guild.name}（エラー: {e}）")

        result_embed = discord.Embed(
            title="送信結果",
            color=discord.Color.green() if not fail_list else discord.Color.orange()
        )
        if success_list:
            result_embed.add_field(
                name=f"成功 ({len(success_list)}件)",
                value="\n".join(success_list[:20]) + ("..." if len(success_list) > 20 else ""),
                inline=False
            )
        if fail_list:
            result_embed.add_field(
                name=f"失敗 ({len(fail_list)}件)",
                value="\n".join(fail_list[:10]) + ("..." if len(fail_list) > 10 else ""),
                inline=False
            )

        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(embed=result_embed, ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_send(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="送信をキャンセルしました。", embed=None, view=self)


class BroadcastColorSelect(discord.ui.Select):
    def __init__(self, target_guilds: list, channel_map: dict):
        self.target_guilds = target_guilds
        self.channel_map = channel_map
        options = [
            discord.SelectOption(label=label, value=label)
            for label in BROADCAST_COLORS
        ]
        super().__init__(placeholder="Embedの色を選択...", options=options)

    async def callback(self, interaction: discord.Interaction):
        color = BROADCAST_COLORS[self.values[0]]
        modal = BroadcastEmbedModal(
            target_guilds=self.target_guilds,
            channel_map=self.channel_map,
            color=color
        )
        await interaction.response.send_modal(modal)


class BroadcastColorView(discord.ui.View):
    def __init__(self, target_guilds: list, channel_map: dict):
        super().__init__(timeout=120)
        self.add_item(BroadcastColorSelect(target_guilds, channel_map))


class BroadcastChannelSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, all_guilds: list, channel_map: dict, remaining: list):
        self.guild = guild
        self.all_guilds = all_guilds
        self.channel_map = channel_map
        self.remaining = remaining

        options = [
            discord.SelectOption(
                label=f"#{ch.name}"[:100],
                description=f"カテゴリ: {ch.category.name if ch.category else 'なし'}",
                value=str(ch.id)
            )
            for ch in guild.text_channels[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="チャンネルなし", value="none")]

        super().__init__(
            placeholder=f"{guild.name} の送信先チャンネルを選択...",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_ch_id = self.values[0]

        if selected_ch_id != "none":
            self.channel_map[self.guild.id] = int(selected_ch_id)

        if self.remaining:
            next_guild = self.remaining[0]
            next_remaining = self.remaining[1:]
            view = BroadcastChannelView(next_guild, self.all_guilds, self.channel_map, next_remaining)
            progress = len(self.all_guilds) - len(next_remaining)
            await interaction.response.edit_message(
                content=f"送信先チャンネルを選択してください（{progress}/{len(self.all_guilds)}）",
                view=view
            )
        else:
            target_guilds = [g for g in self.all_guilds if g.id in self.channel_map]
            view = BroadcastColorView(target_guilds, self.channel_map)
            await interaction.response.edit_message(
                content=f"全 {len(target_guilds)} サーバーの送信先を選択しました。\n次にEmbedの色を選んでください。",
                view=view
            )


class BroadcastChannelView(discord.ui.View):
    def __init__(self, guild: discord.Guild, all_guilds: list, channel_map: dict, remaining: list):
        super().__init__(timeout=300)
        self.add_item(BroadcastChannelSelect(guild, all_guilds, channel_map, remaining))

    @discord.ui.button(label="このサーバーをスキップ", style=discord.ButtonStyle.secondary, row=1)
    async def skip_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        select: BroadcastChannelSelect = self.children[0]
        if select.remaining:
            next_guild = select.remaining[0]
            next_remaining = select.remaining[1:]
            view = BroadcastChannelView(next_guild, select.all_guilds, select.channel_map, next_remaining)
            progress = len(select.all_guilds) - len(next_remaining)
            await interaction.response.edit_message(
                content=f"送信先チャンネルを選択してください（{progress}/{len(select.all_guilds)}）",
                view=view
            )
        else:
            target_guilds = [g for g in select.all_guilds if g.id in select.channel_map]
            if not target_guilds:
                await interaction.response.edit_message(content="送信先が1件もありません。コマンドをやり直してください。", view=None)
                return
            view = BroadcastColorView(target_guilds, select.channel_map)
            await interaction.response.edit_message(
                content=f"{len(target_guilds)} サーバーの送信先を選択しました。\n次にEmbedの色を選んでください。",
                view=view
            )


class BroadcastGuildSelect(discord.ui.Select):
    def __init__(self, guilds: list):
        self.all_guilds = guilds
        options = [discord.SelectOption(label="全サーバーに送信", value="ALL")]
        for g in guilds[:24]:
            options.append(discord.SelectOption(
                label=g.name[:100],
                description=f"メンバー: {g.member_count}人",
                value=str(g.id)
            ))
        super().__init__(
            placeholder="送信対象サーバーを選択（複数可）...",
            options=options,
            min_values=1,
            max_values=len(options)
        )

    async def callback(self, interaction: discord.Interaction):
        if "ALL" in self.values:
            target_guilds = self.all_guilds
        else:
            selected_ids = {int(v) for v in self.values}
            target_guilds = [g for g in self.all_guilds if g.id in selected_ids]

        if not target_guilds:
            await interaction.response.send_message("送信対象サーバーがありません。", ephemeral=True)
            return

        first_guild = target_guilds[0]
        remaining = target_guilds[1:]
        channel_map = {}
        view = BroadcastChannelView(first_guild, target_guilds, channel_map, remaining)
        await interaction.response.edit_message(
            content=f"送信先チャンネルを選択してください（1/{len(target_guilds)}）",
            view=view
        )


class BroadcastGuildView(discord.ui.View):
    def __init__(self, guilds: list):
        super().__init__(timeout=300)
        self.add_item(BroadcastGuildSelect(guilds))


@bot.tree.command(name="owner_broadcast", description="【オーナー限定】指定サーバーにEmbedでお知らせを一斉送信します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_broadcast(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return

    guilds = list(interaction.client.guilds)
    if not guilds:
        await interaction.response.send_message("現在、どのサーバーにも導入されていません。", ephemeral=True)
        return

    view = BroadcastGuildView(guilds)
    await interaction.response.send_message(
        "お知らせ送信先のサーバーを選択してください。\n"
        "「全サーバーに送信」を選ぶと全サーバーが対象になります。",
        view=view,
        ephemeral=True
    )

# ==================== 【サーバーバックアップ & リストア】 ====================
# 既存コードの bot.run(TOKEN) の直前にこのブロックをまるごと貼り付けてください。
# 必要な import は既存コードに含まれています（json, os, discord, app_commands）。
# 追加で必要な import:
import io       # JSONをファイルとして送信するため（既存コードにない場合は先頭に追加）
import datetime # バックアップ日時の記録用


# ─────────────────────────────────────────────
#  ユーティリティ: Permissionsオブジェクト ↔ int 変換
# ─────────────────────────────────────────────

def _perms_to_int(perms: discord.Permissions) -> int:
    return perms.value

def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict:
    allow, deny = overwrite.pair()
    return {"allow": allow.value, "deny": deny.value}


# ─────────────────────────────────────────────
#  バックアップデータ生成
# ─────────────────────────────────────────────

async def _build_backup(guild: discord.Guild) -> dict:
    """サーバー設定を辞書形式に変換して返す"""

    # ── ロール ──────────────────────────────
    roles_data = []
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role.is_default():          # @everyone は別途処理
            continue
        roles_data.append({
            "id":          role.id,
            "name":        role.name,
            "color":       role.color.value,
            "hoist":       role.hoist,
            "mentionable": role.mentionable,
            "permissions": _perms_to_int(role.permissions),
            "position":    role.position,
            "managed":     role.managed,   # Bot管理ロールは復元不可のため記録のみ
        })

    everyone_perms = _perms_to_int(guild.default_role.permissions)

    # ── カテゴリ ──────────────────────────────
    categories_data = []
    for cat in sorted(guild.categories, key=lambda c: c.position):
        overwrites = {}
        for target, ow in cat.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            overwrites[key] = _overwrite_to_dict(ow)

        categories_data.append({
            "id":       cat.id,
            "name":     cat.name,
            "position": cat.position,
            "overwrites": overwrites,
        })

    # ── テキストチャンネル ──────────────────────
    text_channels_data = []
    for ch in sorted(guild.text_channels, key=lambda c: c.position):
        overwrites = {}
        for target, ow in ch.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            overwrites[key] = _overwrite_to_dict(ow)

        # Webhook一覧取得（権限があれば）
        webhooks_data = []
        try:
            for wh in await ch.webhooks():
                webhooks_data.append({
                    "name":       wh.name,
                    "avatar_url": str(wh.avatar.url) if wh.avatar else None,
                })
        except discord.Forbidden:
            pass

        text_channels_data.append({
            "id":          ch.id,
            "name":        ch.name,
            "topic":       ch.topic,
            "position":    ch.position,
            "nsfw":        ch.is_nsfw(),
            "slowmode":    ch.slowmode_delay,
            "category_id": ch.category_id,
            "overwrites":  overwrites,
            "webhooks":    webhooks_data,
        })

    # ── ボイスチャンネル ──────────────────────
    voice_channels_data = []
    for ch in sorted(guild.voice_channels, key=lambda c: c.position):
        overwrites = {}
        for target, ow in ch.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            overwrites[key] = _overwrite_to_dict(ow)

        voice_channels_data.append({
            "id":          ch.id,
            "name":        ch.name,
            "position":    ch.position,
            "bitrate":     ch.bitrate,
            "user_limit":  ch.user_limit,
            "category_id": ch.category_id,
            "overwrites":  overwrites,
        })

    # ── フォーラムチャンネル（存在する場合）────────
    forum_channels_data = []
    for ch in guild.forums:
        overwrites = {}
        for target, ow in ch.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            overwrites[key] = _overwrite_to_dict(ow)

        forum_channels_data.append({
            "id":          ch.id,
            "name":        ch.name,
            "topic":       ch.topic,
            "position":    ch.position,
            "category_id": ch.category_id,
            "overwrites":  overwrites,
        })

    return {
        "meta": {
            "version":        "1.0",
            "guild_id":       guild.id,
            "guild_name":     guild.name,
            "backed_up_at":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "member_count":   guild.member_count,
        },
        "everyone_permissions": everyone_perms,
        "roles":            roles_data,
        "categories":       categories_data,
        "text_channels":    text_channels_data,
        "voice_channels":   voice_channels_data,
        "forum_channels":   forum_channels_data,
    }


# ─────────────────────────────────────────────
#  リストア処理
# ─────────────────────────────────────────────

async def _restore_from_backup(guild: discord.Guild, data: dict) -> tuple[list[str], list[str]]:
    """
    バックアップデータをもとにサーバーを復元する。
    Returns: (success_logs, fail_logs)
    """
    success_logs: list[str] = []
    fail_logs:    list[str] = []

    # ── @everyone 権限を復元 ──────────────────
    try:
        everyone_val = data.get("everyone_permissions", 0)
        await guild.default_role.edit(permissions=discord.Permissions(everyone_val))
        success_logs.append("@everyone 権限を復元しました")
    except Exception as e:
        fail_logs.append(f"@everyone 権限の復元に失敗: {e}")

    # ── ロールを復元（既存ロールと名前で照合、なければ新規作成）──
    # managed ロール（Bot管理）はスキップ
    old_role_id_map: dict[int, discord.Role] = {}  # バックアップID → 復元後Roleオブジェクト
    existing_roles  = {r.name: r for r in guild.roles}

    for r_data in sorted(data.get("roles", []), key=lambda r: r["position"]):
        if r_data.get("managed"):
            fail_logs.append(f"ロール「{r_data['name']}」はBot管理ロールのためスキップ")
            continue
        try:
            if r_data["name"] in existing_roles:
                role = existing_roles[r_data["name"]]
                await role.edit(
                    color=discord.Color(r_data["color"]),
                    hoist=r_data["hoist"],
                    mentionable=r_data["mentionable"],
                    permissions=discord.Permissions(r_data["permissions"]),
                )
                success_logs.append(f"ロール「{r_data['name']}」を更新しました")
            else:
                role = await guild.create_role(
                    name=r_data["name"],
                    color=discord.Color(r_data["color"]),
                    hoist=r_data["hoist"],
                    mentionable=r_data["mentionable"],
                    permissions=discord.Permissions(r_data["permissions"]),
                )
                success_logs.append(f"ロール「{r_data['name']}」を新規作成しました")
            old_role_id_map[r_data["id"]] = role
        except Exception as e:
            fail_logs.append(f"ロール「{r_data['name']}」の復元に失敗: {e}")

    # Overwriteを解決するヘルパー
    def _build_overwrites(raw: dict) -> dict:
        overwrites = {}
        for key, val in raw.items():
            kind, oid = key.split(":", 1)
            oid = int(oid)
            if kind == "role":
                target = old_role_id_map.get(oid) or guild.get_role(oid)
            else:
                target = guild.get_member(oid)
            if target is None:
                continue
            allow = discord.Permissions(val["allow"])
            deny  = discord.Permissions(val["deny"])
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        return overwrites

    existing_cats = {c.name: c for c in guild.categories}

    # ── カテゴリを復元 ────────────────────────
    old_cat_id_map: dict[int, discord.CategoryChannel] = {}

    for c_data in sorted(data.get("categories", []), key=lambda c: c["position"]):
        try:
            overwrites = _build_overwrites(c_data.get("overwrites", {}))
            if c_data["name"] in existing_cats:
                cat = existing_cats[c_data["name"]]
                await cat.edit(overwrites=overwrites)
                success_logs.append(f"カテゴリ「{c_data['name']}」の権限を更新しました")
            else:
                cat = await guild.create_category(name=c_data["name"], overwrites=overwrites)
                success_logs.append(f"カテゴリ「{c_data['name']}」を新規作成しました")
            old_cat_id_map[c_data["id"]] = cat
        except Exception as e:
            fail_logs.append(f"カテゴリ「{c_data['name']}」の復元に失敗: {e}")

    existing_text_chs  = {c.name: c for c in guild.text_channels}
    existing_voice_chs = {c.name: c for c in guild.voice_channels}

    # ── テキストチャンネルを復元 ──────────────
    for ch_data in sorted(data.get("text_channels", []), key=lambda c: c["position"]):
        try:
            overwrites = _build_overwrites(ch_data.get("overwrites", {}))
            category   = old_cat_id_map.get(ch_data.get("category_id"))

            if ch_data["name"] in existing_text_chs:
                ch = existing_text_chs[ch_data["name"]]
                await ch.edit(
                    topic=ch_data.get("topic"),
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode", 0),
                    overwrites=overwrites,
                    category=category,
                )
                success_logs.append(f"テキストch「#{ch_data['name']}」の設定を更新しました")
            else:
                ch = await guild.create_text_channel(
                    name=ch_data["name"],
                    topic=ch_data.get("topic"),
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode", 0),
                    overwrites=overwrites,
                    category=category,
                )
                success_logs.append(f"テキストch「#{ch_data['name']}」を新規作成しました")

            # Webhookの復元
            for wh_data in ch_data.get("webhooks", []):
                try:
                    await ch.create_webhook(name=wh_data["name"])
                    success_logs.append(f"  └ Webhook「{wh_data['name']}」を再作成しました")
                except Exception as we:
                    fail_logs.append(f"  └ Webhook「{wh_data['name']}」の作成に失敗: {we}")

        except Exception as e:
            fail_logs.append(f"テキストch「#{ch_data['name']}」の復元に失敗: {e}")

    # ── ボイスチャンネルを復元 ────────────────
    for ch_data in sorted(data.get("voice_channels", []), key=lambda c: c["position"]):
        try:
            overwrites = _build_overwrites(ch_data.get("overwrites", {}))
            category   = old_cat_id_map.get(ch_data.get("category_id"))

            if ch_data["name"] in existing_voice_chs:
                ch = existing_voice_chs[ch_data["name"]]
                await ch.edit(
                    bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=ch_data.get("user_limit", 0),
                    overwrites=overwrites,
                    category=category,
                )
                success_logs.append(f"ボイスch「{ch_data['name']}」の設定を更新しました")
            else:
                await guild.create_voice_channel(
                    name=ch_data["name"],
                    bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=ch_data.get("user_limit", 0),
                    overwrites=overwrites,
                    category=category,
                )
                success_logs.append(f"ボイスch「{ch_data['name']}」を新規作成しました")
        except Exception as e:
            fail_logs.append(f"ボイスch「{ch_data['name']}」の復元に失敗: {e}")

    return success_logs, fail_logs


# ─────────────────────────────────────────────
#  リストア確認UI
# ─────────────────────────────────────────────

class RestoreConfirmView(discord.ui.View):
    """リストア前の最終確認ボタン"""

    def __init__(self, backup_data: dict):
        super().__init__(timeout=120)
        self.backup_data = backup_data

    @discord.ui.button(label="✅ リストアを実行する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await interaction.response.defer(ephemeral=True)

        for item in self.children:
            item.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

        success_logs, fail_logs = await _restore_from_backup(interaction.guild, self.backup_data)

        # 結果を送信（長い場合は分割）
        result_lines = (
            [f"**リストア完了** ✅成功: {len(success_logs)}件 / ❌失敗: {len(fail_logs)}件\n"]
            + [f"✅ {s}" for s in success_logs]
            + [f"❌ {f}" for f in fail_logs]
        )

        chunk = ""
        messages = []
        for line in result_lines:
            if len(chunk) + len(line) + 1 > 1900:
                messages.append(chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            messages.append(chunk)

        for i, msg in enumerate(messages):
            if i == 0:
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="リストアをキャンセルしました。", embed=None, view=self)


# ─────────────────────────────────────────────
#  スラッシュコマンド: /server_backup
# ─────────────────────────────────────────────

@bot.tree.command(
    name="server_backup",
    description="サーバーのロール・チャンネル構成・権限設定をJSONファイルとしてバックアップします"
)
async def server_backup(interaction: discord.Interaction):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        backup_data = await _build_backup(interaction.guild)
    except Exception as e:
        await interaction.followup.send(f"バックアップ中にエラーが発生しました: {e}", ephemeral=True)
        return

    # JSONをメモリ上のファイルオブジェクトに変換
    json_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    file_obj   = io.BytesIO(json_bytes)

    timestamp  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename   = f"backup_{interaction.guild.id}_{timestamp}.json"

    embed = discord.Embed(
        title="✅ サーバーバックアップ完了",
        color=discord.Color.green()
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    meta = backup_data["meta"]
    embed.add_field(name="サーバー名",       value=meta["guild_name"],   inline=True)
    embed.add_field(name="バックアップ日時", value=meta["backed_up_at"][:19].replace("T", " ") + " UTC", inline=True)
    embed.add_field(
        name="バックアップ内容",
        value=(
            f"ロール数: **{len(backup_data['roles'])}個**\n"
            f"カテゴリ数: **{len(backup_data['categories'])}個**\n"
            f"テキストch: **{len(backup_data['text_channels'])}個**\n"
            f"ボイスch: **{len(backup_data['voice_channels'])}個**\n"
            f"フォーラムch: **{len(backup_data['forum_channels'])}個**"
        ),
        inline=False
    )
    embed.add_field(
        name="使い方",
        value=(
            "このJSONファイルを大切に保管してください。\n"
            "nuke等で破壊された場合は `/server_restore` にこのファイルを添付すると復元できます。"
        ),
        inline=False
    )
    embed.set_footer(text=f"ファイル名: {filename}")

    await interaction.followup.send(
        embed=embed,
        file=discord.File(fp=file_obj, filename=filename),
        ephemeral=True
    )
    print(f"[バックアップ] {interaction.guild.name} のバックアップを作成しました (by {interaction.user})")


# ─────────────────────────────────────────────
#  スラッシュコマンド: /server_restore
# ─────────────────────────────────────────────

@bot.tree.command(
    name="server_restore",
    description="バックアップJSONを添付してサーバー構成を復元します（nuke対策）"
)
async def server_restore(interaction: discord.Interaction, backup_file: discord.Attachment):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    # ファイル形式チェック
    if not backup_file.filename.endswith(".json"):
        await interaction.response.send_message(
            "JSONファイルを添付してください（拡張子: `.json`）",
            ephemeral=True
        )
        return

    # サイズ制限（5MB超はNG）
    if backup_file.size > 5 * 1024 * 1024:
        await interaction.response.send_message("ファイルサイズが大きすぎます（上限: 5MB）", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # ファイルを読み込んでパース
    try:
        raw = await backup_file.read()
        backup_data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        await interaction.followup.send(f"JSONの読み込みに失敗しました: {e}", ephemeral=True)
        return

    # バージョン・必須キーの検証
    if "meta" not in backup_data or "roles" not in backup_data:
        await interaction.followup.send(
            "このファイルは有効なバックアップファイルではありません。",
            ephemeral=True
        )
        return

    # 別サーバーのバックアップでも適用可能（ID不一致は警告のみ）
    meta = backup_data["meta"]
    guild_match = meta.get("guild_id") == interaction.guild.id

    embed = discord.Embed(
        title="⚠️ サーバーリストア確認",
        description=(
            "バックアップデータを確認しました。\n"
            "以下の内容を現在のサーバーに**上書き復元**します。\n\n"
            "※ 既存のロール・チャンネルは**削除されません**。\n"
            "　バックアップにないものはそのまま残ります。\n"
            "　バックアップにあって存在しないものは**新規作成**されます。"
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="バックアップ元サーバー", value=meta.get("guild_name", "不明"), inline=True)
    embed.add_field(
        name="サーバー一致",
        value="✅ 同じサーバー" if guild_match else "⚠️ 別のサーバーのバックアップです",
        inline=True
    )
    embed.add_field(name="バックアップ日時", value=meta.get("backed_up_at", "不明")[:19].replace("T", " ") + " UTC", inline=False)
    embed.add_field(
        name="復元内容",
        value=(
            f"ロール: {len(backup_data.get('roles', []))}個\n"
            f"カテゴリ: {len(backup_data.get('categories', []))}個\n"
            f"テキストch: {len(backup_data.get('text_channels', []))}個\n"
            f"ボイスch: {len(backup_data.get('voice_channels', []))}個"
        ),
        inline=False
    )
    embed.set_footer(text="「リストアを実行する」を押すと即座に処理が始まります。")

    view = RestoreConfirmView(backup_data)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    print(f"[リストア確認] {interaction.guild.name} でリストア確認画面を表示 (by {interaction.user})")


bot.run(TOKEN)