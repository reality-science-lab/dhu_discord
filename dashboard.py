"""ダッシュボード用のデータを書き出す。

発言本文は含めない。メンバー名を含むメンバー別集計と週報本文は、
パスワードで暗号化した ``docs/data.enc`` にのみ格納する（CI では
DASHBOARD_PASSWORD が必須のため平文で公開されることはない。パスワード
未設定の平文 ``data.json`` はローカル開発専用で、コミットしない）。
"""

import base64
import hashlib
import json
import os
from datetime import timedelta

import pandas as pd

from collector import CollectedData
from config import Config
from metrics import METRIC_COLUMNS, METRIC_DEFS_JA, METRIC_LABELS_JA

DOCS_DIR = "docs"
DATA_FILENAME = "data.json"
ENC_FILENAME = "data.enc"  # DASHBOARD_PASSWORD 設定時の暗号化データ
KDF_ITERATIONS = 310_000
TOP_CHANNELS = 5
TOP_THREADS = 5
ACTIVITY_EXPORT_DAYS = 120  # 推移グラフとしてエクスポートする日数の上限


def _normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    """現在の指標カラムだけに揃える（旧スキーマ／欠損に強くする）。"""
    df = history.copy()
    for col in METRIC_COLUMNS:
        if col not in df.columns:
            df[col] = 0
    df = df[["date", *METRIC_COLUMNS]].fillna(0)
    for col in METRIC_COLUMNS:
        df[col] = df[col].astype(int)
    return df


def _kpis(history: pd.DataFrame, last_day) -> list[dict]:
    """直近7日（対象週）と、その前7日の合計を比較してKPIを作る。"""
    df = history.copy()
    df["date"] = pd.to_datetime(df["date"])
    end = pd.Timestamp(last_day)
    this_start = end - pd.Timedelta(days=6)
    prev_end = this_start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=6)

    this_mask = (df["date"] >= this_start) & (df["date"] <= end)
    prev_mask = (df["date"] >= prev_start) & (df["date"] <= prev_end)

    kpis = []
    for col in METRIC_COLUMNS:
        this_val = int(df.loc[this_mask, col].sum())
        prev_val = int(df.loc[prev_mask, col].sum())
        kpis.append(
            {
                "key": col,
                "label": METRIC_LABELS_JA[col],
                "value": this_val,
                "prev": prev_val,
                "delta": this_val - prev_val,
            }
        )
    return kpis


MEMBER_METRIC_KEYS = ["chars", "posts", "mentions_out", "mentions_in", "vc_min", "event_interest"]

MEMBER_METRIC_LABELS_JA = {
    "chars": "発言文字数",
    "posts": "投稿数",
    "mentions_out": "メンション数",
    "mentions_in": "被メンション数",
    "vc_min": "VC参加時間（分・概算）",
    "event_interest": "イベントへの興味",
}

MEMBER_METRIC_DEFS_JA = {
    "chars": "集計期間中に対象テキストチャンネル・スレッドへ投稿したメッセージの合計文字数。",
    "posts": "集計期間中の投稿メッセージ数。",
    "mentions_out": "本人の投稿に含まれる他ユーザーへの@メンションの合計数。",
    "mentions_in": "他のメンバーの投稿で@メンションされた回数。",
    "vc_min": (
        "ボイスチャンネルへの概算参加時間。定期スナップショット（既定15分間隔）で在室が確認された"
        "回数×間隔で概算するため、短時間の参加は取りこぼすことがある。記録開始以降のみ。"
    ),
    "event_interest": (
        "サーバーのスケジュールイベントに「興味あり」を付けた数。終了・削除済みイベントは"
        "Discord APIから消えるため、現在登録されているイベントのみが対象。"
    ),
}


def _vc_minutes_by_user(config: Config, window_start, window_end) -> dict[int, int] | None:
    """vc_history.csv から集計窓内の概算VC参加分数をユーザーID別に返す。

    概算 = 在室が記録されたスナップショット時刻数 × スナップショット間隔（分）。
    CSVが無い（スナップショット運用を始めていない）場合は None。
    """
    path = config.vc_csv_path
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return {}
    if df.empty or not {"ts_utc", "user_id"}.issubset(df.columns):
        return {}
    ts = pd.to_datetime(df["ts_utc"], utc=True, errors="coerce")
    df = df[(ts >= pd.Timestamp(window_start)) & (ts < pd.Timestamp(window_end))]
    if df.empty:
        return {}
    per_user = df.groupby("user_id")["ts_utc"].nunique() * config.vc_snapshot_interval_min
    return {int(uid): int(minutes) for uid, minutes in per_user.items()}


