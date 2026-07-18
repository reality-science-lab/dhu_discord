from datetime import datetime, timedelta, timezone

import pandas as pd
from anthropic import Anthropic
from anthropic.types import Message

from collector import CollectedData
from config import Config
from metrics import METRIC_COLUMNS, METRIC_LABELS_JA

MAX_MESSAGES_IN_PROMPT = 800
TOP_CHANNELS = 5  # チャンネルの盛り上がり上位表示数（3〜5）
TOP_THREADS = 5  # スレッドの盛り上がり上位表示数


def _extract_text(message: Message) -> str:
    """Concatenate the text of all text blocks in the response.

    The response may include non-text blocks (e.g. thinking blocks) before the
    text, so we cannot assume ``content[0]`` is the answer.
    """
    parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    return "\n".join(parts).strip()


def _period_bounds(config: Config, data: CollectedData) -> tuple:
    """対象期間の最初の日と最後の日（含む）を返す。

    period_end は排他的上限なので、直前の瞬間が属する日を最終日とする。
    """
    tz = config.timezone
    first = data.period_start.astimezone(tz).date()
    last = (data.period_end - timedelta(microseconds=1)).astimezone(tz).date()
    return first, last


def _period_label(config: Config, data: CollectedData) -> str:
    first, last = _period_bounds(config, data)
    if first == last:
        return first.strftime("%Y-%m-%d")
    return f"{first:%Y-%m-%d}〜{last:%Y-%m-%d}"


def _format_messages(config: Config, data: CollectedData) -> str:
    tz = config.timezone
    # 期間が複数日にまたがる場合は日付も表示する
    first, last = _period_bounds(config, data)
    multi_day = first != last
    fmt = "%m-%d %H:%M" if multi_day else "%H:%M"
    lines = []
    for msg in data.messages[:MAX_MESSAGES_IN_PROMPT]:
        ts = msg.created_at.astimezone(tz).strftime(fmt)
        lines.append(f"[{ts}] #{msg.channel_name} {msg.author_name}: {msg.content}")
    return "\n".join(lines)


def _metrics_block(data: CollectedData) -> str:
    return (
        f"- 新規参加者数: {data.new_member_count}\n"
        f"- DHUmember数（「閲覧権限」ロール付与・自己紹介からの自動付与）: {data.view_role_granted_count}\n"
        f"- アクティブユーザー数（発言ユニークユーザー）: {data.active_user_count}"
    )


def _totals_block(config: Config, data: CollectedData) -> str:
    return (
        f"- 現在のメンバー総数: {data.total_member_count}\n"
        f"- Administratorロール保持者数: {data.admin_role_count}\n"
        f"- DHUmember数（「閲覧権限」ロール保持者）: {data.view_role_member_count}"
    )


def _channel_top_block(data: CollectedData, top_n: int = TOP_CHANNELS) -> str:
    ranked = sorted(
        ((name, count) for name, count in data.channel_message_counts.items() if count > 0),
        key=lambda kv: kv[1],
        reverse=True,
    )[:top_n]
    if not ranked:
        return "（対象期間に書き込みはありませんでした）"
    return "\n".join(f"- #{name}: {count}件" for name, count in ranked)


def _thread_top_block(data: CollectedData, top_n: int = TOP_THREADS) -> str:
    ranked = sorted(
        (
            (channel, thread, count)
            for (channel, thread), count in data.thread_message_counts.items()
            if count > 0
        ),
        key=lambda t: t[2],
        reverse=True,
    )[:top_n]
    if not ranked:
        return "（対象期間にスレッド内の書き込みはありませんでした）"
    return "\n".join(f"- {channel} › {thread}: {count}件" for channel, thread, count in ranked)


def _weekly_trend_block(history: pd.DataFrame, weeks: int = 8) -> str:
    if history is None or history.empty:
        return "（推移データがまだありません）"
    df = history.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    iso = df["date"].dt.isocalendar()
    df["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(int).astype(str).str.zfill(2)
    agg = df.groupby("week")[METRIC_COLUMNS].sum().tail(weeks)

    header = "週 | " + " | ".join(METRIC_LABELS_JA[c] for c in METRIC_COLUMNS)
    rows = [header]
    for week, row in agg.iterrows():
        rows.append(f"{week} | " + " | ".join(str(int(row[c])) for c in METRIC_COLUMNS))
    return "\n".join(rows)


def _events_block(config: Config, data: CollectedData) -> str:
    if not data.events:
        return "（登録されているイベントはありません）"
    tz = config.timezone
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    lines = []
    for ev in sorted(data.events, key=lambda e: e.created_at or e.scheduled_start or epoch):
        parts = [f"- {ev.name}（状態: {ev.status}"]
        if ev.created_at:
            parts.append(f", 作成: {ev.created_at.astimezone(tz):%Y-%m-%d %H:%M}")
        if ev.scheduled_start:
            parts.append(f", 開催予定: {ev.scheduled_start.astimezone(tz):%Y-%m-%d %H:%M}")
        if ev.user_count is not None:
            parts.append(f", 興味あり: {ev.user_count}人")
        if ev.location:
            parts.append(f", 場所: {ev.location}")
        if ev.created_in_period:
            parts.append(", ★今回の対象期間に立ち上げ")
        parts.append("）")
        lines.append("".join(parts))
    return "\n".join(lines)


def generate_weekly_report(
    client: Anthropic, config: Config, data: CollectedData, history: pd.DataFrame
) -> str:
    period = _period_label(config, data)
    prompt = f"""あなたはDiscordコミュニティの運営アシスタントです。
以下のデータをもとに、管理者向けの「週報」をMarkdownで作成してください。
対象期間は直近1週間（{period}）です。

# 0. 現在の総数（スナップショット）
{_totals_block(config, data)}

# 1. ユーザー数の推移（週次集計・直近）
{_weekly_trend_block(history)}

（参考）今回の対象期間の合計:
{_metrics_block(data)}

# 2. チャンネル・掲示板の盛り上がり（公開チャンネルのみ・対象期間の投稿数・多い順）
{_channel_top_block(data)}

# 2b. スレッドの盛り上がり（公開チャンネル・掲示板配下のスレッド単位・投稿数・多い順）
{_thread_top_block(data)}

# 3. 登録イベント（立ち上がり・実施状況）
{_events_block(config, data)}

# 対象期間のメッセージ履歴（トピック要約の材料）
{_format_messages(config, data)}

# 出力要件（Markdown・日本語・見出し＋箇条書き中心）
## 現在の状況
- 現在のメンバー総数・Administrator数・DHUmember数を簡潔に記載する
## ユーザー数の推移
- 上記の週次集計をもとに、参加者数・DHUmember数・アクティブ数などの増減トレンドを簡潔に述べる
## チャンネル・掲示板の盛り上がり
- 投稿数の多い上位3〜5チャンネル（公開チャンネル・掲示板）を挙げ、各チャンネルで何が話題だったかをメッセージ履歴を根拠に1〜2行で要約する
- 「スレッドの盛り上がり」に特に投稿数の多いスレッドがあれば、どのチャンネル/掲示板の何というスレッドかを明記して触れる
## イベント
- 上記「登録イベント」をもとに、対象期間に立ち上がったイベントと、各イベントの実施状況（開催予定/開催中/終了など）をまとめる
- 該当が無ければ「対象期間に新規イベントはありませんでした」とする

制約:
- 与えられたデータに無い数値・イベント・チャンネルを創作しないこと
- ボイスチャットについては本レポートの対象外"""

    message = client.messages.create(
        model=config.sonnet_model,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(message)
