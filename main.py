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
import aiohttp
import io
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
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY")
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
            "mod_log_channel_id": None,
            "custom_triggers": [],
            "custom_commands": {}
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
        ("mod_log_channel_id", None),
        ("custom_triggers", []),
        ("custom_commands", {})
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

        # BotのオーナーIDの解決（個人所有 / Team所有の両方に対応）
        await resolve_owner_id(client)

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

async def extract_text_from_image(image_url):
    try:
        async with aiohttp.ClientSession() as session:

            async with session.get(image_url) as image_response:
                image_data = await image_response.read()

            form = aiohttp.FormData()
            form.add_field(
                "file",
                image_data,
                filename="image.png"
            )

            async with session.post(
                "https://api.ocr.space/parse/image",
                data=form,
                headers={
                    "apikey": OCR_SPACE_API_KEY
                }
            ) as response:

                result = await response.json()

                if not result.get("ParsedResults"):
                    return ""

                text = ""

                for item in result["ParsedResults"]:
                    text += item.get("ParsedText", "")

                return text.lower()

    except Exception as e:
        print(f"OCR Error: {e}")
        return ""



def get_global_config(all_data: dict) -> dict:
    """グローバル設定（信頼できるユーザーなど）を取得します。"""
    if "global_config" not in all_data:
        all_data["global_config"] = {
            "trusted_users": []
        }
    return all_data["global_config"]


async def resolve_owner_id(client) -> int | None:
    """
    Botの「オーナー」として扱うDiscordユーザーIDを解決します。

    - 個人所有アプリ（Team未使用）の場合:
        application_info().owner.id がそのまま個人ユーザーIDになります。
    - Team所有アプリ（Discord Developer Portalで「Team」に移行した場合）の場合:
        application_info().owner は個人ではなく Team を指すため、
        application_info().team.owner_id（＝そのTeamを作成した個人のユーザーID）を
        優先的にオーナーIDとして採用します。
        これにより、Bot認証（Verification）等の都合でアプリをTeam所有に切り替えても、
        オーナー専用コマンド（!sync, /owner_* , /customtrigger_* , /customcmd_add 等）を
        従来どおりチームオーナー本人が実行できます。

    解決済みの値は client.owner_id にキャッシュされ、以後はAPI呼び出しなしで再利用されます。
    """
    if client.owner_id is not None:
        return client.owner_id

    try:
        app_info = await client.application_info()
    except Exception as e:
        print(f"[警告] application_info の取得に失敗しました: {e}")
        return None

    # Team所有アプリかどうかを判定
    team = getattr(app_info, "team", None)
    if team is not None and getattr(team, "owner_id", None):
        client.owner_id = team.owner_id
        print(f"[システム] Team所有アプリを検出しました。Teamオーナー（{team.owner_id}）をBotオーナーとして採用します。")
    else:
        client.owner_id = app_info.owner.id

    return client.owner_id


async def is_owner_check(interaction: discord.Interaction) -> bool:
    """インタラクションの実行者がBotのオーナーかどうかを判定します。"""
    owner_id = await resolve_owner_id(interaction.client)

    if interaction.user.id != owner_id:
        await interaction.response.send_message("このコマンドはアプリの所有者（オーナー）専用です。", ephemeral=True)
        return False
    return True


async def is_trusted_user(interaction: discord.Interaction) -> bool:
    """実行者がBotのオーナー、またはオーナーによって許可されたユーザーか判定します。"""
    owner_id = await resolve_owner_id(interaction.client)

    user_id = interaction.user.id
    if user_id == owner_id:
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
    owner_id = await resolve_owner_id(interaction.client)

    if interaction.user.id == owner_id:
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
    owner_id = await resolve_owner_id(interaction.client)

    user_id = interaction.user.id
    if user_id == owner_id:
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
    owner_id = await resolve_owner_id(interaction.client)

    if interaction.user.id == owner_id:
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
        owner_id = await resolve_owner_id(client)

        try:
            owner = client.get_user(owner_id) or await client.fetch_user(owner_id)
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
        owner_id = await resolve_owner_id(client)
        if interaction.user.id != owner_id:
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
        owner_id = await resolve_owner_id(client)
        if interaction.user.id != owner_id:
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


