# Discord連携型 AIライター＆コミュニティ分析エージェント

Discordサーバーの活動データを収集し、Claude API（Sonnet）で週報（管理者向け）を生成し、
指標の推移をCSV蓄積・グラフ化するスクリプト群。

週報は**オンデマンド（依頼時）**に生成し、以下を含みます。
- **現在の総数**（メンバー総数・Administrator数・DHUmember数）
- **ユーザー数の推移**（過去からの蓄積。週次集計＋推移グラフ）
- **チャンネル・掲示板の盛り上がり**（直近1週間の投稿数 上位3〜5。公開チャンネル＝「閲覧権限」ロールが閲覧可のチャンネル／掲示板のみ。ようこそ・自己紹介・アナウンス等は除外）
- **スレッドの盛り上がり**（上記チャンネル・掲示板配下のスレッド単位の投稿数 上位5。フォーラムのスレッド、通常チャンネルのアクティブスレッドを含む）
- **イベントの立ち上がり・実施状況**（Discordスケジュールイベント）

モニターする指標は **新規参加者数 / DHUmember数 / アクティブユーザー数** の3つです。
**DHUmember数**は「閲覧権限」ロールの付与数で、自己紹介の投稿から自動付与される運用のため、
新たにDHUmemberになった人数を表します（ロール名は `VIEW_ROLE_NAME`、既定 `閲覧権限`）。

> ボイスチャットの利用状況は対象外です。Discordには過去のVCセッションを返すAPIが無く、
> 取得するにはBotを常時起動して `voice_state_update` を記録し続ける必要があるためです。

## セットアップ

```bash
pip install -r requirements.txt
cp .env.example .env
# .env に DISCORD_TOKEN, ANTHROPIC_API_KEY を設定
# GUILD_ID / INTRO_CHANNEL_ID はデフォルトで設定済み
# 除外したいチャンネルがあれば EXCLUDE_CHANNEL_IDS に指定
```

集計対象チャンネルはBotが閲覧できる全テキストチャンネルを自動対象とします
（`EXCLUDE_CHANNEL_IDS` で指定したチャンネルのみ除外）。

Discord Bot には以下のIntent/権限が必要です。
- Message Content Intent
- Server Members Intent
- View Audit Log 権限

## 週報の生成（オンデマンド）

```bash
python main.py --weekly                 # 生成日から遡って1週間を集計して週報を生成
python main.py --weekly --skip-report   # 指標のみ更新（Claude API呼び出しなし）
python main.py --weekly --since 2026-06-25 --until 2026-07-02   # 期間を明示指定
```

`--weekly` は、**生成日から遡って1週間**（既定7日間）を対象にします。`--since`/`--until` で期間を明示指定もできます。

- 「ユーザー数の推移」は `metrics_history.csv` の**全履歴**（週次集計＋グラフ）を用います。
- 「チャンネルの盛り上がり」「イベント」は**対象期間（直近1週間）**を対象にします。

生成物:
- `docs/data.enc`（集計値と最新週報を含む、パスワード暗号化済みダッシュボードデータ）
- `metrics_history.csv`（指標を**1日1行**で蓄積。同じ日付は上書き）
- `metrics_graph.png`（指標推移グラフ。**凡例・軸ラベルは日本語**）
- `activity_graph.png`（チャンネル・掲示板／スレッドの盛り上がり 横棒グラフ）
- `activity_history.csv`（チャンネル・スレッド別の投稿数を**日別**で蓄積。ダッシュボードの推移グラフの元データ）

`metrics_history.csv` / `metrics_graph.png` / `activity_graph.png` / `activity_history.csv` はリポジトリで追跡し、実行のたびに更新・蓄積されます。

> **グラフの日本語表示**: 日本語対応フォント（例: IPAGothic / Noto Sans CJK JP）が必要です。
> 見つからない場合は凡例が豆腐（□）になります。Ubuntu例: `sudo apt-get install -y fonts-ipafont-gothic`

### 指標だけのバックフィル

推移データを過去から埋めたい場合は、`--weekly` なしで期間を指定すると1日ずつ集計します。

```bash
python main.py --since 2026-06-04 --until 2026-07-02 --skip-report
```

