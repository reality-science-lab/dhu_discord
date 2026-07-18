"""ボイスチャンネル在室者のスナップショットを vc_history.csv に追記する。

DiscordのAPIには過去のVCセッションを返す機能が無いため、定期実行（GitHub Actions cron）で
「その瞬間VCにいる人」を記録し続け、メンバー別ダッシュボードで概算参加時間として集計する。
在室者がいない時刻は行を追記しない（参加時間は在室スナップショット数×実行間隔で概算する）。

プライバシー: CSVにはユーザーIDのみを記録し、表示名は書かない。表示名との紐付けは
週報実行時に行い、暗号化ダッシュボードデータ（docs/data.enc）にのみ格納する。

必要な環境変数: DISCORD_TOKEN, GUILD_ID（.env でも可）
Bot権限: Guilds / Voice States Intent（メッセージ系Intentは不要）
"""

import asyncio
import csv
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

load_dotenv()

CSV_PATH = os.environ.get("VC_CSV_PATH", "vc_history.csv")
CSV_COLUMNS = ["ts_utc", "user_id", "channel"]


async def take_snapshot() -> list[dict]:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
    client = discord.Client(intents=intents, proxy=proxy)
    guild_id = int(os.environ["GUILD_ID"])
    rows: list[dict] = []
    error: BaseException | None = None

    @client.event
    async def on_ready():
        nonlocal error
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise RuntimeError(f"Guild {guild_id} がキャッシュにありません（Botの参加サーバーを確認）")
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                for user_id, state in channel.voice_states.items():
                    member = guild.get_member(user_id)
                    if member is not None and member.bot:
                        continue
                    rows.append({"ts_utc": ts, "user_id": user_id, "channel": channel.name})
        except BaseException as exc:  # noqa: BLE001
            error = exc
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])
    if error is not None:
        raise error
    return rows


def append_rows(rows: list[dict]) -> None:
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = asyncio.run(take_snapshot())
    if not rows:
        print("VC在室者なし（追記しません）。")
        return
    append_rows(rows)
    print(f"VCスナップショットを追記しました: {CSV_PATH}（{len(rows)}人）")


if __name__ == "__main__":
    main()