class CustomTriggerDeleteSelect(discord.ui.Select):
    """カスタムトリガー（単語自動返信）の削除用セレクトメニューです。"""
    def __init__(self, triggers: list, guild_id: int):
        self.guild_id = guild_id
        options = []
        for i, t in enumerate(triggers):
            short_trigger = t["trigger"] if len(t["trigger"]) <= 40 else t["trigger"][:37] + "..."
            match_label = "完全一致" if t.get("match_type") == "exact" else "部分一致"
            options.append(discord.SelectOption(
                label=f"{i+1}. {short_trigger}"[:100],
                description=f"({match_label}) -> {t['response'][:50]}",
                value=str(i)
            ))
            if i >= 24:
                break
        super().__init__(placeholder="削除するトリガーを選択してください", options=options)

    async def callback(self, interaction: discord.Interaction):
        all_data = load_data()
        cfg = get_guild_config(all_data, str(self.guild_id))
        triggers = cfg.get("custom_triggers", [])

        idx = int(self.values[0])
        if idx < len(triggers):
            removed = triggers.pop(idx)
            save_data(all_data)
            await interaction.response.send_message(
                f"カスタムトリガーを削除しました:\n`{removed['trigger']}` -> `{removed['response']}`",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("エラー: トリガーの削除に失敗しました。", ephemeral=True)


class CustomTriggerDeleteView(discord.ui.View):
    """カスタムトリガー削除用セレクトメニューを保持するビューです。"""
    def __init__(self, triggers: list, guild_id: int):
        super().__init__(timeout=180)
        self.add_item(CustomTriggerDeleteSelect(triggers, guild_id))


class CustomCommandDeleteSelect(discord.ui.Select):
    """カスタムコマンド（/customcmd 名前）の削除用セレクトメニューです。"""
    def __init__(self, commands_dict: dict, guild_id: int):
        self.guild_id = guild_id
        options = []
        for i, (name, response) in enumerate(commands_dict.items()):
            options.append(discord.SelectOption(
                label=name[:100],
                description=response[:80] if response else "",
                value=name
            ))
            if i >= 24:
                break
        super().__init__(placeholder="削除するカスタムコマンドを選択してください", options=options)

    async def callback(self, interaction: discord.Interaction):
        all_data = load_data()
        cfg = get_guild_config(all_data, str(self.guild_id))
        commands_dict = cfg.get("custom_commands", {})

        name = self.values[0]
        if name in commands_dict:
            del commands_dict[name]
            save_data(all_data)
            await interaction.response.send_message(f"カスタムコマンド `/customcmd {name}` を削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message("エラー: カスタムコマンドの削除に失敗しました。", ephemeral=True)


class CustomCommandDeleteView(discord.ui.View):
    """カスタムコマンド削除用セレクトメニューを保持するビューです。"""
    def __init__(self, commands_dict: dict, guild_id: int):
        super().__init__(timeout=180)
        self.add_item(CustomCommandDeleteSelect(commands_dict, guild_id))


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


# --------------------------------------------------------------------
# 謝罪コマンド用 UI コンポーネント
# --------------------------------------------------------------------

APOLOGY_TEMPLATE = (
    "今回の件について深く反省しています。{action_text}、その行動が周囲に不快感を"
    "与えてしまったことを理解しました。本来であれば、内容をしっかりと受け止めた上で"
    "行動するべきであり、軽率な振る舞いだったと痛感しています。"
    "「{comment_text}」という指摘はもっともであり、自分の配慮の足りなさを"
    "恥ずかしく思っています。今後は同じことを繰り返さないよう、{improvement_text}。"
    "また、衝動的に動くのではなく、一度立ち止まって考える習慣を身につけていきます。"
    "このたびは不快な思いをさせてしまい、本当に申し訳ありませんでした。"
    "今後はより慎重に行動し、信頼を損なわないよう努力していきます。"
)

APOLOGY_ACTION_OPTIONS = [
    ("act_comment", "動画視聴中にすぐコメント欄を開いた",
     "動画を視聴している最中に、何も考えずすぐにコメント欄を開いてしまい"),
    ("act_talk", "話の途中で割り込んでしまった",
     "人が話している最中に、最後まで聞かずに割り込んでしまい"),
    ("act_ignore", "注意・指示を聞き流してしまった",
     "注意や指示を受けたにもかかわらず、それをきちんと聞かずに行動してしまい"),
    ("act_loud", "周囲を考えずに大きな声・音を出してしまった",
     "周囲の状況を考えずに、大きな声や音を出してしまい"),
    ("act_late", "連絡・報告をせずに行動してしまった",
     "事前の連絡や報告をせずに、自分の判断だけで行動してしまい"),
    ("act_free", "自由入力で入力する", None),
]

APOLOGY_COMMENT_OPTIONS = [
    ("cmt_open", "コメントをすぐ開くな"),
    ("cmt_listen", "最後まで話を聞きなさい"),
    ("cmt_think", "もう少し考えて行動して"),
    ("cmt_quiet", "周りのことも考えて"),
    ("cmt_report", "先に一声かけてほしい"),
    ("cmt_free", "自由入力で入力する"),
]

APOLOGY_IMPROVEMENT_OPTIONS = [
    ("imp_focus", "動画や話の内容に集中する",
     "まずは動画の内容に集中し、周囲の状況や気持ちを考えた行動を心がけます"),
    ("imp_listen", "最後まで話を聞く", "まずは相手の話を最後まで聞き、自分の行動を見直すよう心がけます"),
    ("imp_check", "行動前に一度確認・相談する",
     "行動する前に一度確認や相談をしてから動くよう心がけます"),
    ("imp_quiet", "周囲への配慮を意識する",
     "まずは周囲の状況に目を向け、配慮を欠かさないよう心がけます"),
    ("imp_report", "事前に報告・連絡をする",
     "行動する前に必ず報告や連絡を入れるよう心がけます"),
    ("imp_free", "自由入力で入力する", None),
]


class ApologyFreeTextModal(discord.ui.Modal, title="自由入力"):
    def __init__(self, parent_view: "ApologyBuilderView", field: str, label: str, placeholder: str):
        super().__init__(title=f"自由入力: {label}")
        self.parent_view = parent_view
        self.field = field
        self.text_input = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.paragraph,
            placeholder=placeholder,
            max_length=200,
            required=True
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.text_input.value.strip()
        self.parent_view.set_value(self.field, value, is_custom=True)
        await interaction.response.edit_message(
            embed=self.parent_view.build_preview_embed(),
            view=self.parent_view
        )


class ApologyActionSelect(discord.ui.Select):
    def __init__(self, parent_view: "ApologyBuilderView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label, _ in APOLOGY_ACTION_OPTIONS
        ]
        super().__init__(placeholder="① やってしまった行動を選択...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "act_free":
            await interaction.response.send_modal(
                ApologyFreeTextModal(
                    self.parent_view, "action", "やってしまった行動",
                    "例: 動画を視聴している最中に、何も考えずすぐにコメント欄を開いてしまい"
                )
            )
            return
        text = next(t for v, _, t in APOLOGY_ACTION_OPTIONS if v == choice)
        self.parent_view.set_value("action", text, is_custom=False)
        await interaction.response.edit_message(
            embed=self.parent_view.build_preview_embed(),
            view=self.parent_view
        )


class ApologyCommentSelect(discord.ui.Select):
    def __init__(self, parent_view: "ApologyBuilderView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in APOLOGY_COMMENT_OPTIONS
        ]
        super().__init__(placeholder="② 指摘された一言を選択...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "cmt_free":
            await interaction.response.send_modal(
                ApologyFreeTextModal(
                    self.parent_view, "comment", "指摘された一言",
                    "例: コメントをすぐ開くな"
                )
            )
            return
        text = next(label for v, label in APOLOGY_COMMENT_OPTIONS if v == choice)
        self.parent_view.set_value("comment", text, is_custom=False)
        await interaction.response.edit_message(
            embed=self.parent_view.build_preview_embed(),
            view=self.parent_view
        )


class ApologyImprovementSelect(discord.ui.Select):
    def __init__(self, parent_view: "ApologyBuilderView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label, _ in APOLOGY_IMPROVEMENT_OPTIONS
        ]
        super().__init__(placeholder="③ 今後の改善行動を選択...", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "imp_free":
            await interaction.response.send_modal(
                ApologyFreeTextModal(
                    self.parent_view, "improvement", "今後の改善行動",
                    "例: まずは動画の内容に集中し、周囲の状況や気持ちを考えた行動を心がけます"
                )
            )
            return
        text = next(t for v, _, t in APOLOGY_IMPROVEMENT_OPTIONS if v == choice)
        self.parent_view.set_value("improvement", text, is_custom=False)
        await interaction.response.edit_message(
            embed=self.parent_view.build_preview_embed(),
            view=self.parent_view
        )


class ApologySendButton(discord.ui.Button):
    def __init__(self, parent_view: "ApologyBuilderView"):
        super().__init__(label="この内容で送信する", style=discord.ButtonStyle.danger, row=3)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if not view.is_ready():
            await interaction.response.send_message(
                "①②③のすべての項目を選択（または自由入力）してから送信してください。",
                ephemeral=True
            )
            return
        final_text = view.build_final_text()
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="謝罪文を送信しました。",
            embed=None,
            view=view
        )
        try:
            await interaction.channel.send(f"{interaction.user.mention}\n{final_text}")
        except discord.Forbidden:
            await interaction.followup.send("このチャンネルへの送信権限がないため、送信に失敗しました。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"送信中にエラーが発生しました: {e}", ephemeral=True)


class ApologyResetButton(discord.ui.Button):
    def __init__(self, parent_view: "ApologyBuilderView"):
        super().__init__(label="選択をリセット", style=discord.ButtonStyle.secondary, row=3)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        view.action_text = None
        view.comment_text = None
        view.improvement_text = None
        view.action_is_custom = False
        view.comment_is_custom = False
        view.improvement_is_custom = False
        await interaction.response.edit_message(embed=view.build_preview_embed(), view=view)


class ApologyCancelButton(discord.ui.Button):
    def __init__(self, parent_view: "ApologyBuilderView"):
        super().__init__(label="キャンセル", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        for item in self.parent_view.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="謝罪文の作成をキャンセルしました。",
            embed=None,
            view=self.parent_view
        )


class ApologyBuilderView(discord.ui.View):
    def __init__(self, author: discord.abc.User):
        super().__init__(timeout=600)
        self.author = author
        self.action_text = None
        self.comment_text = None
        self.improvement_text = None
        self.action_is_custom = False
        self.comment_is_custom = False
        self.improvement_is_custom = False

        self.add_item(ApologyActionSelect(self))
        self.add_item(ApologyCommentSelect(self))
        self.add_item(ApologyImprovementSelect(self))
        self.add_item(ApologySendButton(self))
        self.add_item(ApologyResetButton(self))
        self.add_item(ApologyCancelButton(self))

    def set_value(self, field: str, text: str, is_custom: bool):
        if field == "action":
            self.action_text = text
            self.action_is_custom = is_custom
        elif field == "comment":
            self.comment_text = text
            self.comment_is_custom = is_custom
        elif field == "improvement":
            self.improvement_text = text
            self.improvement_is_custom = is_custom

    def is_ready(self) -> bool:
        return bool(self.action_text and self.comment_text and self.improvement_text)

    def build_final_text(self) -> str:
        return APOLOGY_TEMPLATE.format(
            action_text=self.action_text,
            comment_text=self.comment_text,
            improvement_text=self.improvement_text
        )

    def build_preview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="謝罪文プレビュー",
            color=discord.Color.orange() if not self.is_ready() else discord.Color.green()
        )

        def fmt(text, is_custom):
            if text is None:
                return "（未選択）"
            return f"{text}" + ("（自由入力）" if is_custom else "")

        embed.add_field(name="① やってしまった行動", value=fmt(self.action_text, self.action_is_custom), inline=False)
        embed.add_field(name="② 指摘された一言", value=fmt(self.comment_text, self.comment_is_custom), inline=False)
        embed.add_field(name="③ 今後の改善行動", value=fmt(self.improvement_text, self.improvement_is_custom), inline=False)

        if self.is_ready():
            embed.add_field(name="完成文プレビュー", value=self.build_final_text()[:1024], inline=False)
            embed.set_footer(text="内容を確認し、「この内容で送信する」を押すとこのチャンネルに投稿されます。")
        else:
            embed.set_footer(text="①②③をすべて選択（または自由入力）すると完成文が表示されます。")

        return embed


# ====================================================================
# セクション 5: 内部処理・バックアップ・荒らし対策ヘルパー
# ====================================================================

def _perms_to_int(perms: discord.Permissions) -> int:
    return perms.value


def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict:
    allow, deny = overwrite.pair()
    return {"allow": allow.value, "deny": deny.value}


async def _build_backup(guild: discord.Guild) -> dict:
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
    for attempt in range(3):
        try:
            return await coro
        except discord.HTTPException as e:
            if e.status == 429:
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
    success_logs = []
    fail_logs    = []
    try:
        everyone_val = data.get("everyone_permissions", 0)
        await guild.default_role.edit(permissions=discord.Permissions(everyone_val))
        success_logs.append("@everyone 権限を復元しました")
    except Exception as e:
        fail_logs.append(f"@everyone 権限の復元に失敗: {e}")
    old_role_id_map = {}
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
        await asyncio.sleep(0.5)

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

    old_cat_id_map = {}
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
            for wh_data in ch_data.get("webhooks", []):
                wh = await _safe_api_call(
                    ch.create_webhook(name=wh_data["name"]),
                    fail_logs, f"Webhook「{wh_data['name']}」"
                )
                if wh:
                    success_logs.append(f"  Webhook「{wh_data['name']}」を作成しました")
        await asyncio.sleep(0.5)

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
    success_logs: list[str] = []
    fail_logs:    list[str] = []
    me = guild.me

    def _build_overwrites(raw: dict, role_map: dict) -> dict:
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

    old_role_id_map: dict[int, discord.Role] = {}
    existing_roles_by_name = {
        r.name: r for r in guild.roles
        if not r.is_default() and not r.managed
    }
    bot_role_names = {r.name for r in (me.roles if me else [])}

    backup_role_names: set[str] = set()
    for r_data in sorted(data.get("roles", []), key=lambda r: r["position"]):
        if r_data.get("managed"):
            continue
        rname = r_data["name"]
        backup_role_names.add(rname)
        label = f"ロール「{rname}」"
        existing = existing_roles_by_name.get(rname)
        if existing:
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
            if role is not None or existing:
                success_logs.append(f"{label} を更新しました")
                old_role_id_map[r_data["id"]] = existing
        else:
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

    for rname, role in existing_roles_by_name.items():
        if rname not in backup_role_names and rname not in bot_role_names:
            deleted = await _safe_api_call(
                role.delete(reason="リストア: バックアップにないロールを削除"),
                fail_logs, f"ロール「{rname}」削除"
            )
            if deleted is not None or True:
                success_logs.append(f"ロール「{rname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

    try:
        everyone_val = data.get("everyone_permissions", 0)
        await guild.default_role.edit(permissions=discord.Permissions(everyone_val))
        success_logs.append("@everyone 権限を復元しました")
    except Exception as e:
        fail_logs.append(f"@everyone 権限の復元に失敗: {e}")

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
            await _safe_api_call(
                existing_cat.edit(overwrites=overwrites, reason="リストア: カテゴリ設定を更新"),
                fail_logs, label
            )
            success_logs.append(f"{label} を更新しました")
            old_cat_id_map[c_data["id"]] = existing_cat
        else:
            cat = await _safe_api_call(
                guild.create_category(name=cname, overwrites=overwrites, reason="リストア: カテゴリを新規作成"),
                fail_logs, label
            )
            if cat:
                success_logs.append(f"{label} を新規作成しました")
                old_cat_id_map[c_data["id"]] = cat
        await asyncio.sleep(0.5)

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
                for wh_data in ch_data.get("webhooks", []):
                    wh = await _safe_api_call(
                        ch.create_webhook(name=wh_data["name"]),
                        fail_logs, f"Webhook「{wh_data['name']}」"
                    )
                    if wh:
                        success_logs.append(f"  Webhook「{wh_data['name']}」を作成しました")
        await asyncio.sleep(0.5)

    for chname, ch in existing_text_by_name.items():
        if chname not in backup_text_names:
            await _safe_api_call(
                ch.delete(reason="リストア: バックアップにないchを削除"),
                fail_logs, f"テキストch「#{chname}」削除"
            )
            success_logs.append(f"テキストch「#{chname}」を削除しました（バックアップにないため）")
            await asyncio.sleep(0.3)

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
    "action":            "role_strip",
    "exempt_roles":      [],
    "log_channel":       None,
}


def get_antinuke_config(all_data: dict, guild_id_str: str) -> dict:
    cfg = get_guild_config(all_data, guild_id_str)
    if "antinuke" not in cfg:
        cfg["antinuke"] = DEFAULT_ANTINUKE_CONFIG.copy()
    else:
        for k, v in DEFAULT_ANTINUKE_CONFIG.items():
            if k not in cfg["antinuke"]:
                cfg["antinuke"][k] = v
    return cfg["antinuke"]


guild_id_to_user_id_to_action_type = collections.defaultdict(
    lambda: collections.defaultdict(lambda: collections.defaultdict(list))
)

_already_handled: set[tuple[int, int]] = set()


def _record_action(guild_id: int, user_id: int, action_type: str) -> int:
    now = time.time()
    history = guild_id_to_user_id_to_action_type[guild_id][user_id][action_type]
    history.append(now)
    cutoff = now - 60
    while history and history[0] < cutoff:
        history.pop(0)
    return len(history)


def _count_within_window(guild_id: int, user_id: int, action_type: str, window_seconds: int) -> int:
    now = time.time()
    history = guild_id_to_user_id_to_action_type[guild_id][user_id][action_type]
    cutoff = now - window_seconds
    return sum(1 for t in history if t >= cutoff)


async def _strip_roles(guild: discord.Guild, member: discord.Member):
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

    embed = discord.Embed(
        title="antinuke: 不審な操作を検出しました",
        color=discord.Color.red()
    )
    embed.add_field(name="検出内容", value=f"{action_label}が短時間に連続実行されました", inline=False)
    embed.add_field(name="対象ユーザー", value=f"{suspect.mention} (`{suspect.id}`)", inline=False)
    embed.add_field(name="実行した対応", value=result_text, inline=False)
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text=f"サーバー: {guild.name}")

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

    try:
        client = guild._state._get_client()
        owner_id = await resolve_owner_id(client)
        owner = client.get_user(owner_id) or await client.fetch_user(owner_id)
        if owner:
            await owner.send(embed=embed)
    except Exception:
        pass

    print(f"[antinuke] {guild.name}: {suspect} による {action_label} の連続実行を検出しました。")
    await asyncio.sleep(300)
    _already_handled.discard(key)


def _is_exempt(member: discord.Member, guild: discord.Guild, cfg: dict, owner_id: int) -> bool:
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

    # Opusライブラリを自動検索してロードする（Nixpacks環境対応）
    if not discord.opus.is_loaded():
        import ctypes.util
        import glob
        opus_candidates = (
            glob.glob("/root/.nix-profile/lib/libopus*")
            + glob.glob("/nix/store/*/lib/libopus*")
            + glob.glob("/usr/lib/*/libopus*")
            + glob.glob("/usr/lib/libopus*")
        )
        opus_path = next((p for p in opus_candidates if p.endswith(".so") or ".so." in p), None)
        if opus_path:
            try:
                discord.opus.load_opus(opus_path)
                print(f"[システム] Opusをロードしました: {opus_path}")
            except Exception as e:
                print(f"[警告] Opusのロードに失敗しました: {e}")
        else:
            # ctypes.util.find_library でも試みる
            lib = ctypes.util.find_library("opus")
            if lib:
                try:
                    discord.opus.load_opus(lib)
                    print(f"[システム] Opusをロードしました (ctypes): {lib}")
                except Exception as e:
                    print(f"[警告] Opusのロードに失敗しました (ctypes): {e}")
            else:
                print("[警告] Opusライブラリが見つかりませんでした。音声機能が使えない可能性があります。")

    bot.add_view(VerifyButtonView())
    all_data = load_data()

    for guild_id_str, config in all_data.items():
        if guild_id_str in ("user_apps", "global_config"):
            continue
        if not isinstance(config, dict):
            continue
        try:
            bot.add_view(ApprovalRequestView(guild_id=int(guild_id_str)))
            panel_ch_id = config.get("approval_panel_channel_id") or 0
            bot.add_view(ApprovalDecisionView(guild_id=int(guild_id_str), panel_channel_id=panel_ch_id))
        except Exception:
            pass

    resolved_owner_id = await resolve_owner_id(bot)
    if resolved_owner_id is not None:
        print(f"[システム] オーナーIDを確定しました: {resolved_owner_id}")
    else:
        print("[警告] オーナー情報の取得に失敗しました。")

    try:
        await update_bot_status(bot)
    except Exception as e:
        print(f"初期ステータス設定エラー: {e}")
    
    print("--- 起動完了: 現在のサーバー設定一覧 ---")
    for guild_id_str, config in all_data.items():
        if guild_id_str in ("user_apps", "global_config"):
            continue
        if not isinstance(config, dict):
            continue
        try:
            guild_id_int = int(guild_id_str)
        except ValueError:
            print(f"[警告] 不正なサーバーIDキーをスキップしました: {guild_id_str}")
            continue
        guild = bot.get_guild(guild_id_int)
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
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))
    cfg["approval_status"] = "pending"
    save_data(all_data)
    await send_approval_panel(guild)


@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[サーバー脱退] {guild.name} (ID: {guild.id}) から削除されました。")
    await update_bot_status(bot)


# ====================================================================
# 自動モデレーション共通ヘルパー
# ====================================================================

def _is_automod_target(author: discord.Member, guild_config: dict, all_data: dict) -> bool:
    """
    メッセージ送信者がAutoModの対象かどうかを判定します。
    管理者・許可ユーザー・Botオーナー・信頼ユーザーはすべてスキップします。

    注: ここでは同期関数の制約上 resolve_owner_id() を呼べないため、
    bot.owner_id を直接参照します。on_ready() 内で起動時に
    resolve_owner_id(bot) を実行しキャッシュ済みのため、通常稼働中は
    Team所有アプリでも正しいオーナーID（Teamオーナー）が入っています。
    """
    if author.guild_permissions.administrator:
        return False
    if author.id in guild_config.get("allowed_users", []):
        return False
    if author.id == bot.owner_id:
        return False
    global_cfg = get_global_config(all_data)
    if author.id in global_cfg.get("trusted_users", []):
        return False
    return True


async def _run_automod_checks(message: discord.Message, guild_config: dict) -> bool:
    """
    招待リンク削除・NGワード削除を実行します。
    削除した場合は True を返します（呼び出し元でreturnするため）。
    スパム検知はメッセージ送信時のみ対象のため含めていません。
    """
    # 招待リンク削除
    if guild_config.get("automod_invite_enabled", False):
        content_lower = message.content.lower()
        if any(kw in content_lower for kw in (
            "discord.gg/", "discord.com/invite/", "discord.me/", "dsc.gg/"
        )):
            try:
                await message.delete()
                await message.channel.send(
                    f"⚠️ {message.author.mention} 招待リンクの送信は許可されていません（編集による回避も検知します）。",
                    delete_after=5
                )
            except Exception:
                pass
            return True

    # NGワード削除
    if guild_config.get("automod_ng_words_enabled", False):
        ng_words = guild_config.get("ng_words", [])
        if any(ng in message.content for ng in ng_words if ng):
            try:
                await message.delete()
                await message.channel.send(
                    f"⚠️ {message.author.mention} NGワードが含まれているため削除されました（編集による回避も検知します）。",
                    delete_after=5
                )
            except Exception:
                pass
            return True

    return False


async def _run_custom_triggers(message: discord.Message, guild_config: dict):
    """
    登録されたカスタムトリガー（単語自動返信）をチェックし、一致した場合に返信します。
    完全一致(exact)・部分一致(contains)の両方に対応します。
    最初に一致したトリガー1件のみ返信します。
    """
    triggers = guild_config.get("custom_triggers", [])
    if not triggers:
        return

    content = message.content
    content_lower = content.lower()

    for t in triggers:
        trigger_word = t.get("trigger", "")
        if not trigger_word:
            continue
        match_type = t.get("match_type", "contains")

        if match_type == "exact":
            is_match = content_lower == trigger_word.lower()
        else:
            is_match = trigger_word.lower() in content_lower

        if is_match:
            try:
                await message.channel.send(t.get("response", ""))
            except Exception:
                pass
            return


@bot.event
async def on_message(message: discord.Message):
    """
    メッセージ受信時に呼び出されます。
    自動モデレーション（スパム・招待リンク・NGワード）、メッセージ転送、ロールメンションを処理します。
    """
    if message.author.bot or not message.guild:
        return

    guild_id_str = str(message.guild.id)
    all_data = load_data()

    if guild_id_str in all_data:
        guild_config = all_data[guild_id_str]

        if _is_automod_target(message.author, guild_config, all_data):
            # 1. スパム検知 (5秒以内に5回でタイムアウト)
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
                        await message.channel.purge(limit=20, check=lambda m: m.author == message.author)
                        bot.spam_cache[cache_key] = []
                        await message.channel.send(f"⚠️ {message.author.mention} をスパム検知のため一時ミュートし、メッセージを削除しました。")
                        return
                    except Exception:
                        pass

            # 2. 招待リンク・NGワード削除（共通ヘルパー呼び出し）
            if await _run_automod_checks(message, guild_config):
                return

        # 承認済みサーバーでない場合は !コマンド処理のみ
        if guild_config.get("approval_status") != "approved":
            await bot.process_commands(message)
            return

        # 3. メッセージ自動転送処理
        from_id = guild_config.get("from_channel")
        to_id = guild_config.get("to_channel")
        if from_id and to_id and message.channel.id == from_id:
            if message.author.guild_permissions.administrator:
                to_channel = message.guild.get_channel(to_id)
                if to_channel:
                    await to_channel.send(message.content)

        # 4. 自動返信ロールメンション処理
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
                    except Exception:
                        pass

        # 5. カスタムトリガー自動返信処理
        await _run_custom_triggers(message, guild_config)

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
            except Exception:
                pass


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    embed = discord.Embed(
        title="[ログ] メッセージ削除",
        description=f"**送信者:** {message.author.mention}\n**チャンネル:** {message.channel.mention}",
        color=discord.Color.red()
    )
    embed.add_field(name="内容", value=message.content or "（内容なし / Embed・画像など）", inline=False)
    await _send_mod_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """
    メッセージ編集時に呼び出されます。
    ① 監査ログへの記録
    ② 編集後テキストへの招待リンク・NGワード自動モデレーション
    （スパム検知はメッセージ数が変わらないため対象外）
    """
    if before.author.bot or not before.guild or before.content == after.content:
        return

    # --- ① 監査ログ記録 ---
    embed = discord.Embed(
        title="[ログ] メッセージ編集",
        description=(
            f"**送信者:** {before.author.mention}\n"
            f"**チャンネル:** {before.channel.mention}\n"
            f"[メッセージへジャンプ]({after.jump_url})"
        ),
        color=discord.Color.yellow()
    )
    embed.add_field(name="編集前", value=before.content or "（なし）", inline=False)
    embed.add_field(name="編集後", value=after.content or "（なし）", inline=False)
    await _send_mod_log(before.guild, embed)

    # --- ② 編集後メッセージへの自動モデレーション ---
    guild_id_str = str(after.guild.id)
    all_data = load_data()

    if guild_id_str not in all_data:
        return

    guild_config = all_data[guild_id_str]

    # モデレーターは対象外
    if not _is_automod_target(after.author, guild_config, all_data):
        return

    # 招待リンク・NGワード削除（共通ヘルパー呼び出し）
    await _run_automod_checks(after, guild_config)


@bot.event
async def on_member_join(member: discord.Member):
    embed = discord.Embed(
        title="[ログ] メンバー参加",
        description=f"{member.mention} (`{member.id}`)",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="アカウント作成日時", value=member.created_at.strftime("%Y/%m/%d %H:%M:%S UTC"), inline=False)
    await _send_mod_log(member.guild, embed)


@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(
        title="[ログ] メンバー退出",
        description=f"{member.mention} (`{member.id}`)",
        color=discord.Color.dark_gray()
    )
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

    owner_id_for_exempt = await resolve_owner_id(bot)

    if _is_exempt(member, guild, cfg, owner_id_for_exempt):
        return

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

@bot.tree.command(name="help", description="利用可能なコマンド一覧をカテゴリ別に表示します")
async def help_command(interaction: discord.Interaction):
    owner_id = await resolve_owner_id(interaction.client)
    is_owner = (interaction.user.id == owner_id)

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
            "`/my_scan` : サーバー情報、または指定ユーザーの基本情報を確認します\n"
            "`/apology` : セレクトメニューから謝罪文を組み立てて送信します\n"
            "`/customcmd <名前>` : サーバーに登録されたカスタムコマンドを実行します"
        ),
        inline=False
    )
    embed.add_field(
        name="ボイスチャンネル再生機能",
        value=(
            "`/voice_join` : あなたのいるボイスチャンネルにBotを参加させます\n"
            "`/voice_leave` : ボイスチャンネルから退出させます\n"
            "`/voice_play` : 音声ファイル（添付 または 登録名）を再生します。再生中は一時停止・音量調整・切断ができるパネルが表示されます\n"
            "`/voice_sound_list` : 登録済み音源の一覧を表示します\n"
            "`/voice_sound_add` : 音源ファイルを名前付きで登録します（誰でも使用可能）\n"
            "`/voice_sound_remove` : 登録済み音源を削除します（オーナー限定）"
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
                "`/owner_broadcast` : 指定サーバーにEmbedでお知らせを一斉送信します\n"
                "`/eval` : Pythonコードを実行して結果を返します（デバッグ・管理用）"
            ),
            inline=False
        )
        embed.add_field(
            name="BOT所有者専用 - カスタムコマンド機能",
            value=(
                "`/customtrigger_add` : 特定の単語に自動返信するトリガーを追加します\n"
                "`/customtrigger_remove` : 登録済みトリガーを選択して削除します\n"
                "`/customtrigger_list` : 登録済みトリガー一覧を表示します\n"
                "`/customcmd_add` : 「/customcmd 名前」で動くカスタムコマンドを追加します\n"
                "`/customcmd_remove` : 登録済みカスタムコマンドを選択して削除します\n"
                "`/customcmd_list` : 登録済みカスタムコマンド一覧を表示します\n"
                "`/customcmd <名前>` : 登録したカスタムコマンドを実行します（誰でも使用可）"
            ),
            inline=False
        )
    embed.set_footer(text="セキュリティのため、このヘルプは実行したあなたにのみ見えています。")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="hello", description="Botが挨拶を返します")