> **注意（集計の限界）**: 新規参加者数は「現在サーバーに在籍しているメンバーの参加日時」から算出するため、
> 期間中に参加後すぐ退出したメンバーは含まれません。DHUmember数（「閲覧権限」ロール付与）・イベントの立ち上がりはDiscordの監査ログ保持期間（約90日）内でのみ遡れます。
> ボイスチャットの履歴は取得できません（上記参照）。「現在の総数」（メンバー総数・Administrator・DHUmember）は実行時点のスナップショットです。

## オンデマンド実行（GitHub Actions）

`.github/workflows/weekly-report.yml` は**手動実行（`workflow_dispatch`）専用**です（定期スケジュールはしません）。
実行すると週報を生成して暗号化ダッシュボードへ反映し、指標と `docs/data.enc` をリポジトリへコミットします。
週報の Markdown ファイルは作成せず、Actions の実行サマリーや Artifact にも保存しません。

セットアップ:
1. リポジトリの **Secrets** に `DISCORD_TOKEN` / `ANTHROPIC_API_KEY` / `GUILD_ID` / `INTRO_CHANNEL_ID` / `DASHBOARD_PASSWORD` を登録
2. （任意）**Variables** に `EXCLUDE_CHANNEL_IDS` を登録（除外チャンネルがある場合のみ）
3. Actions画面から「Weekly community report」を実行（`since`/`until`/`skip_report` を任意指定可）

## ダッシュボード（GitHub Pages）

`docs/` に常設ダッシュボード（`docs/index.html`）があり、実行のたびに暗号化データ `docs/data.enc` が更新されます。
現在の総数（メンバー総数・Administrator・DHUmember）・KPIタイル（前週比つき）・指標の推移グラフ
（インタラクティブ／表ビュー切替）・**盛り上がりの推移**（チャンネル・スレッド別の日別投稿数。
プルダウンで最大8件まで複数選択、チャンネル＝実線／スレッド＝破線で区別。デフォルト表示は
週報で言及される上位チャンネル）・チャンネル/スレッドの盛り上がり Top5・イベント一覧・最新週報を表示します。
週報は Markdown のままワンクリックでクリップボードへコピーできます。

**プライバシー**: 集計値と週報本文は暗号化データ内にのみ格納します。週報の平文ファイル、
Actions 実行サマリー、Artifact は作成しません。

### パスワード保護

ダッシュボードのデータは **AES-256-GCM で暗号化して配信**され、閲覧時にパスワードの入力が必要です。

- **Secrets に `DASHBOARD_PASSWORD` を登録**してください（未設定だとワークフローがエラーで停止します）
- ワークフロー実行時にデータを暗号化した `docs/data.enc` だけを公開し、平文の `docs/data.json` は削除されます
- ブラウザ側は WebCrypto（PBKDF2-SHA256 310,000回 + AES-GCM）で復号します。誤ったパスワードでは復号できません
- 入力したパスワードは同一タブ内でのみ記憶されます（タブを閉じると再入力）

**制限事項**:
- 全員で1つの共有パスワードです（ユーザー別認証ではありません）。パスワードを変更したら Secrets を更新して再実行してください
- ページのHTML/JS（レイアウト）自体は公開のままですが、データは含まれません
- リポジトリ自体が Public の場合、リポジトリ経由でCSV等が見えてしまうため、**リポジトリは Private を前提**とします
- ユーザー別認証・アクセスログが必要な場合は Cloudflare Access 等の外部ホスティングへの移行が必要です

### GitHub Pages の有効化

1. リポジトリの **Settings → Pages**
2. **Source** = 「Deploy from a branch」
3. **Branch** = `main`、フォルダ = **`/docs`** を選んで **Save**
4. 数分後、`https://<オーナー>.github.io/<リポジトリ>/` で公開されます

> GitHub Pages サイトのHTML/JSと暗号文はインターネット公開されますが、集計値と週報本文は
> `DASHBOARD_PASSWORD` がなければ復号できません。より強いユーザー別認証が必要な場合は外部ホスティング＋認証が必要です。

グラフ描画には Chart.js を `docs/vendor/` に同梱しており、外部CDNに依存しません。

## モデル使い分け

- 週報（推移・チャンネル・イベントの分析）: Sonnet
