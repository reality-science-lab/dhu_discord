"""ボイスチャット在室スナップショットの記録形式と集計。

DiscordのAPIには過去のVCセッションを返す機能が無いため、``vc_snapshot.py`` を
定期実行（既定20分毎）して「その瞬間VCにいる人」を記録し、
**在室スナップショット数 × 実行間隔** で滞在時間を概算する。

ログは公開リポジトリのデータ用ブランチに置くため、CSVにはユーザーIDそのもの
ではなく ``VC_HASH_SECRET`` を鍵とした HMAC-SHA256 の仮名だけを記録する。
表示名との紐付けは週報実行時に在籍メンバーから逆引きし、暗号化された
``docs/data.enc``（＝週報本文とダッシュボード）にのみ現れる。
"""

import csv
import hashlib
import hmac
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # collector が本モジュールを import するため、実行時は読み込まない
    from collector import CollectedData
    from config import Config

CSV_COLUMNS = ["ts_utc", "user_hash", "channel_id", "channel_name"]
# HMAC-SHA256 の先頭16桁（64bit）。数百人規模で衝突は事実上起きず、CSVも短く保てる。
HASH_LENGTH = 16


@dataclass
class VcChannelStat:
    """ボイスチャンネル単位の盛り上がり（対象期間の集計）。"""

    name: str
    minutes: int  # 延べ滞在時間（人×分の概算）
    unique_users: int  # 期間中に一度でも在室したユニーク人数
    snapshots: int  # 在室者が観測されたスナップショット回数


def pseudonym(secret: str, user_id: int) -> str:
    """ユーザーIDを、秘密鍵付きハッシュの仮名に変換する。

    鍵が同じであれば毎回同じ仮名になるため、CSVへの追記と週報時の逆引きの
    両方に使える。鍵を知らない第三者はIDを復元できない。
    """
    if not secret:
        raise RuntimeError(
            "VC_HASH_SECRET が未設定です。公開リポジトリに生のユーザーIDを残さないため、"
            "スナップショットの記録には秘密鍵が必須です。"
        )
    digest = hmac.new(secret.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:HASH_LENGTH]


def load_rows(path: str) -> list[tuple[datetime, str, str, str]]:
    """VC履歴CSVを (時刻, 仮名, チャンネルID, チャンネル名) のリストとして読む。

    ファイルが無い場合（スナップショット運用の開始前）は空リストを返す。
    壊れた行・パースできない時刻は黙って読み飛ばし、週報生成は止めない。
    """
    if not os.path.exists(path):
        return []

    rows: list[tuple[datetime, str, str, str]] = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            user_hash = (row.get("user_hash") or "").strip()
            if not user_hash:
                continue
            try:
                ts = datetime.fromisoformat((row.get("ts_utc") or "").strip())
            except ValueError:
                continue
            if ts.tzinfo is None:
                continue
            channel_name = (row.get("channel_name") or "").strip()
            # channel_id はチャンネル改名に強い集計キー。未記録の行は名前で代用する。
            channel_id = (row.get("channel_id") or "").strip() or channel_name
            rows.append((ts, user_hash, channel_id, channel_name))
    return rows


def attach_vc_stats(config: "Config", data: "CollectedData") -> None:
    """VC履歴CSVを集計し、メンバー別の滞在時間とチャンネル別の盛り上がりを ``data`` に載せる。

    - メンバー別（``MemberStats.vc_minutes``）: メンバー別ダッシュボードと同じ
      直近N日の窓（``member_window_start``〜``period_end``）
    - チャンネル別（``vc_channels``）: 週報と同じ分析窓（``analysis_start``〜``period_end``）

    対象は ``member_stats`` に載っているメンバー（＝DHUmember。Bot・Administrator を除く）
    のみ。仮名が一致しない行（運営・Bot・退出済みメンバー）は集計しない。
    """
    interval = config.vc_snapshot_interval_min
    data.vc_interval_min = interval

    rows = load_rows(config.vc_csv_path)
    if not rows:
        return
    if not config.vc_hash_secret:
        print(
            f"警告: VC_HASH_SECRET が未設定のため、{config.vc_csv_path} の仮名を"
            "メンバーへ紐付けできません。VC実績の集計をスキップします。"
        )
        return

    data.vc_available = True
    by_hash = {pseudonym(config.vc_hash_secret, user_id): user_id for user_id in data.member_stats}

    member_start = data.member_window_start or data.period_start
    analysis_start = data.analysis_start or data.period_start
    until = data.period_end

    member_counts: Counter[int] = Counter()
    # チャンネルID -> 観測行。改名に備えて最新の名前を表示に使う。
    channel_rows: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    channel_names: dict[str, tuple[datetime, str]] = {}

    for ts, user_hash, channel_id, channel_name in rows:
        member_id = by_hash.get(user_hash)
        if member_id is None:
            continue
        if member_start <= ts < until:
            member_counts[member_id] += 1
        if analysis_start <= ts < until:
            channel_rows[channel_id].append((ts, member_id))
            latest = channel_names.get(channel_id)
            if channel_name and (latest is None or ts >= latest[0]):
                channel_names[channel_id] = (ts, channel_name)

    for member_id, count in member_counts.items():
        data.member_stats[member_id].vc_minutes = count * interval

    channels = [
        VcChannelStat(
            name=channel_names.get(channel_id, (until, channel_id))[1],
            minutes=len(entries) * interval,
            unique_users=len({member_id for _, member_id in entries}),
            snapshots=len({ts for ts, _ in entries}),
        )
        for channel_id, entries in channel_rows.items()
    ]
    channels.sort(key=lambda c: (-c.minutes, c.name))
    data.vc_channels = channels

    data.vc_total_minutes = sum(c.minutes for c in channels)
    data.vc_unique_users = len(
        {member_id for entries in channel_rows.values() for _, member_id in entries}
    )
    # 対象期間にスナップショットが1件も無い（＝収集がまだ始まっていない）判定に使う
    data.vc_first_seen = min((ts for ts, *_ in rows), default=None)