async def hello(interaction: discord.Interaction):
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


@bot.tree.command(name="apology", description="セレクトメニューから謝罪文を組み立てて送信します")
async def apology(interaction: discord.Interaction):
    view = ApologyBuilderView(author=interaction.user)
    await interaction.response.send_message(
        embed=view.build_preview_embed(),
        view=view,
        ephemeral=True
    )


@bot.tree.command(name="my_memo", description="あなた専用の個人メモを追加・一覧表示・削除・全消去します")
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


@bot.tree.command(name="my_clip", description="あなた専用のクリップ（テキストやリンク）を保存・管理します")
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


@bot.tree.command(name="say", description="Botに指定したメッセージを代わりに発言させます")
async def say(interaction: discord.Interaction, message: str):
    if not await is_admin_or_allowed(interaction): return
    await interaction.response.send_message("メッセージを送信しました。", ephemeral=True)
    await interaction.channel.send(message)


@bot.tree.command(name="my_scan_channels", description="サーバーのチャンネル構造とカスタム権限をスキャンします")
async def my_scan_channels(interaction: discord.Interaction):
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


@bot.tree.command(name="server_status", description="現在の各種機能の設定状況を確認します")
async def server_status(interaction: discord.Interaction):
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


@bot.tree.command(name="server_backup", description="サーバーのロール・チャンネル構成・権限設定をJSONファイルとしてバックアップします")
async def server_backup(interaction: discord.Interaction):
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
    embed = discord.Embed(title="サーバーバックアップ完了", color=discord.Color.green())
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


