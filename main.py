"""
マクマクBOT - Discord サーバー管理・荒らし対策多機能Bot
Railway 動作対応 / 各種ビュー・永続化対応版
"""

import os
import sys
import json
import asyncio
import urllib.request
import urllib.parse
import base64
import requests
import io
import datetime
import collections
import time
import discord
from discord.ext import commands
from discord import app_commands

# .env ファイルからの環境変数読み込み (ローカル開発用)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ====================================================================
# グローバル設定・環境変数の取得
# ====================================================================

TOKEN = os.getenv("DISCORD_TOKEN")
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY")
ROBLOX_UNIVERSE_ID = os.getenv("ROBLOX_UNIVERSE_ID")

if not TOKEN:
    print("エラー: 環境変数 'DISCORD_TOKEN' が見つかりません。")
    sys.exit(1)

# 承認パネル用デフォルトチャンネル名
APPROVAL_PANEL_CHANNEL_NAME = os.getenv("APPROVAL_PANEL_CHANNEL_NAME", "bot-許可申請")

# インテントの設定 (メッセージ、メンバー一覧の取得を有効化)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# JSONデータファイルのパス設定 (Railway用の永続化パス '/app/data' の存在チェック)
if os.path.exists("/app/data"):
    JSON_FILE = "/app/data/allowed_users.json"
else:
    JSON_FILE = "allowed_users.json"

# Botのカスタムステータステキスト保存用変数
current_custom_status = None


# ====================================================================
# セクション 1: データベース・設定処理
# ====================================================================

def load_data() -> dict:
    """JSONファイルから設定データを読み込みます。"""
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[JSON読み込みエラー] {e}")
            return {}
    return {}


def save_data(data: dict):
    """設定データをJSONファイルに書き込みます。ディレクトリが存在しない場合は作成します。"""
    try:
        dir_name = os.path.dirname(JSON_FILE)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            print(f"[システム] 保存先フォルダを作成しました: {dir_name}")

        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[JSON保存エラー] {e}")


def get_guild_config(all_data: dict, guild_id_str: str) -> dict:
    """指定されたサーバーの設定を取得します。存在しない場合はデフォルト値で初期化します。"""
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
            "approval_panel_channel_id": None,
            "warnings": {},
            "automod_spam_enabled": False,
            "automod_invite_enabled": False,
            "automod_ng_words_enabled": False,
            "ng_words": [],
            "mod_log_channel_id": None
        }
    
    # 既存データへの互換性のためキーが無ければ追加
    cfg = all_data[guild_id_str]
    if "approval_status" not in cfg:
        cfg["approval_status"] = "pending"
    for key, default in [
        ("warnings", {}),
        ("automod_spam_enabled", False),
        ("automod_invite_enabled", False),
        ("automod_ng_words_enabled", False),
        ("ng_words", []),
        ("mod_log_channel_id", None)
    ]:
        if key not in cfg:
            cfg[key] = default

    return cfg


def is_guild_approved(all_data: dict, guild_id_str: str) -> bool:
    """サーバーがBot所有者から利用許可されているかを確認します。"""
    cfg = get_guild_config(all_data, guild_id_str)
    return cfg.get("approval_status") == "approved"


def get_user_app_data(all_data: dict, user_id_str: str) -> dict:
    """指定されたユーザーの個人用データ（メモ、クリップ）を取得します。"""
    if "user_apps" not in all_data:
        all_data["user_apps"] = {}
    if user_id_str not in all_data["user_apps"]:
        all_data["user_apps"][user_id_str] = {
            "memos": [],
            "bookmarks": []
        }
    return all_data["user_apps"][user_id_str]


# ====================================================================
# セクション 2: 権限チェックヘルパー & カスタムCommandTree
# ====================================================================

class ApprovalCommandTree(app_commands.CommandTree):
    """
    サーバーが承認されているかチェックするカスタム CommandTree です。
    承認されていないサーバーでは、管理者および一般ユーザーのコマンド実行を無効化します。
    """
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # DMやプライベートチャット内はチェックを通す
        if not interaction.guild:
            return True

        client = interaction.client

        # BotのオーナーIDの解決
        if client.owner_id is None:
            try:
                app_info = await client.application_info()
                client.owner_id = app_info.owner.id
            except Exception:
                pass

        # Botオーナー本人の実行であれば無条件で許可
        if interaction.user.id == client.owner_id:
            return True

        # サーバーの承認状況をチェック
        all_data = load_data()
        if not is_guild_approved(all_data, str(interaction.guild.id)):
            await interaction.response.send_message(
                "エラー: BOT所有者の認証がまだです。\n"
                "このサーバーはBOT所有者の利用許可を受けていないため、コマンドは無効化されています。\n"
                "サーバー管理者に申請パネルからの許可申請をご依頼ください。",
                ephemeral=True
            )
            return False

        return True


# Botクライアントのインスタンス化
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    tree_cls=ApprovalCommandTree
)


def get_global_config(all_data: dict) -> dict:
    """グローバル設定（信頼できるユーザーなど）を取得します。"""
    if "global_config" not in all_data:
        all_data["global_config"] = {
            "trusted_users": []
        }
    return all_data["global_config"]


async def is_owner_check(interaction: discord.Interaction) -> bool:
    """インタラクションの実行者がBotのオーナーかどうかを判定します。"""
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id
    
    if interaction.user.id != interaction.client.owner_id:
        await interaction.response.send_message("このコマンドはアプリの所有者（オーナー）専用です。", ephemeral=True)
        return False
    return True


async def is_trusted_user(interaction: discord.Interaction) -> bool:
    """実行者がBotのオーナー、またはオーナーによって許可されたユーザーか判定します。"""
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

    user_id = interaction.user.id
    if user_id == interaction.client.owner_id:
        return True

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    if user_id in global_cfg.get("trusted_users", []):
        return True

    await interaction.response.send_message(
        "このコマンドはBotの所有者、または許可されたユーザー専用です。", ephemeral=True
    )
    return False


async def is_admin_or_allowed(interaction: discord.Interaction) -> bool:
    """実行者がサーバー管理者、または設定された『コマンド許可ユーザー』かどうかを判定します。"""
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

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


async def is_moderator(interaction: discord.Interaction) -> bool:
    """
    実行者が「Botオーナー」「グローバル信頼ユーザー」「サーバー管理者」「サーバー内許可ユーザー」
    のいずれかに該当するかどうかを判定します（管理・モデレーションコマンド用）。
    """
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

    user_id = interaction.user.id
    if user_id == interaction.client.owner_id:
        return True

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    if user_id in global_cfg.get("trusted_users", []):
        return True

    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return False

    if interaction.user.guild_permissions.administrator:
        return True

    cfg = get_guild_config(all_data, str(interaction.guild.id))
    if user_id in cfg.get("allowed_users", []):
        return True

    await interaction.response.send_message(
        "このコマンドを実行する権限がありません（モデレーター権限が必要です）。", ephemeral=True
    )
    return False


async def is_guild_admin(interaction: discord.Interaction) -> bool:
    """実行者がサーバーの管理者（Administrator）かどうかを判定します。"""
    if interaction.client.owner_id is None:
        app_info = await interaction.client.application_info()
        interaction.client.owner_id = app_info.owner.id

    if interaction.user.id == interaction.client.owner_id:
        return True

    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return False

    if interaction.user.guild_permissions.administrator:
        return True

    await interaction.response.send_message("このコマンドはサーバー管理者専用です。", ephemeral=True)
    return False


# ====================================================================
# セクション 3: 共有 Embed / 表示用ヘルパー関数
# ====================================================================

def create_user_list_embed(allowed_users: list) -> discord.Embed:
    """コマンド使用を許可されたユーザー一覧の Embed を作成します。"""
    embed = discord.Embed(
        title="コマンド使用許可ユーザー一覧", 
        description="現在、以下のユーザーに権限が与えられています。\n※サーバー管理者は登録なしですべてのコマンドを使用できます。",
        color=discord.Color.blue()
    )
    if not allowed_users:
        embed.add_field(name="登録ユーザー", value="開示できるユーザーはいません。", inline=False)
        embed.set_footer(text="登録者数: 0名")
    else:
        user_mentions = [f"・<@{user_id}>" for user_id in allowed_users]
        embed.add_field(name="登録ユーザー", value="\n".join(user_mentions), inline=False)
        embed.set_footer(text=f"登録者数: {len(allowed_users)}名")
    return embed


