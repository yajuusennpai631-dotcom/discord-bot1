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
import aiohttp.web
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

# --- Discord OAuth2 関連設定（サーバーブラックリスト認証用）---
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "")
OAUTH_SECRET_KEY = os.getenv("OAUTH_SECRET_KEY", "default_secret_change_me")
OAUTH_PORT = int(os.getenv("PORT", "8080"))  # Railway は PORT 環境変数で起動ポートを渡す

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
        ("custom_commands", {}),
        ("welcome_channel_id", None),
        ("welcome_message", None),
        ("welcome_role_id", None),
        ("stats_category_id", None),
        ("stats_member_ch_id", None),
        ("stats_online_ch_id", None),
        ("stats_bot_ch_id", None),
        ("alt_check_enabled", False),
        ("alt_check_days", 30),
        ("alt_check_action", "notify"),
        ("iplogger_check_enabled", False),
        ("giveaways", {}),
        # --- 経済システム（メッセージ報酬・ロールショップ・自販機） ---
        ("economy_enabled", False),
        ("economy_currency_name", "コイン"),
        ("economy_reward_min", 1),
        ("economy_reward_max", 5),
        ("economy_cooldown_seconds", 60),
        ("economy_balances", {}),       # {user_id_str: int}
        ("economy_last_earned", {}),    # {user_id_str: unix_timestamp}
        ("role_shop", []),              # [{"id": int, "role_id": int, "name": str, "price": int}]
        ("owned_shop_roles", {}),       # {user_id_str: [role_shop_item_id, ...]}
        ("vending_items", []),          # [{"id": int, "name": str, "price": int, "stock": int,
                                         #   "type": "text"|"file", "content": str (テキスト時),
                                         #   "storage_channel_id"/"storage_message_id"/"file_name" (ファイル時)}]
        ("economy_next_id", 1),         # role_shop / vending_items 共通のID発行用カウンタ
        ("vending_panel_channel_id", None),  # 設置型自販機パネルのチャンネルID
        ("vending_panel_message_id", None),  # 設置型自販機パネルのメッセージID
        ("vending_storage_channel_id", None),  # 自販機のファイル商品を保管するBot専用チャンネルのID
        ("role_shop_panel_channel_id", None),  # 設置型ロールショップパネルのチャンネルID
        ("role_shop_panel_message_id", None),  # 設置型ロールショップパネルのメッセージID
        # --- /work コマンド（2時間ごとのコイン稼ぎ） ---
        ("economy_work_reward_min", 10),
        ("economy_work_reward_max", 50),
        ("economy_work_cooldown_seconds", 7200),  # 既定2時間
        ("economy_last_work", {}),      # {user_id_str: unix_timestamp}
        # --- サーバーブラックリスト（特定サーバー参加者自動BAN） ---
        ("server_blacklist_enabled", False),    # 機能ON/OFF
        ("server_blacklist_ids", []),           # BANの対象となるDiscordサーバーID一覧（int）
        ("server_blacklist_action", "ban"),     # 'ban' または 'kick'
        ("server_blacklist_log_channel_id", None),  # 処理結果を通知するチャンネルID
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
        # DM・グループDMはBotオーナーのみ許可（上記でオーナーは通過済み）
        await interaction.response.send_message("このコマンドを実行する権限がありません（管理者または許可ユーザー専用）。", ephemeral=True)
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
    bot.add_view(GiveawayJoinView())  # プレゼント参加ボタンの永続化
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

    # 設置型ロールショップパネルの永続化View再登録（再起動後もボタンが反応するようにする）
    for guild_id_str, config in all_data.items():
        if guild_id_str in ("user_apps", "global_config"):
            continue
        if not isinstance(config, dict):
            continue
        panel_message_id = config.get("role_shop_panel_message_id")
        panel_channel_id = config.get("role_shop_panel_channel_id")
        if not panel_message_id or not panel_channel_id:
            continue
        try:
            shop_items = config.get("role_shop", [])
            view = RoleShopView(shop_items) if shop_items else discord.ui.View(timeout=None)
            bot.add_view(view, message_id=panel_message_id)
        except Exception as e:
            print(f"[警告] ロールショップパネルの再登録に失敗しました（guild={guild_id_str}）: {e}")

    # 設置型自販機パネルの永続化View再登録（再起動後もボタンが反応するようにする）
    for guild_id_str, config in all_data.items():
        if guild_id_str in ("user_apps", "global_config"):
            continue
        if not isinstance(config, dict):
            continue
        panel_message_id = config.get("vending_panel_message_id")
        panel_channel_id = config.get("vending_panel_channel_id")
        if not panel_message_id or not panel_channel_id:
            continue
        try:
            items = config.get("vending_items", [])
            view = VendingMachineView(items) if items else discord.ui.View(timeout=None)
            bot.add_view(view, message_id=panel_message_id)
        except Exception as e:
            print(f"[警告] 自販機パネルの再登録に失敗しました（guild={guild_id_str}）: {e}")

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

        # 統計チャンネルの自動更新ループを再起動
        stats_ch_ids = [
            config.get("stats_member_ch_id"),
            config.get("stats_online_ch_id"),
            config.get("stats_bot_ch_id"),
        ]
        if guild and any(stats_ch_ids):
            _stats_tasks[guild_id_int] = asyncio.create_task(_stats_loop(guild))
            print(f"  > 統計ループ: 再起動しました")

        # 進行中プレゼントのタスクを再起動
        giveaways = config.get("giveaways", {})
        now_utc = discord.utils.utcnow()
        for msg_id_str, gw in list(giveaways.items()):
            try:
                end_dt = datetime.datetime.fromisoformat(gw["end_at"])
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                ch_id = gw.get("channel_id")
                ch = guild.get_channel(ch_id) if guild and ch_id else None
                if ch and end_dt > now_utc:
                    msg_id = int(msg_id_str)
                    task = asyncio.create_task(_run_giveaway(ch, msg_id, guild_id_int, end_dt))
                    _giveaway_tasks[msg_id] = task
                    print(f"  > プレゼントタスク: msg_id={msg_id_str} を再起動しました")
                elif end_dt <= now_utc:
                    # 期限切れのプレゼントを即時処理
                    ch2 = guild.get_channel(gw.get("channel_id")) if guild else None
                    if ch2:
                        msg_id = int(msg_id_str)
                        asyncio.create_task(_run_giveaway(ch2, msg_id, guild_id_int, now_utc))
            except Exception as e:
                print(f"  > [警告] プレゼントタスク復元に失敗: {e}")
    print("---------------------------------------")
    print(f"ログインユーザー: {bot.user.name} (ID: {bot.user.id})")

    # 起動時にスラッシュコマンドを自動同期（コマンド候補欄に表示されない問題の対策）
    try:
        synced = await bot.tree.sync()
        print(f"[システム] スラッシュコマンドを自動同期しました: {len(synced)}個（反映まで最大1時間）")
    except Exception as e:
        print(f"[警告] スラッシュコマンドの自動同期に失敗しました: {e}")

    print("スラッシュコマンドを即時反映したい場合は、サーバー上で '!sync' と発言してください。")


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[サーバー参加] {guild.name} (ID: {guild.id}) に導入されました。")
    await update_bot_status(bot)
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))

    # すでに承認済みのサーバーはステータスを保持し、申請パネルも再送しない
    if cfg.get("approval_status") == "approved":
        print(f"[サーバー参加] {guild.name} はすでに承認済みのためステータスを保持します。")
        save_data(all_data)
        return

    # 新規サーバーまたは未承認サーバーのみ pending に設定して申請パネルを送る
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


async def _check_iplogger(message: discord.Message) -> bool:
    """
    メッセージ内のURLをスキャンし、既知のIPロガー系ドメインまたは
    短縮URLの展開先がIPロガーであれば削除・通知します。
    True を返すと呼び出し元でメッセージ処理を中断します。
    """
    # 既知のIPロガー・フィッシング系ドメイン一覧
    IPLOGGER_DOMAINS = {
        "grabify.link", "iplogger.org", "iplogger.com", "iplogger.ru",
        "2no.co", "yip.su", "ps3cfw.com", "stopify.co", "lovebird.guru",
        "blasze.com", "blasze.tk", "iplis.ru", "02ip.ru", "ezstat.ru",
        "linezing.com", "trackyou.live", "ipgrab.io", "loggly.io",
        "track.ly", "link.tl", "bc.vc", "shorturl.at",
        "jackass.wtf", "screenshot.exposed",
    }
    # URL短縮サービス（展開チェック対象）
    URL_SHORTENERS = {
        "bit.ly", "tinyurl.com", "t.co", "ow.ly", "is.gd",
        "buff.ly", "adf.ly", "goo.gl", "cutt.ly", "rebrand.ly",
        "tiny.cc", "rb.gy", "short.io",
    }

    import re
    urls = re.findall(r"https?://[^\s<>\"']+", message.content)
    if not urls:
        return False

    detected_url = None
    detected_reason = None

    for url in urls:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower().lstrip("www.")
        except Exception:
            continue

        # 直接一致チェック
        if any(domain == d or domain.endswith("." + d) for d in IPLOGGER_DOMAINS):
            detected_url = url
            detected_reason = f"既知のIPロガードメイン: `{domain}`"
            break

        # 短縮URL展開チェック
        if any(domain == d or domain.endswith("." + d) for d in URL_SHORTENERS):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        final_url = str(resp.url)
                        final_domain = urlparse(final_url).netloc.lower().lstrip("www.")
                        if any(final_domain == d or final_domain.endswith("." + d) for d in IPLOGGER_DOMAINS):
                            detected_url = url
                            detected_reason = f"短縮URL展開先がIPロガー: `{final_domain}`"
                            break
            except Exception:
                pass

    if not detected_url:
        return False

    try:
        await message.delete()
    except Exception:
        pass

    warn_embed = discord.Embed(
        title="🚨 IPロガーリンクを検知・削除しました",
        color=discord.Color.red()
    )
    warn_embed.add_field(name="送信者", value=message.author.mention, inline=True)
    warn_embed.add_field(name="検知理由", value=detected_reason, inline=False)
    warn_embed.set_footer(text="このリンクは個人情報（IPアドレス）を収集する危険なURLです")

    try:
        await message.channel.send(
            f"[!] {message.author.mention} 危険なIPロガーリンクを検知したため削除しました。",
            delete_after=10
        )
    except Exception:
        pass

    await _send_mod_log(message.guild, warn_embed)
    return True