@bot.tree.command(name="server_restore", description="バックアップJSONを添付してサーバー構成を復元します")
async def server_restore(interaction: discord.Interaction, backup_file: discord.Attachment):
    if not await is_guild_admin(interaction): return
    if not interaction.guild: return
    if not backup_file.filename.endswith(".json"):
        await interaction.response.send_message("JSONファイルを添付してください（拡張子: `.json`）", ephemeral=True)
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
        await interaction.followup.send("このファイルは有効なバックアップファイルではありません。", ephemeral=True)
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


@bot.tree.command(name="server_copy", description="【許可制】指定した別のサーバーの構成（ロール・チャンネル等）をこのサーバーに上書きコピーします")
async def server_copy(interaction: discord.Interaction, コピー元のサーバーid: str):
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
        await interaction.response.send_message("指定されたIDのサーバーが見つかりません。Botがそのサーバーに参加しているか確認してください。", ephemeral=True)
        return
    if source_guild.id == interaction.guild.id:
        await interaction.response.send_message("コピー元とコピー先（現在のサーバー）が同じです。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
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


@bot.tree.command(name="antinuke", description="不審な連続操作の自動検出をON/OFFします")
@discord.app_commands.choices(状態=[
    discord.app_commands.Choice(name="有効にする", value="on"),
    discord.app_commands.Choice(name="無効にする", value="off"),
])
async def antinuke(interaction: discord.Interaction, 状態: discord.app_commands.Choice[str]):
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


@bot.tree.command(name="antinuke_level", description="検出時の対応レベルを設定します（ロール剥奪 or BAN）")
@discord.app_commands.choices(対応=[
    discord.app_commands.Choice(name="ロール剥奪のみ（推奨）", value="role_strip"),
    discord.app_commands.Choice(name="BANを試行する", value="ban"),
])
async def antinuke_level(interaction: discord.Interaction, 対応: discord.app_commands.Choice[str]):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return
    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))
    cfg["action"] = 対応.value
    save_data(all_data)
    await interaction.response.send_message(f"対応レベルを「{対応.name}」に設定しました。", ephemeral=True)