def build_approval_request_embed(guild: discord.Guild) -> discord.Embed:
    """サーバー管理者が使用する「Bot導入の利用申請パネル」用の Embed を作成します。"""
    embed = discord.Embed(
        title="このBOTの導入にはBOT所有者の許可が必要です",
        description=(
            "このBOTを継続して利用するには、**BOT所有者の承認**が必要です。\n\n"
            "下のボタンを押すと、BOT所有者に参加許可申請のDMが送信されます。\n"
            "所有者が**許可**すればBotが利用可能になります。\n"
            "所有者が**拒否**した場合、Botは自動的にサーバーから退出します。"
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="このサーバー", value=guild.name, inline=True)
    embed.add_field(name="メンバー数", value=f"{guild.member_count}人", inline=True)
    embed.set_footer(text="サーバー管理者がボタンを押して申請してください")
    return embed


GUILDS_PER_PAGE = 5

def build_guild_list_embed(guilds: list, page: int) -> discord.Embed:
    """導入中サーバーの一覧 Embed を改ページ対応で作成します。"""
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


async def update_bot_status(client, text=None):
    """Botのプレゼンス（カスタムステータス）を更新します。"""
    global current_custom_status
    if text:
        current_custom_status = text
    
    status_text = current_custom_status if current_custom_status else f"{len(client.guilds)}個のサーバー"
    activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
    await client.change_presence(status=discord.Status.online, activity=activity)
    print(f"[ステータス更新] {status_text} を視聴中 (Online)")


# ====================================================================
# セクション 4: UIビュー部品 (ボタン・セレクト・モーダル)
# ====================================================================

class ApprovalRequestView(discord.ui.View):
    """サーバー管理者がBot所有者に利用許可申請を送るためのボタンビューです。"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="BOT所有者に許可申請を送る", style=discord.ButtonStyle.primary, custom_id="send_approval_request")
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
            title="新しいサーバー導入の許可申請",
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
    """Bot所有者のDMに送信される、サーバー承認・拒否を選択するボタンビューです。"""
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

    @discord.ui.button(label="許可する", style=discord.ButtonStyle.success, custom_id="approve_guild")
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
            content=f"**{guild_name}** の利用を許可しました。",
            embed=None,
            view=self
        )

        await self._notify_panel_channel(
            client,
            "BOT所有者がこのサーバーでの利用を許可しました。全機能が利用可能になりました。"
        )

    @discord.ui.button(label="拒否する", style=discord.ButtonStyle.danger, custom_id="reject_guild")
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
            content=f"**{guild_name}** への導入を拒否しました。サーバーから退出します。",
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


class GuildLeaveConfirmView(discord.ui.View):
    """オーナーが指定サーバーから脱退する際の最終確認用ボタンビューです。"""
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
                content=f"**{guild_name}** から脱退しました。",
                embed=None,
                view=None
            )
        except discord.HTTPException as e:
            await interaction.response.edit_message(
                content=f"脱退に失敗しました: `{e}`",
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
    """オーナー脱退用サーバーリスト選択メニューです。"""
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
                "この操作は **取り消せません。**"
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
    """導入サーバー一覧と脱退選択機能をまとめた管理用ビューです。"""
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

    @discord.ui.button(label="前へ", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.guilds = list(interaction.client.guilds)
        self._rebuild_select()
        self.update_buttons(self.guilds)
        embed = build_guild_list_embed(self.guilds, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="次へ", style=discord.ButtonStyle.secondary, row=1)
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


class MemoDeleteSelect(discord.ui.Select):
    """ユーザー個人用メモの削除用セレクトメニューです。"""
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
    """個人メモ削除用セレクトメニューを保持するビューです。"""
    def __init__(self, memos):
        super().__init__(timeout=180)
        self.add_item(MemoDeleteSelect(memos))


class UserManageView(discord.ui.View):
    """コマンド許可ユーザーを追加・削除するためのユーザー選択メニュービューです。"""
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
    """ロール自動付与・剥奪用のボタンを表示する動的パネルビューです。"""
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
    """サーバー参加時のメンバー認証ボタン用ビューです。"""
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


class RestoreConfirmView(discord.ui.View):
    """
    サーバーリストアの実行確認用ボタンビューです。
    段階的リストア: 既存を更新 → 不足を追加 → 余分を削除、という順序で処理します。
    """

    def __init__(self, backup_data: dict):
        super().__init__(timeout=300)  # 大規模サーバーでも余裕を持たせる
        self.backup_data = backup_data
        self._running = False  # 二重実行防止フラグ

    @discord.ui.button(label="段階的にリストアを実行する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """確認ボタンが押されたとき、段階的リストア（更新・追加・削除）を実行します。"""
        if not interaction.guild:
            return

        # 二重実行防止
        if self._running:
            await interaction.response.send_message(
                "すでにリストア実行中です。しばらくお待ちください。", ephemeral=True
            )
            return
        self._running = True

        # ボタンを即座に無効化
        for item in self.children:
            item.disabled = True

        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

        guild = interaction.guild
        print(f"[リストア開始] サーバー: {guild.name} (by {interaction.user})")

        await interaction.followup.send(
            ">> 段階的リストアを開始します...\n"
            "既存のチャンネル・ロールを更新しながら、不足分を追加・余分を削除します。",
            ephemeral=True
        )

        success_logs, fail_logs = await _smart_restore_from_backup(guild, self.backup_data)
        print(f"[リストア] 完了: 成功={len(success_logs)}, 失敗={len(fail_logs)}")

        # 結果を分割送信（2000文字制限対策）
        result_lines = (
            [f"** リストア完了 ** 成功: {len(success_logs)}件 / 失敗: {len(fail_logs)}件\n"]
            + [f"[OK] {s}" for s in success_logs]
            + [f"[NG] {f}" for f in fail_logs]
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
        for msg in messages:
            await interaction.followup.send(msg, ephemeral=True)

        self._running = False

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """キャンセルボタンが押されたとき、リストアを中断します。"""
        if self._running:
            await interaction.response.send_message(
                "リストアはすでに実行中のためキャンセルできません。", ephemeral=True
            )
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="リストアをキャンセルしました。", embed=None, view=self
        )


class ServerCopyConfirmView(discord.ui.View):
    """
    サーバーコピーの実行確認用ボタンビューです。
    """

    def __init__(self, source_guild_name: str, backup_data: dict):
        super().__init__(timeout=300)
        self.source_guild_name = source_guild_name
        self.backup_data = backup_data
        self._running = False

    @discord.ui.button(label="段階的にコピーを実行する", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        if self._running:
            await interaction.response.send_message(
                "すでにコピー実行中です。しばらくお待ちください。", ephemeral=True
            )
            return
        self._running = True

        for item in self.children:
            item.disabled = True

        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass

        guild = interaction.guild
        print(f"[サーバーコピー開始] コピー元: {self.source_guild_name} -> コピー先: {guild.name} (by {interaction.user})")

        await interaction.followup.send(
            ">> 段階的コピーを開始します...\n"
            f"「{self.source_guild_name}」の構造をもとに、既存のチャンネル・ロールを更新・追加・削除します。",
            ephemeral=True
        )

        success_logs, fail_logs = await _smart_restore_from_backup(guild, self.backup_data)
        print(f"[サーバーコピー] 完了: 成功={len(success_logs)}, 失敗={len(fail_logs)}")

        result_lines = (
            [f"** コピー完了 ** 成功: {len(success_logs)}件 / 失敗: {len(fail_logs)}件\n"]
            + [f"[OK] {s}" for s in success_logs]
            + [f"[NG] {f}" for f in fail_logs]
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
        for msg in messages:
            await interaction.followup.send(msg, ephemeral=True)

        self._running = False

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._running:
            await interaction.response.send_message(
                "コピーはすでに実行中のためキャンセルできません。", ephemeral=True
            )
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="サーバーコピーをキャンセルしました。", embed=None, view=self
        )


# ====================================================================
# 謝罪コマンド用 UI部品（高自由度・絵文字なし・自然な文体版）
# ====================================================================

class FullCustomApologyModal(discord.ui.Modal, title="完全自由入力で謝罪文を作成"):
    """一番自由に書けるモード"""
    content = discord.ui.TextInput(
        label="謝罪文を自由に書いてください",
        style=discord.TextStyle.paragraph,
        placeholder="ここに全文を入力...（主要部分を自由に変更可能）",
        max_length=3900,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="謝罪いたします",
            description=self.content.value,
            color=discord.Color.orange()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(embed=embed)


class StructuredApologyModal(discord.ui.Modal, title="主要部分を分けて編集"):
    """主要部分を分けつつ自由に変更できるモード"""
    action = discord.ui.TextInput(
        label="1. 何をしてしまったか",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )
    
    pointed = discord.ui.TextInput(
        label="2. 指摘されたこと / 相手の気持ち",
        style=discord.TextStyle.paragraph,
        max_length=400,
        required=True
    )
    
    reflection = discord.ui.TextInput(
        label="3. 自分の反省・気づき",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )
    
    improvement = discord.ui.TextInput(
        label="4. 今後どうするか（改善策）",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="謝罪いたします",
            color=discord.Color.orange()
        )
        embed.add_field(name="1. 行動", value=self.action.value, inline=False)
        embed.add_field(name="2. 指摘", value=self.pointed.value, inline=False)
        embed.add_field(name="3. 反省", value=self.reflection.value, inline=False)
        embed.add_field(name="4. 改善", value=self.improvement.value, inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(embed=embed)


class ApologyView(discord.ui.View):
    """謝罪コマンド メインビュー"""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="完全自由入力", style=discord.ButtonStyle.primary, row=0)
    async def full_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FullCustomApologyModal())

    @discord.ui.button(label="主要部分を分けて編集", style=discord.ButtonStyle.primary, row=0)
    async def structured(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StructuredApologyModal()
        modal.action.default = "動画を見てる途中でつい何も考えずにコメント欄を開けてしまいました"
        modal.pointed.default = "コメントをすぐ開くなという指摘"
        modal.reflection.default = "自分の配慮が全然足りてなくて、軽率だったなと反省しています"
        modal.improvement.default = "今後は動画をちゃんと最後まで見てから行動するようにします。一度立ち止まって考える癖もつけていきます"
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="動画コメント謝罪テンプレート", style=discord.ButtonStyle.gray, row=1)
    async def video_template(self, interaction: discord.Interaction, button: discord.ui.Button):
        template = """今回の件、本当に申し訳ありませんでした。

動画を見てる途中でつい何も考えずにコメント欄を開けてしまって、周りの人に不快な思いをさせてしまいました。

ちゃんと最後まで見てから行動するべきだったのに、完全に軽率でした。「コメントすぐ開くな」っていう指摘もその通りで、自分の配慮の足りなさを痛感しています。

今後は動画の内容にしっかり集中して、人の気持ちも考えて行動するようにします。衝動的に動く癖も直していきたいと思います。

このたびは本当にごめんなさい。次からは気をつけます。"""

        embed = discord.Embed(
            title="謝罪いたします",
            description=template,
            color=discord.Color.orange()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.send_message(embed=embed)


# ====================================================================
# 謝罪コマンド
# ====================================================================

@bot.tree.command(name="apology", description="謝罪文を作って送るやつです")
async def apology(interaction: discord.Interaction):
    embed = discord.Embed(
        title="謝罪文作成",
        description=(
            "主要な部分を自分で変えられます。\n\n"
            "・完全自由入力 → 1つの欄で好きに全文を書けます\n"
            "・主要部分を分けて編集 → 行動・指摘・反省・改善をそれぞれ変えられます\n"
            "・動画コメント謝罪テンプレート → そのまま使えます"
        ),
        color=discord.Color.orange()
    )
    view = ApologyView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

class GuildDetailSelect(discord.ui.Select):
    """サーバー詳細確認用セレクトメニューです。"""
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
        approval_label = {"approved": "許可済み", "pending_review": "確認待ち", "pending": "未申請"}.get(approval_status, approval_status)
        settings = [
            f"利用許可: {approval_label}",
            f"{'ON' if cfg.get('from_channel') else 'OFF'} メッセージ転送",
            f"{'ON' if cfg.get('verify_channel') else 'OFF'} サーバー認証",
            f"{'ON' if cfg.get('announce_channel') else 'OFF'} 配信お知らせ",
            f"{'ON' if cfg.get('mention_trigger_channel') else 'OFF'} 自動返信メンション",
            f"{'ON' if cfg.get('panel_roles') else 'OFF'} ロールパネル",
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
    """サーバー詳細表示用セレクトメニューを保持する改ページ対応ビューです。"""
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

    @discord.ui.button(label="前へ", style=discord.ButtonStyle.secondary, row=1)
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

    @discord.ui.button(label="次へ", style=discord.ButtonStyle.secondary, row=1)
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


BROADCAST_COLORS = {
    "ブルー":   discord.Color.blue(),
    "グリーン": discord.Color.green(),
    "レッド":   discord.Color.red(),
    "ゴールド": discord.Color.gold(),
    "パープル": discord.Color.purple(),
    "グレー":   discord.Color.greyple(),
}


class BroadcastEmbedModal(discord.ui.Modal, title="お知らせ内容を入力"):
    """一斉送信（ブロードキャスト）用のお知らせ内容を入力するモーダルです。"""
    embed_title = discord.ui.TextInput(
        label="タイトル",
        placeholder="例: メンテナンスのお知らせ",
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
    """一斉送信の最終確認ボタンビューです。"""
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
                fail_list.append(f"x {guild.name}（チャンネルが見つかりません）")
                continue
            try:
                await ch.send(embed=broadcast_embed)
                success_list.append(f"o {guild.name} → #{ch.name}")
            except discord.Forbidden:
                fail_list.append(f"x {guild.name} → #{ch.name}（送信権限なし）")
            except Exception as e:
                fail_list.append(f"x {guild.name}（エラー: {e}）")

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
    """一斉送信メッセージの Embed 枠線の色を選択するセレクトメニューです。"""
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
    """Embedカラー選択メニューを保持するビューです。"""
    def __init__(self, target_guilds: list, channel_map: dict):
        super().__init__(timeout=120)
        self.add_item(BroadcastColorSelect(target_guilds, channel_map))


class BroadcastChannelSelect(discord.ui.Select):
    """サーバーごとの送信先チャンネルを選択するセレクトメニューです。"""
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
    """一斉送信先チャンネル選択およびスキップボタンを保持するビューです。"""
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
    """一斉送信の送信対象サーバー（複数選択可）を選択するメニューです。"""
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
    """送信対象サーバー選択メニューを保持するビューです。"""
    def __init__(self, guilds: list):
        super().__init__(timeout=300)
        self.add_item(BroadcastGuildSelect(guilds))


# ====================================================================
# セクション 5: 内部処理・バックアップ・荒らし対策ヘルパー
# ====================================================================

def _perms_to_int(perms: discord.Permissions) -> int:
    return perms.value


def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict:
    allow, deny = overwrite.pair()
    return {"allow": allow.value, "deny": deny.value}


async def _build_backup(guild: discord.Guild) -> dict:
    """サーバー設定（ロール、チャンネル構造、権限）をJSONシリアライズ可能な辞書データにビルドします。"""
    roles_data = []
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role.is_default():
            continue
        roles_data.append({
            "id":          role.id,
            "name":        role.name,
            "color":       role.color.value,
            "hoist":       role.hoist,
            "mentionable": role.mentionable,
            "permissions": _perms_to_int(role.permissions),
            "position":    role.position,
            "managed":     role.managed,
        })

    everyone_perms = _perms_to_int(guild.default_role.permissions)

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

    text_channels_data = []
    for ch in sorted(guild.text_channels, key=lambda c: c.position):
        overwrites = {}
        for target, ow in ch.overwrites.items():
            key = f"role:{target.id}" if isinstance(target, discord.Role) else f"member:{target.id}"
            overwrites[key] = _overwrite_to_dict(ow)

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


async def _wipe_guild(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """サーバー内の全チャンネル、カテゴリー、カスタムロールを削除します。"""
    success_logs: list[str] = []
    fail_logs:    list[str] = []

    me = guild.me

    for ch in list(guild.channels):
        if isinstance(ch, discord.CategoryChannel):
            continue
        try:
            await ch.delete(reason="アンチnukeリストア: 全削除して再構築")
            success_logs.append(f"チャンネル「#{ch.name}」を削除しました")
        except discord.Forbidden:
            fail_logs.append(f"チャンネル「#{ch.name}」の削除に失敗（権限不足）")
        except Exception as e:
            fail_logs.append(f"チャンネル「#{ch.name}」の削除に失敗: {e}")

    for cat in list(guild.categories):
        try:
            await cat.delete(reason="アンチnukeリストア: 全削除して再構築")
            success_logs.append(f"カテゴリ「{cat.name}」を削除しました")
        except discord.Forbidden:
            fail_logs.append(f"カテゴリ「{cat.name}」の削除に失敗（権限不足）")
        except Exception as e:
            fail_logs.append(f"カテゴリ「{cat.name}」の削除に失敗: {e}")

    for role in list(guild.roles):
        if role.is_default():
            continue
        if role.managed:
            continue
        if me and role in me.roles and role >= me.top_role:
            fail_logs.append(f"ロール「{role.name}」はBotの権限保持に必要なためスキップしました")
            continue
        try:
            await role.delete(reason="アンチnukeリストア: 全削除して再構築")
            success_logs.append(f"ロール「{role.name}」を削除しました")
        except discord.Forbidden:
            fail_logs.append(f"ロール「{role.name}」の削除に失敗（権限不足）")
        except Exception as e:
            fail_logs.append(f"ロール「{role.name}」の削除に失敗: {e}")

    return success_logs, fail_logs


async def _safe_api_call(coro, fail_logs: list, label: str):
    """
    Discord API呼び出しをレート制限対応でラップします。
    429エラー（レート制限）の場合はretry_afterだけ待機してリトライします。
    """
    for attempt in range(3):
        try:
            return await coro
        except discord.HTTPException as e:
            if e.status == 429:
                # レート制限: retry_after秒待機してリトライ
                retry_after = float(e.response.headers.get("Retry-After", 1.0))
                print(f"[リストア] レート制限 - {retry_after:.1f}秒待機 ({label})")
                await asyncio.sleep(retry_after + 0.5)
            else:
                fail_logs.append(f"{label} の操作に失敗: {e}")
                return None
        except Exception as e:
            fail_logs.append(f"{label} の操作に失敗: {e}")
            return None
    fail_logs.append(f"{label} はリトライ上限に達しました")
    return None


async def _restore_from_backup(guild: discord.Guild, data: dict) -> tuple[list[str], list[str]]:
    """バックアップデータからロール・チャンネル構造・権限を再構成します。"""
    success_logs = []
    fail_logs    = []

    # ① @everyone 権限の復元
    try:
        everyone_val = data.get("everyone_permissions", 0)
        await guild.default_role.edit(permissions=discord.Permissions(everyone_val))
        success_logs.append("@everyone 権限を復元しました")
    except Exception as e:
        fail_logs.append(f"@everyone 権限の復元に失敗: {e}")

    # 旧ロールID → 新ロールオブジェクトのマッピング
    old_role_id_map = {}

    # ② ロールの復元（APIレート制限対策: 各ロール作成後に少し待機）
    for r_data in sorted(data.get("roles", []), key=lambda r: r["position"]):
        if r_data.get("managed"):
            fail_logs.append(f"ロール「{r_data['name']}」はBot管理ロールのためスキップ")
            continue
        label = f"ロール「{r_data['name']}」"
        role = await _safe_api_call(
            guild.create_role(
                name=r_data["name"],
                color=discord.Color(r_data["color"]),
                hoist=r_data["hoist"],
                mentionable=r_data["mentionable"],
                permissions=discord.Permissions(r_data["permissions"]),
            ),
            fail_logs, label
        )
        if role:
            success_logs.append(f"{label} を作成しました")
            old_role_id_map[r_data["id"]] = role
        # レート制限対策: ロール作成ごとに0.5秒待機
        await asyncio.sleep(0.5)

    def _build_overwrites(raw: dict) -> dict:
        """バックアップの権限上書き情報を discord.PermissionOverwrite 辞書に変換します。"""
        overwrites = {}
        for key, val in raw.items():
            kind, oid = key.split(":", 1)
            oid = int(oid)
            if kind == "role":
                # 旧IDから新ロールへマッピング（見つからなければ現存ロールで試みる）
                target = old_role_id_map.get(oid) or guild.get_role(oid)
            else:
                target = guild.get_member(oid)
            if target is None:
                continue
            allow = discord.Permissions(val["allow"])
            deny  = discord.Permissions(val["deny"])
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        return overwrites

    # カテゴリID → 新カテゴリオブジェクトのマッピング
    old_cat_id_map = {}

    # ③ カテゴリの復元
    for c_data in sorted(data.get("categories", []), key=lambda c: c["position"]):
        label = f"カテゴリ「{c_data['name']}」"
        overwrites = _build_overwrites(c_data.get("overwrites", {}))
        cat = await _safe_api_call(
            guild.create_category(name=c_data["name"], overwrites=overwrites),
            fail_logs, label
        )
        if cat:
            success_logs.append(f"{label} を作成しました")
            old_cat_id_map[c_data["id"]] = cat
        await asyncio.sleep(0.5)

    # ④ テキストチャンネルの復元
    for ch_data in sorted(data.get("text_channels", []), key=lambda c: c["position"]):
        label = f"テキストch「#{ch_data['name']}」"
        overwrites = _build_overwrites(ch_data.get("overwrites", {}))
        category   = old_cat_id_map.get(ch_data.get("category_id"))
        ch = await _safe_api_call(
            guild.create_text_channel(
                name=ch_data["name"],
                topic=ch_data.get("topic"),
                nsfw=ch_data.get("nsfw", False),
                slowmode_delay=ch_data.get("slowmode", 0),
                overwrites=overwrites,
                category=category,
            ),
            fail_logs, label
        )
        if ch:
            success_logs.append(f"{label} を作成しました")
            # Webhookの復元
            for wh_data in ch_data.get("webhooks", []):
                wh = await _safe_api_call(
                    ch.create_webhook(name=wh_data["name"]),
                    fail_logs, f"Webhook「{wh_data['name']}」"
                )
                if wh:
                    success_logs.append(f"  Webhook「{wh_data['name']}」を作成しました")
        await asyncio.sleep(0.5)

    # ⑤ ボイスチャンネルの復元
    for ch_data in sorted(data.get("voice_channels", []), key=lambda c: c["position"]):
        label = f"ボイスch「{ch_data['name']}」"
        overwrites = _build_overwrites(ch_data.get("overwrites", {}))
        category   = old_cat_id_map.get(ch_data.get("category_id"))
        ch = await _safe_api_call(
            guild.create_voice_channel(
                name=ch_data["name"],
                bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                user_limit=ch_data.get("user_limit", 0),
                overwrites=overwrites,
                category=category,
            ),
            fail_logs, label
        )
        if ch:
            success_logs.append(f"{label} を作成しました")
        await asyncio.sleep(0.5)

    print(f"[リストア完了] 成功: {len(success_logs)}件 / 失敗: {len(fail_logs)}件")
    return success_logs, fail_logs


async def _smart_restore_from_backup(guild: discord.Guild, data: dict) -> tuple[list[str], list[str]]:
    """
    バックアップデータから段階的にサーバーを復元します。
    全削除はせず、以下の順序で処理します:
      1. バックアップにあるロールを「更新（既存）」または「作成（新規）」
      2. バックアップにないロールを削除
      3. バックアップにあるカテゴリを「更新」または「作成」
      4. バックアップにないカテゴリを削除（中のチャンネルは先に移動）
      5. バックアップにあるテキスト/ボイスchを「更新」または「作成」
      6. バックアップにないテキスト/ボイスchを削除
    """
    success_logs: list[str] = []
    fail_logs:    list[str] = []
    me = guild.me

    # ------------------------------------------------
    # ヘルパー: バックアップの権限設定をDiscordオブジェクトに変換
    # ------------------------------------------------
    def _build_overwrites(raw: dict, role_map: dict) -> dict:
        """旧ロールID→新ロールオブジェクトのマップを使って PermissionOverwrite 辞書を生成します。"""
        ow_dict = {}
        for key, val in raw.items():
            kind, oid = key.split(":", 1)
            oid = int(oid)
            if kind == "role":
                target = role_map.get(oid) or guild.get_role(oid)
            else:
                target = guild.get_member(oid)
            if target is None:
                continue
            allow = discord.Permissions(val["allow"])
            deny  = discord.Permissions(val["deny"])
            ow_dict[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        return ow_dict

    # ================================================
    # Step 1: ロールの段階的リストア
    # ================================================
    # 旧バックアップID → 新ロールオブジェクトのマッピング
    old_role_id_map: dict[int, discord.Role] = {}

    # 現在のサーバーのカスタムロールを名前で辞書化
    existing_roles_by_name = {
        r.name: r for r in guild.roles
        if not r.is_default() and not r.managed
    }
    # Botが持つロールはいじらない
    bot_role_names = {r.name for r in (me.roles if me else [])}

    backup_role_names: set[str] = set()
    for r_data in sorted(data.get("roles", []), key=lambda r: r["position"]):
        if r_data.get("managed"):
            # Bot連携ロールはスキップ
            continue
        rname = r_data["name"]
        backup_role_names.add(rname)
        label = f"ロール「{rname}」"
        existing = existing_roles_by_name.get(rname)

        if existing:
            # 既存ロールをバックアップ内容で更新
            role = await _safe_api_call(
                existing.edit(
                    color=discord.Color(r_data["color"]),
                    hoist=r_data["hoist"],
                    mentionable=r_data["mentionable"],
                    permissions=discord.Permissions(r_data["permissions"]),
                    reason="リストア: ロール設定を更新",
                ),
                fail_logs, label
            )
            if role is not None or existing:  # editはNoneを返すので existing を使う
                success_logs.append(f"{label} を更新しました")
                old_role_id_map[r_data["id"]] = existing
        else:
            # 存在しないロールを新規作成
            role = await _safe_api_call(
                guild.create_role(
                    name=rname,
                    color=discord.Color(r_data["color"]),
                    hoist=r_data["hoist"],
                    mentionable=r_data["mentionable"],
                    permissions=discord.Permissions(r_data["permissions"]),
                    reason="リストア: ロールを新規作成",
                ),
                fail_logs, label
            )
            if role:
                success_logs.append(f"{label} を新規作成しました")
                old_role_id_map[r_data["id"]] = role
        await asyncio.sleep(0.5)

    # バックアップにないロールを削除（Botのロールは除く）
    for rname, role in existing_roles_by_name.items():
        if rname not in backup_role_names and rname not in bot_role_names:
            deleted = await _safe_api_call(
                role.delete(reason="リストア: バックアップにないロールを削除"),
                fail_logs, f"ロール「{rname}」削除"
            )
            if deleted is not None or True:  # deleteはNoneを返す
                success_logs.append(f"ロール「{rname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

    # @everyone権限を復元
    try:
        everyone_val = data.get("everyone_permissions", 0)
        await guild.default_role.edit(permissions=discord.Permissions(everyone_val))
        success_logs.append("@everyone 権限を復元しました")
    except Exception as e:
        fail_logs.append(f"@everyone 権限の復元に失敗: {e}")

    # ================================================
    # Step 2: カテゴリの段階的リストア
    # ================================================
    old_cat_id_map: dict[int, discord.CategoryChannel] = {}
    existing_cats_by_name = {c.name: c for c in guild.categories}
    backup_cat_names: set[str] = set()

    for c_data in sorted(data.get("categories", []), key=lambda c: c["position"]):
        cname = c_data["name"]
        backup_cat_names.add(cname)
        label = f"カテゴリ「{cname}」"
        overwrites = _build_overwrites(c_data.get("overwrites", {}), old_role_id_map)
        existing_cat = existing_cats_by_name.get(cname)

        if existing_cat:
            # 既存カテゴリを更新
            await _safe_api_call(
                existing_cat.edit(overwrites=overwrites, reason="リストア: カテゴリ設定を更新"),
                fail_logs, label
            )
            success_logs.append(f"{label} を更新しました")
            old_cat_id_map[c_data["id"]] = existing_cat
        else:
            # 存在しないカテゴリを新規作成
            cat = await _safe_api_call(
                guild.create_category(name=cname, overwrites=overwrites, reason="リストア: カテゴリを新規作成"),
                fail_logs, label
            )
            if cat:
                success_logs.append(f"{label} を新規作成しました")
                old_cat_id_map[c_data["id"]] = cat
        await asyncio.sleep(0.5)

    # バックアップにないカテゴリを削除（中のchは先にカテゴリなしへ移動）
    for cname, cat in existing_cats_by_name.items():
        if cname not in backup_cat_names:
            for ch in list(cat.channels):
                await _safe_api_call(
                    ch.edit(category=None, reason="リストア: カテゴリ削除前にチャンネルを移動"),
                    fail_logs, f"ch「{ch.name}」の移動"
                )
                await asyncio.sleep(0.3)
            await _safe_api_call(
                cat.delete(reason="リストア: バックアップにないカテゴリを削除"),
                fail_logs, f"カテゴリ「{cname}」削除"
            )
            success_logs.append(f"カテゴリ「{cname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

    # ================================================
    # Step 3: テキストチャンネルの段階的リストア
    # ================================================
    existing_text_by_name = {c.name: c for c in guild.text_channels}
    backup_text_names: set[str] = set()

    for ch_data in sorted(data.get("text_channels", []), key=lambda c: c["position"]):
        chname = ch_data["name"]
        backup_text_names.add(chname)
        label = f"テキストch「#{chname}」"
        overwrites = _build_overwrites(ch_data.get("overwrites", {}), old_role_id_map)
        category   = old_cat_id_map.get(ch_data.get("category_id"))
        existing_ch = existing_text_by_name.get(chname)

        if existing_ch:
            # 既存チャンネルを更新
            await _safe_api_call(
                existing_ch.edit(
                    topic=ch_data.get("topic"),
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode", 0),
                    overwrites=overwrites,
                    category=category,
                    reason="リストア: チャンネル設定を更新",
                ),
                fail_logs, label
            )
            success_logs.append(f"{label} を更新しました")
        else:
            # 存在しないチャンネルを新規作成
            ch = await _safe_api_call(
                guild.create_text_channel(
                    name=chname,
                    topic=ch_data.get("topic"),
                    nsfw=ch_data.get("nsfw", False),
                    slowmode_delay=ch_data.get("slowmode", 0),
                    overwrites=overwrites,
                    category=category,
                    reason="リストア: テキストchを新規作成",
                ),
                fail_logs, label
            )
            if ch:
                success_logs.append(f"{label} を新規作成しました")
                # Webhookの復元
                for wh_data in ch_data.get("webhooks", []):
                    wh = await _safe_api_call(
                        ch.create_webhook(name=wh_data["name"]),
                        fail_logs, f"Webhook「{wh_data['name']}」"
                    )
                    if wh:
                        success_logs.append(f"  Webhook「{wh_data['name']}」を作成しました")
        await asyncio.sleep(0.5)

    # バックアップにないテキストchを削除
    for chname, ch in existing_text_by_name.items():
        if chname not in backup_text_names:
            await _safe_api_call(
                ch.delete(reason="リストア: バックアップにないchを削除"),
                fail_logs, f"テキストch「#{chname}」削除"
            )
            success_logs.append(f"テキストch「#{chname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

    # ================================================
    # Step 4: ボイスチャンネルの段階的リストア
    # ================================================
    existing_voice_by_name = {c.name: c for c in guild.voice_channels}
    backup_voice_names: set[str] = set()

    for ch_data in sorted(data.get("voice_channels", []), key=lambda c: c["position"]):
        chname = ch_data["name"]
        backup_voice_names.add(chname)
        label = f"ボイスch「{chname}」"
        overwrites = _build_overwrites(ch_data.get("overwrites", {}), old_role_id_map)
        category   = old_cat_id_map.get(ch_data.get("category_id"))
        existing_ch = existing_voice_by_name.get(chname)

        if existing_ch:
            # 既存ボイスchを更新
            await _safe_api_call(
                existing_ch.edit(
                    bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=ch_data.get("user_limit", 0),
                    overwrites=overwrites,
                    category=category,
                    reason="リストア: ボイスch設定を更新",
                ),
                fail_logs, label
            )
            success_logs.append(f"{label} を更新しました")
        else:
            # 存在しないボイスchを新規作成
            ch = await _safe_api_call(
                guild.create_voice_channel(
                    name=chname,
                    bitrate=min(ch_data.get("bitrate", 64000), guild.bitrate_limit),
                    user_limit=ch_data.get("user_limit", 0),
                    overwrites=overwrites,
                    category=category,
                    reason="リストア: ボイスchを新規作成",
                ),
                fail_logs, label
            )
            if ch:
                success_logs.append(f"{label} を新規作成しました")
        await asyncio.sleep(0.5)

    # バックアップにないボイスchを削除
    for chname, ch in existing_voice_by_name.items():
        if chname not in backup_voice_names:
            await _safe_api_call(
                ch.delete(reason="リストア: バックアップにないchを削除"),
                fail_logs, f"ボイスch「{chname}」削除"
            )
            success_logs.append(f"ボイスch「{chname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

    print(f"[段階的リストア完了] 成功: {len(success_logs)}件 / 失敗: {len(fail_logs)}件")
    return success_logs, fail_logs


def find_approval_panel_channel(guild: discord.Guild):
    """
    承認申請パネルを送信可能なチャンネルを探します。
    APPROVAL_PANEL_CHANNEL_NAME と一致するものを優先し、見つからない場合はメッセージ送信許可のあるチャンネルを返します。
    """
    channel = discord.utils.find(
        lambda c: c.name == APPROVAL_PANEL_CHANNEL_NAME and isinstance(c, discord.TextChannel),
        guild.text_channels
    )
    if channel:
        return channel
    for ch in guild.text_channels:
        perms = ch.permissions_for(guild.me)
        if perms.send_messages:
            return ch
    return None


async def send_approval_panel(guild: discord.Guild):
    """サーバーに「Bot導入の利用申請パネル」を送信し、チャンネルIDを設定保存します。"""
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


# ====================================================================
# 荒らし対策 (Anti-nuke) ロジック
# ====================================================================

DEFAULT_ANTINUKE_CONFIG = {
    "enabled":           False,
    "threshold_count":   3,
    "threshold_seconds": 10,
    "action":            "role_strip",  # "role_strip" (ロール剥奪) または "ban" (BAN)
    "exempt_roles":      [],
    "log_channel":       None,
}


def get_antinuke_config(all_data: dict, guild_id_str: str) -> dict:
    """指定されたサーバーの Anti-nuke 設定を初期化・取得します。"""
    cfg = get_guild_config(all_data, guild_id_str)
    if "antinuke" not in cfg:
        cfg["antinuke"] = DEFAULT_ANTINUKE_CONFIG.copy()
    else:
        for k, v in DEFAULT_ANTINUKE_CONFIG.items():
            if k not in cfg["antinuke"]:
                cfg["antinuke"][k] = v
    return cfg["antinuke"]


# 履歴記録用: {guild_id: {user_id: {action_type: [timestamps]}}}
guild_id_to_user_id_to_action_type = collections.defaultdict(
    lambda: collections.defaultdict(lambda: collections.defaultdict(list))
)

# 処置が連続で重複発動しないためのロック用セット: {(guild_id, user_id)}
_already_handled: set[tuple[int, int]] = set()


def _record_action(guild_id: int, user_id: int, action_type: str) -> int:
    """監査ログの操作タイムスタンプを記録し、直近60秒より古い履歴を削除します。"""
    now = time.time()
    history = guild_id_to_user_id_to_action_type[guild_id][user_id][action_type]
    history.append(now)

    cutoff = now - 60
    while history and history[0] < cutoff:
        history.pop(0)

    return len(history)


def _count_within_window(guild_id: int, user_id: int, action_type: str, window_seconds: int) -> int:
    """指定された秒数（窓幅）の中に含まれる操作回数をカウントします。"""
    now = time.time()
    history = guild_id_to_user_id_to_action_type[guild_id][user_id][action_type]
    cutoff = now - window_seconds
    return sum(1 for t in history if t >= cutoff)


async def _strip_roles(guild: discord.Guild, member: discord.Member):
    """メンバーから危険な管理者・編集権限を持つロールをすべて剥奪します。"""
    dangerous_perms = [
        "administrator", "manage_guild", "manage_channels", "manage_roles",
        "ban_members", "kick_members", "manage_webhooks"
    ]
    roles_to_remove = []
    for role in member.roles:
        if role.is_default():
            continue
        if any(getattr(role.permissions, p, False) for p in dangerous_perms):
            roles_to_remove.append(role)

    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove, reason="antinuke: 緊急ロール剥奪")
        except discord.Forbidden:
            pass
        except Exception:
            pass


async def _handle_nuke_detected(guild: discord.Guild, suspect: discord.Member, action_type: str, cfg: dict):
    """荒らし連続操作を検出した際の処置（ロール剥奪 / BAN）を実行し、ログチャンネルやオーナーDMに通知します。"""
    key = (guild.id, suspect.id)
    if key in _already_handled:
        return
    _already_handled.add(key)

    action_label = {
        "ban_member":     "メンバーBAN",
        "channel_delete": "チャンネル削除",
        "role_delete":    "ロール削除",
        "webhook_create": "Webhook作成",
    }.get(action_type, action_type)

    result_text = ""

    try:
        if cfg["action"] == "ban":
            try:
                await guild.ban(suspect, reason=f"antinuke: {action_label}の連続実行を検出")
                result_text = f"{suspect} をBANしました。"
            except discord.Forbidden:
                result_text = f"{suspect} のBANに失敗しました（権限不足）。ロール剥奪を試みます。"
                await _strip_roles(guild, suspect)
            except Exception as e:
                result_text = f"{suspect} のBAN中にエラー: {e}。ロール剥奪を試みます。"
                await _strip_roles(guild, suspect)
        else:
            await _strip_roles(guild, suspect)
            result_text = f"{suspect} の危険な権限を持つロールを剥奪しました。"
    except Exception as e:
        result_text = f"自動対応中にエラーが発生しました: {e}"

    # 通知用 Embed の構築
    embed = discord.Embed(
        title="antinuke: 不審な操作を検出しました",
        color=discord.Color.red()
    )
    embed.add_field(name="検出内容", value=f"{action_label}が短時間に連続実行されました", inline=False)
    embed.add_field(name="対象ユーザー", value=f"{suspect.mention} (`{suspect.id}`)", inline=False)
    embed.add_field(name="実行した対応", value=result_text, inline=False)
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text=f"サーバー: {guild.name}")

    # サーバーのログチャンネルへ送信
    all_data = load_data()
    cfg_full = get_antinuke_config(all_data, str(guild.id))
    log_ch_id = cfg_full.get("log_channel")
    if log_ch_id:
        ch = guild.get_channel(log_ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

    # BotオーナーへのDM通知
    try:
        client = guild._state._get_client()
        if client.owner_id is None:
            app_info = await client.application_info()
            client.owner_id = app_info.owner.id
        owner = client.get_user(client.owner_id) or await client.fetch_user(client.owner_id)
        if owner:
            await owner.send(embed=embed)
    except Exception:
        pass

    print(f"[antinuke] {guild.name}: {suspect} による {action_label} の連続実行を検出しました。")

    # 処置後、5分間（300秒）は同一人物への重複発動を防止するためのクールダウン
    await asyncio.sleep(300)
    _already_handled.discard(key)


def _is_exempt(member: discord.Member, guild: discord.Guild, cfg: dict, owner_id: int) -> bool:
    """荒らし検出処置の免除対象（Botオーナー、サーバーオーナー、Bot自身、免除ロール保持者）かどうかを判定します。"""
    if member.id == owner_id:
        return True
    if member.id == guild.owner_id:
        return True
    if member.bot:
        return True
    exempt_role_ids = set(cfg.get("exempt_roles", []))
    if exempt_role_ids and any(r.id in exempt_role_ids for r in member.roles):
        return True
    return False


def _build_antinuke_status_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    """Anti-nuke 設定状況のステータス Embed を構築します。"""
    embed = discord.Embed(
        title=f"{guild.name} - antinuke 設定状況",
        color=discord.Color.blue() if cfg["enabled"] else discord.Color.greyple()
    )
    embed.add_field(name="状態", value="有効" if cfg["enabled"] else "無効", inline=True)
    embed.add_field(
        name="対応レベル",
        value="BANも試行" if cfg["action"] == "ban" else "ロール剥奪のみ",
        inline=True
    )
    embed.add_field(
        name="検出条件",
        value=f"{cfg['threshold_seconds']}秒間に{cfg['threshold_count']}回以上",
        inline=True
    )

    log_ch_id = cfg.get("log_channel")
    log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
    embed.add_field(
        name="通知先チャンネル",
        value=log_ch.mention if log_ch else "未設定（オーナーDMのみ）",
        inline=False
    )

    exempt_role_ids = cfg.get("exempt_roles", [])
    exempt_roles = [guild.get_role(rid) for rid in exempt_role_ids]
    exempt_roles = [r for r in exempt_roles if r]
    embed.add_field(
        name="免除ロール",
        value=", ".join(r.mention for r in exempt_roles) if exempt_roles else "なし",
        inline=False
    )

    embed.add_field(
        name="監視対象の操作",
        value="メンバーBAN / チャンネル削除 / ロール削除 / Webhook作成",
        inline=False
    )
    return embed


# ====================================================================
# セクション 6: ボットイベントリスナー
# ====================================================================

@bot.event
async def on_ready():
    """Bot起動完了時に呼び出されます。ビューの永続化再登録やプレゼンス設定を行います。"""
    # 認証ボタンビューの再登録
    bot.add_view(VerifyButtonView())
    all_data = load_data()

    # 各承認要求関係ビューの再登録
    for guild_id_str, config in all_data.items():
        if guild_id_str == "user_apps":
            continue
        if config.get("approval_status") in ("pending", "pending_review"):
            bot.add_view(ApprovalRequestView(guild_id=int(guild_id_str)))
            panel_ch_id = config.get("approval_panel_channel_id") or 0
            bot.add_view(ApprovalDecisionView(guild_id=int(guild_id_str), panel_channel_id=panel_ch_id))

    # オーナー情報の解決
    if bot.owner_id is None:
        try:
            app_info = await bot.application_info()
            bot.owner_id = app_info.owner.id
            print(f"[システム] オーナーIDを確定しました: {bot.owner_id}")
        except Exception as e:
            print(f"[警告] オーナー情報の取得に失敗しました: {e}")

    # 初期プレゼンスの更新
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
    """Botが新しくサーバーに参加した際に呼び出されます。ステータス更新と承認申請パネルを送信します。"""
    print(f"[サーバー参加] {guild.name} (ID: {guild.id}) に導入されました。")
    await update_bot_status(bot)

    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))
    cfg["approval_status"] = "pending"
    save_data(all_data)

    await send_approval_panel(guild)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    """Botがサーバーから脱退（キック）された際に呼び出され、ステータスを更新します。"""
    print(f"[サーバー脱退] {guild.name} (ID: {guild.id}) から削除されました。")
    await update_bot_status(bot)


@bot.event
async def on_message(message: discord.Message):
    """
    メッセージ受信時に呼び出されます。
    承認サーバーのみ、指定チャンネルの『メッセージ自動転送』および『自動返信ロールメンション（本文引用付き）』を実行します。
    """
    if message.author.bot or not message.guild:
        return
    guild_id_str = str(message.guild.id)
    all_data = load_data()
    
    if guild_id_str in all_data:
        guild_config = all_data[guild_id_str]

        # モデレーターはAuto-Modの対象外
        is_mod = False
        if message.author.guild_permissions.administrator:
            is_mod = True
        elif message.author.id in guild_config.get("allowed_users", []):
            is_mod = True
        elif message.author.id == bot.owner_id:
            is_mod = True
        else:
            global_cfg = get_global_config(all_data)
            if message.author.id in global_cfg.get("trusted_users", []):
                is_mod = True

        if not is_mod:
            # 1. 自動モデレーション: スパム検知 (5秒以内に5回でタイムアウト)
            if guild_config.get("automod_spam_enabled", False):
                now = datetime.datetime.now().timestamp()
                cache_key = f"{message.guild.id}-{message.author.id}"
                if not hasattr(bot, "spam_cache"):
                    bot.spam_cache = {}
                history = bot.spam_cache.get(cache_key, [])
                history = [t for t in history if now - t < 5.0]
                history.append(now)
                bot.spam_cache[cache_key] = history

                if len(history) >= 5:
                    try:
                        await message.author.timeout(datetime.timedelta(minutes=10), reason="自動モデレーション: 短時間大量送信スパム")
                        # limitを増やして、短時間に送信されたすべてのスパムメッセージを確実に削除する
                        await message.channel.purge(limit=20, check=lambda m: m.author == message.author)
                        bot.spam_cache[cache_key] = []
                        await message.channel.send(f"⚠️ {message.author.mention} をスパム検知のため一時ミュートし、メッセージを削除しました。")
                        return
                    except:
                        pass

            # 2. 自動モデレーション: 招待リンク削除
            if guild_config.get("automod_invite_enabled", False):
                content_lower = message.content.lower()
                if "discord.gg/" in content_lower or "discord.com/invite/" in content_lower or "discord.me/" in content_lower or "dsc.gg/" in content_lower:
                    try:
                        await message.delete()
                        await message.channel.send(f"⚠️ {message.author.mention} 招待リンクの送信は許可されていません。", delete_after=5)
                        return
                    except:
                        pass

            # 3. 自動モデレーション: NGワード検知
            if guild_config.get("automod_ng_words_enabled", False):
                ng_words = guild_config.get("ng_words", [])
                if any(ng in message.content for ng in ng_words if ng):
                    try:
                        await message.delete()
                        await message.channel.send(f"⚠️ {message.author.mention} NGワードが含まれているため削除されました。", delete_after=5)
                        return
                    except:
                        pass


        # 承認済みサーバーでない場合は、!コマンド処理のみ受け付けてメッセージ処理はスルー
        if guild_config.get("approval_status") != "approved":
            await bot.process_commands(message)
            return
        
        # 4. メッセージ自動転送処理
        from_id = guild_config.get("from_channel")
        to_id = guild_config.get("to_channel")
        if from_id and to_id and message.channel.id == from_id:
            if message.author.guild_permissions.administrator:
                to_channel = message.guild.get_channel(to_id)
                if to_channel:
                    await to_channel.send(message.content)
                
        # 5. 自動返信ロールメンション処理
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
                except discord.Forbidden:
                    try:
                        await message.channel.send(
                            full_reply_text,
                            allowed_mentions=discord.AllowedMentions(roles=[role])
                        )
                    except:
                        pass

    await bot.process_commands(message)


async def _send_mod_log(guild: discord.Guild, embed: discord.Embed):
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))
    log_ch_id = cfg.get("mod_log_channel_id")
    if log_ch_id:
        ch = guild.get_channel(log_ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except:
                pass

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild: return
    embed = discord.Embed(title="[ログ] メッセージ削除", description=f"**送信者:** {message.author.mention}\n**チャンネル:** {message.channel.mention}", color=discord.Color.red())
    embed.add_field(name="内容", value=message.content or "（内容なし / Embed・画像など）", inline=False)
    await _send_mod_log(message.guild, embed)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content: return
    embed = discord.Embed(title="[ログ] メッセージ編集", description=f"**送信者:** {before.author.mention}\n**チャンネル:** {before.channel.mention}\n[メッセージへジャンプ]({after.jump_url})", color=discord.Color.yellow())
    embed.add_field(name="編集前", value=before.content or "（なし）", inline=False)
    embed.add_field(name="編集後", value=after.content or "（なし）", inline=False)
    await _send_mod_log(before.guild, embed)

@bot.event
async def on_member_join(member: discord.Member):
    embed = discord.Embed(title="[ログ] メンバー参加", description=f"{member.mention} (`{member.id}`)", color=discord.Color.green())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="アカウント作成日時", value=member.created_at.strftime("%Y/%m/%d %H:%M:%S UTC"), inline=False)
    await _send_mod_log(member.guild, embed)

@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(title="[ログ] メンバー退出", description=f"{member.mention} (`{member.id}`)", color=discord.Color.dark_gray())
    embed.set_thumbnail(url=member.display_avatar.url)
    await _send_mod_log(member.guild, embed)
@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    """監査ログ作成時に呼び出されます。短期間の連続操作を検知して緊急処置を起動します。"""
    guild = entry.guild
    if not guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(guild.id))
    if not cfg.get("enabled"):
        return

    action_map = {
        discord.AuditLogAction.ban:             "ban_member",
        discord.AuditLogAction.channel_delete:  "channel_delete",
        discord.AuditLogAction.role_delete:     "role_delete",
        discord.AuditLogAction.webhook_create:  "webhook_create",
    }
    action_type = action_map.get(entry.action)
    if action_type is None:
        return

    user = entry.user
    if user is None:
        return

    member = guild.get_member(user.id)
    if member is None:
        return

    if bot.owner_id is None:
        try:
            app_info = await bot.application_info()
            bot.owner_id = app_info.owner.id
        except Exception:
            pass

    # 免除対象者は処理しない
    if _is_exempt(member, guild, cfg, bot.owner_id):
        return

    # 操作件数を記録して判定
    _record_action(guild.id, member.id, action_type)
    threshold_count   = cfg.get("threshold_count", 3)
    threshold_seconds = cfg.get("threshold_seconds", 10)

    recent_count = _count_within_window(guild.id, member.id, action_type, threshold_seconds)

    if recent_count >= threshold_count:
        asyncio.create_task(_handle_nuke_detected(guild, member, action_type, cfg))


# ====================================================================
# セクション 7: テキストコマンド
# ====================================================================

@bot.command(name="sync")
@commands.is_owner()
async def sync_command(ctx):
    """
    【オーナー限定・チャットコマンド】スラッシュコマンドの同期を行います。
    使い方:
      !sync       → このサーバーに即時同期（数秒で反映・テスト用）
      !sync global → 全サーバーにグローバル同期（反映まで最大1時間）
      !sync clear  → このサーバーのギルドコマンドをクリア
    """
    arg = ctx.message.content.replace("!sync", "").strip().lower()

    if arg == "global":
        await ctx.send("全サーバーへグローバル同期中... 反映まで最大1時間かかります。")
        try:
            synced = await bot.tree.sync()
            await ctx.send(f"グローバル同期完了: {len(synced)}個のコマンドを同期しました。")
        except discord.errors.HTTPException as e:
            await ctx.send(f"Discord側で制限がかかっています。5〜10分後に再試行してください。\n`{e}`")

    elif arg == "clear":
        if not ctx.guild:
            await ctx.send("このコマンドはサーバー内で実行してください。")
            return
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        await ctx.send("このサーバーのギルドコマンドをクリアしました。グローバルコマンドのみが有効です。")

    else:
        if not ctx.guild:
            await ctx.send("サーバー内で実行してください。グローバル同期は `!sync global` を使用してください。")
            return
        await ctx.send("このサーバーへ即時同期中...")
        try:
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(
                f"このサーバーへの即時同期が完了しました（{len(synced)}個）。\n"
                "すぐに `/` で確認できます。\n"
                "全サーバーへ反映したい場合は `!sync global` を実行してください（最大1時間）。"
            )
        except discord.errors.HTTPException as e:
            await ctx.send(f"同期に失敗しました。\n`{e}`")


@sync_command.error
async def sync_command_error(ctx, error):
    if isinstance(error, commands.NotOwner):
        await ctx.send("このコマンドはBotの所有者（オーナー）のみ実行できます。")


# ====================================================================
# セクション 8: スラッシュコマンド
# ====================================================================

# --------------------------------------------------------------------
# 1. 一般ユーザー向けコマンド
# --------------------------------------------------------------------

@bot.tree.command(name="help", description="利用可能なコマンド一覧をカテゴリ別に表示します")
async def help_command(interaction: discord.Interaction):
    """利用者が実行可能なコマンド一覧を Embed で DMライク（ephemeral）に返答します。"""
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
        name="一般ユーザー向け機能",
        value=(
            "`/help` : このコマンド一覧をあなただけに表示します\n"
            "`/hello` : Botが挨拶を返します\n"
            "`/search` : 各種検索サイトやWikipediaのリンク・概要を生成します\n"
            "`/my_scan` : サーバー情報、または指定ユーザーの基本情報を確認します"
        ),
        inline=False
    )
    
    embed.add_field(
        name="個人用プライベート機能 (他の人には見えません)",
        value=(
            "`/my_memo` : あなた専用の個人メモを追加・一覧表示・削除・全消去します\n"
            "`/my_clip` : あなた専用のクリップ（テキストやリンク）を保存・管理します"
        ),
        inline=False
    )
    
    if is_admin or is_allowed or is_owner:
        embed.add_field(
            name="管理者・許可ユーザー専用コマンド",
            value=(
                "`/my_scan_channels` : サーバーのチャンネル構造とカスタム権限をスキャンします\n"
                "`/my_audit_perms` : @everyone の不適切な権限をスキャンします\n"
                "`/my_check_url` : URLの安全性をVirusTotalでチェックします\n"
                "`/say` : Botに指定したメッセージを代わりに発言させます"
            ),
            inline=False
        )
    
    if is_admin or is_owner:
        embed.add_field(
            name="サーバー管理者専用コマンド",
            value=(
                "`/server_status` : 現在の各種機能の設定状況を確認します\n"
                "`/server_list_users` : コマンド使用許可リストの確認・編集を行います\n"
                "`/server_create_channel` : 新しいテキストチャンネルを作成します\n"
                "`/server_role_panel` : 指定ロールを取得できるボタン付きパネルを設置します\n"
                "`/server_forward_setup` / `reset` : メッセージ自動転送の設定を行います\n"
                "`/server_announce_setup` / `send` : 配信お知らせ機能の設定と送信を行います\n"
                "`/server_verify_setup` / `btn` : メンバー認証用パネルを設置します\n"
                "`/server_mention_setup` / `reset` : 自動返信ロールメンションの設定と解除を行います\n"
                "`/server_backup` : サーバーのロール・チャンネル・権限をJSONバックアップします\n"
                "`/server_restore` : バックアップJSONからサーバー構成を復元します\n"
                "`/antinuke` : 不審な連続操作の自動検出を有効・無効にします\n"
                "`/antinuke_level` : 検出時の対応（ロール剥奪 or BAN）を設定します\n"
                "`/antinuke_threshold` : 検出条件の操作回数・時間幅を設定します\n"
                "`/antinuke_notify` : 通知先チャンネルと免除ロールを設定します\n"
                "`/antinuke_status` : 現在の設定状況を確認します"
            ),
            inline=False
        )
    
    if is_owner:
        embed.add_field(
            name="BOT所有者専用コマンド",
            value=(
                "`!sync` : スラッシュコマンドをDiscord側へ即時同期します (通常チャット形式)\n"
                "`/owner_status` : Botの視聴中ステータス文字をリアルタイムで変更します\n"
                "`/owner_guilds` : 導入中のサーバー一覧を確認し、任意のサーバーから脱退できます\n"
                "`/owner_guild_detail` : サーバーの詳細情報（ch数・ロール数・Bot設定状況）と招待リンクを取得します\n"
                "`/owner_broadcast` : 指定サーバーにEmbedでお知らせを一斉送信します"
            ),
            inline=False
        )
    
    embed.set_footer(text="セキュリティのため、このヘルプは実行したあなたにのみ見えています。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="hello", description="Botが挨拶を返します")
async def hello(interaction: discord.Interaction):
    """実行ユーザーに向けて簡単な挨拶メッセージを送信します。"""
    await interaction.response.send_message(f"こんにちは、{interaction.user.mention}さん。")


@bot.tree.command(name="search", description="各種検索サイトやWikipediaの検索リンクを生成します")
@discord.app_commands.choices(engine=[
    discord.app_commands.Choice(name="Google (ウェブ検索)", value="google"),
    discord.app_commands.Choice(name="YouTube (動画検索)", value="youtube"),
    discord.app_commands.Choice(name="GitHub (コード検索)", value="github"),
    discord.app_commands.Choice(name="X /旧Twitter", value="x"),
    discord.app_commands.Choice(name="Wikipedia (百科事典)", value="wiki")
])
async def search(interaction: discord.Interaction, engine: discord.app_commands.Choice[str], query: str):
    """Wikipediaの概要取得や、各種検索エンジンの直リンクを生成して送信します。"""
    eng = engine.value

    if eng == "wiki":
        await interaction.response.defer(ephemeral=False)
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://ja.wikipedia.org/api/rest_v1/page/summary/{encoded_query}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                embed = discord.Embed(
                    title=f"Wiki検索結果: {data.get('title', query)}", 
                    description=data.get('extract', '概要なし'), 
                    color=discord.Color.blue()
                )
                if "content_urls" in data:
                    embed.url = data["content_urls"]["desktop"]["page"]
                if "thumbnail" in data:
                    embed.set_thumbnail(url=data["thumbnail"]["source"])
                await interaction.followup.send(embed=embed)
        except Exception:
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
    """指定されたユーザーのアカウント作成日・ロール状況、または実行中サーバーの基本情報を取得します。"""
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
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="サーバー名", value=g.name, inline=True)
        embed.add_field(name="サーバーID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="オーナー", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="メンバー数", value=f"{g.member_count} 人", inline=True)
        embed.add_field(name="ブースト状況", value=f"Level {g.premium_tier} ({g.premium_subscription_count}回)", inline=True)
        embed.add_field(name="サーバー作成日", value=discord.utils.format_dt(g.created_at, style="F"), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=False)

# ====================================================================
# 謝罪コマンド（ここに追加）
# ====================================================================

@bot.tree.command(name="apology", description="丁寧な謝罪文を作成して送信します")
async def apology(interaction: discord.Interaction):
    """謝罪コマンド"""
    view = ApologySelectView()
    await interaction.response.send_message(
        embed=view.get_preview_embed(),
        view=view,
        ephemeral=False
    )



# --------------------------------------------------------------------
# 2. 個人用プライベート機能 (ephemeral で実行者自身にのみ返答)
# --------------------------------------------------------------------

@bot.tree.command(name="my_memo", description="あなた専用の個人メモを追加・一覧表示・削除・全消去します")
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="メモを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="選択して削除する", value="delete"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_memo(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    """個人用メモの作成・閲覧・削除を行います。データはJSONに保存され永続化されます。"""
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


@bot.tree.command(name="my_clip", description="あなた専用のクリップ（テキストやリンク）を保存・管理します")
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="クリップを追加する", value="add"),
    discord.app_commands.Choice(name="一覧を表示する", value="list"),
    discord.app_commands.Choice(name="全て消去する", value="clear")
])
async def my_clip(interaction: discord.Interaction, action: discord.app_commands.Choice[str], content: str = None):
    """個人用ブックマーク・クリップの作成・閲覧・削除を行います。"""
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


# --------------------------------------------------------------------
# 3. 管理者・許可ユーザー専用コマンド
# --------------------------------------------------------------------

@bot.tree.command(name="say", description="Botに指定したメッセージを代わりに発言させます")
async def say(interaction: discord.Interaction, message: str):
    """実行ユーザーの代わりに、Botから指定テキストをそのチャンネルに投稿します。"""
    if not await is_admin_or_allowed(interaction): return
    await interaction.response.send_message("メッセージを送信しました。", ephemeral=True)
    await interaction.channel.send(message)


@bot.tree.command(name="my_scan_channels", description="サーバーのチャンネル構造とカスタム権限をスキャンします")
async def my_scan_channels(interaction: discord.Interaction):
    """
    サーバー内のチャンネルをチェックし、
    特定ロールの閲覧制限が個別設定されているカスタム権限チャンネルをリストアップします。
    """
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return
    await interaction.response.defer(ephemeral=True)
    g = interaction.guild
    report = [
        f"**{g.name} チャンネルレポート**", 
        f"カテゴリー: {len(g.categories)} | テキスト: {len(g.text_channels)} | ボイス: {len(g.voice_channels)}\n", 
        "個別権限が設定されているチャンネル:"
    ]
    
    count = 0
    for ch in g.channels:
        if isinstance(ch, discord.CategoryChannel): continue
        if ch.overwrites:
            roles = []
            for target, ow in ch.overwrites.items():
                if isinstance(target, discord.Role):
                    if ow.view_channel is False or ow.read_messages is False: 
                        roles.append(f"制限あり: {target.name}")
                    elif ow.view_channel is True or ow.read_messages is True: 
                        roles.append(f"閲覧可: {target.name}")
            if roles:
                count += 1
                report.append(f"・{ch.mention} -> {', '.join(roles[:3])}")
    if count == 0: 
        report.append("個別設定されたチャンネルはありません。")
    full_rep = "\n".join(report)
    await interaction.followup.send(
        embed=discord.Embed(title="フルスキャン結果", description=full_rep[:1950], color=discord.Color.red()), 
        ephemeral=True
    )


@bot.tree.command(name="my_audit_perms", description="@everyoneの権限設定をスキャンします")
async def my_audit_perms(interaction: discord.Interaction):
    """
    全テキストチャンネルにおける @everyone 権限をチェックし、
    閲覧や発言が許可されているセキュリティ上懸念のある設定をスキャン通知します。
    """
    if not await is_admin_or_allowed(interaction): return
    if not interaction.guild: return
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


@bot.tree.command(name="my_check_url", description="URLの安全性をVirusTotalでチェックします")
async def my_check_url(interaction: discord.Interaction, url: str):
    """VirusTotal APIを使用して、指定されたURLが安全かどうか（危険・不審判定）をチェックします。"""
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


# --------------------------------------------------------------------
# 4. サーバー管理者専用コマンド (ギルド管理者専用)
# --------------------------------------------------------------------

@bot.tree.command(name="server_status", description="現在の各種機能の設定状況を確認します")
async def server_status(interaction: discord.Interaction):
    """Botの機能（メッセージ転送、メンバー認証、お知らせ、自動返信など）の有効無効設定ステータスを表示します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    g = interaction.guild
    g_id_str = str(g.id)
    all_data = load_data()
    cfg = get_guild_config(all_data, g_id_str)

    embed = discord.Embed(title=f"{g.name} - 設定状況", description="このサーバーで有効化されている設定一覧です。", color=discord.Color.blue())
    if g.icon: 
        embed.set_thumbnail(url=g.icon.url)

    approval_status = cfg.get("approval_status", "pending")
    approval_label = {"approved": "許可済み", "pending_review": "所有者確認待ち", "pending": "未申請"}.get(approval_status, approval_status)
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
    """管理者以外の非管理者ユーザーにコマンド使用権限を与えるリストを管理・表示します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    g_id = str(interaction.guild.id)
    all_data = load_data()
    config = get_guild_config(all_data, g_id)
    embed = create_user_list_embed(config.get("allowed_users", []))
    await interaction.response.send_message(embed=embed, view=UserManageView(), ephemeral=True)


@bot.tree.command(name="server_create_channel", description="新しいテキストチャンネルを作成します")
async def server_create_channel(interaction: discord.Interaction, name: str, category: discord.CategoryChannel = None):
    """指定された名前とカテゴリー（任意）で新しくテキストチャンネルを作成します。"""
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
    """メンバーが任意でクリックして付与・解除できるロール選択式ボタンパネルを作成・送信します。"""
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
    """特定のチャンネルに書かれた内容を、別の指定チャンネルへBotがオウム返し転送する設定を行います。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = from_channel.id, to_channel.id
    save_data(all_data)
    await interaction.response.send_message("転送設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_forward_reset", description="チャンネルの転送設定を解除します")
async def server_forward_reset(interaction: discord.Interaction):
    """メッセージ自動転送設定を解除・無効化します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["from_channel"], cfg["to_channel"] = None, None
    save_data(all_data)
    await interaction.response.send_message("転送設定を解除しました。", ephemeral=True)


@bot.tree.command(name="server_announce_setup", description="お知らせ用のチャンネルとロールを設定します")
async def server_announce_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    """お知らせ配信で使用する宛先テキストチャンネルと、自動メンション対象ロールを設定します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["announce_channel"], cfg["announce_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("お知らせ設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_announce_send", description="設定されたチャンネルにロールメンション付きでお知らせを送信します")
async def server_announce_send(interaction: discord.Interaction, message: str):
    """設定されたお知らせ用チャンネルに、ロールメンション付きで告知メッセージを送信します。"""
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
    """メンバー認証パネルを送信するチャンネルと、認証成功時に付与するロールを設定します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["verify_channel"], cfg["verify_role"] = channel.id, role.id
    save_data(all_data)
    await interaction.response.send_message("認証設定を保存しました。", ephemeral=True)


@bot.tree.command(name="server_verify_btn", description="設定されたチャンネルに認証用ボタンパネルを送信します")
async def server_verify_btn(interaction: discord.Interaction, title: str = "サーバー認証", description: str = "ボタンを押すと認証が完了します。", image_file: discord.Attachment = None):
    """設定された認証チャンネルに、認証ボタン付きのパネルメッセージ（画像添付可）を送信します。"""
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


@bot.tree.command(name="server_mention_setup", description="指定chへの投稿時、指定メッセージ＆指定ロールで元の文章を含めて返信（Reply）します")
async def server_mention_setup(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role, text: str):
    """指定チャンネルへの新規書き込みに対して、Botが引用インライン返信しつつ指定ロールをメンションする機能です。"""
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
            "自動返信ロールメンション（本文引用付き）を設定しました。\n"
            f"・監視チャンネル: {channel.mention}\n"
            f"・通知するロール: {role.mention}\n"
            f"・返信するテキスト: `{text}`\n"
            "誰かが書き込むと、Botがメッセージを引用しながらロールメンションを付けてインライン返信します。", 
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"設定の保存中にエラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="server_mention_reset", description="自動ロールメンションの監視・返信設定を解除します")
async def server_mention_reset(interaction: discord.Interaction):
    """自動返信ロールメンションの監視設定を解除・初期化します。"""
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


@bot.tree.command(
    name="server_backup",
    description="サーバーのロール・チャンネル構成・権限設定をJSONファイルとしてバックアップします"
)
async def server_backup(interaction: discord.Interaction):
    """現在のサーバーの設定（ロール、チャンネル構造、権限）をJSONファイル化して保存用に送信します。"""
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    await interaction.response.defer(ephemeral=True)

    try:
        backup_data = await _build_backup(interaction.guild)
    except Exception as e:
        await interaction.followup.send(f"バックアップ中にエラーが発生しました: {e}", ephemeral=True)
        return

    json_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
    file_obj   = io.BytesIO(json_bytes)

    timestamp  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename   = f"backup_{interaction.guild.id}_{timestamp}.json"

    embed = discord.Embed(
        title="サーバーバックアップ完了",
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
            f"カテゴリー数: **{len(backup_data['categories'])}個**\n"
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
            "サーバーが破壊された場合は `/server_restore` にこのファイルを添付すると復元できます。"
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


@bot.tree.command(
    name="server_restore",
    description="バックアップJSONを添付してサーバー構成を復元します"
)
async def server_restore(interaction: discord.Interaction, backup_file: discord.Attachment):
    """
    バックアップJSONファイルを読み込み、サーバーを一旦初期化した上で、
    バックアップされたロールやチャンネル、権限の上書き再構築を行います。
    """
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return

    if not backup_file.filename.endswith(".json"):
        await interaction.response.send_message(
            "JSONファイルを添付してください（拡張子: `.json`）",
            ephemeral=True
        )
        return

    if backup_file.size > 5 * 1024 * 1024:
        await interaction.response.send_message("ファイルサイズが大きすぎます（上限: 5MB）", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        raw = await backup_file.read()
        backup_data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        await interaction.followup.send(f"JSONの読み込みに失敗しました: {e}", ephemeral=True)
        return

    if "meta" not in backup_data or "roles" not in backup_data:
        await interaction.followup.send(
            "このファイルは有効なバックアップファイルではありません。",
            ephemeral=True
        )
        return

    meta = backup_data["meta"]
    guild_match = meta.get("guild_id") == interaction.guild.id

    embed = discord.Embed(
        title="[ サーバー段階的リストア確認 ]",
        description=(
            "バックアップデータを確認しました。\n\n"
            "-- この操作の動作（全削除はしません） --\n"
            "(1) バックアップにあるロール  -> 既存なら更新、なければ新規作成\n"
            "(2) バックアップにないロール  -> 削除（@everyone・Bot連携ロールを除く）\n"
            "(3) カテゴリも同様に 更新 / 新規作成 / 削除\n"
            "(4) チャンネルも同様に 更新 / 新規作成 / 削除\n\n"
            "* バックアップにないチャンネル・ロールは削除されます。\n"
            "* この操作は取り消せません。"
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="バックアップ元サーバー", value=meta.get("guild_name", "不明"), inline=True)
    embed.add_field(
        name="サーバー一致",
        value="[一致] 同じサーバー" if guild_match else "[注意] 別のサーバーのバックアップです",
        inline=True
    )
    embed.add_field(name="バックアップ日時", value=meta.get("backed_up_at", "不明")[:19].replace("T", " ") + " UTC", inline=False)
    embed.add_field(
        name="復元対象（バックアップ内容）",
        value=(
            f"ロール    : {len(backup_data.get('roles', []))}個\n"
            f"カテゴリ  : {len(backup_data.get('categories', []))}個\n"
            f"テキストch: {len(backup_data.get('text_channels', []))}個\n"
            f"ボイスch  : {len(backup_data.get('voice_channels', []))}個"
        ),
        inline=False
    )
    embed.set_footer(text="「段階的にリストアを実行する」を押すと処理が開始されます。サーバーが空になることはありません。")

    view = RestoreConfirmView(backup_data)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    print(f"[リストア確認] {interaction.guild.name} でリストア確認画面を表示 (by {interaction.user})")


@bot.tree.command(
    name="server_copy",
    description="【許可制】指定した別のサーバーの構成（ロール・チャンネル等）をこのサーバーに上書きコピーします"
)
async def server_copy(interaction: discord.Interaction, コピー元のサーバーid: str):
    """Botが参加している別のサーバーから現在のサーバーへ、構造のコピー（段階的リストア）を行います。"""
    if not await is_trusted_user(interaction):
        return
    if not interaction.guild:
        return

    try:
        source_guild_id = int(コピー元のサーバーid)
    except ValueError:
        await interaction.response.send_message("サーバーIDは数値で入力してください。", ephemeral=True)
        return

    source_guild = interaction.client.get_guild(source_guild_id)
    if not source_guild:
        await interaction.response.send_message(
            f"指定されたIDのサーバーが見つかりません。Botがそのサーバーに参加しているか確認してください。",
            ephemeral=True
        )
        return

    if source_guild.id == interaction.guild.id:
        await interaction.response.send_message(
            "コピー元とコピー先（現在のサーバー）が同じです。", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        # メモリ上でコピー元のバックアップデータを生成
        backup_data = await _build_backup(source_guild)
    except Exception as e:
        await interaction.followup.send(f"コピー元サーバーのデータ取得に失敗しました: {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title="[ サーバーコピー確認 ]",
        description=(
            f"コピー元サーバー「**{source_guild.name}**」の構造データを取得しました。\n\n"
            "-- この操作の動作（全削除はしません） --\n"
            "(1) コピー元にあるロール  -> 既存なら更新、なければ新規作成\n"
            "(2) コピー元にないロール  -> 削除（@everyone・Bot連携ロールを除く）\n"
            "(3) カテゴリも同様に 更新 / 新規作成 / 削除\n"
            "(4) チャンネルも同様に 更新 / 新規作成 / 削除\n\n"
            "* コピー元にないチャンネル・ロールは削除されます。\n"
            "* この操作は取り消せません。"
        ),
        color=discord.Color.red()
    )
    embed.add_field(name="コピー元", value=f"{source_guild.name} ({source_guild.id})", inline=False)
    embed.add_field(
        name="コピーされる内容",
        value=(
            f"ロール    : {len(backup_data.get('roles', []))}個\n"
            f"カテゴリ  : {len(backup_data.get('categories', []))}個\n"
            f"テキストch: {len(backup_data.get('text_channels', []))}個\n"
            f"ボイスch  : {len(backup_data.get('voice_channels', []))}個"
        ),
        inline=False
    )
    embed.set_footer(text="「段階的にコピーを実行する」を押すと処理が開始されます。サーバーが空になることはありません。")

    view = ServerCopyConfirmView(source_guild.name, backup_data)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    print(f"[サーバーコピー確認] {interaction.guild.name} でコピー確認画面を表示 (by {interaction.user})")

# --------------------------------------------------------------------
# 5. 荒らし対策 (Anti-nuke) コマンド
# --------------------------------------------------------------------

@bot.tree.command(
    name="antinuke",
    description="不審な連続操作の自動検出をON/OFFします"
)
@discord.app_commands.choices(状態=[
    discord.app_commands.Choice(name="有効にする", value="on"),
    discord.app_commands.Choice(name="無効にする", value="off"),
])
async def antinuke(interaction: discord.Interaction, 状態: discord.app_commands.Choice[str]):
    """荒らし緊急自動対応機能（Anti-nuke）の有効無効設定を行います。"""
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))
    cfg["enabled"] = (状態.value == "on")
    save_data(all_data)

    if cfg["enabled"]:
        msg = (
            "antinukeを有効にしました。\n"
            f"現在の検出条件: {cfg['threshold_seconds']}秒間に{cfg['threshold_count']}回の不審な操作で発動します。\n"
            "設定変更は /antinuke_level /antinuke_threshold /antinuke_notify で行えます。"
        )
    else:
        msg = "antinukeを無効にしました。"

    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(
    name="antinuke_level",
    description="検出時の対応レベルを設定します（ロール剥奪 or BAN）"
)
@discord.app_commands.choices(対応=[
    discord.app_commands.Choice(name="ロール剥奪のみ（推奨）", value="role_strip"),
    discord.app_commands.Choice(name="BANを試行する", value="ban"),
])
async def antinuke_level(interaction: discord.Interaction, 対応: discord.app_commands.Choice[str]):
    """連続荒らし行為検出時に、Botが即座に行う緊急アクションのレベルを設定します。"""
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))
    cfg["action"] = 対応.value
    save_data(all_data)

    await interaction.response.send_message(
        f"対応レベルを「{対応.name}」に設定しました。",
        ephemeral=True
    )


@bot.tree.command(
    name="antinuke_threshold",
    description="何秒間に何回の操作で検出するかを設定します（デフォルト: 10秒で3回）"
)
async def antinuke_threshold(
    interaction: discord.Interaction,
    回数: app_commands.Range[int, 1, 50],
    秒数: app_commands.Range[int, 1, 60],
):
    """緊急処置を発動するための、短時間当たりの閾値（操作回数と秒数の組み合わせ）を設定します。"""
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))
    cfg["threshold_count"] = 回数
    cfg["threshold_seconds"] = 秒数
    save_data(all_data)

    await interaction.response.send_message(
        f"検出条件を「{秒数}秒間に{回数}回以上」に変更しました。\n"
        "回数を少なく・秒数を短くすると検出が敏感になります。誤検知が出る場合は緩めてください。",
        ephemeral=True
    )


@bot.tree.command(
    name="antinuke_notify",
    description="通知先チャンネルと、検出から除外するロールを設定します"
)
async def antinuke_notify(
    interaction: discord.Interaction,
    通知先チャンネル: discord.TextChannel = None,
    免除ロール: discord.Role = None,
    免除ロール解除: discord.Role = None,
):
    """荒らし検知時の通知報告先テキストチャンネルと、誤検知防止のためのホワイトリスト免除ロールを設定します。"""
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))

    changed = []

    if 通知先チャンネル is not None:
        cfg["log_channel"] = 通知先チャンネル.id
        changed.append(f"通知先チャンネルを {通知先チャンネル.mention} に設定しました。")

    if 免除ロール is not None:
        exempt = set(cfg.get("exempt_roles", []))
        exempt.add(免除ロール.id)
        cfg["exempt_roles"] = list(exempt)
        changed.append(f"{免除ロール.mention} を免除ロールに追加しました。")

    if 免除ロール解除 is not None:
        exempt = set(cfg.get("exempt_roles", []))
        exempt.discard(免除ロール解除.id)
        cfg["exempt_roles"] = list(exempt)
        changed.append(f"{免除ロール解除.mention} を免除ロールから外しました。")

    if not changed:
        await interaction.response.send_message(
            "変更するパラメータを1つ以上指定してください（通知先チャンネル / 免除ロール / 免除ロール解除）。\n"
            "現在の設定は /antinuke_status で確認できます。",
            ephemeral=True
        )
        return

    save_data(all_data)

    await interaction.response.send_message("\n".join(changed), ephemeral=True)


@bot.tree.command(
    name="antinuke_status",
    description="現在のantinuke設定状況を確認します"
)
async def antinuke_status(interaction: discord.Interaction):
    """緊急荒らし対策機能の、稼働状況および閾値、免除ロール等の設定一覧を Embed で表示します。"""
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))

    embed = _build_antinuke_status_embed(interaction.guild, cfg)
    embed.set_footer(text="設定変更: /antinuke /antinuke_level /antinuke_threshold /antinuke_notify")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --------------------------------------------------------------------
# 6. モデレーション＆管理 (Moderation) コマンド
# --------------------------------------------------------------------

@bot.tree.command(name="warn", description="【モデレーター専用】ユーザーに警告を与えます")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = "理由なし"):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    warnings = cfg.setdefault("warnings", {})
    user_warnings = warnings.setdefault(str(user.id), [])
    
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    user_warnings.append({"reason": reason, "date": timestamp, "moderator": interaction.user.id})
    save_data(all_data)
    
    await interaction.response.send_message(f"[警告] {user.mention} に警告を与えました。\n理由: {reason}")
    try:
        await user.send(f"[警告] **{interaction.guild.name}** で警告を受けました。\n理由: {reason}")
    except:
        pass


@bot.tree.command(name="warnings", description="【モデレーター専用】ユーザーの警告履歴を確認します")
async def warnings(interaction: discord.Interaction, user: discord.Member):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    user_warnings = cfg.get("warnings", {}).get(str(user.id), [])
    
    if not user_warnings:
        await interaction.response.send_message(f"{user.mention} には警告履歴がありません。", ephemeral=True)
        return
        
    embed = discord.Embed(title=f"[ 警告履歴: {user.display_name} ]", color=discord.Color.orange())
    for i, w in enumerate(user_warnings, 1):
        date = w["date"][:19].replace("T", " ")
        embed.add_field(name=f"警告 {i} ({date})", value=f"理由: {w['reason']}\n担当: <@{w['moderator']}>", inline=False)
        
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="mute", description="【モデレーター専用】ユーザーを一時的にミュート（タイムアウト）します")
async def mute(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "理由なし"):
    if not await is_moderator(interaction): return
    try:
        duration = datetime.timedelta(minutes=minutes)
        await user.timeout(duration, reason=reason)
        await interaction.response.send_message(f"[ミュート] {user.mention} を {minutes} 分間ミュートしました。\n理由: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("権限が不足しているためミュートできません。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="ban", description="【モデレーター専用】ユーザーをBANします")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "理由なし"):
    if not await is_moderator(interaction): return
    try:
        await user.ban(reason=reason)
        await interaction.response.send_message(f"[BAN] {user.mention} をBANしました。\n理由: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("権限が不足しているためBANできません。", ephemeral=True)


@bot.tree.command(name="kick", description="【モデレーター専用】ユーザーをキックします")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "理由なし"):
    if not await is_moderator(interaction): return
    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(f"[KICK] {user.mention} をキックしました。\n理由: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("権限が不足しているためキックできません。", ephemeral=True)


@bot.tree.command(name="purge", description="【モデレーター専用】現在のチャンネルのメッセージを一括削除します")
async def purge(interaction: discord.Interaction, amount: int):
    if not await is_moderator(interaction): return
    if amount < 1 or amount > 100:
        await interaction.response.send_message("1〜100の範囲で指定してください。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"[一括削除] {len(deleted)} 件のメッセージを削除しました。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("権限が不足しているため削除できません。", ephemeral=True)


@bot.tree.command(name="automod_toggle", description="【モデレーター専用】自動モデレーションのON/OFFを切り替えます")
@discord.app_commands.choices(機能=[
    discord.app_commands.Choice(name="スパム検知", value="spam"),
    discord.app_commands.Choice(name="招待リンク削除", value="invite"),
    discord.app_commands.Choice(name="NGワード検知", value="ngword")
])
async def automod_toggle(interaction: discord.Interaction, 機能: discord.app_commands.Choice[str], 有効化: bool):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    
    key = f"automod_{機能.value}_enabled"
    if 機能.value == "ngword":
        key = "automod_ng_words_enabled"
        
    cfg[key] = 有効化
    save_data(all_data)
    
    status = "ON" if 有効化 else "OFF"
    await interaction.response.send_message(f"[設定変更] 自動モデレーション「{機能.name}」を **{status}** に設定しました。", ephemeral=True)


@bot.tree.command(name="automod_ngword_add", description="【モデレーター専用】NGワードを追加します")
async def automod_ngword_add(interaction: discord.Interaction, word: str):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ng_words = set(cfg.get("ng_words", []))
    ng_words.add(word)
    cfg["ng_words"] = list(ng_words)
    save_data(all_data)
    await interaction.response.send_message(f"[追加] NGワードに「{word}」を追加しました。", ephemeral=True)


@bot.tree.command(name="automod_ngword_remove", description="【モデレーター専用】NGワードを削除します")
async def automod_ngword_remove(interaction: discord.Interaction, word: str):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    ng_words = set(cfg.get("ng_words", []))
    ng_words.discard(word)
    cfg["ng_words"] = list(ng_words)
    save_data(all_data)
    await interaction.response.send_message(f"[削除] NGワードから「{word}」を削除しました。", ephemeral=True)


@bot.tree.command(name="modlog_set", description="【モデレーター専用】監査ログの出力先チャンネルを設定します")
async def modlog_set(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not await is_moderator(interaction): return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    
    if channel:
        cfg["mod_log_channel_id"] = channel.id
        msg = f"[設定完了] 監査ログの出力先を {channel.mention} に設定しました。"
    else:
        cfg["mod_log_channel_id"] = None
        msg = "[設定解除] 監査ログの出力を無効にしました。"
        
    save_data(all_data)
    await interaction.response.send_message(msg, ephemeral=True)
# --------------------------------------------------------------------
# 6. BOT所有者専用コマンド (オーナー限定)
# --------------------------------------------------------------------

@bot.tree.command(name="owner_status", description="【オーナー限定】Botの視聴中ステータスの文字をリアルタイムで変更します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_status(interaction: discord.Interaction, text: str):
    """【オーナー限定】Botのプレゼンス「〜を視聴中」の文言をカスタム設定します。'reset'で初期化されます。"""
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
    """【オーナー限定】現在Botが導入されているサーバー一覧を閲覧し、セレクトメニューから脱退指示を実行します。"""
    if not await is_owner_check(interaction):
        return

    guilds = list(interaction.client.guilds)

    if not guilds:
        await interaction.response.send_message("現在、どのサーバーにも導入されていません。", ephemeral=True)
        return

    embed = build_guild_list_embed(guilds, page=0)
    view = GuildListView(guilds, page=0)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="owner_guild_detail", description="【オーナー限定】サーバーの詳細情報と招待リンクを取得します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_guild_detail(interaction: discord.Interaction):
    """【オーナー限定】選択した導入中サーバーの、管理者設定、メンバー数詳細、使い捨て招待リンク等の詳細監査を行います。"""
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


@bot.tree.command(name="owner_broadcast", description="【オーナー限定】指定サーバーにEmbedでおお知らせを一斉送信します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_broadcast(interaction: discord.Interaction):
    """【オーナー限定】指定または全サーバーへ向け、Bot開発者発信のアナウンス Embed を一斉マルチポスト送信します。"""
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


# ====================================================================
# セクション 7: 信頼されたユーザー（Trusted Users）管理コマンド
# ====================================================================

@bot.tree.command(name="owner_trust_add", description="【オーナー限定】強力なコマンドを実行できる信頼ユーザーを追加します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_trust_add(interaction: discord.Interaction, user: discord.User):
    """【オーナー限定】指定したユーザーに、server_copy などの強力なコマンドを実行する権限を付与します。"""
    if not await is_owner_check(interaction):
        return

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    trusted = global_cfg.setdefault("trusted_users", [])

    if user.id in trusted:
        await interaction.response.send_message(f"{user.mention} は既に信頼リストに追加されています。", ephemeral=True)
        return

    trusted.append(user.id)
    save_data(all_data)
    await interaction.response.send_message(f"{user.mention} を信頼リストに追加しました。", ephemeral=True)


@bot.tree.command(name="owner_trust_remove", description="【オーナー限定】信頼ユーザーリストから削除します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_trust_remove(interaction: discord.Interaction, user: discord.User):
    """【オーナー限定】指定したユーザーから、強力なコマンドの実行権限を剥奪します。"""
    if not await is_owner_check(interaction):
        return

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    trusted = global_cfg.get("trusted_users", [])

    if user.id not in trusted:
        await interaction.response.send_message(f"{user.mention} は信頼リストに存在しません。", ephemeral=True)
        return

    trusted.remove(user.id)
    save_data(all_data)
    await interaction.response.send_message(f"{user.mention} を信頼リストから削除しました。", ephemeral=True)


@bot.tree.command(name="owner_trust_list", description="【オーナー限定】現在の信頼ユーザー一覧を表示します")
@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
@app_commands.allowed_installs(guilds=True, users=False)
async def owner_trust_list(interaction: discord.Interaction):
    """【オーナー限定】現在強力なコマンドを実行できるユーザーの一覧を表示します。"""
    if not await is_owner_check(interaction):
        return

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    trusted = global_cfg.get("trusted_users", [])

    if not trusted:
        await interaction.response.send_message("現在、信頼されたユーザーは登録されていません。", ephemeral=True)
        return

    mentions = [f"<@{uid}> (`{uid}`)" for uid in trusted]
    embed = discord.Embed(
        title="[ 信頼されたユーザー一覧 ]",
        description="\n".join(mentions),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ====================================================================
# Botの起動
# ====================================================================

bot.run(TOKEN)