async def _run_automod_checks(message: discord.Message, guild_config: dict) -> bool:
    """
    招待リンク削除・NGワード削除・IPロガー検知を実行します。
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
                    f"[!] {message.author.mention} 招待リンクの送信は許可されていません（編集による回避も検知します）。",
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
                    f"[!] {message.author.mention} NGワードが含まれているため削除されました（編集による回避も検知します）。",
                    delete_after=5
                )
            except Exception:
                pass
            return True

    # IPロガー検知
    if guild_config.get("iplogger_check_enabled", False):
        if await _check_iplogger(message):
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


# ====================================================================
# BOT へのDM ⇔ オーナー間メッセージリレー機能
# ====================================================================
#
# ・一般ユーザーがBOTにDMを送ると、送信者名（とID）付きのembedでBOTオーナーへ転送される
# ・オーナーは転送されたembedメッセージに「返信(Reply)」するか、
#   `!reply <ユーザーID> <内容>` コマンドを使うことで、そのユーザーへBOTを通して返信できる
#
DM_RELAY_MAX_ENTRIES = 500  # 転送メッセージID -> 送信者ユーザーIDの対応表の最大保持件数


def _remember_dm_relay(all_data: dict, forwarded_message_id: int, user_id: int):
    """オーナーへ転送したメッセージIDと、元の送信者ユーザーIDの対応を記録します。"""
    global_cfg = get_global_config(all_data)
    relay_map = global_cfg.setdefault("dm_relay_map", {})
    relay_map[str(forwarded_message_id)] = str(user_id)

    # 対応表が肥大化しないよう、古いものから削除して件数を制限する
    if len(relay_map) > DM_RELAY_MAX_ENTRIES:
        for old_key in list(relay_map.keys())[: len(relay_map) - DM_RELAY_MAX_ENTRIES]:
            del relay_map[old_key]


async def _forward_dm_to_owner(message: discord.Message, owner_id: int):
    """一般ユーザーからBOTへのDMを、送信者情報付きのembedでオーナーへ転送します。"""
    try:
        owner_user = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
    except Exception as e:
        print(f"[DMリレー] オーナー情報の取得に失敗しました: {e}")
        return

    embed = discord.Embed(
        title="📨 BOTにDMが届きました",
        description=message.content if message.content else "(本文なし／添付ファイルのみ)",
        color=discord.Color.blue(),
        timestamp=message.created_at
    )
    embed.set_author(
        name=f"{message.author} ({message.author.id})",
        icon_url=message.author.display_avatar.url if message.author.display_avatar else discord.utils.MISSING
    )
    embed.set_footer(text="このメッセージに「返信」するか、!reply <ユーザーID> <内容> でこのユーザーへ返信できます")

    # 添付ファイルの処理（画像はembedに表示、それ以外はそのままファイルとして転送）
    files_to_send = []
    image_url = None
    extra_image_urls = []
    try:
        for a in message.attachments:
            if a.content_type and a.content_type.startswith("image/") and image_url is None:
                image_url = a.url
            elif a.content_type and a.content_type.startswith("image/"):
                extra_image_urls.append(a.url)
            else:
                files_to_send.append(await a.to_file())
        if image_url:
            embed.set_image(url=image_url)
        if extra_image_urls:
            embed.add_field(name="その他の添付画像", value="\n".join(extra_image_urls), inline=False)
    except Exception as e:
        print(f"[DMリレー] 添付ファイルの処理中にエラーが発生しました: {e}")

    try:
        sent_msg = await owner_user.send(embed=embed, files=files_to_send if files_to_send else None)
    except discord.Forbidden:
        print("[DMリレー] オーナーへの転送に失敗しました（オーナーがBOTからのDMを拒否しています）。")
        return
    except Exception as e:
        print(f"[DMリレー] オーナーへの転送に失敗しました: {e}")
        return

    all_data = load_data()
    _remember_dm_relay(all_data, sent_msg.id, message.author.id)
    save_data(all_data)

    try:
        await message.add_reaction("📨")
    except Exception:
        pass


async def _handle_owner_dm_reply(message: discord.Message) -> bool:
    """
    オーナーがBOTに送ったDMが、転送済みのユーザーメッセージへの「返信(Reply)」かどうかを判定し、
    返信であれば対象ユーザーへ内容を送信します。
    戻り値が True の場合、この関数内で処理が完結したことを示します（process_commandsを呼ぶ必要はありません）。
    """
    if not message.reference or not message.reference.message_id:
        return False

    all_data = load_data()
    global_cfg = get_global_config(all_data)
    relay_map = global_cfg.get("dm_relay_map", {})
    target_user_id_str = relay_map.get(str(message.reference.message_id))

    if not target_user_id_str:
        return False

    await _send_owner_reply_to_user(message, int(target_user_id_str))
    return True


async def _send_owner_reply_to_user(message: discord.Message, target_user_id: int):
    """オーナーからの返信内容を、対象ユーザーへDMで送信します。"""
    try:
        target_user = bot.get_user(target_user_id) or await bot.fetch_user(target_user_id)
    except Exception:
        target_user = None

    if target_user is None:
        try:
            await message.channel.send("[NG] 送信先のユーザーが見つかりませんでした。")
        except Exception:
            pass
        return

    content = message.content or None
    try:
        files = [await a.to_file() for a in message.attachments] if message.attachments else None
    except Exception:
        files = None

    if not content and not files:
        try:
            await message.channel.send("[!] 送信する内容が空です。")
        except Exception:
            pass
        return

    try:
        await target_user.send(content=content, files=files)
        await message.add_reaction("✅")
    except discord.Forbidden:
        try:
            await message.channel.send(f"[NG] {target_user} へのDM送信が拒否されました（DMをブロックしている可能性があります）。")
        except Exception:
            pass
    except Exception as e:
        try:
            await message.channel.send(f"[NG] 送信中にエラーが発生しました: {e}")
        except Exception:
            pass


async def _handle_dm_relay(message: discord.Message):
    """BOT宛のDMを処理します（一般ユーザー→オーナー転送、オーナー→ユーザー返信）。"""
    owner_id = await resolve_owner_id(bot)
    if owner_id is None:
        return

    if message.author.id == owner_id:
        # オーナーからのDM: 転送メッセージへの「返信」であれば対象ユーザーへ送信する
        handled = await _handle_owner_dm_reply(message)
        if not handled:
            # 返信形式でなければ通常のコマンド（!reply <ID> <内容> や !sync 等）として処理する
            await bot.process_commands(message)
        return
    else:
        # 一般ユーザーからのDM: オーナーへ転送する
        await _forward_dm_to_owner(message, owner_id)
        return


@bot.command(name="reply")
async def reply_command(ctx: commands.Context, user_id: int, *, content: str = ""):
    """
    オーナー専用: BOTのDM内で `!reply <ユーザーID> <内容>` と送ることで、
    指定したユーザーへBOTを通して返信します（転送embedへの「返信」操作の代替手段）。
    """
    if ctx.guild is not None:
        return  # DM専用コマンド

    owner_id = await resolve_owner_id(bot)
    if owner_id is None or ctx.author.id != owner_id:
        return

    if not content and not ctx.message.attachments:
        await ctx.send("[!] 使い方: `!reply <ユーザーID> <返信内容>`")
        return

    await _send_owner_reply_to_user(ctx.message, user_id)


@reply_command.error
async def reply_command_error(ctx: commands.Context, error):
    if ctx.guild is not None:
        return
    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
        await ctx.send("[!] 使い方: `!reply <ユーザーID> <返信内容>`")
    else:
        print(f"[!replyコマンドエラー] {error}")


@bot.event
async def on_message(message: discord.Message):
    """
    メッセージ受信時に呼び出されます。
    自動モデレーション（スパム・招待リンク・NGワード）、メッセージ転送、ロールメンションを処理します。
    また、BOT宛のDM（オーナー⇔ユーザー間のメッセージリレー）も処理します。
    """
    if message.author.bot:
        return

    if not message.guild:
        # ギルドに属さないメッセージ = DM -> オーナー⇔ユーザー間のリレー処理
        await _handle_dm_relay(message)
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
                        await message.channel.send(f"[!] {message.author.mention} をスパム検知のため一時ミュートし、メッセージを削除しました。")
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
                    # 転送メッセージにも招待リンクチェックを適用
                    content_lower = message.content.lower()
                    if guild_config.get("automod_invite_enabled", False) and any(
                        kw in content_lower for kw in (
                            "discord.gg/", "discord.com/invite/", "discord.me/", "dsc.gg/"
                        )
                    ):
                        try:
                            await message.delete()
                            await message.channel.send(
                                f"[!] {message.author.mention} 転送元チャンネルでも招待リンクの送信は許可されていません。",
                                delete_after=5
                            )
                        except Exception:
                            pass
                    else:
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

        # 6. 経済システム: メッセージ送信報酬（クールダウン付き）
        if guild_config.get("economy_enabled", False):
            _grant_message_reward(all_data, guild_config, message)

    await bot.process_commands(message)


def _grant_message_reward(all_data: dict, guild_config: dict, message: discord.Message):
    """
    メッセージ送信に対して少額の通貨を自動付与します。
    スパム対策として、ユーザーごとにクールダウン（既定60秒）を設け、
    クールダウン中の連投には一切報酬を与えません（カウントの蓄積もしません）。
    """
    import random

    user_id_str = str(message.author.id)
    now_ts = time.time()

    cooldown = guild_config.get("economy_cooldown_seconds", 60)
    last_earned_map = guild_config.setdefault("economy_last_earned", {})
    last_ts = last_earned_map.get(user_id_str, 0)

    if now_ts - last_ts < cooldown:
        # クールダウン中。報酬なし（不正な高速連投での稼ぎを防止）。
        return

    reward_min = guild_config.get("economy_reward_min", 1)
    reward_max = guild_config.get("economy_reward_max", 5)
    if reward_max < reward_min:
        reward_max = reward_min
    reward = random.randint(reward_min, reward_max)

    balances = guild_config.setdefault("economy_balances", {})
    balances[user_id_str] = balances.get(user_id_str, 0) + reward
    last_earned_map[user_id_str] = now_ts

    save_data(all_data)


def get_balance(guild_config: dict, user_id: int) -> int:
    """指定ユーザーの所持金（通貨）を取得します。"""
    return guild_config.get("economy_balances", {}).get(str(user_id), 0)


def add_balance(guild_config: dict, user_id: int, amount: int):
    """指定ユーザーの所持金に amount を加算（負数で減算）します。"""
    balances = guild_config.setdefault("economy_balances", {})
    user_id_str = str(user_id)
    balances[user_id_str] = balances.get(user_id_str, 0) + amount
    if balances[user_id_str] < 0:
        balances[user_id_str] = 0


def issue_economy_id(guild_config: dict) -> int:
    """role_shop / vending_items 用の連番IDを発行します。"""
    new_id = guild_config.get("economy_next_id", 1)
    guild_config["economy_next_id"] = new_id + 1
    return new_id


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
    deleted = await _run_automod_checks(after, guild_config)
    if deleted and guild_config.get("automod_invite_enabled", False):
        content_lower = after.content.lower()
        if any(kw in content_lower for kw in ("discord.gg/", "discord.com/invite/", "discord.me/", "dsc.gg/")):
            # チャンネル全体の過去メッセージをスキャンして即削除（直近100件）
            try:
                async for msg in after.channel.history(limit=100):
                    if msg.id == after.id:
                        continue
                    if msg.author.bot:
                        continue
                    if not _is_automod_target(msg.author, guild_config, all_data):
                        continue
                    m_content_lower = msg.content.lower()
                    if any(kw in m_content_lower for kw in ("discord.gg/", "discord.com/invite/", "discord.me/", "dsc.gg/")):
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            except Exception as e:
                print(f"[自動モデレーション] チャンネルスキャンエラー: {e}")



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

    # ウェルカムメッセージ送信
    all_data = load_data()
    cfg = get_guild_config(all_data, str(member.guild.id))
    welcome_ch_id = cfg.get("welcome_channel_id")
    welcome_msg = cfg.get("welcome_message")
    welcome_role_id = cfg.get("welcome_role_id")

    if welcome_ch_id and welcome_msg:
        ch = member.guild.get_channel(welcome_ch_id)
        if ch:
            try:
                # {user} {server} {count} プレースホルダー対応
                formatted = welcome_msg.replace("{user}", member.mention)
                formatted = formatted.replace("{username}", str(member))
                formatted = formatted.replace("{server}", member.guild.name)
                formatted = formatted.replace("{count}", str(member.guild.member_count))
                welcome_embed = discord.Embed(
                    description=formatted,
                    color=discord.Color.green()
                )
                welcome_embed.set_thumbnail(url=member.display_avatar.url)
                welcome_embed.set_footer(text=f"{member.guild.name} へようこそ！")
                await ch.send(embed=welcome_embed)
            except Exception as e:
                print(f"[ウェルカム送信エラー] {e}")

    if welcome_role_id:
        role = member.guild.get_role(welcome_role_id)
        if role:
            try:
                await member.add_roles(role, reason="ウェルカム自動ロール付与")
            except Exception:
                pass

    # alt_check: 新規アカウント検知
    alt_enabled = cfg.get("alt_check_enabled", False)
    if alt_enabled:
        threshold_days = cfg.get("alt_check_days", 30)
        account_age = (discord.utils.utcnow() - member.created_at).days
        if account_age < threshold_days:
            action = cfg.get("alt_check_action", "notify")
            alt_embed = discord.Embed(
                title="[!] 新規アカウント検知 (alt_check)",
                color=discord.Color.orange()
            )
            alt_embed.set_thumbnail(url=member.display_avatar.url)
            alt_embed.add_field(name="ユーザー", value=f"{member.mention} (`{member.id}`)", inline=False)
            alt_embed.add_field(name="アカウント作成日", value=member.created_at.strftime("%Y/%m/%d"), inline=True)
            alt_embed.add_field(name="アカウント日齢", value=f"{account_age}日", inline=True)
            alt_embed.add_field(name="閾値", value=f"{threshold_days}日未満", inline=True)
            alt_embed.add_field(name="実行アクション", value={"notify": "通知のみ", "kick": "キック", "ban": "BAN"}.get(action, action), inline=True)
            alt_embed.timestamp = discord.utils.utcnow()

            await _send_mod_log(member.guild, alt_embed)

            if action == "kick":
                try:
                    await member.send(
                        f"**{member.guild.name}** への参加が拒否されました。\n"
                        f"アカウントが作成されてから{threshold_days}日以上経過していないと参加できません（現在{account_age}日）。"
                    )
                except Exception:
                    pass
                try:
                    await member.kick(reason=f"alt_check: アカウント日齢 {account_age}日（閾値: {threshold_days}日）")
                except Exception:
                    pass
            elif action == "ban":
                try:
                    await member.ban(reason=f"alt_check: アカウント日齢 {account_age}日（閾値: {threshold_days}日）")
                except Exception:
                    pass

    # =========================================================
    # サーバーブラックリスト: 特定サーバー参加者を自動BAN/KICK
    # =========================================================
    if cfg.get("server_blacklist_enabled", False) and OAUTH_REDIRECT_URI:
        blacklist_ids = cfg.get("server_blacklist_ids", [])
        if blacklist_ids:
            # HMAC署名付きのstateトークンを生成（guild_id:user_id）
            import hmac
            import hashlib
            state_payload = f"{member.guild.id}:{member.id}"
            signature = hmac.new(
                OAUTH_SECRET_KEY.encode(),
                state_payload.encode(),
                hashlib.sha256
            ).hexdigest()
            state = base64.urlsafe_b64encode(
                f"{state_payload}:{signature}".encode()
            ).decode()

            # OAuth2 認証URL を生成
            params = urllib.parse.urlencode({
                "client_id": DISCORD_CLIENT_ID,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "response_type": "code",
                "scope": "guilds",
                "state": state,
            })
            oauth_url = f"https://discord.com/oauth2/authorize?{params}"

            # ユーザーにDMで認証URLを送信
            try:
                dm_embed = discord.Embed(
                    title=f"[!] {member.guild.name} への参加確認",
                    description=(
                        "このサーバーへの参加には**認証**が必要です。\n\n"
                        "下のリンクをクリックしてDiscord認証を完了してください。\n"
                        "**認証を行わない場合、参加確認ができないため処理が行われません。**"
                    ),
                    color=discord.Color.orange()
                )
                dm_embed.add_field(
                    name="認証リンク",
                    value=f"[こちらをクリックして認証する]({oauth_url})",
                    inline=False
                )
                dm_embed.set_footer(text="このリンクはあなた専用です。他の人と共有しないでください。")
                await member.send(embed=dm_embed)
                print(f"[サーバーBL] {member} に認証URLをDM送信しました（サーバー: {member.guild.name}）")
            except discord.Forbidden:
                # DMが無効の場合はログのみ
                print(f"[サーバーBL] {member} へのDM送信に失敗しました（DM無効）")
            except Exception as e:
                print(f"[サーバーBL] DM送信エラー: {e}")


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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
            "`/calc` : 数式を計算して結果を返します\n"
            "`/poll` : 投票パネルを作成します\n"
            "`/giveaway` : プレゼント企画を作成・管理します\n"
            "`/warnings` : 指定ユーザーの警告履歴を確認します\n"
            "`/server_stats` : サーバーの統計情報を表示します\n"
            "`/iplogger_check` : URLがIPロガーでないかチェックします\n"
            "`/customcmd <名前>` : サーバーに登録されたカスタムコマンドを実行します\n"
            "`/gift` : 自分のコインを他のユーザーに贈ります"
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
                "`/say` : Botに指定したメッセージを代わりに発言させます\n"
                "`/dm_user` : 指定ユーザーにDMを送信します\n"
                "`/embed_builder` : GUIでEmbedメッセージを作成してチャンネルに送信します\n"
                "`/warn` : ユーザーに警告を付与します\n"
                "`/kick` : ユーザーをサーバーからキックします\n"
                "`/ban` : ユーザーをサーバーからBANします\n"
                "`/mute` : ユーザーをタイムアウト（ミュート）します\n"
                "`/purge` : 指定件数のメッセージを一括削除します\n"
                "`/slowmode` : チャンネルの低速モードを設定します"
            ),
            inline=False
        )
    if is_admin or is_owner:
        embed.add_field(
            name="サーバー管理者専用コマンド (1/2)",
            value=(
                "`/server_status` : 現在の各種機能の設定状況を確認します\n"
                "`/server_list_users` : コマンド使用許可リストの確認・編集を行います\n"
                "`/server_create_channel` : 新しいテキストチャンネルを作成します\n"
                "`/server_copy` : チャンネルをコピーして複製します\n"
                "`/server_role_panel` : 指定ロールを取得できるボタン付きパネルを設置します\n"
                "`/server_forward_setup` / `/server_forward_reset` : メッセージ自動転送の設定・解除を行います\n"
                "`/server_announce_setup` / `/server_announce_send` : 配信お知らせ機能の設定と送信を行います\n"
                "`/server_verify_setup` / `/server_verify_btn` : メンバー認証用パネルを設置します\n"
                "`/server_mention_setup` / `/server_mention_reset` : 自動返信ロールメンションの設定と解除を行います\n"
                "`/server_stats` : メンバー数などをチャンネル名に反映する統計機能を設定します\n"
                "`/server_backup` : サーバーのロール・チャンネル・権限をJSONバックアップします\n"
                "`/server_restore` : バックアップJSONからサーバー構成を復元します"
            ),
            inline=False
        )
        embed.add_field(
            name="サーバー管理者専用コマンド (2/2)",
            value=(
                "`/welcome_setup` : 新規参加者へのウェルカムメッセージ・ロールを設定します\n"
                "`/modlog_set` : モデレーションログの通知先チャンネルを設定します\n"
                "`/automod_toggle` : 自動モデレーション機能（スパム・招待リンク・NGワード）を切り替えます\n"
                "`/automod_ngword_add` / `/automod_ngword_remove` : NGワードの追加・削除を行います\n"
                "`/alt_check` : 新規アカウント（サブ垢）の自動検出設定を行います\n"
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
                "`/owner_trust_add` / `/owner_trust_remove` / `/owner_trust_list` : 信頼ユーザーの追加・削除・一覧管理を行います\n"
                "`/eval` : Pythonコードを実行して結果を返します（デバッグ・管理用）\n"
                "`/eval_help` : /eval で使えるコード例を一覧表示します"
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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"こんにちは、{interaction.user.mention}さん。")


@bot.tree.command(name="search", description="各種検索サイトやWikipediaの検索リンクを生成します")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
async def apology(interaction: discord.Interaction):
    view = ApologyBuilderView(author=interaction.user)
    await interaction.response.send_message(
        embed=view.build_preview_embed(),
        view=view,
        ephemeral=True
    )


@bot.tree.command(name="my_memo", description="あなた専用の個人メモを追加・一覧表示・削除・全消去します")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
async def say(interaction: discord.Interaction, message: str):
    # DM・グループDMの場合はBotオーナーのみ許可、サーバー内は通常の権限チェック
    owner_id = await resolve_owner_id(interaction.client)
    if not interaction.guild:
        # DM / グループDM
        if interaction.user.id != owner_id:
            await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
            return
    else:
        # サーバー内は従来の権限チェック
        if not await is_admin_or_allowed(interaction):
            return

    await interaction.response.send_message("メッセージを送信しました。", ephemeral=True)

    # DMチャンネルでは interaction.channel が None になる場合があるので followup で対処
    if interaction.channel:
        await interaction.channel.send(message)
    else:
        # DMチャンネルが取得できない場合はユーザーのDMへ直接送信
        await interaction.user.send(message)


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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
# セクション 8-EX: 追加機能コマンド群
# /slowmode / /poll / /welcome_setup / /server_stats / /dm_user
# ====================================================================

# --------------------------------------------------------------------
# /slowmode
# --------------------------------------------------------------------

@bot.tree.command(name="slowmode", description="【モデレーター専用】チャンネルのスロウモードを設定・解除します")
async def slowmode(
    interaction: discord.Interaction,
    秒数: app_commands.Range[int, 0, 21600],
    チャンネル: discord.TextChannel = None,
):
    """
    秒数に 0 を指定すると解除。省略時は現在のチャンネルに適用。
    最大 21600秒（6時間）まで設定可能。
    """
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return

    target_ch = チャンネル or interaction.channel
    try:
        await target_ch.edit(slowmode_delay=秒数, reason=f"スロウモード設定 by {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message("権限が不足しているため設定できません。", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)
        return

    if 秒数 == 0:
        msg = f"{target_ch.mention} のスロウモードを**解除**しました。"
    elif 秒数 < 60:
        msg = f"{target_ch.mention} のスロウモードを **{秒数}秒** に設定しました。"
    elif 秒数 < 3600:
        msg = f"{target_ch.mention} のスロウモードを **{秒数 // 60}分{秒数 % 60}秒** に設定しました。"
    else:
        h = 秒数 // 3600
        m = (秒数 % 3600) // 60
        msg = f"{target_ch.mention} のスロウモードを **{h}時間{m}分** に設定しました。"

    await interaction.response.send_message(msg, ephemeral=True)


# --------------------------------------------------------------------
# /poll — リアクション投票パネル
# --------------------------------------------------------------------

@bot.tree.command(name="poll", description="【管理者専用】絵文字ボタン付きの投票パネルを作成します")
async def poll(
    interaction: discord.Interaction,
    質問: str,
    選択肢1: str,
    選択肢2: str,
    選択肢3: str = None,
    選択肢4: str = None,
    選択肢5: str = None,
):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    choices_raw = [選択肢1, 選択肢2, 選択肢3, 選択肢4, 選択肢5]
    choices = [c for c in choices_raw if c]

    EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    embed = discord.Embed(
        title=f"[STATS] {質問}",
        color=discord.Color.blurple()
    )
    for i, choice in enumerate(choices):
        embed.add_field(name=f"{EMOJIS[i]} {choice}", value="\u200b", inline=False)
    embed.set_footer(text=f"投票者: {interaction.user} | ボタンを押して投票してください")

    view = PollView(choices, EMOJIS[:len(choices)])
    await interaction.response.send_message(embed=embed, view=view)


class PollView(discord.ui.View):
    """投票パネル用ビュー。各選択肢のボタン押下で票数を集計します。"""

    def __init__(self, choices: list[str], emojis: list[str]):
        super().__init__(timeout=None)
        self.choices = choices
        self.emojis = emojis
        # {user_id: choice_index} — 1人1票
        self.votes: dict[int, int] = {}
        for i, (choice, emoji) in enumerate(zip(choices, emojis)):
            btn = discord.ui.Button(
                label=choice[:40],
                emoji=emoji,
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_choice_{i}",
                row=i // 3,
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

        # 結果表示ボタン
        result_btn = discord.ui.Button(
            label="現在の結果を見る",
            style=discord.ButtonStyle.secondary,
            emoji="[STATS]",
            custom_id="poll_result",
            row=2,
        )
        result_btn.callback = self._show_result
        self.add_item(result_btn)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            uid = interaction.user.id
            prev = self.votes.get(uid)
            if prev == index:
                # 同じ選択肢を再押し→取消
                del self.votes[uid]
                await interaction.response.send_message(
                    f"{self.emojis[index]} **{self.choices[index]}** への投票を取り消しました。",
                    ephemeral=True
                )
            else:
                self.votes[uid] = index
                await interaction.response.send_message(
                    f"{self.emojis[index]} **{self.choices[index]}** に投票しました！\n"
                    "もう一度押すと取り消せます。",
                    ephemeral=True
                )
        return callback

    async def _show_result(self, interaction: discord.Interaction):
        total = len(self.votes)
        lines = []
        for i, (choice, emoji) in enumerate(zip(self.choices, self.emojis)):
            count = sum(1 for v in self.votes.values() if v == i)
            pct = int(count / total * 100) if total > 0 else 0
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"{emoji} **{choice}**\n`{bar}` {count}票 ({pct}%)")
        embed = discord.Embed(
            title="[STATS] 現在の投票結果",
            description="\n\n".join(lines) if lines else "まだ投票はありません。",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"総投票数: {total}票")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /welcome_setup — ウェルカムメッセージ設定
# --------------------------------------------------------------------

@bot.tree.command(name="welcome_setup", description="【管理者専用】メンバー参加時のウェルカムメッセージを設定します")
@discord.app_commands.choices(操作=[
    discord.app_commands.Choice(name="設定する", value="set"),
    discord.app_commands.Choice(name="解除する", value="reset"),
    discord.app_commands.Choice(name="現在の設定を確認", value="status"),
])
async def welcome_setup(
    interaction: discord.Interaction,
    操作: discord.app_commands.Choice[str],
    チャンネル: discord.TextChannel = None,
    メッセージ: str = None,
    自動付与ロール: discord.Role = None,
):
    """
    メッセージ内で使えるプレースホルダー:
      {user}     → メンションになります
      {username} → ユーザー名
      {server}   → サーバー名
      {count}    → 現在のメンバー数
    """
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    if 操作.value == "reset":
        cfg["welcome_channel_id"] = None
        cfg["welcome_message"] = None
        cfg["welcome_role_id"] = None
        save_data(all_data)
        await interaction.response.send_message("ウェルカムメッセージの設定を解除しました。", ephemeral=True)
        return

    if 操作.value == "status":
        ch_id = cfg.get("welcome_channel_id")
        msg = cfg.get("welcome_message")
        role_id = cfg.get("welcome_role_id")
        ch = interaction.guild.get_channel(ch_id) if ch_id else None
        role = interaction.guild.get_role(role_id) if role_id else None
        embed = discord.Embed(title="ウェルカムメッセージ設定状況", color=discord.Color.teal())
        embed.add_field(name="送信チャンネル", value=ch.mention if ch else "未設定", inline=True)
        embed.add_field(name="自動付与ロール", value=role.mention if role else "なし", inline=True)
        embed.add_field(
            name="メッセージ内容",
            value=f"```\n{msg}\n```" if msg else "未設定",
            inline=False
        )
        embed.set_footer(text="プレースホルダー: {user} {username} {server} {count}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # 操作 == "set"
    if not チャンネル or not メッセージ:
        await interaction.response.send_message(
            "設定する場合は「チャンネル」と「メッセージ」の両方を指定してください。",
            ephemeral=True
        )
        return

    cfg["welcome_channel_id"] = チャンネル.id
    cfg["welcome_message"] = メッセージ
    cfg["welcome_role_id"] = 自動付与ロール.id if 自動付与ロール else None
    save_data(all_data)

    preview = メッセージ.replace("{user}", interaction.user.mention)
    preview = preview.replace("{username}", str(interaction.user))
    preview = preview.replace("{server}", interaction.guild.name)
    preview = preview.replace("{count}", str(interaction.guild.member_count))

    embed = discord.Embed(
        title="ウェルカムメッセージを設定しました",
        color=discord.Color.green()
    )
    embed.add_field(name="送信チャンネル", value=チャンネル.mention, inline=True)
    embed.add_field(name="自動付与ロール", value=自動付与ロール.mention if 自動付与ロール else "なし", inline=True)
    embed.add_field(name="プレビュー", value=preview[:500], inline=False)
    embed.set_footer(text="メンバーが参加するたびにこのメッセージが送信されます")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /server_stats — リアルタイム統計チャンネル
# --------------------------------------------------------------------

# 統計更新タスクの管理（guild_id -> task）
_stats_tasks: dict[int, asyncio.Task] = {}


async def _update_stats_channels(guild: discord.Guild):
    """統計チャンネルの名前をメンバー数などに合わせて更新します。"""
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild.id))

    member_ch_id = cfg.get("stats_member_ch_id")
    online_ch_id = cfg.get("stats_online_ch_id")
    bot_ch_id = cfg.get("stats_bot_ch_id")

    total = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    humans = total - bots
    online = sum(
        1 for m in guild.members
        if not m.bot and m.status != discord.Status.offline
    )

    updates = [
        (member_ch_id, f"👥 メンバー: {humans}人"),
        (online_ch_id, f"[+] オンライン: {online}人"),
        (bot_ch_id,    f"[BOT] Bot: {bots}体"),
    ]
    for ch_id, new_name in updates:
        if not ch_id:
            continue
        ch = guild.get_channel(ch_id)
        if ch and ch.name != new_name:
            try:
                await ch.edit(name=new_name, reason="サーバー統計更新")
            except Exception:
                pass


async def _stats_loop(guild: discord.Guild):
    """5分ごとに統計チャンネルを更新するループタスクです。"""
    while True:
        try:
            await _update_stats_channels(guild)
        except Exception as e:
            print(f"[stats_loop エラー] {guild.name}: {e}")
        await asyncio.sleep(300)  # 5分ごとに更新


@bot.tree.command(name="server_stats", description="【管理者専用】サーバー統計をリアルタイムでチャンネル名に表示します")
@discord.app_commands.choices(操作=[
    discord.app_commands.Choice(name="設定する（カテゴリを指定）", value="set"),
    discord.app_commands.Choice(name="解除する", value="reset"),
    discord.app_commands.Choice(name="今すぐ更新", value="update"),
    discord.app_commands.Choice(name="現在の設定を確認", value="status"),
])
async def server_stats(
    interaction: discord.Interaction,
    操作: discord.app_commands.Choice[str],
    カテゴリ: discord.CategoryChannel = None,
):
    """
    「設定する」を実行するとカテゴリ内に
      👥 メンバー: XX人
      [+] オンライン: XX人
      [BOT] Bot: XX体
    の3つのボイスチャンネルを自動作成し、5分ごとに名前を更新します。
    """
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    guild = interaction.guild

    if 操作.value == "status":
        member_ch = guild.get_channel(cfg.get("stats_member_ch_id"))
        online_ch = guild.get_channel(cfg.get("stats_online_ch_id"))
        bot_ch    = guild.get_channel(cfg.get("stats_bot_ch_id"))
        cat       = guild.get_channel(cfg.get("stats_category_id"))
        embed = discord.Embed(title="サーバー統計チャンネル設定状況", color=discord.Color.blue())
        embed.add_field(name="カテゴリ", value=cat.name if cat else "未設定", inline=False)
        embed.add_field(name="メンバー数ch", value=member_ch.mention if member_ch else "未設定", inline=True)
        embed.add_field(name="オンラインch", value=online_ch.mention if online_ch else "未設定", inline=True)
        embed.add_field(name="Botch",        value=bot_ch.mention    if bot_ch    else "未設定", inline=True)
        task_running = guild.id in _stats_tasks and not _stats_tasks[guild.id].done()
        embed.add_field(name="自動更新", value="稼働中（5分ごと）" if task_running else "停止中", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if 操作.value == "update":
        ch_ids = [cfg.get("stats_member_ch_id"), cfg.get("stats_online_ch_id"), cfg.get("stats_bot_ch_id")]
        if not any(ch_ids):
            await interaction.response.send_message("統計チャンネルが設定されていません。先に「設定する」を実行してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _update_stats_channels(guild)
        await interaction.followup.send("統計チャンネルを今すぐ更新しました。", ephemeral=True)
        return

    if 操作.value == "reset":
        for ch_key in ("stats_member_ch_id", "stats_online_ch_id", "stats_bot_ch_id"):
            ch_id = cfg.get(ch_key)
            if ch_id:
                ch = guild.get_channel(ch_id)
                if ch:
                    try:
                        await ch.delete(reason="統計チャンネル解除")
                    except Exception:
                        pass
        cfg["stats_category_id"] = None
        cfg["stats_member_ch_id"] = None
        cfg["stats_online_ch_id"] = None
        cfg["stats_bot_ch_id"] = None
        save_data(all_data)
        task = _stats_tasks.pop(guild.id, None)
        if task and not task.done():
            task.cancel()
        await interaction.response.send_message("統計チャンネルの設定を解除し、チャンネルを削除しました。", ephemeral=True)
        return

    # 操作 == "set"
    if not カテゴリ:
        await interaction.response.send_message(
            "設定する場合は「カテゴリ」を指定してください。\n"
            "指定したカテゴリ内に統計ボイスチャンネルが自動作成されます。",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    total = guild.member_count
    bots  = sum(1 for m in guild.members if m.bot)
    humans = total - bots
    online = sum(1 for m in guild.members if not m.bot and m.status != discord.Status.offline)

    # 既存チャンネルがあれば削除してから再作成
    for ch_key in ("stats_member_ch_id", "stats_online_ch_id", "stats_bot_ch_id"):
        old_ch_id = cfg.get(ch_key)
        if old_ch_id:
            old_ch = guild.get_channel(old_ch_id)
            if old_ch:
                try:
                    await old_ch.delete(reason="統計チャンネル再作成")
                except Exception:
                    pass

    # 閲覧のみ・接続不可のパーミッション
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=False
        )
    }

    try:
        member_ch = await guild.create_voice_channel(
            name=f"👥 メンバー: {humans}人",
            category=カテゴリ,
            overwrites=overwrites,
            reason="サーバー統計チャンネル作成"
        )
        online_ch = await guild.create_voice_channel(
            name=f"[+] オンライン: {online}人",
            category=カテゴリ,
            overwrites=overwrites,
            reason="サーバー統計チャンネル作成"
        )
        bot_ch = await guild.create_voice_channel(
            name=f"[BOT] Bot: {bots}体",
            category=カテゴリ,
            overwrites=overwrites,
            reason="サーバー統計チャンネル作成"
        )
    except discord.Forbidden:
        await interaction.followup.send("チャンネル作成権限が不足しています。", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"チャンネル作成中にエラーが発生しました: {e}", ephemeral=True)
        return

    cfg["stats_category_id"]  = カテゴリ.id
    cfg["stats_member_ch_id"] = member_ch.id
    cfg["stats_online_ch_id"] = online_ch.id
    cfg["stats_bot_ch_id"]    = bot_ch.id
    save_data(all_data)

    # 既存タスクをキャンセルして新しいループを開始
    old_task = _stats_tasks.pop(guild.id, None)
    if old_task and not old_task.done():
        old_task.cancel()
    _stats_tasks[guild.id] = asyncio.create_task(_stats_loop(guild))

    embed = discord.Embed(
        title="サーバー統計チャンネルを設定しました",
        color=discord.Color.green()
    )
    embed.add_field(name="カテゴリ",       value=カテゴリ.name,         inline=False)
    embed.add_field(name="メンバー数",     value=member_ch.mention,    inline=True)
    embed.add_field(name="オンライン人数", value=online_ch.mention,     inline=True)
    embed.add_field(name="Bot数",          value=bot_ch.mention,        inline=True)
    embed.set_footer(text="5分ごとに自動更新されます。Bot再起動後は /server_stats 設定する を再実行してください。")
    await interaction.followup.send(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /dm_user — 特定ユーザーへのDM送信
# --------------------------------------------------------------------

@bot.tree.command(name="dm_user", description="【オーナー・許可ユーザー専用】指定したユーザーにBotからDMを送信します")
async def dm_user(
    interaction: discord.Interaction,
    ユーザー: discord.User,
    メッセージ: str,
    匿名送信: bool = False,
):
    """
    匿名送信=True にすると送信者情報をDMに含めません（サーバー名のみ記載）。
    """
    if not await is_admin_or_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        description=メッセージ,
        color=discord.Color.blurple()
    )
    if interaction.guild:
        embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    if not 匿名送信:
        embed.set_footer(text=f"送信者: {interaction.user} | {interaction.guild.name if interaction.guild else 'Direct'}")
    else:
        embed.set_footer(text=f"このメッセージは {interaction.guild.name if interaction.guild else 'Bot'} から送信されました")

    try:
        await ユーザー.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            f"{ユーザー.mention} へのDM送信に失敗しました。\n"
            "DM受信を拒否している可能性があります。",
            ephemeral=True
        )
        return
    except Exception as e:
        await interaction.followup.send(f"送信中にエラーが発生しました: {e}", ephemeral=True)
        return

    result_embed = discord.Embed(
        title="DM送信完了",
        color=discord.Color.green()
    )
    result_embed.add_field(name="送信先", value=f"{ユーザー} (`{ユーザー.id}`)", inline=False)
    result_embed.add_field(name="内容", value=メッセージ[:500], inline=False)
    result_embed.add_field(name="匿名送信", value="はい" if 匿名送信 else "いいえ", inline=True)
    await interaction.followup.send(embed=result_embed, ephemeral=True)

    # モデレーションログにも記録
    if interaction.guild:
        log_embed = discord.Embed(
            title="[ログ] Bot経由DM送信",
            color=discord.Color.purple()
        )
        log_embed.add_field(name="送信者", value=interaction.user.mention, inline=True)
        log_embed.add_field(name="送信先", value=f"{ユーザー.mention} (`{ユーザー.id}`)", inline=True)
        log_embed.add_field(name="内容", value=メッセージ[:500], inline=False)
        log_embed.add_field(name="匿名送信", value="はい" if 匿名送信 else "いいえ", inline=True)
        log_embed.timestamp = discord.utils.utcnow()
        await _send_mod_log(interaction.guild, log_embed)


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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
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
        "channel": interaction.channel,
        "guild": interaction.guild,
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
    embed.set_footer(text=f"実行時間: {elapsed:.2f}ms | 実行者: {interaction.user}")

    await interaction.followup.send(embed=embed, ephemeral=False)


# ====================================================================
# セクション 12: /eval_help コマンド（オーナー限定・コード例一覧）
# ====================================================================

EVAL_EXAMPLES = {
    "Bot管理・デバッグ": [
        {
            "title": "Bot起動時間・レイテンシ確認",
            "code": "f'レイテンシ: {round(bot.latency * 1000)}ms'",
            "desc": "BotのWebSocketレイテンシをミリ秒で確認します。"
        },
        {
            "title": "導入サーバー数を確認",
            "code": "f'導入サーバー数: {len(bot.guilds)}個'",
            "desc": "現在Botが参加しているサーバーの総数を返します。"
        },
        {
            "title": "導入サーバー名一覧を表示",
            "code": "print('\\n'.join([f'{i+1}. {g.name} (ID:{g.id})' for i, g in enumerate(bot.guilds)]))",
            "desc": "参加中の全サーバー名とIDを一覧表示します。"
        },
        {
            "title": "Botユーザー情報を確認",
            "code": "f'{bot.user} (ID: {bot.user.id})'",
            "desc": "Botのユーザー名とIDを返します。"
        },
        {
            "title": "キャッシュ済みユーザー数を確認",
            "code": "f'キャッシュ済みユーザー数: {len(bot.users)}人'",
            "desc": "Botがキャッシュしている全ユーザー数を返します。"
        },
        {
            "title": "現在のカスタムステータスを確認",
            "code": "current_custom_status or 'デフォルト（サーバー数カウント）'",
            "desc": "現在設定されているカスタムステータスの文字列を確認します。"
        },
        {
            "title": "スパムキャッシュの状態を確認",
            "code": "getattr(bot, 'spam_cache', {})",
            "desc": "AutoModのスパム検知キャッシュの現在状態を確認します。"
        },
        {
            "title": "登録済みスラッシュコマンド数を確認",
            "code": "f'グローバルコマンド数: {len(bot.tree.get_commands())}個'",
            "desc": "グローバルに登録されているスラッシュコマンドの数を返します。"
        },
    ],
    "サーバー情報確認": [
        {
            "title": "現在のサーバー情報を確認",
            "code": "f'{guild.name} / メンバー: {guild.member_count}人 / ロール: {len(guild.roles)}個 / ch: {len(guild.channels)}個'",
            "desc": "コマンドを実行したサーバーの基本情報を返します。"
        },
        {
            "title": "サーバーのオーナーを確認",
            "code": "f'オーナー: {guild.owner} (ID: {guild.owner_id})'",
            "desc": "サーバーのオーナー名とIDを返します。"
        },
        {
            "title": "サーバーのロール一覧を表示",
            "code": "print('\\n'.join([f'{r.position}: {r.name} (ID:{r.id})' for r in sorted(guild.roles, key=lambda r: -r.position)]))",
            "desc": "サーバーの全ロールを権限位置順に一覧表示します。"
        },
        {
            "title": "テキストチャンネル一覧を表示",
            "code": "print('\\n'.join([f'#{c.name} (ID:{c.id})' for c in guild.text_channels]))",
            "desc": "サーバーの全テキストチャンネルを一覧表示します。"
        },
        {
            "title": "BotメンバーとHumanメンバーの内訳",
            "code": "bots = sum(1 for m in guild.members if m.bot); print(f'Human: {guild.member_count - bots}人 / Bot: {bots}体')",
            "desc": "サーバーメンバーの人間とBot数の内訳を表示します。"
        },
        {
            "title": "承認済みサーバー一覧を確認",
            "code": "data = load_data(); approved = [sid for sid, cfg in data.items() if isinstance(cfg, dict) and cfg.get('approval_status') == 'approved']; print(f'承認済み: {len(approved)}サーバー\\n' + '\\n'.join(approved))",
            "desc": "Bot利用が承認済みのサーバーID一覧を表示します。"
        },
        {
            "title": "サーバーのブースト状況を確認",
            "code": "f'{guild.name} ブーストLv.{guild.premium_tier} / ブースト数: {guild.premium_subscription_count}回'",
            "desc": "サーバーのNitroブーストレベルと回数を返します。"
        },
        {
            "title": "antinuke設定状況を確認",
            "code": "data = load_data(); cfg = get_guild_config(data, str(guild.id)); an = cfg.get('antinuke', {}); f\"antinuke: {'有効' if an.get('enabled') else '無効'} / 閾値: {an.get('threshold_seconds',10)}秒で{an.get('threshold_count',3)}回\"",
            "desc": "現在のサーバーのantinuke設定をまとめて返します。"
        },
    ],
    "データ操作・JSON確認": [
        {
            "title": "JSONデータ全体を確認",
            "code": "import json; data = load_data(); print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])",
            "desc": "保存されているJSONデータ全体を整形して表示します（先頭1500文字）。"
        },
        {
            "title": "現在のサーバー設定を確認",
            "code": "import json; data = load_data(); cfg = get_guild_config(data, str(guild.id)); print(json.dumps(cfg, ensure_ascii=False, indent=2))",
            "desc": "コマンド実行サーバーの設定JSONをそのまま表示します。"
        },
        {
            "title": "コマンド許可ユーザーを確認",
            "code": "data = load_data(); cfg = get_guild_config(data, str(guild.id)); allowed = cfg.get('allowed_users', []); f'許可ユーザー数: {len(allowed)}人\\n' + ', '.join([str(uid) for uid in allowed]) or 'なし'",
            "desc": "現在のサーバーのコマンド許可ユーザーIDを一覧表示します。"
        },
        {
            "title": "カスタムトリガー一覧を確認",
            "code": "data = load_data(); cfg = get_guild_config(data, str(guild.id)); triggers = cfg.get('custom_triggers', []); print('\\n'.join([f\"{t['trigger']} ({t.get('match_type','contains')}) -> {t['response']}\" for t in triggers])) if triggers else print('なし')",
            "desc": "現在のサーバーに登録されたカスタムトリガーを全件表示します。"
        },
        {
            "title": "カスタムコマンド一覧を確認",
            "code": "data = load_data(); cfg = get_guild_config(data, str(guild.id)); cmds = cfg.get('custom_commands', {}); print('\\n'.join([f'/customcmd {k} -> {v}' for k, v in cmds.items()])) if cmds else print('なし')",
            "desc": "現在のサーバーに登録されたカスタムコマンドを全件表示します。"
        },
        {
            "title": "全サーバーの警告数を確認",
            "code": "data = load_data(); [(sid, len(get_guild_config(data, sid).get('warnings', {}))) for sid in data if sid not in ('user_apps','global_config') and isinstance(data[sid], dict)]",
            "desc": "全サーバーの警告レコード数を一覧で返します。"
        },
        {
            "title": "グローバル信頼ユーザー一覧を確認",
            "code": "data = load_data(); trusted = data.get('global_config', {}).get('trusted_users', []); f'信頼ユーザー数: {len(trusted)}人\\n' + ', '.join([str(uid) for uid in trusted]) or 'なし'",
            "desc": "グローバル信頼ユーザーのID一覧を表示します。"
        },
        {
            "title": "NGワード一覧を確認",
            "code": "data = load_data(); cfg = get_guild_config(data, str(guild.id)); ng = cfg.get('ng_words', []); ', '.join(ng) if ng else 'NGワード未登録'",
            "desc": "現在のサーバーに登録されているNGワードを表示します。"
        },
    ]
}


@bot.tree.command(name="eval_help", description="【オーナー限定】/evalで使えるコード例を一覧表示します")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
@discord.app_commands.choices(カテゴリ=[
    discord.app_commands.Choice(name="Bot管理・デバッグ用", value="Bot管理・デバッグ"),
    discord.app_commands.Choice(name="サーバー情報確認用", value="サーバー情報確認"),
    discord.app_commands.Choice(name="データ操作・JSON確認用", value="データ操作・JSON確認"),
])
async def eval_help(interaction: discord.Interaction, カテゴリ: discord.app_commands.Choice[str]):
    if not await is_owner_check(interaction):
        return

    category_key = カテゴリ.value
    examples = EVAL_EXAMPLES.get(category_key, [])

    color_map = {
        "Bot管理・デバッグ":    discord.Color.blurple(),
        "サーバー情報確認":     discord.Color.teal(),
        "データ操作・JSON確認": discord.Color.gold(),
    }

    embed = discord.Embed(
        title=f"/eval コード例一覧 ― {カテゴリ.name}",
        description=(
            "コピーして `/eval コード:` に貼り付けるだけで実行できます。\n"
            "複数行コードは \\`\\`\\` で囲んでも実行できます。\n"
            "※ `guild` / `bot` / `channel` 変数はそのまま使用可能です。"
        ),
        color=color_map.get(category_key, discord.Color.blue())
    )

    for ex in examples:
        embed.add_field(
            name=f"🔹 {ex['title']}",
            value=(
                f"{ex['desc']}\n"
                f"```py\n{ex['code']}\n```"
            ),
            inline=False
        )

    embed.set_footer(text=f"カテゴリ: {カテゴリ.name} | 全{len(examples)}件 | /eval_help で他カテゴリも確認できます")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ====================================================================
# セクション 13: 追加機能コマンド群
# /calc / /giveaway / /alt_check / /iplogger_check / /embed_builder
# ====================================================================

# --------------------------------------------------------------------
# /calc — 数式計算
# --------------------------------------------------------------------

@bot.tree.command(name="calc", description="数式を計算して結果を返します")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=False)
async def calc(interaction: discord.Interaction, 数式: str):
    """
    四則演算・累乗・括弧・関数（sin/cos/sqrt等）に対応。
    import や exec などの危険な操作は禁止されています。
    """
    import math
    import ast as _ast
    import operator as _op

    # 許可する演算子・関数のみのサンドボックス
    ALLOWED_OPS = {
        _ast.Add:    _op.add,
        _ast.Sub:    _op.sub,
        _ast.Mult:   _op.mul,
        _ast.Div:    _op.truediv,
        _ast.Pow:    _op.pow,
        _ast.USub:   _op.neg,
        _ast.UAdd:   _op.pos,
        _ast.Mod:    _op.mod,
        _ast.FloorDiv: _op.floordiv,
    }
    ALLOWED_FUNCS = {
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "log2": math.log2, "exp": math.exp, "abs": abs,
        "ceil": math.ceil, "floor": math.floor, "round": round,
        "factorial": math.factorial, "degrees": math.degrees,
        "radians": math.radians,
    }
    ALLOWED_CONSTS = {
        "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
    }

    def _safe_eval(node):
        if isinstance(node, _ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("文字列や他の型は使用できません")
        elif isinstance(node, _ast.BinOp):
            op_type = type(node.op)
            if op_type not in ALLOWED_OPS:
                raise ValueError(f"使用できない演算子です: {op_type.__name__}")
            left  = _safe_eval(node.left)
            right = _safe_eval(node.right)
            if op_type == _ast.Div and right == 0:
                raise ZeroDivisionError("0で割ることはできません")
            return ALLOWED_OPS[op_type](left, right)
        elif isinstance(node, _ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in ALLOWED_OPS:
                raise ValueError(f"使用できない演算子です: {op_type.__name__}")
            return ALLOWED_OPS[op_type](_safe_eval(node.operand))
        elif isinstance(node, _ast.Call):
            if not isinstance(node.func, _ast.Name):
                raise ValueError("関数呼び出しの形式が不正です")
            func_name = node.func.id
            if func_name not in ALLOWED_FUNCS:
                raise ValueError(f"使用できない関数です: `{func_name}`")
            args = [_safe_eval(a) for a in node.args]
            return ALLOWED_FUNCS[func_name](*args)
        elif isinstance(node, _ast.Name):
            if node.id in ALLOWED_CONSTS:
                return ALLOWED_CONSTS[node.id]
            raise ValueError(f"使用できない変数です: `{node.id}`")
        else:
            raise ValueError(f"サポートされていない式の形式です: {type(node).__name__}")

    expr = 数式.strip()
    # 全角数字・記号を半角に変換
    expr = expr.translate(str.maketrans(
        "０１２３４５６７８９＋－×÷＊＾（）　",
        "0123456789+-*/*^ () "
    ))
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/")

    try:
        tree = _ast.parse(expr, mode="eval")
        result = _safe_eval(tree.body)
    except ZeroDivisionError as e:
        await interaction.response.send_message(f"[NG] エラー: {e}", ephemeral=True)
        return
    except (ValueError, TypeError) as e:
        await interaction.response.send_message(f"[NG] 計算エラー: {e}", ephemeral=True)
        return
    except SyntaxError:
        await interaction.response.send_message("[NG] 数式の形式が正しくありません。", ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"[NG] 予期しないエラー: {e}", ephemeral=True)
        return

    # 結果の整形（整数なら小数点なし）
    if isinstance(result, float) and result.is_integer():
        result_str = str(int(result))
    elif isinstance(result, float):
        result_str = f"{result:.10g}"
    else:
        result_str = str(result)

    embed = discord.Embed(color=discord.Color.blurple())
    embed.add_field(name="計算式", value=f"```\n{数式}\n```", inline=False)
    embed.add_field(name="結果",   value=f"```\n{result_str}\n```", inline=False)
    embed.set_footer(text="使用可能: + - * / ** % // | sin cos sqrt log pi e ...")
    await interaction.response.send_message(embed=embed)


# --------------------------------------------------------------------
# /giveaway — 抽選プレゼント機能
# --------------------------------------------------------------------

# 実行中プレゼント管理 {message_id: asyncio.Task}
_giveaway_tasks: dict[int, asyncio.Task] = {}


class GiveawayJoinView(discord.ui.View):
    """プレゼント参加ボタンビュー。カスタムIDで永続化対応。"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="[*] 参加する",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_join"
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        giveaways = cfg.get("giveaways", {})
        msg_id_str = str(interaction.message.id)

        if msg_id_str not in giveaways:
            await interaction.response.send_message("このプレゼント企画は終了または削除されました。", ephemeral=True)
            return

        gw = giveaways[msg_id_str]
        participants = gw.get("participants", [])
        uid = interaction.user.id

        if uid in participants:
            # 参加取消
            participants.remove(uid)
            gw["participants"] = participants
            save_data(all_data)
            await interaction.response.send_message("プレゼント企画への参加を取り消しました。", ephemeral=True)
        else:
            participants.append(uid)
            gw["participants"] = participants
            save_data(all_data)
            await interaction.response.send_message("[*] プレゼント企画に参加しました！もう一度押すと取り消せます。", ephemeral=True)

        # Embed の参加者数を更新
        try:
            embed = interaction.message.embeds[0]
            for i, field in enumerate(embed.fields):
                if "参加者" in field.name:
                    embed.set_field_at(i, name="参加者数", value=f"{len(participants)}人", inline=True)
                    break
            await interaction.message.edit(embed=embed)
        except Exception:
            pass