@bot.tree.command(name="antinuke_threshold", description="何秒間に何回の操作で検出するかを設定します（デフォルト: 10秒で3回）")
async def antinuke_threshold(
    interaction: discord.Interaction,
    回数: app_commands.Range[int, 1, 50],
    秒数: app_commands.Range[int, 1, 60],
):
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


@bot.tree.command(name="antinuke_notify", description="通知先チャンネルと、検出から除外するロールを設定します")
async def antinuke_notify(
    interaction: discord.Interaction,
    通知先チャンネル: discord.TextChannel = None,
    免除ロール: discord.Role = None,
    免除ロール解除: discord.Role = None,
):
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


@bot.tree.command(name="antinuke_status", description="現在のantinuke設定状況を確認します")
async def antinuke_status(interaction: discord.Interaction):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return
    all_data = load_data()
    cfg = get_antinuke_config(all_data, str(interaction.guild.id))
    embed = _build_antinuke_status_embed(interaction.guild, cfg)
    embed.set_footer(text="設定変更: /antinuke /antinuke_level /antinuke_threshold /antinuke_notify")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    except Exception:
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


@bot.tree.command(name="owner_status", description="【オーナー限定】Botの視聴中ステータスの文字をリアルタイムで変更します")
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


@bot.tree.command(name="owner_guild_detail", description="【オーナー限定】サーバーの詳細情報と招待リンクを取得します")
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