def _members_payload(config: Config, data: CollectedData) -> dict:
    """メンバー別ダッシュボード用データ。名前を含むため暗号化データにのみ入れる前提。"""
    tz = config.timezone
    window_start = data.member_window_start or data.period_start
    window_end = data.period_end
    vc = _vc_minutes_by_user(config, window_start, window_end)

    rows = [
        {
            "name": st.name,
            "chars": st.chars,
            "posts": st.posts,
            "mentions_out": st.mentions_out,
            "mentions_in": st.mentions_in,
            "vc_min": (vc.get(uid, 0) if vc is not None else None),
            "event_interest": st.event_interest,
        }
        for uid, st in data.member_stats.items()
    ]

    return {
        "window_days": config.member_activity_days,
        "window": {
            "start": window_start.astimezone(tz).date().isoformat(),
            "end": (window_end - timedelta(microseconds=1)).astimezone(tz).date().isoformat(),
        },
        "vc_available": vc is not None,
        "vc_interval_min": config.vc_snapshot_interval_min,
        "metric_keys": MEMBER_METRIC_KEYS,
        "metric_labels": MEMBER_METRIC_LABELS_JA,
        "metric_defs": MEMBER_METRIC_DEFS_JA,
        "rows": rows,
    }


def _activity_payload(activity: pd.DataFrame | None) -> dict:
    """日別の盛り上がり履歴を、日付軸＋系列（0埋め済み counts 配列）の形にする。"""
    empty = {"dates": [], "series": []}
    if activity is None or activity.empty:
        return empty
    df = activity.copy()
    df["date"] = df["date"].astype(str)
    dates_dt = pd.to_datetime(df["date"])
    cutoff = dates_dt.max() - pd.Timedelta(days=ACTIVITY_EXPORT_DAYS - 1)
    df = df[dates_dt >= cutoff]
    if df.empty:
        return empty

    date_range = pd.date_range(pd.to_datetime(df["date"]).min(), pd.to_datetime(df["date"]).max(), freq="D")
    dates = [d.strftime("%Y-%m-%d") for d in date_range]

    series = []
    for (kind, name), group in df.groupby(["kind", "name"]):
        by_date = dict(zip(group["date"], group["count"]))
        series.append(
            {
                "kind": kind,
                "name": name,
                "total": int(group["count"].sum()),
                "counts": [int(by_date.get(d, 0)) for d in dates],
            }
        )
    series.sort(key=lambda s: s["total"], reverse=True)
    return {"dates": dates, "series": series}


