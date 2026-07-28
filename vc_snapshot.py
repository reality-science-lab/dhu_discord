"""ボイスチャンネルの在室者を「見回り」、VC履歴CSVに1行ずつ追記する。

DiscordのAPIには過去のVCセッションを返す機能が無いため、定期的に「その瞬間VCにいる人」を
記録し続け、週報側で「在室が観測された時間帯の数 × 実行間隔」として滞在時間を概算する。
在室者がいない時刻は行を追記しない（＝空コミットを作らない）。

このスクリプト自体は1回ぶんの見回りしかしない。20分間隔は
.github/workflows/vc-snapshot.yml が1回のジョブの中でこれを繰り返し呼ぶことで作る
（GitHubのcronは短間隔だと発火が落とされるため、cronは毎時1回だけにしている）。

プライバシー: 記録するのはユーザーIDそのものではなく、VC_HASH_SECRET を鍵とした
HMAC-SHA256 の仮名（vc.pseudonym）。公開リポジトリのデータ用ブランチに置いても、
鍵を知らない第三者はどのアカウントかを特定できない。表示名との紐付けは週報実行時
のみ行い、暗号化された docs/data.enc にしか現れない。

必要な環境変数: DISCORD_TOKEN, GUILD_ID, VC_HASH_SECRET（.env でも可）
Bot権限: Guilds / Voice States Intent（メッセージ系Intentは不要）
"""

import asyncio
import csv
import os
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv

from vc import CSV_COLUMNS, pseudonym

load_dotenv()

CSV_PATH = os.environ.get("VC_CSV_PATH", "vc_history.csv")


async def take_snapshot(secret: str) -> list[dict]:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True

    # discord.py の aiohttp セッションは明示しないと HTTPS_PROXY を見ない（collector と同じ扱い）
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
            afk_channel_id = guild.afk_channel.id if guild.afk_channel else None
            for channel in [*guild.voice_channels, *guild.stage_channels]:
                # AFK（自動移動）チャンネルは「参加」ではないため集計しない
                if channel.id == afk_channel_id:
                    continue
                for user_id in channel.voice_states:
                    # メンバーIntentを持たないためキャッシュは基本空。取得できたときだけBotを除く
                    # （取りこぼしても、週報側でDHUmemberに絞る際に落ちる）
                    member = guild.get_member(user_id)
                    if member is not None and member.bot:
                        continue
                    rows.append(
                        {
                            "ts_utc": ts,
                            "user_hash": pseudonym(secret, user_id),
                            "channel_id": channel.id,
                            "channel_name": channel.name,
                        }
                    )
        except BaseException as exc:  # noqa: BLE001
            error = exc
        finally:
            await client.close()

    await client.start(os.environ["DISCORD_TOKEN"])
    if error is not None:
        raise error
    return rows


def append_rows(rows: list[dict], path: str = CSV_PATH) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    # 鍵が無いまま生IDを書き出す事故を防ぐため、接続前に確認する
    secret = os.environ.get("VC_HASH_SECRET", "")
    if not secret:
        raise SystemExit(
            "VC_HASH_SECRET が未設定です。公開リポジトリに個人の行動履歴を残さないため、"
            "在室者IDは秘密鍵付きハッシュで仮名化して記録します。"
            "Settings → Secrets and variables → Actions に VC_HASH_SECRET を登録してください"
            "（週報ワークフローにも同じ値が必要です）。"
        )

    rows = asyncio.run(take_snapshot(secret))
    if not rows:
        print("VC在室者なし（追記しません）。")
        return
    append_rows(rows)
    print(f"VCスナップショットを追記しました: {CSV_PATH}（{len(rows)}人）")


if __name__ == "__main__":
    main()