@bot.tree.command(name="owner_broadcast", description="【オーナー限定】指定サーバーにEmbedでおお知らせを一斉送信します")
async def owner_broadcast(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return
    guilds = list(interaction.client.guilds)
    if not guilds:
        await interaction.response.send_message("現在、どのサーバーにも導入されていません。", ephemeral=True)
        return
    view = BroadcastGuildView(guilds)
    await interaction.response.send_message(
        "お知らせ送信先のサーバーを選択してください。\n「全サーバーに送信」を選ぶと全サーバーが対象になります。",
        view=view,
        ephemeral=True
    )


@bot.tree.command(name="owner_trust_add", description="【オーナー限定】強力なコマンドを実行できる信頼ユーザーを追加します")
async def owner_trust_add(interaction: discord.Interaction, user: discord.User):
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
async def owner_trust_remove(interaction: discord.Interaction, user: discord.User):
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
async def owner_trust_list(interaction: discord.Interaction):
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
# セクション 9: カスタムコマンド機能（BOT所有者専用）
# ====================================================================
# ① customtrigger : メッセージ内の特定の単語に自動返信するトリガー
# ② customcmd     : 「/customcmd 名前」で動く疑似スラッシュコマンド
#    （実際のスラッシュコマンドを動的追加するとDiscord側のsync負荷や
#      コマンド数制限の問題が出やすいため、1つのコマンドの引数として
#      名前を渡す方式にしています）
# ====================================================================

@bot.tree.command(name="customtrigger_add", description="【オーナー限定】特定の単語に自動返信するカスタムトリガーを追加します")
@discord.app_commands.choices(一致方法=[
    discord.app_commands.Choice(name="部分一致（文章にこの単語が含まれていれば反応）", value="contains"),
    discord.app_commands.Choice(name="完全一致（メッセージ全体がこの単語と同じ場合のみ反応）", value="exact"),
])
async def customtrigger_add(
    interaction: discord.Interaction,
    トリガー: str,
    返信内容: str,
    一致方法: discord.app_commands.Choice[str]
):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    triggers = cfg.setdefault("custom_triggers", [])

    # 同じトリガー文字列・一致方法の組み合わせが既にあれば上書き
    for t in triggers:
        if t["trigger"] == トリガー and t.get("match_type", "contains") == 一致方法.value:
            t["response"] = 返信内容
            save_data(all_data)
            await interaction.response.send_message(
                f"既存のトリガー「{トリガー}」（{一致方法.name}）の返信内容を更新しました。",
                ephemeral=True
            )
            return

    triggers.append({
        "trigger": トリガー,
        "response": 返信内容,
        "match_type": 一致方法.value
    })
    save_data(all_data)
    await interaction.response.send_message(
        f"カスタムトリガーを追加しました。\n"
        f"・トリガー: `{トリガー}`\n"
        f"・一致方法: {一致方法.name}\n"
        f"・返信内容: `{返信内容}`\n\n"
        "対象チャンネルで誰かがこの単語を含むメッセージを送信すると、Botが自動で返信します。",
        ephemeral=True
    )


@bot.tree.command(name="customtrigger_remove", description="【オーナー限定】登録済みのカスタムトリガーを選択して削除します")
async def customtrigger_remove(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    triggers = cfg.get("custom_triggers", [])
    if not triggers:
        await interaction.response.send_message("削除できるカスタムトリガーがありません。", ephemeral=True)
        return

    view = CustomTriggerDeleteView(triggers, interaction.guild.id)
    await interaction.response.send_message("削除したいカスタムトリガーをメニューから選んでください：", view=view, ephemeral=True)


@bot.tree.command(name="customtrigger_list", description="【オーナー限定】登録済みのカスタムトリガー一覧を表示します")
async def customtrigger_list(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    triggers = cfg.get("custom_triggers", [])

    embed = discord.Embed(
        title=f"{interaction.guild.name} - カスタムトリガー一覧",
        color=discord.Color.blue()
    )
    if not triggers:
        embed.description = "登録されているカスタムトリガーはありません。\n`/customtrigger_add` で追加できます。"
    else:
        for i, t in enumerate(triggers, 1):
            match_label = "完全一致" if t.get("match_type") == "exact" else "部分一致"
            embed.add_field(
                name=f"{i}. 「{t['trigger']}」（{match_label}）",
                value=f"返信: {t['response'][:200]}",
                inline=False
            )
        embed.set_footer(text=f"登録数: {len(triggers)}件")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="customcmd_add", description="【オーナー限定】「/customcmd 名前」で動くカスタムコマンドを追加します")
async def customcmd_add(interaction: discord.Interaction, 名前: str, 返信内容: str):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    名前 = 名前.strip().lower()
    if not 名前 or len(名前) > 80:
        await interaction.response.send_message("コマンド名は1〜80文字で指定してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    commands_dict = cfg.setdefault("custom_commands", {})

    is_update = 名前 in commands_dict
    commands_dict[名前] = 返信内容
    save_data(all_data)

    if is_update:
        await interaction.response.send_message(
            f"カスタムコマンド `/customcmd {名前}` の内容を更新しました。",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"カスタムコマンドを追加しました。\n"
            f"`/customcmd {名前}` と入力すると以下の内容が返信されます：\n`{返信内容}`",
            ephemeral=True
        )


@bot.tree.command(name="customcmd_remove", description="【オーナー限定】登録済みのカスタムコマンドを選択して削除します")
async def customcmd_remove(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    commands_dict = cfg.get("custom_commands", {})
    if not commands_dict:
        await interaction.response.send_message("削除できるカスタムコマンドがありません。", ephemeral=True)
        return

    view = CustomCommandDeleteView(commands_dict, interaction.guild.id)
    await interaction.response.send_message("削除したいカスタムコマンドをメニューから選んでください：", view=view, ephemeral=True)


@bot.tree.command(name="customcmd_list", description="【オーナー限定】登録済みのカスタムコマンド一覧を表示します")
async def customcmd_list(interaction: discord.Interaction):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    commands_dict = cfg.get("custom_commands", {})

    embed = discord.Embed(
        title=f"{interaction.guild.name} - カスタムコマンド一覧",
        color=discord.Color.blue()
    )
    if not commands_dict:
        embed.description = "登録されているカスタムコマンドはありません。\n`/customcmd_add` で追加できます。"
    else:
        for name, response in commands_dict.items():
            embed.add_field(name=f"/customcmd {name}", value=response[:200], inline=False)
        embed.set_footer(text=f"登録数: {len(commands_dict)}件")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def customcmd_name_autocomplete(interaction: discord.Interaction, current: str):
    """/customcmd の名前引数オートコンプリート用関数です。"""
    if not interaction.guild:
        return []
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    commands_dict = cfg.get("custom_commands", {})
    current_lower = current.lower()
    matches = [name for name in commands_dict.keys() if current_lower in name.lower()]
    return [
        discord.app_commands.Choice(name=name, value=name)
        for name in matches[:25]
    ]


@bot.tree.command(name="customcmd", description="登録されたカスタムコマンドを実行します")
@discord.app_commands.autocomplete(名前=customcmd_name_autocomplete)
async def customcmd(interaction: discord.Interaction, 名前: str):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    commands_dict = cfg.get("custom_commands", {})

    response = commands_dict.get(名前.strip().lower())
    if response is None:
        await interaction.response.send_message(
            f"カスタムコマンド「{名前}」は見つかりませんでした。`/customcmd_list` で登録済みコマンドを確認できます。",
            ephemeral=True
        )
        return

    await interaction.response.send_message(response)


# ====================================================================
# セクション 10: ボイスチャンネル再生機能
# ====================================================================
# ① /voice_join  : ボイスチャンネルに参加
# ② /voice_leave : ボイスチャンネルから退出
# ③ /voice_play  : 添付ファイルまたは登録済み音源を再生（コントロールパネル付き）
# ④ /voice_sound_add / remove / list : サーバーごとの登録済み音源管理（オーナー限定）
# ====================================================================

# 音声機能用の保存先ディレクトリ（登録済みmp3の保存場所）
if os.path.exists("/app/data"):
    SOUNDS_DIR = "/app/data/sounds"
else:
    SOUNDS_DIR = "sounds"

ALLOWED_AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a")
MAX_AUDIO_FILE_SIZE = 15 * 1024 * 1024  # 15MB


def get_guild_sounds_dir(guild_id: int) -> str:
    """サーバーごとの音源保存ディレクトリを取得し、なければ作成します。"""
    path = os.path.join(SOUNDS_DIR, str(guild_id))
    os.makedirs(path, exist_ok=True)
    return path


def get_registered_sounds(all_data: dict, guild_id_str: str) -> dict:
    """登録済み音源の名前 -> ファイルパス の辞書を取得します。"""
    cfg = get_guild_config(all_data, guild_id_str)
    if "registered_sounds" not in cfg:
        cfg["registered_sounds"] = {}
    return cfg["registered_sounds"]


class VoicePlayApprovalView(discord.ui.View):
    """
    /voice_play 実行時にオーナーのDMへ送る「再生許可申請」ビューです。
    オーナーが許可すると実際の再生が開始されます。
    """
    def __init__(
        self,
        interaction: discord.Interaction,
        voice_client: discord.VoiceClient,
        volume_source: discord.PCMVolumeTransformer,
        source_label: str,
        audio_path: str,
        is_temp_file: bool,
        音量: int,
    ):
        super().__init__(timeout=300)
        self.interaction = interaction
        self.voice_client = voice_client
        self.volume_source = volume_source
        self.source_label = source_label
        self.audio_path = audio_path
        self.is_temp_file = is_temp_file
        self.音量 = 音量
        self._handled = False

    @discord.ui.button(label="許可する", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        client = interaction.client
        owner_id = await resolve_owner_id(client)
        if interaction.user.id != owner_id:
            await interaction.response.send_message("このボタンはBOT所有者専用です。", ephemeral=True)
            return
        if self._handled:
            await interaction.response.send_message("この申請はすでに処理済みです。", ephemeral=True)
            return
        self._handled = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"**{self.source_label}** の再生を許可しました。",
            embed=None,
            view=self
        )

        # 接続が切れていた場合は再接続
        if not self.voice_client.is_connected():
            try:
                member = self.interaction.guild.get_member(self.interaction.user.id)
                if not member or not member.voice or not member.voice.channel:
                    await self.interaction.followup.send("再生を許可しましたが、あなたがボイスチャンネルにいないため再生できませんでした。", ephemeral=True)
                    return
                self.voice_client = await member.voice.channel.connect()
            except Exception as e:
                await self.interaction.followup.send(f"再接続に失敗しました: {e}", ephemeral=True)
                return

        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

        # 再生開始
        import shutil
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
        try:
            ffmpeg_source = discord.FFmpegPCMAudio(self.audio_path, executable=ffmpeg_path)
            volume_source = discord.PCMVolumeTransformer(ffmpeg_source, volume=self.音量 / 100)
        except Exception as e:
            await self.interaction.followup.send(f"再生開始に失敗しました: {e}", ephemeral=True)
            return

        control_view = VoiceControlView(
            voice_client=self.voice_client,
            source=volume_source,
            source_label=self.source_label,
            requester=self.interaction.user,
            audio_path=self.audio_path,
            is_temp_file=self.is_temp_file,
        )

        def _after_play(error):
            if error:
                print(f"[ボイス再生エラー] {error}")
            if control_view.loop:
                asyncio.run_coroutine_threadsafe(control_view._replay(), self.voice_client._state.loop)
            else:
                if self.is_temp_file and os.path.exists(self.audio_path):
                    try:
                        os.remove(self.audio_path)
                    except Exception:
                        pass

        self.voice_client.play(volume_source, after=_after_play)
        await self.interaction.followup.send(embed=control_view.build_status_embed(), view=control_view)

    @discord.ui.button(label="拒否する", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        client = interaction.client
        owner_id = await resolve_owner_id(client)
        if interaction.user.id != owner_id:
            await interaction.response.send_message("このボタンはBOT所有者専用です。", ephemeral=True)
            return
        if self._handled:
            await interaction.response.send_message("この申請はすでに処理済みです。", ephemeral=True)
            return
        self._handled = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"**{self.source_label}** の再生を拒否しました。",
            embed=None,
            view=self
        )
        # 一時ファイルを削除
        if self.is_temp_file and os.path.exists(self.audio_path):
            try:
                os.remove(self.audio_path)
            except Exception:
                pass
        await self.interaction.followup.send("再生申請がBOT所有者によって拒否されました。", ephemeral=True)


class VoiceNextSelect(discord.ui.Select):
    """曲切り替え用：登録済み音源から次の曲を選ぶセレクトメニューです。"""
    def __init__(self, guild: discord.Guild, parent_view: "VoiceControlView"):
        self.parent_view = parent_view
        all_data = load_data()
        sounds = get_registered_sounds(all_data, str(guild.id))
        options = [
            discord.SelectOption(label=name, value=name)
            for name in list(sounds.keys())[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="（登録済み音源なし）", value="__none__")]
        super().__init__(placeholder="次に再生する音源を選択...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await interaction.response.send_message("登録済み音源がありません。/voice_sound_add で登録してください。", ephemeral=True)
            return
        name = self.values[0]
        all_data = load_data()
        sounds = get_registered_sounds(all_data, str(interaction.guild.id))
        audio_path = sounds.get(name)
        if not audio_path or not os.path.exists(audio_path):
            await interaction.response.send_message("音源ファイルが見つかりませんでした。再登録してください。", ephemeral=True)
            return

        view = self.parent_view
        if not view.voice_client.is_connected():
            await interaction.response.send_message("Botはすでにボイスチャンネルから切断されています。", ephemeral=True)
            return

        # 現在の再生を止めて新しい曲を再生
        if view.voice_client.is_playing() or view.voice_client.is_paused():
            view.voice_client.stop()

        import shutil
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
        ffmpeg_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_path)
        volume_source = discord.PCMVolumeTransformer(ffmpeg_source, volume=view.source.volume)

        # 状態を更新
        view.source = volume_source
        view.source_label = name
        view.audio_path = audio_path
        view.is_temp_file = False
        view.paused = False
        view._update_pause_button_label()

        def _after_play(error):
            if error:
                print(f"[ボイス再生エラー] {error}")
            if view.loop:
                asyncio.run_coroutine_threadsafe(view._replay(), interaction.client.loop)

        view.voice_client.play(volume_source, after=_after_play)

        # セレクトを除いたビューに戻す（行4を削除）
        for item in list(view.children):
            if isinstance(item, VoiceNextSelect):
                view.remove_item(item)

        await interaction.response.edit_message(embed=view.build_status_embed(), view=view)


class VoiceControlView(discord.ui.View):
    """
    再生中にチャンネルへ表示するコントロールパネルです。
    一時停止/再開・音量調整・ループ・曲切り替え・停止（切断）に対応します。
    """
    def __init__(
        self,
        voice_client: discord.VoiceClient,
        source: discord.PCMVolumeTransformer,
        source_label: str,
        requester: discord.abc.User,
        audio_path: str = "",
        is_temp_file: bool = False,
    ):
        super().__init__(timeout=600)
        self.voice_client = voice_client
        self.source = source
        self.source_label = source_label
        self.requester = requester
        self.audio_path = audio_path
        self.is_temp_file = is_temp_file
        self.paused = False
        self.loop = False
        self._update_pause_button_label()
        self._update_loop_button_label()

    def _update_pause_button_label(self):
        self.pause_button.label = "再開" if self.paused else "一時停止"
        self.pause_button.style = discord.ButtonStyle.success if self.paused else discord.ButtonStyle.secondary

    def _update_loop_button_label(self):
        self.loop_button.label = "ループ: ON" if self.loop else "ループ: OFF"
        self.loop_button.style = discord.ButtonStyle.success if self.loop else discord.ButtonStyle.secondary

    def build_status_embed(self) -> discord.Embed:
        vol_percent = int(self.source.volume * 100)
        status_label = "一時停止中" if self.paused else "再生中"
        loop_label = "ON" if self.loop else "OFF"
        embed = discord.Embed(
            title="ボイス再生コントロール",
            description=f"再生ファイル: `{self.source_label}`",
            color=discord.Color.green() if not self.paused else discord.Color.greyple()
        )
        embed.add_field(name="状態", value=status_label, inline=True)
        embed.add_field(name="音量", value=f"{vol_percent}%", inline=True)
        embed.add_field(name="ループ", value=loop_label, inline=True)
        embed.add_field(name="再生先チャンネル", value=self.voice_client.channel.mention, inline=False)
        embed.set_footer(text=f"リクエスト: {self.requester}")
        return embed

    async def _replay(self):
        """ループ再生時に同じファイルを再再生します。"""
        if not self.loop or not self.voice_client.is_connected():
            return
        if not self.audio_path or not os.path.exists(self.audio_path):
            return
        import shutil
        ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
        try:
            ffmpeg_source = discord.FFmpegPCMAudio(self.audio_path, executable=ffmpeg_path)
            volume_source = discord.PCMVolumeTransformer(ffmpeg_source, volume=self.source.volume)
            self.source = volume_source

            def _after_play(error):
                if error:
                    print(f"[ボイス再生エラー（ループ）] {error}")
                if self.loop:
                    asyncio.run_coroutine_threadsafe(self._replay(), self.voice_client._state.loop)

            self.voice_client.play(volume_source, after=_after_play)
        except Exception as e:
            print(f"[ループ再生エラー] {e}")

    async def _check_still_connected(self, interaction: discord.Interaction) -> bool:
        if not self.voice_client.is_connected():
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                content="Botはすでにボイスチャンネルから切断されています。",
                embed=None,
                view=self
            )
            return False
        return True

    @discord.ui.button(label="一時停止", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_still_connected(interaction):
            return
        if self.paused:
            if self.voice_client.is_paused():
                self.voice_client.resume()
            self.paused = False
        else:
            if self.voice_client.is_playing():
                self.voice_client.pause()
            self.paused = True
        self._update_pause_button_label()
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="音量 -10%", style=discord.ButtonStyle.primary, row=0)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_still_connected(interaction):
            return
        self.source.volume = max(0.0, round(self.source.volume - 0.1, 2))
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="音量 +10%", style=discord.ButtonStyle.primary, row=0)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_still_connected(interaction):
            return
        self.source.volume = min(2.0, round(self.source.volume + 0.1, 2))
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="ループ: OFF", style=discord.ButtonStyle.secondary, row=1)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_still_connected(interaction):
            return
        self.loop = not self.loop
        self._update_loop_button_label()
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="曲を切り替え", style=discord.ButtonStyle.primary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_still_connected(interaction):
            return
        if not interaction.guild:
            return
        # 既にセレクトが表示されている場合は削除
        for item in list(self.children):
            if isinstance(item, VoiceNextSelect):
                self.remove_item(item)
        # 登録済み音源セレクトを row=3 に追加
        select = VoiceNextSelect(interaction.guild, self)
        select.row = 3
        self.add_item(select)
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="停止して切断", style=discord.ButtonStyle.danger, row=2)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.loop = False
        if self.voice_client.is_connected():
            self.voice_client.stop()
            await self.voice_client.disconnect()
        for item in self.children:
            item.disabled = True
        embed = self.build_status_embed()
        embed.title = "ボイス再生を停止しました"
        embed.color = discord.Color.greyple()
        await interaction.response.edit_message(embed=embed, view=self)