def _build_giveaway_embed(prize: str, host: discord.Member, end_dt: datetime.datetime, winners: int, participants: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"[*] プレゼント企画: {prize}",
        color=discord.Color.gold()
    )
    embed.add_field(name="景品", value=prize, inline=True)
    embed.add_field(name="当選人数", value=f"{winners}人", inline=True)
    embed.add_field(name="参加者数", value=f"{participants}人", inline=True)
    embed.add_field(name="主催者", value=host.mention, inline=True)
    embed.add_field(name="終了日時", value=discord.utils.format_dt(end_dt, style="F"), inline=True)
    embed.set_footer(text="[*] ボタンを押して参加！もう一度押すと取り消せます")
    embed.timestamp = end_dt
    return embed


async def _run_giveaway(channel: discord.TextChannel, message_id: int, guild_id: int, end_dt: datetime.datetime):
    """指定時刻まで待機し、抽選を実行します。"""
    now = discord.utils.utcnow()
    wait_seconds = (end_dt - now).total_seconds()
    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    # 抽選実行
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild_id))
    giveaways = cfg.get("giveaways", {})
    msg_id_str = str(message_id)

    if msg_id_str not in giveaways:
        return

    gw = giveaways[msg_id_str]
    participants = gw.get("participants", [])
    winner_count = gw.get("winners", 1)
    prize = gw.get("prize", "景品")

    import random
    if not participants:
        result_embed = discord.Embed(
            title="[*] プレゼント企画 終了",
            description=f"**{prize}**\n\n参加者がいなかったため、当選者なしで終了しました。",
            color=discord.Color.greyple()
        )
    else:
        actual_winners = min(winner_count, len(participants))
        chosen = random.sample(participants, actual_winners)
        mentions = " ".join(f"<@{uid}>" for uid in chosen)
        result_embed = discord.Embed(
            title="[*] プレゼント企画 終了！",
            description=f"**景品: {prize}**\n\n[WIN] 当選者: {mentions}\nおめでとうございます！",
            color=discord.Color.gold()
        )
        result_embed.add_field(name="参加者数", value=f"{len(participants)}人", inline=True)
        result_embed.add_field(name="当選人数", value=f"{actual_winners}人", inline=True)

    result_embed.timestamp = discord.utils.utcnow()

    # 元メッセージを更新してボタンを無効化
    try:
        msg = await channel.fetch_message(message_id)
        disabled_view = discord.ui.View()
        disabled_btn = discord.ui.Button(
            label="[*] 終了（参加受付終了）",
            style=discord.ButtonStyle.secondary,
            disabled=True
        )
        disabled_view.add_item(disabled_btn)
        await msg.edit(view=disabled_view)
    except Exception:
        pass

    try:
        await channel.send(embed=result_embed)
    except Exception:
        pass

    # データから削除
    del giveaways[msg_id_str]
    save_data(all_data)
    _giveaway_tasks.pop(message_id, None)


