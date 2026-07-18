import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _split_ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


@dataclass
class Config:
    discord_token: str = field(default_factory=lambda: os.environ["DISCORD_TOKEN"])
    anthropic_api_key: str = field(default_factory=lambda: os.environ["ANTHROPIC_API_KEY"])
    guild_id: int = field(default_factory=lambda: int(os.environ["GUILD_ID"]))
    exclude_channel_ids: list[int] = field(
        default_factory=lambda: _split_ids(os.environ.get("EXCLUDE_CHANNEL_IDS", ""))
    )
    intro_channel_id: int = field(default_factory=lambda: int(os.environ["INTRO_CHANNEL_ID"]))
    # 自己紹介の投稿から自動付与される「閲覧権限」ロール名（ダッシュボードでは DHUmember 数として扱う）
    view_role_name: str = field(default_factory=lambda: os.environ.get("VIEW_ROLE_NAME", "閲覧権限"))
    # 運営（管理者）ロール名
    admin_role_name: str = field(default_factory=lambda: os.environ.get("ADMIN_ROLE_NAME", "Administrator"))
    # 「チャンネルの盛り上がり」から除外するチャンネル名キーワード（部分一致）。
    # 既定: ようこそ / 自己紹介 / アナウンス（絵文字プレフィックス等に強い部分一致で判定）
    ranking_exclude_keywords: list[str] = field(
        default_factory=lambda: [
            s.strip()
            for s in os.environ.get("RANKING_EXCLUDE_KEYWORDS", "ようこそ,自己紹介,アナウンス").split(",")
            if s.strip()
        ]
    )
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo(os.environ.get("TIMEZONE", "Asia/Tokyo")))
    haiku_model: str = field(default_factory=lambda: os.environ.get("HAIKU_MODEL", "claude-haiku-4-5-20251001"))
    sonnet_model: str = field(default_factory=lambda: os.environ.get("SONNET_MODEL", "claude-sonnet-5"))
    # 旧運用との互換用。週報本文はここへ書き出さず、暗号化ダッシュボードにのみ格納する。
    output_dir: str = field(default_factory=lambda: os.environ.get("OUTPUT_DIR", "reports"))
    metrics_csv_path: str = field(default_factory=lambda: os.environ.get("METRICS_CSV_PATH", "metrics_history.csv"))
    metrics_graph_path: str = field(
        default_factory=lambda: os.environ.get("METRICS_GRAPH_PATH", "metrics_graph.png")
    )
    # チャンネル/スレッドの盛り上がりグラフ（横棒）の出力先
    activity_graph_path: str = field(
        default_factory=lambda: os.environ.get("ACTIVITY_GRAPH_PATH", "activity_graph.png")
    )
    # チャンネル/スレッドの盛り上がり 日別履歴CSV
    activity_csv_path: str = field(
        default_factory=lambda: os.environ.get("ACTIVITY_CSV_PATH", "activity_history.csv")
    )
    # メンバー別ダッシュボードの集計単位日数（直近N日）
    member_activity_days: int = field(
        default_factory=lambda: int(os.environ.get("MEMBER_ACTIVITY_DAYS", "7"))
    )