async def _ensure_voice_connected(interaction: discord.Interaction) -> discord.VoiceClient | None:
    """
    実行者が参加しているボイスチャンネルにBotを接続（または移動）します。
    失敗時はNoneを返し、エラーメッセージを送信済みの状態にします。
    """
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return None

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice or not member.voice.channel:
        await interaction.response.send_message("先にボイスチャンネルに参加してから実行してください。", ephemeral=True)
        return None

    target_channel = member.voice.channel
    voice_client = interaction.guild.voice_client

    try:
        if voice_client is None:
            voice_client = await target_channel.connect()
        elif voice_client.channel.id != target_channel.id:
            await voice_client.move_to(target_channel)
    except discord.ClientException as e:
        await interaction.response.send_message(f"ボイス接続でエラーが発生しました: {e}", ephemeral=True)
        return None
    except Exception as e:
        await interaction.response.send_message(f"ボイスチャンネルへの接続に失敗しました: {e}", ephemeral=True)
        return None

    return voice_client


@bot.tree.command(name="voice_join", description="あなたが参加しているボイスチャンネルにBotを参加させます")
async def voice_join(interaction: discord.Interaction):
    voice_client = await _ensure_voice_connected(interaction)
    if voice_client is None:
        return
    await interaction.response.send_message(f"{voice_client.channel.mention} に参加しました。", ephemeral=True)


@bot.tree.command(name="voice_leave", description="ボイスチャンネルからBotを退出させます")
async def voice_leave(interaction: discord.Interaction):
    if not interaction.guild or not interaction.guild.voice_client:
        await interaction.response.send_message("Botは現在どのボイスチャンネルにも参加していません。", ephemeral=True)
        return
    channel_name = interaction.guild.voice_client.channel.name
    await interaction.guild.voice_client.disconnect()
    await interaction.response.send_message(f"「{channel_name}」から退出しました。", ephemeral=True)


@bot.tree.command(name="voice_play", description="ボイスチャンネルで音声ファイルを再生します（添付または登録済み音源）")
async def voice_play(
    interaction: discord.Interaction,
    添付ファイル: discord.Attachment = None,
    登録名: str = None,
    音量: app_commands.Range[int, 1, 200] = 100
):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    if not 添付ファイル and not 登録名:
        await interaction.response.send_message(
            "「添付ファイル」または「登録名」のどちらかを指定してください。\n"
            "登録済み音源の名前は `/voice_sound_list` で確認できます。",
            ephemeral=True
        )
        return

    if 添付ファイル and 登録名:
        await interaction.response.send_message("添付ファイルと登録名は同時に指定できません。どちらか一方にしてください。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    # --- 再生対象ファイルの決定 ---
    if 添付ファイル:
        if not 添付ファイル.filename.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
            await interaction.followup.send(
                f"対応していないファイル形式です。対応形式: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}",
                ephemeral=True
            )
            return
        if 添付ファイル.size > MAX_AUDIO_FILE_SIZE:
            await interaction.followup.send("ファイルサイズが大きすぎます（上限: 15MB）。", ephemeral=True)
            return

        temp_dir = os.path.join("temp_audio")
        os.makedirs(temp_dir, exist_ok=True)
        safe_name = f"{interaction.id}_{添付ファイル.filename}"
        audio_path = os.path.join(temp_dir, safe_name)
        try:
            await 添付ファイル.save(audio_path)
        except Exception as e:
            await interaction.followup.send(f"ファイルの保存に失敗しました: {e}", ephemeral=True)
            return
        source_label = 添付ファイル.filename
        is_temp_file = True
    else:
        all_data = load_data()
        sounds = get_registered_sounds(all_data, str(interaction.guild.id))
        if 登録名 not in sounds:
            await interaction.followup.send(
                f"登録名「{登録名}」が見つかりません。`/voice_sound_list` で確認してください。",
                ephemeral=True
            )
            return
        audio_path = sounds[登録名]
        if not os.path.exists(audio_path):
            await interaction.followup.send("登録された音源ファイルがサーバー上に見つかりませんでした。再登録してください。", ephemeral=True)
            return
        source_label = 登録名
        is_temp_file = False

    # --- ボイス接続 ---
    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice or not member.voice.channel:
        await interaction.followup.send("先にボイスチャンネルに参加してから実行してください。", ephemeral=True)
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return

    target_channel = member.voice.channel
    voice_client = interaction.guild.voice_client
    try:
        if voice_client is None:
            voice_client = await target_channel.connect()
        elif voice_client.channel.id != target_channel.id:
            await voice_client.move_to(target_channel)
    except Exception as e:
        await interaction.followup.send(f"ボイスチャンネルへの接続に失敗しました: {e}", ephemeral=True)
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return

    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop()

    # --- 再生開始 ---
    # FFmpegのパスを自動検索（Nixpacks環境では /nix/store 以下に置かれる場合があるため）
    import shutil
    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    print(f"[ボイス] FFmpegパス: {ffmpeg_path}")

    try:
        ffmpeg_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_path)
        volume_source = discord.PCMVolumeTransformer(ffmpeg_source, volume=音量 / 100)
    except Exception as e:
        await interaction.followup.send(
            f"音声ファイルの読み込みに失敗しました（FFmpegが利用できない可能性があります）: {e}",
            ephemeral=True
        )
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return

    # --- 再生許可申請をオーナーに送る ---
    owner_id = await resolve_owner_id(interaction.client)
    try:
        owner = interaction.client.get_user(owner_id) or await interaction.client.fetch_user(owner_id)
    except Exception:
        owner = None

    if not owner:
        await interaction.followup.send("BOT所有者の情報を取得できませんでした。時間をおいて再試行してください。", ephemeral=True)
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return

    request_embed = discord.Embed(
        title="ボイス再生の許可申請",
        description="以下のサーバーで音声ファイルの再生リクエストが届きました。",
        color=discord.Color.orange()
    )
    request_embed.add_field(name="サーバー", value=interaction.guild.name, inline=True)
    request_embed.add_field(name="申請者", value=f"{interaction.user} ({interaction.user.mention})", inline=True)
    request_embed.add_field(name="ファイル名", value=source_label, inline=False)
    request_embed.add_field(name="チャンネル", value=voice_client.channel.name, inline=True)
    request_embed.timestamp = discord.utils.utcnow()

    approval_view = VoicePlayApprovalView(
        interaction=interaction,
        voice_client=voice_client,
        volume_source=volume_source,
        source_label=source_label,
        audio_path=audio_path,
        is_temp_file=is_temp_file,
        音量=音量,
    )

    try:
        await owner.send(embed=request_embed, view=approval_view)
    except discord.Forbidden:
        await interaction.followup.send("BOT所有者へのDM送信に失敗しました（DM拒否設定の可能性があります）。", ephemeral=True)
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return
    except Exception as e:
        await interaction.followup.send(f"申請送信中にエラーが発生しました: {e}", ephemeral=True)
        if is_temp_file and os.path.exists(audio_path):
            os.remove(audio_path)
        return

    await interaction.followup.send(
        f"BOT所有者に再生許可申請を送信しました。\nファイル: `{source_label}`\n所有者が許可するとボイスチャンネルで再生が始まります（申請は5分間有効です）。",
        ephemeral=True
    )