def _write_encrypted(payload: dict, password: str, path: str) -> None:
    """payload を AES-256-GCM で暗号化して書き出す（鍵は PBKDF2-SHA256 で導出）。

    ブラウザ側は WebCrypto（PBKDF2 + AES-GCM）で同じ手順で復号する。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, KDF_ITERATIONS, dklen=32)
    ciphertext = AESGCM(key).encrypt(iv, raw, None)

    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    envelope = {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iter": KDF_ITERATIONS,
        "salt": b64(salt),
        "iv": b64(iv),
        "ct": b64(ciphertext),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(envelope, f)


def _read_encrypted(path: str, password: str) -> dict | None:
    """既存の暗号化 payload を読む。復号できない場合は古いデータを引き継がない。"""
    if not os.path.exists(path):
        return None

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        with open(path, encoding="utf-8") as f:
            envelope = json.load(f)
        b64 = lambda value: base64.b64decode(value)  # noqa: E731
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            b64(envelope["salt"]),
            int(envelope["iter"]),
            dklen=32,
        )
        raw = AESGCM(key).decrypt(b64(envelope["iv"]), b64(envelope["ct"]), None)
        return json.loads(raw.decode("utf-8"))
    except (InvalidTag, KeyError, ValueError, TypeError, OSError, json.JSONDecodeError):
        return None


def write_dashboard_data(
    config: Config,
    data: CollectedData,
    history: pd.DataFrame,
    activity: pd.DataFrame | None = None,
    weekly_report: str | None = None,
) -> str:
    tz = config.timezone
    os.makedirs(DOCS_DIR, exist_ok=True)
    history = _normalize_history(history)

    first_day = data.period_start.astimezone(tz).date()
    last_day = (data.period_end - timedelta(microseconds=1)).astimezone(tz).date()

    repo = os.environ.get("GITHUB_REPOSITORY", "uni-scope/dhw")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    workflow_url = f"{server}/{repo}/actions/workflows/weekly-report.yml"
    actions_url = f"{server}/{repo}/actions"

    channels_top = sorted(
        ({"name": name, "count": count} for name, count in data.channel_message_counts.items() if count > 0),
        key=lambda c: c["count"],
        reverse=True,
    )[:TOP_CHANNELS]

    threads_top = sorted(
        (
            {"channel": channel, "name": thread, "count": count}
            for (channel, thread), count in data.thread_message_counts.items()
            if count > 0
        ),
        key=lambda t: t["count"],
        reverse=True,
    )[:TOP_THREADS]

    events = [
        {
            "name": ev.name,
            "status": ev.status,
            "scheduled_start": ev.scheduled_start.astimezone(tz).isoformat() if ev.scheduled_start else None,
            "user_count": ev.user_count,
            "created_in_period": ev.created_in_period,
        }
        for ev in data.events
    ]

    payload = {
        "generated_at": data.period_end.astimezone(tz).isoformat(),
        "period": {"start": first_day.isoformat(), "end": last_day.isoformat()},
        "repo": repo,
        "workflow_url": workflow_url,
        "actions_url": actions_url,
        "metric_columns": METRIC_COLUMNS,
        "metric_labels": METRIC_LABELS_JA,
        "metric_defs": METRIC_DEFS_JA,
        "kpis": _kpis(history, last_day),
        "history": history[["date", *METRIC_COLUMNS]].to_dict(orient="records"),
        "channels_top": channels_top,
        "threads_top": threads_top,
        # 盛り上がりの推移（日別）と、デフォルト表示する系列（＝週報で言及される上位チャンネル）
        "activity": _activity_payload(activity),
        "activity_defaults": [{"kind": "channel", "name": c["name"]} for c in channels_top],
        "events": events,
        # メンバー別ダッシュボード（docs/members.html）。名前を含むため暗号化前提。
        "members": _members_payload(config, data),
        # 現在時点のスナップショット（総数）。「閲覧権限」ロールは DHUmember として表記する。
        "totals": {
            "member_count": data.total_member_count,
            "admin_role_count": data.admin_role_count,
            "dhumember_count": data.view_role_member_count,
            "view_role_name": config.view_role_name,
            "admin_role_name": config.admin_role_name,
        },
    }

    plain_path = os.path.join(DOCS_DIR, DATA_FILENAME)
    enc_path = os.path.join(DOCS_DIR, ENC_FILENAME)

    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if weekly_report and not password:
        raise RuntimeError(
            "週報本文を平文公開しないため、DASHBOARD_PASSWORD の設定が必要です。"
        )

    # 指標だけを更新する実行では、直前の週報を消さずに引き継ぐ。
    previous = _read_encrypted(enc_path, password) if password else None
    if weekly_report:
        payload["weekly_report"] = {
            "period": {"start": first_day.isoformat(), "end": last_day.isoformat()},
            "generated_at": data.period_end.astimezone(tz).isoformat(),
            "markdown": weekly_report,
        }
    elif previous and previous.get("weekly_report"):
        payload["weekly_report"] = previous["weekly_report"]

    if password:
        # パスワード運用時: 暗号化データのみを配信し、平文は削除する
        _write_encrypted(payload, password, enc_path)
        if os.path.exists(plain_path):
            os.remove(plain_path)
        return enc_path

    # パスワード未設定時（ローカル開発など）は従来どおり平文
    with open(plain_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    if os.path.exists(enc_path):
        os.remove(enc_path)
    return plain_path