@bot.tree.command(name="giveaway", description="【管理者専用】プレゼント企画を開始します")
@discord.app_commands.choices(操作=[
    discord.app_commands.Choice(name="開始する", value="start"),
    discord.app_commands.Choice(name="終了する（即時抽選）", value="end"),
    discord.app_commands.Choice(name="一覧を表示", value="list"),
])
async def giveaway(
    interaction: discord.Interaction,
    操作: discord.app_commands.Choice[str],
    景品: str = None,
    時間_分: int = None,
    当選人数: int = 1,
):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    if 操作.value == "list":
        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        giveaways = cfg.get("giveaways", {})
        if not giveaways:
            await interaction.response.send_message("現在進行中のプレゼント企画はありません。", ephemeral=True)
            return
        embed = discord.Embed(title="進行中のプレゼント企画", color=discord.Color.gold())
        for msg_id, gw in giveaways.items():
            end_dt = datetime.datetime.fromisoformat(gw["end_at"])
            embed.add_field(
                name=f"[*] {gw['prize']}",
                value=(
                    f"メッセージID: `{msg_id}`\n"
                    f"参加者: {len(gw.get('participants', []))}人 / "
                    f"当選: {gw['winners']}人\n"
                    f"終了: {discord.utils.format_dt(end_dt, style='R')}"
                ),
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if 操作.value == "end":
        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))
        giveaways = cfg.get("giveaways", {})
        if not giveaways:
            await interaction.response.send_message("進行中のプレゼント企画がありません。", ephemeral=True)
            return
        # 最新のプレゼントを即時終了
        latest_id = int(list(giveaways.keys())[-1])
        task = _giveaway_tasks.pop(latest_id, None)
        if task and not task.done():
            task.cancel()
        gw = giveaways[str(latest_id)]
        ch = interaction.guild.get_channel(gw.get("channel_id", interaction.channel.id))
        if ch:
            gw["end_at"] = discord.utils.utcnow().isoformat()
            save_data(all_data)
            asyncio.create_task(_run_giveaway(ch, latest_id, interaction.guild.id, discord.utils.utcnow()))
        await interaction.response.send_message("プレゼント企画を即時終了して抽選を実行します。", ephemeral=True)
        return

    # 操作 == "start"
    if not 景品 or not 時間_分:
        await interaction.response.send_message("「開始する」の場合は「景品」と「時間_分」を指定してください。", ephemeral=True)
        return
    if 時間_分 < 1 or 時間_分 > 43200:
        await interaction.response.send_message("時間は1分〜43200分（30日）で指定してください。", ephemeral=True)
        return
    if 当選人数 < 1 or 当選人数 > 20:
        await interaction.response.send_message("当選人数は1〜20人で指定してください。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    end_dt = discord.utils.utcnow() + datetime.timedelta(minutes=時間_分)
    embed = _build_giveaway_embed(景品, interaction.user, end_dt, 当選人数, 0)
    view = GiveawayJoinView()

    msg = await interaction.channel.send(embed=embed, view=view)

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg.setdefault("giveaways", {})[str(msg.id)] = {
        "prize": 景品,
        "winners": 当選人数,
        "end_at": end_dt.isoformat(),
        "channel_id": interaction.channel.id,
        "host_id": interaction.user.id,
        "participants": [],
    }
    save_data(all_data)

    task = asyncio.create_task(_run_giveaway(interaction.channel, msg.id, interaction.guild.id, end_dt))
    _giveaway_tasks[msg.id] = task

    await interaction.followup.send(
        f"[*] プレゼント企画を開始しました！\n景品: **{景品}** / {時間_分}分後に抽選 / 当選{当選人数}人",
        ephemeral=True
    )


# --------------------------------------------------------------------
# /alt_check — 新規アカウント検知設定
# --------------------------------------------------------------------

@bot.tree.command(name="alt_check", description="【管理者専用】新規アカウント（垢BAN逃れ）の自動検知を設定します")
@discord.app_commands.choices(操作=[
    discord.app_commands.Choice(name="有効にする", value="on"),
    discord.app_commands.Choice(name="無効にする", value="off"),
    discord.app_commands.Choice(name="現在の設定を確認", value="status"),
])
@discord.app_commands.choices(アクション=[
    discord.app_commands.Choice(name="通知のみ（ログチャンネルに報告）", value="notify"),
    discord.app_commands.Choice(name="キック（参加拒否）", value="kick"),
    discord.app_commands.Choice(name="BAN", value="ban"),
])
async def alt_check(
    interaction: discord.Interaction,
    操作: discord.app_commands.Choice[str],
    アクション: discord.app_commands.Choice[str] = None,
    閾値_日数: app_commands.Range[int, 1, 365] = None,
):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    if 操作.value == "status":
        embed = discord.Embed(title="alt_check 設定状況", color=discord.Color.blue())
        embed.add_field(name="状態", value="有効" if cfg.get("alt_check_enabled") else "無効", inline=True)
        embed.add_field(name="閾値", value=f"{cfg.get('alt_check_days', 30)}日未満", inline=True)
        action_label = {"notify": "通知のみ", "kick": "キック", "ban": "BAN"}.get(cfg.get("alt_check_action", "notify"), "不明")
        embed.add_field(name="アクション", value=action_label, inline=True)
        embed.set_footer(text="参加メンバーのアカウント作成日が閾値より新しい場合に反応します")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if 操作.value == "off":
        cfg["alt_check_enabled"] = False
        save_data(all_data)
        await interaction.response.send_message("alt_check を無効にしました。", ephemeral=True)
        return

    # on
    cfg["alt_check_enabled"] = True
    if アクション:
        cfg["alt_check_action"] = アクション.value
    if 閾値_日数:
        cfg["alt_check_days"] = 閾値_日数
    save_data(all_data)

    action_label = {"notify": "通知のみ", "kick": "キック", "ban": "BAN"}.get(cfg["alt_check_action"], "通知のみ")
    await interaction.response.send_message(
        f"alt_check を有効にしました。\n"
        f"・閾値: アカウント作成から **{cfg['alt_check_days']}日未満** で反応\n"
        f"・アクション: **{action_label}**\n"
        "ログチャンネルを設定していない場合は通知が届きません（`/modlog_set` で設定してください）。",
        ephemeral=True
    )


# --------------------------------------------------------------------
# /iplogger_check — IPロガー自動検知設定
# --------------------------------------------------------------------

@bot.tree.command(name="iplogger_check", description="【管理者専用】IPロガー・フィッシングリンクの自動検知・削除を設定します")
@discord.app_commands.choices(状態=[
    discord.app_commands.Choice(name="有効にする", value="on"),
    discord.app_commands.Choice(name="無効にする", value="off"),
])
async def iplogger_check(interaction: discord.Interaction, 状態: discord.app_commands.Choice[str]):
    if not await is_guild_admin(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["iplogger_check_enabled"] = (状態.value == "on")
    save_data(all_data)

    if 状態.value == "on":
        await interaction.response.send_message(
            "IPロガー検知を **有効** にしました。\n"
            "grabify.link / iplogger.org などの既知ドメインと、\n"
            "bit.ly 等の短縮URLの展開先も自動チェックします。\n"
            "検知した場合はメッセージを即削除し、モデレーションログに記録します。",
            ephemeral=True
        )
    else:
        await interaction.response.send_message("IPロガー検知を **無効** にしました。", ephemeral=True)


# --------------------------------------------------------------------
# /embed_builder — GUIでEmbedを作成して送信
# --------------------------------------------------------------------

class EmbedBuilderModal(discord.ui.Modal, title="Embed内容を入力"):
    """Embedのタイトル・説明・フッターを入力するモーダル。"""
    embed_title = discord.ui.TextInput(
        label="タイトル",
        placeholder="例: お知らせ",
        max_length=256,
        required=False
    )
    embed_description = discord.ui.TextInput(
        label="本文",
        style=discord.TextStyle.paragraph,
        placeholder="Embedの本文を入力してください...",
        max_length=4000,
        required=False
    )
    embed_footer = discord.ui.TextInput(
        label="フッター",
        placeholder="例: ※詳細はお問い合わせください",
        max_length=2048,
        required=False
    )
    embed_image_url = discord.ui.TextInput(
        label="画像URL（省略可）",
        placeholder="https://example.com/image.png",
        required=False
    )
    embed_thumbnail_url = discord.ui.TextInput(
        label="サムネイルURL（省略可）",
        placeholder="https://example.com/thumb.png",
        required=False
    )

    def __init__(self, parent_view: "EmbedBuilderView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        pv = self.parent_view
        pv.embed_data["title"]         = self.embed_title.value or None
        pv.embed_data["description"]   = self.embed_description.value or None
        pv.embed_data["footer"]        = self.embed_footer.value or None
        pv.embed_data["image_url"]     = self.embed_image_url.value or None
        pv.embed_data["thumbnail_url"] = self.embed_thumbnail_url.value or None
        await interaction.response.edit_message(embed=pv.build_preview(), view=pv)


EMBED_COLOR_OPTIONS = {
    "ブルー":   discord.Color.blue(),
    "グリーン": discord.Color.green(),
    "レッド":   discord.Color.red(),
    "ゴールド": discord.Color.gold(),
    "パープル": discord.Color.purple(),
    "オレンジ": discord.Color.orange(),
    "グレー":   discord.Color.greyple(),
    "ティール": discord.Color.teal(),
    "白":       discord.Color.from_rgb(255, 255, 255),
    "黒":       discord.Color.from_rgb(30, 30, 30),
}


class EmbedColorSelect(discord.ui.Select):
    def __init__(self, parent_view: "EmbedBuilderView"):
        self.parent_view = parent_view
        options = [discord.SelectOption(label=name, value=name) for name in EMBED_COLOR_OPTIONS]
        super().__init__(placeholder="枠線の色を選択...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.embed_data["color"] = self.values[0]
        await interaction.response.edit_message(embed=self.parent_view.build_preview(), view=self.parent_view)


class EmbedBuilderView(discord.ui.View):
    """Embed作成GUIビュー。"""

    def __init__(self, author: discord.abc.User, target_channel: discord.TextChannel):
        super().__init__(timeout=600)
        self.author = author
        self.target_channel = target_channel
        self.embed_data: dict = {
            "title": None,
            "description": None,
            "footer": None,
            "color": "ブルー",
            "image_url": None,
            "thumbnail_url": None,
            "fields": [],   # [{"name": str, "value": str, "inline": bool}]
        }
        self.add_item(EmbedColorSelect(self))

    def build_preview(self) -> discord.Embed:
        color = EMBED_COLOR_OPTIONS.get(self.embed_data.get("color", "ブルー"), discord.Color.blue())
        embed = discord.Embed(
            title=self.embed_data.get("title") or "（タイトル未入力）",
            description=self.embed_data.get("description") or "（本文未入力）",
            color=color
        )
        for f in self.embed_data.get("fields", []):
            embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
        if self.embed_data.get("footer"):
            embed.set_footer(text=self.embed_data["footer"])
        if self.embed_data.get("image_url"):
            embed.set_image(url=self.embed_data["image_url"])
        if self.embed_data.get("thumbnail_url"):
            embed.set_thumbnail(url=self.embed_data["thumbnail_url"])
        embed.timestamp = discord.utils.utcnow()
        return embed

    @discord.ui.button(label="[NOTE] 内容を編集", style=discord.ButtonStyle.primary, row=0)
    async def edit_content(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このパネルはあなた専用です。", ephemeral=True)
            return
        modal = EmbedBuilderModal(self)
        # 現在の値をプリフィル
        if self.embed_data.get("title"):
            modal.embed_title.default = self.embed_data["title"]
        if self.embed_data.get("description"):
            modal.embed_description.default = self.embed_data["description"]
        if self.embed_data.get("footer"):
            modal.embed_footer.default = self.embed_data["footer"]
        if self.embed_data.get("image_url"):
            modal.embed_image_url.default = self.embed_data["image_url"]
        if self.embed_data.get("thumbnail_url"):
            modal.embed_thumbnail_url.default = self.embed_data["thumbnail_url"]
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="➕ フィールド追加", style=discord.ButtonStyle.secondary, row=0)
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このパネルはあなた専用です。", ephemeral=True)
            return
        if len(self.embed_data["fields"]) >= 25:
            await interaction.response.send_message("フィールドは最大25個までです。", ephemeral=True)
            return
        await interaction.response.send_modal(EmbedFieldModal(self))

    @discord.ui.button(label="[DEL]️ フィールド削除", style=discord.ButtonStyle.secondary, row=0)
    async def remove_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このパネルはあなた専用です。", ephemeral=True)
            return
        if not self.embed_data["fields"]:
            await interaction.response.send_message("削除できるフィールドがありません。", ephemeral=True)
            return
        # 末尾のフィールドを削除
        removed = self.embed_data["fields"].pop()
        await interaction.response.edit_message(embed=self.build_preview(), view=self)
        await interaction.followup.send(f"フィールド「{removed['name']}」を削除しました。", ephemeral=True)

    @discord.ui.button(label="[OK] このチャンネルに送信", style=discord.ButtonStyle.success, row=2)
    async def send_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このパネルはあなた専用です。", ephemeral=True)
            return
        embed = self.build_preview()
        try:
            await self.target_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("送信権限がありません。", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"送信エラー: {e}", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"[OK] {self.target_channel.mention} にEmbedを送信しました。",
            embed=None,
            view=self
        )

    @discord.ui.button(label="[NG] キャンセル", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("このパネルはあなた専用です。", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Embed作成をキャンセルしました。", embed=None, view=self)


class EmbedFieldModal(discord.ui.Modal, title="フィールドを追加"):
    field_name = discord.ui.TextInput(
        label="フィールド名",
        placeholder="例: 注意事項",
        max_length=256,
        required=True
    )
    field_value = discord.ui.TextInput(
        label="フィールドの内容",
        style=discord.TextStyle.paragraph,
        placeholder="フィールドの内容を入力...",
        max_length=1024,
        required=True
    )
    field_inline = discord.ui.TextInput(
        label="横並び表示（yes / no）",
        placeholder="yes または no",
        default="no",
        max_length=3,
        required=False
    )

    def __init__(self, parent_view: "EmbedBuilderView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        inline = self.field_inline.value.strip().lower() in ("yes", "y", "true", "1")
        self.parent_view.embed_data["fields"].append({
            "name": self.field_name.value,
            "value": self.field_value.value,
            "inline": inline,
        })
        await interaction.response.edit_message(embed=self.parent_view.build_preview(), view=self.parent_view)


@bot.tree.command(name="embed_builder", description="【管理者専用】GUIでEmbedメッセージを作成してチャンネルに送信します")
async def embed_builder(
    interaction: discord.Interaction,
    送信先チャンネル: discord.TextChannel = None,
):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    target_ch = 送信先チャンネル or interaction.channel
    view = EmbedBuilderView(author=interaction.user, target_channel=target_ch)
    await interaction.response.send_message(
        f"Embedビルダーを起動しました。送信先: {target_ch.mention}\n"
        "「[NOTE] 内容を編集」でタイトル・本文・画像URLを入力し、色を選んで「[OK] 送信」を押してください。",
        embed=view.build_preview(),
        view=view,
        ephemeral=True
    )


# ====================================================================
# セクション 14: 経済システム（通貨・ロールショップ・自販機）
# ====================================================================
#
# 概要:
#   ・メッセージ送信ごとにクールダウン付きで少額の通貨を自動付与（on_message側で処理）
#   ・通貨はサーバーごとに独立して管理（guild_config["economy_balances"]）
#   ・ロールショップ: 通貨を消費してロールを購入できる
#   ・自販機: 通貨を消費して「アイテム（テキスト内容）」を購入できる。在庫管理あり
#
# 注意:
#   ・通貨の稼ぎ方は「メッセージ送信」のみで、クールダウン（既定60秒）を必ず挟みます。
#     これにより連投・自動送信スクリプトによる無制限な稼ぎを防止します。
#   ・管理者は /economy_give で手動付与・没収ができますが、乱用防止のため
#     実行ログ（コマンド実行者）が Embed のフッターに残るようにしています。
# --------------------------------------------------------------------

def _format_currency(guild_config: dict, amount: int) -> str:
    """通貨名付きの金額表示文字列を作成します。"""
    name = guild_config.get("economy_currency_name", "コイン")
    return f"{amount:,} {name}"


# --------------------------------------------------------------------
# /economy_setup — 経済システムの有効化・各種パラメータ設定
# --------------------------------------------------------------------

@bot.tree.command(name="economy_setup", description="【管理者専用】通貨システム（メッセージ報酬・workコマンド）を設定します")
@discord.app_commands.describe(
    有効化="経済システムを有効化するか",
    通貨名="表示する通貨の名前（例: コイン、ポイント）",
    最小報酬="1メッセージあたりの最小付与額",
    最大報酬="1メッセージあたりの最大付与額",
    クールダウン秒="次のメッセージ報酬が発生するまでの待機時間（秒）。連投対策のため必須です。",
    work最小報酬="/workコマンド1回あたりの最小付与額",
    work最大報酬="/workコマンド1回あたりの最大付与額",
    workクールダウン秒="/workコマンドを再実行できるまでの待機時間（秒）。既定は7200秒（2時間）です。"
)
async def economy_setup(
    interaction: discord.Interaction,
    有効化: bool = None,
    通貨名: str = None,
    最小報酬: int = None,
    最大報酬: int = None,
    クールダウン秒: int = None,
    work最小報酬: int = None,
    work最大報酬: int = None,
    workクールダウン秒: int = None,
):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    if 有効化 is not None:
        cfg["economy_enabled"] = 有効化
    if 通貨名:
        cfg["economy_currency_name"] = 通貨名[:20]
    if 最小報酬 is not None:
        if 最小報酬 < 0:
            await interaction.response.send_message("最小報酬は0以上で指定してください。", ephemeral=True)
            return
        cfg["economy_reward_min"] = 最小報酬
    if 最大報酬 is not None:
        if 最大報酬 < 0:
            await interaction.response.send_message("最大報酬は0以上で指定してください。", ephemeral=True)
            return
        cfg["economy_reward_max"] = 最大報酬
    if クールダウン秒 is not None:
        if クールダウン秒 < 10:
            await interaction.response.send_message(
                "クールダウンは10秒以上で指定してください（連投による無制限な稼ぎを防ぐための最低制限です）。",
                ephemeral=True
            )
            return
        cfg["economy_cooldown_seconds"] = クールダウン秒
    if work最小報酬 is not None:
        if work最小報酬 < 0:
            await interaction.response.send_message("work最小報酬は0以上で指定してください。", ephemeral=True)
            return
        cfg["economy_work_reward_min"] = work最小報酬
    if work最大報酬 is not None:
        if work最大報酬 < 0:
            await interaction.response.send_message("work最大報酬は0以上で指定してください。", ephemeral=True)
            return
        cfg["economy_work_reward_max"] = work最大報酬
    if workクールダウン秒 is not None:
        if workクールダウン秒 < 60:
            await interaction.response.send_message(
                "workクールダウンは60秒以上で指定してください。", ephemeral=True
            )
            return
        cfg["economy_work_cooldown_seconds"] = workクールダウン秒

    save_data(all_data)

    embed = discord.Embed(title="経済システム設定", color=discord.Color.green())
    embed.add_field(name="有効化", value="[OK] 有効" if cfg.get("economy_enabled") else "[NG] 無効", inline=True)
    embed.add_field(name="通貨名", value=cfg.get("economy_currency_name", "コイン"), inline=True)
    embed.add_field(
        name="報酬額（1メッセージ）",
        value=f"{cfg.get('economy_reward_min', 1)} 〜 {cfg.get('economy_reward_max', 5)}",
        inline=True
    )
    embed.add_field(name="クールダウン（メッセージ）", value=f"{cfg.get('economy_cooldown_seconds', 60)}秒", inline=True)
    embed.add_field(
        name="報酬額（/work）",
        value=f"{cfg.get('economy_work_reward_min', 10)} 〜 {cfg.get('economy_work_reward_max', 50)}",
        inline=True
    )
    work_cd = cfg.get('economy_work_cooldown_seconds', 7200)
    embed.add_field(name="クールダウン（/work）", value=f"{work_cd}秒（{work_cd // 3600}時間）", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /balance — 所持金確認
# --------------------------------------------------------------------

@bot.tree.command(name="balance", description="自分または指定したユーザーの所持金を確認します")
async def balance(interaction: discord.Interaction, ユーザー: discord.Member = None):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    target = ユーザー or interaction.user
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    amount = get_balance(cfg, target.id)

    embed = discord.Embed(
        title="[COIN] 所持金",
        description=f"{target.mention} の所持金: **{_format_currency(cfg, amount)}**",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=(ユーザー is None))


# --------------------------------------------------------------------
# /work — 2時間ごとにコインを稼げる労働コマンド
# --------------------------------------------------------------------

@bot.tree.command(name="work", description="働いてコインを稼ぎます（2時間ごとに1回）")
async def work(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    if not cfg.get("economy_enabled", False):
        await interaction.response.send_message(
            "このサーバーでは経済システムが有効になっていません。管理者に /economy_setup での有効化を依頼してください。",
            ephemeral=True
        )
        return

    import random

    user_id_str = str(interaction.user.id)
    now_ts = time.time()

    cooldown = cfg.get("economy_work_cooldown_seconds", 7200)
    last_work_map = cfg.setdefault("economy_last_work", {})
    last_ts = last_work_map.get(user_id_str, 0)
    remaining = cooldown - (now_ts - last_ts)

    if remaining > 0:
        hours, rem = divmod(int(remaining), 3600)
        minutes, seconds = divmod(rem, 60)
        await interaction.response.send_message(
            f"まだ働けません。次に働けるまで: {hours}時間{minutes}分{seconds}秒",
            ephemeral=True
        )
        return

    reward_min = cfg.get("economy_work_reward_min", 10)
    reward_max = cfg.get("economy_work_reward_max", 50)
    if reward_max < reward_min:
        reward_max = reward_min
    reward = random.randint(reward_min, reward_max)

    add_balance(cfg, interaction.user.id, reward)
    last_work_map[user_id_str] = now_ts
    save_data(all_data)

    embed = discord.Embed(
        title="[WORK] 労働完了",
        description=(
            f"働いて **{_format_currency(cfg, reward)}** を獲得しました！\n"
            f"現在の所持金: **{_format_currency(cfg, get_balance(cfg, interaction.user.id))}**"
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text="次に働けるのは2時間後です（サーバー設定により変動する場合があります）")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /economy_give — 管理者による手動付与・没収
# --------------------------------------------------------------------

@bot.tree.command(name="economy_give", description="【管理者専用】指定ユーザーの所持金を増減させます")
@discord.app_commands.describe(
    ユーザー="対象ユーザー",
    金額="増減させる金額（負の数を指定すると没収）"
)
async def economy_give(interaction: discord.Interaction, ユーザー: discord.Member, 金額: int):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    add_balance(cfg, ユーザー.id, 金額)
    save_data(all_data)

    new_balance = get_balance(cfg, ユーザー.id)
    embed = discord.Embed(
        title="[COIN] 所持金を変更しました",
        description=(
            f"対象: {ユーザー.mention}\n"
            f"変更額: {'+' if 金額 >= 0 else ''}{金額}\n"
            f"現在の所持金: **{_format_currency(cfg, new_balance)}**"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --------------------------------------------------------------------
# /gift — 他のユーザーへコインをギフト
# --------------------------------------------------------------------

@bot.tree.command(name="gift", description="自分のコインを他のユーザーに贈ります")
@discord.app_commands.describe(
    ユーザー="贈り先のユーザー",
    金額="贈る金額（1以上）",
    メッセージ="一言メッセージ（省略可）"
)
async def gift(
    interaction: discord.Interaction,
    ユーザー: discord.Member,
    金額: app_commands.Range[int, 1, 999999999],
    メッセージ: str = None,
):
    if not interaction.guild:
        await interaction.response.send_message("このコマンドはサーバー内で実行してください。", ephemeral=True)
        return

    # 自分自身へのギフトは禁止
    if ユーザー.id == interaction.user.id:
        await interaction.response.send_message("自分自身にギフトはできません。", ephemeral=True)
        return

    # Bot へのギフトは禁止
    if ユーザー.bot:
        await interaction.response.send_message("Botにギフトはできません。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    if not cfg.get("economy_enabled", False):
        await interaction.response.send_message(
            "このサーバーでは経済システムが有効になっていません。",
            ephemeral=True
        )
        return

    sender_balance = get_balance(cfg, interaction.user.id)
    if sender_balance < 金額:
        await interaction.response.send_message(
            f"所持金が不足しています。\n"
            f"必要: {_format_currency(cfg, 金額)} / 所持: {_format_currency(cfg, sender_balance)}",
            ephemeral=True
        )
        return

    # 送信者から引いて受取人へ加算
    add_balance(cfg, interaction.user.id, -金額)
    add_balance(cfg, ユーザー.id, 金額)
    save_data(all_data)

    # ギフト受取通知を受取人にDMで送る（失敗しても握り潰す）
    try:
        dm_embed = discord.Embed(
            title="[GIFT] コインを受け取りました！",
            description=(
                f"**{interaction.guild.name}** で {interaction.user.mention} から "
                f"**{_format_currency(cfg, 金額)}** 受け取りました！"
            ),
            color=discord.Color.gold()
        )
        if メッセージ:
            dm_embed.add_field(name="メッセージ", value=メッセージ, inline=False)
        dm_embed.add_field(
            name="現在の所持金",
            value=_format_currency(cfg, get_balance(cfg, ユーザー.id)),
            inline=True
        )
        dm_embed.set_footer(text=interaction.guild.name)
        await ユーザー.send(embed=dm_embed)
    except Exception:
        pass

    # 実行チャンネルに公開Embedで通知
    result_embed = discord.Embed(
        title="[GIFT] ギフト完了！",
        description=(
            f"{interaction.user.mention} → {ユーザー.mention}\n"
            f"**{_format_currency(cfg, 金額)}** を贈りました！"
        ),
        color=discord.Color.gold()
    )
    if メッセージ:
        result_embed.add_field(name="メッセージ", value=メッセージ, inline=False)
    result_embed.add_field(
        name="あなたの残り所持金",
        value=_format_currency(cfg, get_balance(cfg, interaction.user.id)),
        inline=True
    )
    result_embed.set_footer(text=f"送信者: {interaction.user}")
    await interaction.response.send_message(embed=result_embed)


# --------------------------------------------------------------------
# ロールショップ
# --------------------------------------------------------------------

class RoleShopBuyButton(discord.ui.Button):
    """ロールショップの各商品に対応する購入ボタン。"""

    def __init__(self, item: dict):
        self.item_id = item["id"]
        self.role_id = item["role_id"]
        self.price = item["price"]
        label = f"{item['name']} ({item['price']:,})"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success,
            custom_id=f"roleshop_buy_{item['id']}"
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return

        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))

        shop_items = cfg.get("role_shop", [])
        item = next((i for i in shop_items if i["id"] == self.item_id), None)
        if item is None:
            await interaction.response.send_message("この商品は既に削除されています。", ephemeral=True)
            return

        role = interaction.guild.get_role(item["role_id"])
        if role is None:
            await interaction.response.send_message(
                "対応するロールがサーバー上に見つかりません。管理者に確認してください。", ephemeral=True
            )
            return

        owned = cfg.setdefault("owned_shop_roles", {})
        user_owned = owned.setdefault(str(interaction.user.id), [])
        if item["id"] in user_owned:
            await interaction.response.send_message("このロールは既に購入済みです。", ephemeral=True)
            return

        user_balance = get_balance(cfg, interaction.user.id)
        if user_balance < item["price"]:
            await interaction.response.send_message(
                f"所持金が不足しています。必要: {_format_currency(cfg, item['price'])} / "
                f"所持: {_format_currency(cfg, user_balance)}",
                ephemeral=True
            )
            return

        try:
            await interaction.user.add_roles(role, reason="ロールショップ購入")
        except discord.Forbidden:
            await interaction.response.send_message(
                "ロールを付与する権限がBotにありません（ロール順位を確認してください）。", ephemeral=True
            )
            return

        add_balance(cfg, interaction.user.id, -item["price"])
        user_owned.append(item["id"])
        save_data(all_data)

        await interaction.response.send_message(
            f"[OK] **{role.name}** を購入しました！ 残り所持金: {_format_currency(cfg, get_balance(cfg, interaction.user.id))}",
            ephemeral=True
        )


class RoleShopView(discord.ui.View):
    """ロールショップの商品一覧パネル（購入ボタン付き）。"""

    def __init__(self, shop_items: list):
        super().__init__(timeout=None)
        # Discordの制約上、1ビューに置けるボタンは最大25個
        for item in shop_items[:25]:
            self.add_item(RoleShopBuyButton(item))


def _build_role_shop_embed(guild_config: dict, guild: discord.Guild) -> discord.Embed:
    shop_items = guild_config.get("role_shop", [])
    embed = discord.Embed(
        title="[SHOP] ロールショップ",
        description="ボタンを押すとロールを購入できます。" if shop_items else "現在、販売中のロールはありません。",
        color=discord.Color.purple()
    )
    for item in shop_items:
        role = guild.get_role(item["role_id"])
        role_text = role.mention if role else "（ロール削除済み）"
        embed.add_field(
            name=f"{item['name']}（ID: {item['id']}）",
            value=f"{role_text}\n価格: {item['price']:,}",
            inline=True
        )
    return embed


async def _refresh_role_shop_panel(guild: discord.Guild, guild_config: dict):
    """
    設置済みのロールショップパネル（role_shop_panel_channel_id / role_shop_panel_message_id）が
    存在する場合、最新の商品情報でメッセージを編集して更新します。
    パネルが見つからない場合（手動削除等）は静かに諦めます（エラーにしません）。
    """
    channel_id = guild_config.get("role_shop_panel_channel_id")
    message_id = guild_config.get("role_shop_panel_message_id")
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    shop_items = guild_config.get("role_shop", [])
    new_embed = _build_role_shop_embed(guild_config, guild)
    new_view = RoleShopView(shop_items) if shop_items else None

    try:
        await message.edit(embed=new_embed, view=new_view)
    except discord.HTTPException:
        pass


@bot.tree.command(name="roleshop_add", description="【管理者専用】ロールショップに商品を追加します")
@discord.app_commands.describe(ロール="販売するロール", 価格="購入に必要な通貨額", 表示名="ショップに表示する商品名")
async def roleshop_add(interaction: discord.Interaction, ロール: discord.Role, 価格: int, 表示名: str = None):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return
    if 価格 < 1:
        await interaction.response.send_message("価格は1以上で指定してください。", ephemeral=True)
        return
    if ロール >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "指定されたロールはBotの最高ロールより上位にあるため付与できません。ロールの順位を確認してください。",
            ephemeral=True
        )
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    new_id = issue_economy_id(cfg)
    cfg.setdefault("role_shop", []).append({
        "id": new_id,
        "role_id": ロール.id,
        "name": 表示名 or ロール.name,
        "price": 価格,
    })
    save_data(all_data)

    await _refresh_role_shop_panel(interaction.guild, cfg)

    await interaction.response.send_message(
        f"[OK] ロールショップに **{表示名 or ロール.name}**（{ロール.mention} / 価格: {価格:,}）を追加しました。"
        f"（商品ID: {new_id}）\n"
        f"設置済みパネルがある場合は自動的に更新されました。",
        ephemeral=True
    )


@bot.tree.command(name="roleshop_remove", description="【管理者専用】ロールショップから商品を削除します")
@discord.app_commands.describe(商品id="削除する商品のID（/roleshop で確認できます）")
async def roleshop_remove(interaction: discord.Interaction, 商品id: int):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    shop_items = cfg.get("role_shop", [])
    before_len = len(shop_items)
    cfg["role_shop"] = [i for i in shop_items if i["id"] != 商品id]
    save_data(all_data)

    if len(cfg["role_shop"]) == before_len:
        await interaction.response.send_message("指定されたIDの商品が見つかりませんでした。", ephemeral=True)
    else:
        await _refresh_role_shop_panel(interaction.guild, cfg)
        await interaction.response.send_message(
            f"[OK] 商品ID {商品id} をロールショップから削除しました。設置済みパネルがある場合は自動的に更新されました。",
            ephemeral=True
        )


@bot.tree.command(name="roleshop", description="【管理者専用】このチャンネルにロールショップパネルを設置します")
async def roleshop(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    # 既存の設置済みパネルがあれば、ボタンを無効化したメッセージとして残す
    old_channel_id = cfg.get("role_shop_panel_channel_id")
    old_message_id = cfg.get("role_shop_panel_message_id")
    if old_channel_id and old_message_id:
        old_channel = interaction.guild.get_channel(old_channel_id)
        if old_channel:
            try:
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.edit(
                    content="[!] このロールショップパネルは新しいパネルに置き換えられたため無効です。",
                    embed=None,
                    view=None
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    shop_items = cfg.get("role_shop", [])
    embed = _build_role_shop_embed(cfg, interaction.guild)
    view = RoleShopView(shop_items) if shop_items else None

    await interaction.response.send_message(embed=embed, view=view)
    panel_message = await interaction.original_response()

    cfg["role_shop_panel_channel_id"] = interaction.channel.id
    cfg["role_shop_panel_message_id"] = panel_message.id
    save_data(all_data)

# --------------------------------------------------------------------
# 自販機
# --------------------------------------------------------------------

class VendingBuyButton(discord.ui.Button):
    """自販機の各アイテムに対応する購入ボタン。"""

    def __init__(self, item: dict):
        self.item_id = item["id"]
        stock = item.get("stock", 0)
        disabled = stock <= 0
        label = f"{item['name']} ({item['price']:,}) 残{stock}"
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary if not disabled else discord.ButtonStyle.secondary,
            custom_id=f"vending_buy_{item['id']}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return

        all_data = load_data()
        cfg = get_guild_config(all_data, str(interaction.guild.id))

        items = cfg.get("vending_items", [])
        item = next((i for i in items if i["id"] == self.item_id), None)
        if item is None:
            await interaction.response.send_message("この商品は既に削除されています。", ephemeral=True)
            return

        if item.get("stock", 0) <= 0:
            await interaction.response.send_message("この商品は売り切れです。", ephemeral=True)
            return

        user_balance = get_balance(cfg, interaction.user.id)
        if user_balance < item["price"]:
            await interaction.response.send_message(
                f"所持金が不足しています。必要: {_format_currency(cfg, item['price'])} / "
                f"所持: {_format_currency(cfg, user_balance)}",
                ephemeral=True
            )
            return

        item_type = item.get("type", "text")
        content_text = None
        discord_file = None

        if item_type == "file":
            # ファイル商品の場合、先に実体を取得できるか確認してから決済する
            # （取得失敗時に課金してしまうのを防ぐため）
            discord_file = await _fetch_vending_file(interaction.guild, item)
            if discord_file is None:
                await interaction.response.send_message(
                    "[NG] 商品ファイルの取得に失敗しました。管理者に確認してください（決済は行われていません）。",
                    ephemeral=True
                )
                return
        else:
            content_text = item.get("content", "（内容が設定されていません）")

        # 在庫減算・所持金減算
        item["stock"] -= 1
        add_balance(cfg, interaction.user.id, -item["price"])
        save_data(all_data)

        # 購入内容はDMで送付（失敗時はephemeralメッセージにフォールバック）
        try:
            if item_type == "file":
                await interaction.user.send(
                    f"[VEND] 自販機で **{item['name']}** を購入しました。ファイルを添付します。",
                    file=discord_file
                )
                await interaction.response.send_message(
                    f"[OK] **{item['name']}** を購入しました。ファイルをDMに送信しました。", ephemeral=True
                )
            else:
                await interaction.user.send(
                    f"[VEND] 自販機で **{item['name']}** を購入しました。\n\n内容:\n{content_text}"
                )
                await interaction.response.send_message(
                    f"[OK] **{item['name']}** を購入しました。内容をDMに送信しました。", ephemeral=True
                )
        except discord.Forbidden:
            if item_type == "file":
                # discord.File は一度送信すると再利用できないため、フォールバック用に取り直す
                fallback_file = await _fetch_vending_file(interaction.guild, item)
                await interaction.response.send_message(
                    f"[OK] **{item['name']}** を購入しました。\n"
                    f"（DMが送れなかったため、こちらにファイルを添付します）",
                    file=fallback_file,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"[OK] **{item['name']}** を購入しました。\n"
                    f"（DMが送れなかったため、こちらに表示します）\n内容:\n{content_text}",
                    ephemeral=True
                )

        # パネルの在庫表示を更新
        try:
            new_view = VendingMachineView(cfg.get("vending_items", []))
            new_embed = _build_vending_embed(cfg)
            await interaction.message.edit(embed=new_embed, view=new_view)
        except Exception:
            pass


class VendingMachineView(discord.ui.View):
    """自販機の商品一覧パネル（購入ボタン付き）。"""

    def __init__(self, items: list):
        super().__init__(timeout=None)
        for item in items[:25]:
            self.add_item(VendingBuyButton(item))


def _build_vending_embed(guild_config: dict) -> discord.Embed:
    items = guild_config.get("vending_items", [])
    embed = discord.Embed(
        title="[VEND] 自販機",
        description="ボタンを押すと商品を購入できます。内容（テキスト／ファイル）はDMで送付されます。" if items else "現在、販売中の商品はありません。",
        color=discord.Color.teal()
    )
    for item in items:
        stock = item.get("stock", 0)
        type_label = "ファイル" if item.get("type") == "file" else "テキスト"
        embed.add_field(
            name=f"{item['name']}（ID: {item['id']}）",
            value=f"価格: {item['price']:,}\n在庫: {stock if stock > 0 else '売り切れ'}\n種別: {type_label}",
            inline=True
        )
    return embed


async def _refresh_vending_panel(guild: discord.Guild, guild_config: dict):
    """
    設置済みの自販機パネル（vending_panel_channel_id / vending_panel_message_id）が
    存在する場合、最新の商品・在庫情報でメッセージを編集して更新します。
    パネルが見つからない場合（手動削除等）は静かに諦めます（エラーにしません）。
    """
    channel_id = guild_config.get("vending_panel_channel_id")
    message_id = guild_config.get("vending_panel_message_id")
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    items = guild_config.get("vending_items", [])
    new_embed = _build_vending_embed(guild_config)
    new_view = VendingMachineView(items) if items else None

    try:
        await message.edit(embed=new_embed, view=new_view)
    except discord.HTTPException:
        pass


async def _ensure_vending_storage_channel(guild: discord.Guild, guild_config: dict):
    """
    自販機のファイル商品を保管するための、Bot専用（@everyone非表示）チャンネルを
    取得します。存在しない場合は新規に作成します。
    作成・取得に失敗した場合は None を返します。
    """
    channel_id = guild_config.get("vending_storage_channel_id")
    if channel_id:
        existing = guild.get_channel(channel_id)
        if existing is not None:
            return existing

    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, read_message_history=True
            ),
        }
        channel = await guild.create_text_channel(
            "vending-file-storage",
            overwrites=overwrites,
            reason="自販機のファイル商品保管用チャンネルを自動作成"
        )
    except (discord.Forbidden, discord.HTTPException):
        return None

    guild_config["vending_storage_channel_id"] = channel.id
    return channel


async def _store_vending_file(guild: discord.Guild, attachment: discord.Attachment, guild_config: dict):
    """
    自販機で配布するファイルを保管チャンネルへ再アップロードして永続化します。
    （スラッシュコマンドの添付ファイルURLは時間が経つと失効するため、保管チャンネルの
    メッセージ本体を正本として扱い、購入時には毎回そこから取得し直します。）
    保存に成功した場合は item に追加するフィールド（storage_channel_id / storage_message_id /
    file_name）の dict を、失敗した場合は None を返します。
    """
    channel = await _ensure_vending_storage_channel(guild, guild_config)
    if channel is None:
        return None

    try:
        file_bytes = await attachment.read()
        upload_file = discord.File(io.BytesIO(file_bytes), filename=attachment.filename)
        storage_message = await channel.send(
            content=f"[VEND-FILE] {attachment.filename}（自販機販売用の保管メッセージです。削除しないでください）",
            file=upload_file
        )
    except (discord.Forbidden, discord.HTTPException):
        return None

    return {
        "storage_channel_id": channel.id,
        "storage_message_id": storage_message.id,
        "file_name": attachment.filename[:200],
    }


async def _fetch_vending_file(guild: discord.Guild, item: dict):
    """
    保管チャンネルに保存された自販機のファイル商品の実体を取得し、discord.File として
    返します。URLを保存せずメッセージ本体から毎回取得するため、CDN URLの失効による
    配布失敗を避けられます。取得できなかった場合は None を返します。
    """
    channel_id = item.get("storage_channel_id")
    message_id = item.get("storage_message_id")
    if not channel_id or not message_id:
        return None

    channel = guild.get_channel(channel_id)
    if channel is None:
        return None

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

    if not message.attachments:
        return None

    attachment = message.attachments[0]
    try:
        file_bytes = await attachment.read()
    except (discord.NotFound, discord.HTTPException):
        return None

    filename = item.get("file_name") or attachment.filename
    return discord.File(io.BytesIO(file_bytes), filename=filename)


@bot.tree.command(name="vendingmachine_add", description="【管理者専用】自販機に商品を追加します（テキスト or ファイル）")
@discord.app_commands.describe(
    商品名="自販機に表示する商品名",
    価格="購入に必要な通貨額",
    在庫数="販売する個数",
    内容="購入時にDMで送るテキスト内容（シリアルコード等）。添付ファイルを使う場合は不要です",
    添付ファイル="購入時にDMで送るファイル（画像・PDF・ZIP等）。テキストの代わりにこちらを指定できます"
)
async def vendingmachine_add(
    interaction: discord.Interaction,
    商品名: str,
    価格: int,
    在庫数: int,
    内容: str = None,
    添付ファイル: discord.Attachment = None,
):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return
    if 価格 < 1:
        await interaction.response.send_message("価格は1以上で指定してください。", ephemeral=True)
        return
    if 在庫数 < 0:
        await interaction.response.send_message("在庫数は0以上で指定してください。", ephemeral=True)
        return
    if not 内容 and not 添付ファイル:
        await interaction.response.send_message(
            "「内容」（テキスト）または「添付ファイル」のどちらかを指定してください。", ephemeral=True
        )
        return
    if 内容 and 添付ファイル:
        await interaction.response.send_message(
            "「内容」と「添付ファイル」は同時に指定できません。どちらか一方にしてください。", ephemeral=True
        )
        return

    # ファイル保存に時間がかかる場合があるため、先に応答を保留する
    await interaction.response.defer(ephemeral=True)

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    new_id = issue_economy_id(cfg)

    new_item = {
        "id": new_id,
        "name": 商品名[:80],
        "price": 価格,
        "stock": 在庫数,
    }

    if 添付ファイル:
        stored = await _store_vending_file(interaction.guild, 添付ファイル, cfg)
        if stored is None:
            await interaction.followup.send(
                "[NG] ファイルの保存に失敗しました。Botに「チャンネルの管理」権限があるか確認してから再度お試しください。",
                ephemeral=True
            )
            return
        new_item["type"] = "file"
        new_item.update(stored)
        type_label = "ファイル"
    else:
        new_item["type"] = "text"
        new_item["content"] = 内容[:1500]
        type_label = "テキスト"

    cfg.setdefault("vending_items", []).append(new_item)
    save_data(all_data)

    await _refresh_vending_panel(interaction.guild, cfg)

    await interaction.followup.send(
        f"[OK] 自販機に **{商品名}**（価格: {価格:,} / 在庫: {在庫数} / 種別: {type_label}）を追加しました。"
        f"（商品ID: {new_id}）\n"
        f"設置済みパネルがある場合は自動的に更新されました。",
        ephemeral=True
    )


@bot.tree.command(name="vendingmachine_remove", description="【管理者専用】自販機から商品を削除します")
@discord.app_commands.describe(商品id="削除する商品のID（/vendingmachine で確認できます）")
async def vendingmachine_remove(interaction: discord.Interaction, 商品id: int):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    items = cfg.get("vending_items", [])
    before_len = len(items)
    removed_item = next((i for i in items if i["id"] == 商品id), None)
    cfg["vending_items"] = [i for i in items if i["id"] != 商品id]
    save_data(all_data)

    if len(cfg["vending_items"]) == before_len:
        await interaction.response.send_message("指定されたIDの商品が見つかりませんでした。", ephemeral=True)
    else:
        # ファイル商品の場合、保管チャンネルに残った実体メッセージも削除する（ベストエフォート）
        if removed_item and removed_item.get("type") == "file":
            storage_ch = interaction.guild.get_channel(removed_item.get("storage_channel_id"))
            if storage_ch:
                try:
                    storage_msg = await storage_ch.fetch_message(removed_item.get("storage_message_id"))
                    await storage_msg.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        await _refresh_vending_panel(interaction.guild, cfg)
        await interaction.response.send_message(
            f"[OK] 商品ID {商品id} を自販機から削除しました。設置済みパネルがある場合は自動的に更新されました。",
            ephemeral=True
        )


@bot.tree.command(name="vendingmachine", description="【管理者専用】このチャンネルに自販機パネルを設置します")
async def vendingmachine(interaction: discord.Interaction):
    if not await is_admin_or_allowed(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    # 既存の設置済みパネルがあれば、ボタンを無効化したメッセージとして残す
    old_channel_id = cfg.get("vending_panel_channel_id")
    old_message_id = cfg.get("vending_panel_message_id")
    if old_channel_id and old_message_id:
        old_channel = interaction.guild.get_channel(old_channel_id)
        if old_channel:
            try:
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.edit(
                    content="[!] この自販機パネルは新しいパネルに置き換えられたため無効です。",
                    embed=None,
                    view=None
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    items = cfg.get("vending_items", [])
    embed = _build_vending_embed(cfg)
    view = VendingMachineView(items) if items else None

    await interaction.response.send_message(embed=embed, view=view)
    panel_message = await interaction.original_response()

    cfg["vending_panel_channel_id"] = interaction.channel.id
    cfg["vending_panel_message_id"] = panel_message.id
    save_data(all_data)


# ====================================================================
# セクション X: サーバーブラックリスト コマンドグループ
# ====================================================================

server_blacklist_group = app_commands.Group(
    name="server_blacklist",
    description="特定サーバー参加者の自動BAN/KICK機能を管理します（モデレーター専用）"
)


@server_blacklist_group.command(name="toggle", description="サーバーブラックリスト機能のON/OFFを切り替えます")
async def sbl_toggle(interaction: discord.Interaction):
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return
    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))

    # OAuth2設定が不完全な場合は警告
    if not OAUTH_REDIRECT_URI or not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        await interaction.response.send_message(
            "[NG] 環境変数 `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` / `OAUTH_REDIRECT_URI` が設定されていないため、この機能を有効にできません。\n"
            "Railwayの環境変数ページでこれらを設定してください。",
            ephemeral=True
        )
        return

    new_state = not cfg.get("server_blacklist_enabled", False)
    cfg["server_blacklist_enabled"] = new_state
    save_data(all_data)

    state_text = "[ON] 有効" if new_state else "[OFF] 無効"
    await interaction.response.send_message(
        f"サーバーブラックリスト機能を **{state_text}** にしました。",
        ephemeral=True
    )


@server_blacklist_group.command(name="add", description="ブラックリストにDiscordサーバーIDを追加します")
@app_commands.describe(server_id="BANの対象とするDiscordサーバーID")
async def sbl_add(interaction: discord.Interaction, server_id: str):
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return

    try:
        sid = int(server_id)
    except ValueError:
        await interaction.response.send_message("[NG] サーバーIDは数値で入力してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    bl = cfg.setdefault("server_blacklist_ids", [])

    if sid in bl:
        await interaction.response.send_message(f"[NG] サーバーID `{sid}` はすでに登録されています。", ephemeral=True)
        return

    bl.append(sid)
    save_data(all_data)
    await interaction.response.send_message(
        f"[OK] サーバーID `{sid}` をブラックリストに追加しました。\n"
        f"このサーバーに参加しているユーザーが自サーバーに入ると認証URLが送信されます。",
        ephemeral=True
    )


@server_blacklist_group.command(name="remove", description="ブラックリストからDiscordサーバーIDを削除します")
@app_commands.describe(server_id="削除するDiscordサーバーID")
async def sbl_remove(interaction: discord.Interaction, server_id: str):
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return

    try:
        sid = int(server_id)
    except ValueError:
        await interaction.response.send_message("[NG] サーバーIDは数値で入力してください。", ephemeral=True)
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    bl = cfg.get("server_blacklist_ids", [])

    if sid not in bl:
        await interaction.response.send_message(f"[NG] サーバーID `{sid}` はリストに存在しません。", ephemeral=True)
        return

    bl.remove(sid)
    save_data(all_data)
    await interaction.response.send_message(f"[OK] サーバーID `{sid}` をブラックリストから削除しました。", ephemeral=True)


@server_blacklist_group.command(name="list", description="ブラックリストに登録されているサーバーID一覧を表示します")
async def sbl_list(interaction: discord.Interaction):
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    bl = cfg.get("server_blacklist_ids", [])
    enabled = cfg.get("server_blacklist_enabled", False)
    action = cfg.get("server_blacklist_action", "ban")

    embed = discord.Embed(
        title="サーバーブラックリスト 設定状況",
        color=discord.Color.red() if enabled else discord.Color.greyple()
    )
    embed.add_field(name="機能状態", value="[ON] 有効" if enabled else "[OFF] 無効", inline=True)
    embed.add_field(name="処置", value="BAN" if action == "ban" else "キック", inline=True)
    embed.add_field(name="OAuth2設定", value="[OK]" if OAUTH_REDIRECT_URI else "[NG] 未設定", inline=True)

    if bl:
        lines = [f"`{sid}`" for sid in bl]
        embed.add_field(name=f"登録サーバーID ({len(bl)}件)", value="\n".join(lines[:20]), inline=False)
    else:
        embed.add_field(name="登録サーバーID", value="なし", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@server_blacklist_group.command(name="action", description="ブラックリスト対象者への処置を設定します")
@app_commands.describe(action="ban: BAN / kick: キック")
@app_commands.choices(action=[
    app_commands.Choice(name="BAN（永久追放）", value="ban"),
    app_commands.Choice(name="キック（退出のみ）", value="kick"),
])
async def sbl_action(interaction: discord.Interaction, action: str):
    if not await is_moderator(interaction):
        return
    if not interaction.guild:
        return

    all_data = load_data()
    cfg = get_guild_config(all_data, str(interaction.guild.id))
    cfg["server_blacklist_action"] = action
    save_data(all_data)

    label = "BAN（永久追放）" if action == "ban" else "キック（退出のみ）"
    await interaction.response.send_message(f"[OK] ブラックリスト対象者への処置を **{label}** に設定しました。", ephemeral=True)


bot.tree.add_command(server_blacklist_group)


# ====================================================================
# OAuth2 コールバック Webサーバー（aiohttp）
# ====================================================================

async def _oauth2_callback_handler(request):
    """
    Discord OAuth2 コールバックエンドポイント。
    ユーザーが認証を完了したときにここへリダイレクトされる。
    stateを検証し、ユーザーの参加サーバー一覧を取得してBL照合を行う。
    """
    import hmac
    import hashlib

    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")

    if not code or not state:
        return aiohttp.web.Response(
            text="<html><body><h2>[NG] 無効なリクエストです。</h2></body></html>",
            content_type="text/html"
        )

    # --- state の検証（HMAC署名確認） ---
    try:
        decoded = base64.urlsafe_b64decode(state.encode()).decode()
        parts = decoded.split(":")
        if len(parts) != 3:
            raise ValueError("state フォーマット不正")
        guild_id_str, user_id_str, sig = parts
        guild_id = int(guild_id_str)
        user_id = int(user_id_str)
        expected_sig = hmac.new(
            OAUTH_SECRET_KEY.encode(),
            f"{guild_id_str}:{user_id_str}".encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("署名不一致")
    except Exception as e:
        print(f"[OAuth2] state検証エラー: {e}")
        return aiohttp.web.Response(
            text="<html><body><h2>[NG] 認証トークンが無効です。もう一度やり直してください。</h2></body></html>",
            content_type="text/html"
        )

    # --- Discord API に code を送りアクセストークンを取得 ---
    async with aiohttp.ClientSession() as session:
        token_res = await session.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        token_data = await token_res.json()

    access_token = token_data.get("access_token")
    if not access_token:
        print(f"[OAuth2] トークン取得失敗: {token_data}")
        return aiohttp.web.Response(
            text="<html><body><h2>[NG] 認証に失敗しました。もう一度お試しください。</h2></body></html>",
            content_type="text/html"
        )

    # --- アクセストークンでユーザーの参加サーバー一覧を取得 ---
    async with aiohttp.ClientSession() as session:
        guilds_res = await session.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_guilds = await guilds_res.json()

    if not isinstance(user_guilds, list):
        return aiohttp.web.Response(
            text="<html><body><h2>[NG] サーバー情報の取得に失敗しました。</h2></body></html>",
            content_type="text/html"
        )

    user_guild_ids = {int(g["id"]) for g in user_guilds}

    # --- ブラックリストとの照合 ---
    all_data = load_data()
    cfg = get_guild_config(all_data, str(guild_id))

    if not cfg.get("server_blacklist_enabled", False):
        # 機能が無効になっていれば認証OKとして終了
        return aiohttp.web.Response(
            text="<html><body><h2>[OK] 認証が完了しました。サーバーをお楽しみください！</h2></body></html>",
            content_type="text/html"
        )

    blacklist_ids = set(cfg.get("server_blacklist_ids", []))
    matched = user_guild_ids & blacklist_ids

    target_guild = bot.get_guild(guild_id)
    action = cfg.get("server_blacklist_action", "ban")
    action_label = "BAN" if action == "ban" else "キック"

    if matched:
        # ブラックリスト対象サーバーに参加しているため処置を実行
        matched_id_text = ", ".join(str(sid) for sid in matched)
        print(f"[サーバーBL] ユーザー {user_id} がBL対象サーバー({matched_id_text})に在籍 -> {action_label} 実行")

        if target_guild:
            member = target_guild.get_member(user_id)
            if member:
                try:
                    if action == "ban":
                        await target_guild.ban(
                            discord.Object(id=user_id),
                            reason=f"サーバーBL: BL対象サーバー({matched_id_text})への参加を検出"
                        )
                    else:
                        await member.kick(
                            reason=f"サーバーBL: BL対象サーバー({matched_id_text})への参加を検出"
                        )

                    # ログチャンネルへ通知
                    log_ch_id = cfg.get("server_blacklist_log_channel_id") or cfg.get("mod_log_channel_id")
                    if log_ch_id:
                        log_ch = target_guild.get_channel(log_ch_id)
                        if log_ch:
                            bl_embed = discord.Embed(
                                title=f"[BL] サーバーブラックリスト - {action_label}実行",
                                color=discord.Color.red()
                            )
                            bl_embed.add_field(
                                name="対象ユーザー",
                                value=f"<@{user_id}> (`{user_id}`)",
                                inline=False
                            )
                            bl_embed.add_field(
                                name="検出されたBL対象サーバーID",
                                value=matched_id_text,
                                inline=False
                            )
                            bl_embed.add_field(name="実行した処置", value=action_label, inline=True)
                            bl_embed.timestamp = discord.utils.utcnow()
                            try:
                                await log_ch.send(embed=bl_embed)
                            except Exception:
                                pass

                except Exception as e:
                    print(f"[サーバーBL] {action_label}実行エラー: {e}")

        return aiohttp.web.Response(
            text=(
                "<html><body>"
                f"<h2>[BL] このサーバーには参加できません。</h2>"
                f"<p>あなたは参加が制限されているサーバーに在籍しているため、{action_label}されました。</p>"
                "</body></html>"
            ),
            content_type="text/html"
        )
    else:
        # ブラックリスト対象サーバーに参加していない -> 認証OK
        print(f"[サーバーBL] ユーザー {user_id} はBL対象サーバーに在籍なし -> 認証OK")
        return aiohttp.web.Response(
            text=(
                "<html><body>"
                "<h2>[OK] 認証が完了しました！</h2>"
                "<p>サーバーに問題なく参加できます。このページを閉じてください。</p>"
                "</body></html>"
            ),
            content_type="text/html"
        )


async def _start_web_server():
    """aiohttp による OAuth2 コールバック受け取り用 Webサーバーを起動します。"""
    app = aiohttp.web.Application()
    app.router.add_get("/callback", _oauth2_callback_handler)
    app.router.add_get("/", lambda req: aiohttp.web.Response(text="Bot is running."))
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", OAUTH_PORT)
    await site.start()
    print(f"[Webサーバー] OAuth2コールバックサーバーを起動しました（ポート: {OAUTH_PORT}）")


# ====================================================================
# Botの起動
# ====================================================================

async def _main():
    """BotとWebサーバーを同時に起動します。"""
    # OAuth2設定が揃っている場合のみWebサーバーを起動
    if DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and OAUTH_REDIRECT_URI:
        await _start_web_server()
    else:
        print("[警告] OAuth2の環境変数（DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET / OAUTH_REDIRECT_URI）が未設定のため、Webサーバーは起動しません。")
        print("       サーバーブラックリスト機能を使用する場合は、これらの環境変数を設定してください。")

    async with bot:
        await bot.start(TOKEN)


asyncio.run(_main())