@bot.tree.command(name="voice_sound_add", description="サーバーで使い回せる音源を名前付きで登録します（誰でも使用可能）")
async def voice_sound_add(interaction: discord.Interaction, 登録名: str, ファイル: discord.Attachment):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    登録名 = 登録名.strip()
    if not 登録名 or len(登録名) > 50:
        await interaction.response.send_message("登録名は1〜50文字で指定してください。", ephemeral=True)
        return

    if not ファイル.filename.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
        await interaction.response.send_message(
            f"対応していないファイル形式です。対応形式: {', '.join(ALLOWED_AUDIO_EXTENSIONS)}",
            ephemeral=True
        )
        return
    if ファイル.size > MAX_AUDIO_FILE_SIZE:
        await interaction.response.send_message("ファイルサイズが大きすぎます（上限: 15MB）。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    sounds_dir = get_guild_sounds_dir(interaction.guild.id)
    ext = os.path.splitext(ファイル.filename)[1].lower()
    save_path = os.path.join(sounds_dir, f"{登録名}{ext}")

    try:
        await ファイル.save(save_path)
    except Exception as e:
        await interaction.followup.send(f"ファイルの保存に失敗しました: {e}", ephemeral=True)
        return

    all_data = load_data()
    sounds = get_registered_sounds(all_data, str(interaction.guild.id))
    is_update = 登録名 in sounds
    sounds[登録名] = save_path
    save_data(all_data)

    if is_update:
        await interaction.followup.send(f"登録名「{登録名}」の音源を更新しました。", ephemeral=True)
    else:
        await interaction.followup.send(
            f"音源を登録しました。\n`/voice_play 登録名:{登録名}` で再生できます。",
            ephemeral=True
        )


@bot.tree.command(name="voice_sound_remove", description="【オーナー限定】登録済みの音源を削除します")
async def voice_sound_remove(interaction: discord.Interaction, 登録名: str):
    if not await is_owner_check(interaction):
        return
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    sounds = get_registered_sounds(all_data, str(interaction.guild.id))
    if 登録名 not in sounds:
        await interaction.response.send_message(f"登録名「{登録名}」は見つかりませんでした。", ephemeral=True)
        return

    file_path = sounds.pop(登録名)
    save_data(all_data)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass

    await interaction.response.send_message(f"登録名「{登録名}」を削除しました。", ephemeral=True)


@bot.tree.command(name="voice_sound_list", description="登録済みの音源一覧を表示します")
async def voice_sound_list(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    sounds = get_registered_sounds(all_data, str(interaction.guild.id))

    embed = discord.Embed(
        title=f"{interaction.guild.name} - 登録済み音源一覧",
        color=discord.Color.blue()
    )
    if not sounds:
        embed.description = "登録されている音源はありません。\n`/voice_sound_add` で登録できます。"
    else:
        embed.description = "\n".join([f"・`{name}`" for name in sounds.keys()])
        embed.set_footer(text=f"登録数: {len(sounds)}件 | /voice_play 登録名:<名前> で再生できます")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def voice_sound_name_autocomplete(interaction: discord.Interaction, current: str):
    """登録名引数のオートコンプリート用関数です。"""
    if not interaction.guild:
        return []
    all_data = load_data()
    sounds = get_registered_sounds(all_data, str(interaction.guild.id))
    current_lower = current.lower()
    matches = [name for name in sounds.keys() if current_lower in name.lower()]
    return [discord.app_commands.Choice(name=name, value=name) for name in matches[:25]]


voice_play.autocomplete("登録名")(voice_sound_name_autocomplete)
voice_sound_remove.autocomplete("登録名")(voice_sound_name_autocomplete)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """
    Botだけがボイスチャンネルに取り残された場合、自動的に切断します（リソース節約のため）。
    """
    if member.bot:
        return
    if not member.guild.voice_client:
        return
    voice_client = member.guild.voice_client
    channel = voice_client.channel
    # チャンネル内の人間メンバー数をチェック
    human_members = [m for m in channel.members if not m.bot]
    if len(human_members) == 0:
        await asyncio.sleep(60)  # 1分待って誰も戻らなければ切断
        if voice_client.is_connected():
            human_members_recheck = [m for m in voice_client.channel.members if not m.bot]
            if len(human_members_recheck) == 0:
                await voice_client.disconnect()
                print(f"[ボイス自動切断] {member.guild.name} で誰もいなくなったため切断しました。")


# ====================================================================
# セクション 11: eval コマンド（オーナー限定・コード実行）
# ====================================================================

@bot.tree.command(name="eval", description="【オーナー限定】Pythonコードを実行して結果を返します")
async def eval_command(interaction: discord.Interaction, コード: str):
    """
    オーナー限定のPythonコード実行コマンドです。
    Botの内部状態（guilds, get_guild, load_data など）にアクセスできます。
    複数行コードは ``` で囲んでも実行できます。
    """
    if not await is_owner_check(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    # コードブロック記法（```python ... ``` や ``` ... ```）を除去
    code = コード.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        # 最初の行（```python など）と最後の行（```）を除去
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)

    # 実行結果を受け取るための変数
    import io
    import traceback
    import time

    stdout_capture = io.StringIO()
    result_value = None
    error_text = None

    # コード実行に使えるローカル変数（Botの内部にアクセスできるようにする）
    local_vars = {
        "bot": bot,
        "interaction": interaction,
        "discord": discord,
        "load_data": load_data,
        "save_data": save_data,
        "get_guild_config": get_guild_config,
        "asyncio": asyncio,
    }

    start_time = time.perf_counter()
    try:
        # 最後の式の値を返すために、exec前にコンパイルして最後の行をevalで取得
        import ast as _ast
        try:
            tree = _ast.parse(code, mode="exec")
        except SyntaxError as e:
            raise e

        # 最後のノードがExprの場合はevalで値を取得
        if tree.body and isinstance(tree.body[-1], _ast.Expr):
            last_expr = tree.body.pop()
            exec_code = compile(tree, "<eval>", "exec")
            eval_code = compile(_ast.Expression(body=last_expr.value), "<eval>", "eval")

            import contextlib
            with contextlib.redirect_stdout(stdout_capture):
                exec(exec_code, local_vars)
                result_value = eval(eval_code, local_vars)

            # コルーチンの場合はawait
            if asyncio.iscoroutine(result_value):
                result_value = await result_value
        else:
            import contextlib
            with contextlib.redirect_stdout(stdout_capture):
                exec(compile(tree, "<eval>", "exec"), local_vars)

    except Exception:
        error_text = traceback.format_exc()

    elapsed = (time.perf_counter() - start_time) * 1000  # ms

    # Embed作成
    stdout_text = stdout_capture.getvalue()

    if error_text:
        embed = discord.Embed(title="eval - エラー", color=discord.Color.red())
        embed.add_field(
            name="エラー内容",
            value=f"```py\n{error_text[:1000]}\n```",
            inline=False
        )
    else:
        embed = discord.Embed(title="eval - 実行完了", color=discord.Color.green())
        if stdout_text:
            embed.add_field(
                name="出力 (print)",
                value=f"```\n{stdout_text[:900]}\n```",
                inline=False
            )
        if result_value is not None:
            embed.add_field(
                name="戻り値",
                value=f"```py\n{repr(result_value)[:900]}\n```",
                inline=False
            )
        if not stdout_text and result_value is None:
            embed.add_field(name="結果", value="（出力なし）", inline=False)

    # 実行したコードも表示
    code_preview = code if len(code) <= 500 else code[:497] + "..."
    embed.add_field(
        name="実行コード",
        value=f"```py\n{code_preview}\n```",
        inline=False
    )
    embed.set_footer(text=f"実行時間: {elapsed:.2f}ms")

    await interaction.followup.send(embed=embed, ephemeral=True)


# ====================================================================
# Botの起動
# ====================================================================

bot.run(TOKEN)