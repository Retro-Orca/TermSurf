# Telnet テキストブラウザ（Playwright レンダリング）

このプロジェクトは、Telnet 経由で操作できる簡易テキストブラウザです。Web ページを Playwright（Chromium）で描画した後の DOM を取得し、端末座標にレイアウトして表示します。画像（`<img>` / CSS `background-image`）は ASCII アートとして表示できます。

## 主な機能
- **検索**
  - **推奨:** Google Custom Search JSON API（CSE）による検索（API キーは外部設定）
  - **代替:** Playwright による Google SERP 取得（スクレイピング）
- **描画**
  - Playwright のレンダリング後 DOM をスナップショットし、端末向けにレイアウト
- **画像**
  - Pillow で ASCII 化（WEBP 対応は Pillow のビルドに依存）
  - SVG は CairoSVG があれば PNG へ変換（任意）
- **リンク/画像一覧**
  - ページ末尾にリンク・画像 URL を番号付きで表示し、番号入力で遷移
- **任意: PHP 簡易サーバ**
  - `php start <dir> <port>` / `php stop`

## 依存関係
- Python 3.10+（推奨: 3.11）
- 必須:
  - playwright
  - requests
  - beautifulsoup4
- 推奨:
  - lxml（BeautifulSoup の高速パーサ）
  - pillow（ASCII 画像）
  - python-dotenv（`.env` 読み込み）
- 任意:
  - cairosvg（SVG→PNG 変換）

インストール例:
```bash
python -m pip install -U playwright requests beautifulsoup4 lxml pillow python-dotenv cairosvg
python -m playwright install chromium
```

## 起動方法
```bash
python server.py
```

既定では `127.0.0.1:2323` で待ち受けます。変更する場合:
- `TELNET_HOST`（既定: `127.0.0.1`）
- `TELNET_PORT`（既定: `2323`）

接続例:
```bash
telnet 127.0.0.1 2323
```

## 検索（API キーの外部設定）
**API キー等はコードに埋め込まれていません。** 環境変数または `.env` に設定してください。

### `.env` の例（推奨）
同じディレクトリに `.env` を作成し、以下を記入:
```ini
GOOGLE_API_KEY=YOUR_API_KEY
GOOGLE_CSE_ID=YOUR_CSE_ID
SEARCH_PROVIDER=auto
```

- `SEARCH_PROVIDER`
  - `auto`（既定）: API キーがあれば CSE、なければ Playwright
  - `cse`: CSE API を強制（`GOOGLE_API_KEY` / `GOOGLE_CSE_ID` 必須）
  - `playwright`: Playwright による SERP 取得を強制

`.env.example` を同梱しています（値は自分で設定してください）。

## 主なコマンド（Telnet 内）
コマンド名は英語のままです（互換性のため）。`help` で一覧を表示できます。

- `open N` : 検索結果 N を開く
- `follow N` : 現在ページのリンク N を開く
- `goto <url>` : URL を直接開く
- `reload` : 再読み込み
- `searchmode auto|cse|playwright` : 検索モード切替
- `ua pc|mobile` : User-Agent 切替
- `js on|off` : Playwright レイアウト ON/OFF
- `images on|off` : ASCII 画像の自動埋め込み ON/OFF
- `img list|all|N` : 画像一覧 / 全表示 / 指定表示
- `resolution 640x480|80x30` : 80x30 端末向けプリセット
- `php start <dir> <port>` / `php stop` : PHP 簡易サーバ
- `save [file]` : 現在表示テキストをファイル保存
- `clear` / `exit`

## 懸念される点（既知の制約・バグになり得るもの）
1. **Google SERP スクレイピングの不安定性**
   - Playwright で Google 検索結果を取得する方式は、レート制限・ブロック・同意画面などで失敗することがあります。
   - **対策:** CSE API の利用（推奨）、または `PW_HEADLESS=0` で挙動確認。

2. **ページ側の挙動による `networkidle` タイムアウト**
   - 広告・解析等でネットワーク通信が止まらないページでは `networkidle` がタイムアウトしやすいです。
   - **対策:** 本実装では失敗時に `domcontentloaded` へフォールバックします。

3. **環境差による依存関係**
   - `lxml` がない環境では `html.parser` にフォールバックします（精度・速度が落ちる場合があります）。
   - Pillow の WEBP 対応はビルド構成に依存します。

4. **Telnet の平文通信**
   - Telnet は暗号化されません。ローカル利用または閉域利用を前提にしてください。

## 設定（環境変数）
- `TEXT_BROWSER_TIMEOUT` : タイムアウト秒（既定: 20）
- `TEXT_BROWSER_WIDTH` : 表示幅（列数、既定: 110）
- `TEXT_ROW_ASPECT` : 行高補正（既定: 0.52）
- `LAYOUT_MAX_NODES` : DOM ノード最大数（既定: 800）
- `AUTO_IMG_MAX` : 自動 ASCII 画像の最大数（既定: 3）
- `ASCII_IMG_WIDTH` : ASCII 画像の幅（既定: 68）
- `FILTER_ICON_LINKS` : アイコン/バナーリンク除外（既定: 1）
- `PW_HEADLESS` : 0 で非ヘッドレス（既定: 1）

## 免責
外部サイトの取得・表示は、対象サイトの利用規約・robots.txt 等に従ってください。特に検索結果ページの取得は制限される可能性があります。
