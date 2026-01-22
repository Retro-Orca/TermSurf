# -*- coding: utf-8 -*-
"""
Telnet テキストブラウザ（Playwright 対応／Google 検索対応）

機能概要
- 検索:
  - Playwright による Google 検索結果ページ（SERP）取得（スクレイピング）
  - もしくは Google Custom Search JSON API（任意・推奨）
- 描画: Playwright（Chromium）でレンダリング後の DOM をスナップショットし、端末座標へレイアウト
- 画像: <img> と CSS background-image を ASCII 化（Pillow。WEBP 可。SVG は CairoSVG があれば PNG へ変換）
- 重複抑制: IoU（矩形の重なり）+ 抜粋の重複で、テキストの二重描画を抑制
- コマンド: 既定のコマンド名は英語（互換性維持）。ヘルプ表示などの説明文は日本語
- 任意: PHP の簡易サーバ起動（php start <dir> <port> / php stop）
- 端末プリセット: resolution 640x480（≒80x30）

注意
- Google の SERP スクレイピングは、レート制限・ブロックされる可能性があり、利用規約に抵触する場合があります。
  可能であれば Google Custom Search API（CSE）を利用してください（API キーはコードに埋め込まず、環境変数/.env で指定）。
"""


import os, re, io, textwrap, pathlib, webbrowser, socketserver, subprocess, importlib, importlib.util
from urllib.parse import urljoin, urldefrag, urlparse, quote_plus

# ---------------- 設定 / 定数 ----------------
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/118 Safari/537.36")
UA_MOBILE  = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/118 Mobile Safari/537.36")

TIMEOUT_SEC    = float(os.getenv("TEXT_BROWSER_TIMEOUT", "20"))
TERM_WIDTH     = int(os.getenv("TEXT_BROWSER_WIDTH",  "110"))   # columns
ROW_ASPECT     = float(os.getenv("TEXT_ROW_ASPECT",   "0.52"))  # row height scale vs width
MAX_NODES      = int(os.getenv("LAYOUT_MAX_NODES",    "800"))   # max DOM nodes per viewport
AUTO_IMG_MAX   = int(os.getenv("AUTO_IMG_MAX",        "3"))     # auto ASCII images per page
ASCII_IMG_W    = int(os.getenv("ASCII_IMG_WIDTH",     "68"))
FILTER_ICONS   = os.getenv("FILTER_ICON_LINKS", "1") == "1"     # filter icon/banner links

# ---------------- 検索プロバイダ設定 ----------------
# SEARCH_PROVIDER:
#   auto        : API キーがあれば CSE、なければ Playwright スクレイピングを使用
#   cse         : Google Custom Search JSON API を使用（GOOGLE_API_KEY / GOOGLE_CSE_ID が必須）
#   playwright  : Playwright で Google SERP を取得（スクレイピング）
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "auto").strip().lower()

# Google Custom Search JSON API（任意・推奨）
# 重要: API キー/CX はコードに埋め込まず、環境変数または .env に記載してください。
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GOOGLE_CSE_ID  = os.getenv("GOOGLE_CSE_ID", "").strip()  # Custom Search Engine ID (cx)

# ヘッドレス切替: PW_HEADLESS=0 で実ブラウザ表示（ヘッドレスがブロックされる場合の確認用）
PW_HEADLESS    = os.getenv("PW_HEADLESS", "1") == "1"

# レイアウト抽出で対象にする CSS セレクタ（ホワイトリスト）
SEL_WHITELIST  = os.getenv("LAYOUT_SELECTOR",
    "header,nav,main,article,section,aside,footer,div,figure,figcaption,"
    "h1,h2,h3,h4,h5,h6,p,ul,ol,li,a,button,input,textarea,select,table,th,td,span,img"
)

# ---------------- .env（任意） ----------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import requests
from bs4 import BeautifulSoup, UnicodeDammit

# 画像処理（Pillow）
try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# SVG→PNG 変換（任意: CairoSVG）。Windows の Cairo DLL 不足などにも耐えるようにする
try:
    _spec = importlib.util.find_spec("cairosvg")
    if _spec is not None:
        try:
            cairosvg = importlib.import_module("cairosvg")  # type: ignore
            HAVE_CAIROSVG = True
        except Exception:
            cairosvg = None
            HAVE_CAIROSVG = False
    else:
        cairosvg = None
        HAVE_CAIROSVG = False
except Exception:
    cairosvg = None
    HAVE_CAIROSVG = False

# Playwright（必須）
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:
    HAVE_PW = False

# ---------------- ANSI ----------------
CSI = "\x1b["
CLEAR_SCREEN = f"{CSI}2J{CSI}H"

# ---------------- ユーティリティ ----------------
def to_unicode(html_bytes: bytes, fallback_text: str = "") -> str:
    dammit = UnicodeDammit(html_bytes, is_html=True)
    return dammit.unicode_markup or fallback_text

def compress_blank(s: str) -> str:
    lines, prev = [], False
    for line in s.splitlines():
        blank = (line.strip() == "")
        if blank and prev: continue
        lines.append(line); prev = blank
    return "\n".join(lines)

ASCII_RAMP = "@%#*+=-:. "
def img_bytes_to_ascii(b: bytes, width: int = ASCII_IMG_W) -> str:
    if not HAVE_PIL:
        return "(Pillow not installed; ASCII image disabled)\n"
    try:
        im = Image.open(io.BytesIO(b)).convert("L")
        w = max(20, int(width))
        h = max(1, int(im.height * (w / im.width) * ROW_ASPECT))
        im = im.resize((w, h))
        pixels = im.getdata()
        chars = "".join(ASCII_RAMP[int(p/255*(len(ASCII_RAMP)-1))] for p in pixels)
        lines = [chars[i:i+w] for i in range(0, len(chars), w)]
        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"(ASCII conversion error: {e} / If WEBP, ensure Pillow has WEBP support)\n"

def fetch_bytes(session: requests.Session, url: str, referer: str | None) -> tuple[bytes,str]:
    r = session.get(url, timeout=TIMEOUT_SEC, headers={
        "User-Agent": session.headers.get("User-Agent", UA_DESKTOP),
        "Referer": referer or url,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }, stream=True)
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").lower()
    data = r.content
    if ("svg" in ctype or url.lower().endswith(".svg")) and HAVE_CAIROSVG and cairosvg:
        try:
            data = cairosvg.svg2png(bytestring=data)  # type: ignore[union-attr]
            ctype = "image/png"
        except Exception:
            pass
    return data, ctype

# 重なり抑制のための矩形 IoU
def rect_iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = aw * ah
    area_b = bw * bh
    return inter / max(1, (area_a + area_b - inter))

# ---------------- Playwright: Google 検索 ----------------
def playwright_google_search(query: str, num: int = 10, ua: str = UA_DESKTOP, timeout: float = TIMEOUT_SEC):
    """
    Playwright で Google の検索結果ページ（SERP）を取得し、[{title, link, snippet}, ...] を返します。

    注意: スクレイピングはブロックされる可能性があり、利用規約に抵触する場合があります。
    """
    if not HAVE_PW:
        raise RuntimeError("Playwright が見つかりません")

    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&gl=JP&num=20&pws=0&safe=off"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=PW_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = browser.new_page(
            user_agent=ua,
            viewport={"width": 1280, "height": 1600}
        )
        # できるだけ日本語の応答を優先
        page.set_extra_http_headers({"Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"})
        # webdriver フラグを隠す（ベストエフォート）
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

        page.goto(url, wait_until="domcontentloaded", timeout=timeout*1000)

        # 同意ダイアログがあれば同意（存在する場合のみ）
        for sel in [
            'button:has-text("同意")',
            'button:has-text("同意して続行")',
            'button:has-text("I agree")',
            '#L2AGLb',
            'form[action*="consent"] [type="submit"]'
        ]:
            try:
                page.locator(sel).first.click(timeout=1500)
                page.wait_for_timeout(300)
                break
            except Exception:
                pass

        try:
            page.wait_for_selector("#search", timeout=timeout*1000)
        except Exception:
            pass

        # raw 文字列で \s 警告を避けつつ、広めのセレクタで結果を抽出
        items = page.evaluate(r"""
            () => {
              const out=[];
              const push = (a,t,s) => {
                if(!a||!t) return; const h=a.href||'';
                if(!h) return;
                if(h.includes('/search?') || h.includes('webcache.googleusercontent.com')) return;
                if(h.includes('policies.google.') || h.includes('accounts.google.')) return;
                out.push({title:t.trim(), link:h, snippet:(s||'').trim()});
              };

              // Strategy 1: typical result blocks
              document.querySelectorAll('#search .g, #search .tF2Cxc, #search .MjjYud').forEach(el=>{
                const a = el.querySelector('a'); const h3 = el.querySelector('h3');
                const sn = el.querySelector('.VwiC3b, .IsZvec, .aCOpRe, .MUxGbd')?.innerText;
                if (a && h3) push(a, h3.textContent||'', sn);
              });

              // Strategy 2: a > h3 within #search
              if (out.length < 5) {
                document.querySelectorAll('#search a h3').forEach(h3=>{
                  const a = h3.closest('a');
                  const cont = h3.closest('.g, .MjjYud, .tF2Cxc');
                  const sn = cont?.querySelector('.VwiC3b, .IsZvec, .aCOpRe, .MUxGbd')?.innerText;
                  if (a) push(a, h3.textContent||'', sn);
                });
              }

              // Strategy 3: last resort (links that contain h3)
              if (out.length < 3) {
                document.querySelectorAll('#search a').forEach(a=>{
                  const h3 = a.querySelector('h3'); if(!h3) return;
                  push(a, h3.textContent||'', '');
                });
              }
              return out;
            }
        """)
        browser.close()

    results=[]
    seen=set()
    for it in items or []:
        t=(it.get('title') or '').strip()
        l=(it.get('link') or '').strip()
        s=(it.get('snippet') or '').strip()
        if not t or not l: continue
        if l in seen: continue
        seen.add(l)
        results.append({'title': t, 'link': l, 'snippet': s})
        if len(results) >= num: break
    return results


def google_cse_search(
    query: str,
    num: int = 10,
    *,
    api_key: str | None = None,
    cse_id: str | None = None,
    session: requests.Session | None = None,
    timeout: float = TIMEOUT_SEC,
):
    """
    Google Custom Search JSON API を使って検索します（推奨）。

    - API キー（GOOGLE_API_KEY）と、検索エンジン ID（GOOGLE_CSE_ID / cx）が必要です。
    - API キーはコードに埋め込まず、環境変数または .env で指定してください。
    """
    api_key = (api_key or GOOGLE_API_KEY).strip()
    cse_id = (cse_id or GOOGLE_CSE_ID).strip()
    if not api_key or not cse_id:
        raise RuntimeError("Google CSE API を使うには GOOGLE_API_KEY と GOOGLE_CSE_ID（cx）を設定してください。")

    sess = session or requests.Session()
    url = "https://www.googleapis.com/customsearch/v1"
    n = max(1, min(10, int(num)))  # API の num は 1..10
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": n,
        "hl": "ja",
        "gl": "JP",
        "safe": "off",
    }
    r = sess.get(url, params=params, timeout=timeout, headers={
        "User-Agent": sess.headers.get("User-Agent", UA_DESKTOP),
        "Accept": "application/json",
    })
    r.raise_for_status()
    data = r.json()

    results: list[dict] = []
    for it in (data.get("items") or []):
        title = (it.get("title") or "").strip()
        link = (it.get("link") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        if title and link:
            results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= num:
            break
    return results

    return results

# ---------------- Playwright: DOM スナップショット（レイアウト用） ----------------
def snapshot_dom(url: str, ua: str, viewport_w=1200, viewport_h=1800, timeout=TIMEOUT_SEC):
    """
    Playwright で JS 実行後の DOM ノードを収集し、座標/サイズ/z-index/テキスト/画像情報を返します。
    """
    if not HAVE_PW:
        raise RuntimeError("Playwright が見つかりません")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=PW_HEADLESS)
        page = browser.new_page(user_agent=ua, viewport={"width": viewport_w, "height": viewport_h})
        page.set_extra_http_headers({"Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"})
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        # networkidle はサイトによってはタイムアウトしやすいので、失敗時は domcontentloaded にフォールバック
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout*1000)
        except Exception:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout*1000)
            page.wait_for_timeout(300)
        final_url = page.url

        total_h = min(int(page.evaluate("() => document.body.scrollHeight || document.documentElement.scrollHeight || 3000")), 8000)
        chunks = []
        y = 0
        step = viewport_h - 100
        while y < total_h and len(chunks) < 10:
            page.evaluate("yy => window.scrollTo(0, yy)", y)
            page.wait_for_timeout(100)
            part = page.evaluate(r"""
                ({ sel, maxn }) => {
                    const L = [];
                    const els = Array.from(document.querySelectorAll(sel)).slice(0, maxn);
                    const W = window.innerWidth; const H = window.innerHeight; const Y = window.scrollY;
                    for (const el of els) {
                        const st = getComputedStyle(el);
                        if (st.visibility==='hidden' || st.display==='none') continue;
                        const r = el.getBoundingClientRect();
                        const x = Math.max(0, Math.round(r.left));
                        const y = Math.max(0, Math.round(r.top + Y));
                        const w = Math.max(0, Math.round(r.width));
                        const h = Math.max(0, Math.round(r.height));
                        if (w<2 || h<2) continue;
                        let txt = '';
                        if (el.tagName.toLowerCase()!=='img') {
                            txt = (el.innerText||'').replace(/\s+/g,' ').trim().slice(0, 2000);
                        }
                        let bg = st.backgroundImage && st.backgroundImage.startsWith('url(') ? st.backgroundImage : '';
                        let z = parseInt(st.zIndex) || 0;
                        let fw = st.fontWeight||'';
                        let tag = el.tagName.toLowerCase();
                        let href = (el.closest('a') && el.closest('a').href) || '';
                        let src = (tag==='img' && el.src) || '';
                        let disp = st.display || '';
                        L.push({tag, x, y, w, h, z, txt, href, src, bg, fw, disp});
                    }
                    return {L, W, Y, H};
                }
            """, {"sel": SEL_WHITELIST, "maxn": MAX_NODES//3})
            chunks.append(part)
            y += step

        browser.close()

        nodes = []
        for c in chunks:
            nodes.extend(c["L"])
        nodes.sort(key=lambda n: (n["z"], n["y"], n["x"], n["h"]*n["w"]))
        return nodes, final_url

# ---------------- 端末キャンバス ----------------
class TermCanvas:
    def __init__(self, width_cols: int):
        self.W = width_cols
        self.lines = []

    def _ensure_rows(self, rows: int):
        while len(self.lines) < rows:
            self.lines.append([" "] * self.W)

    def draw_text_block(self, top_row: int, left_col: int, width_cols: int, text: str, bold=False):
        if width_cols <= 5 or not text: return
        wrapped = []
        for para in text.splitlines():
            para = para.rstrip()
            if not para:
                wrapped.append("")
                continue
            wrapped.extend(textwrap.wrap(para, width=width_cols, break_long_words=False, replace_whitespace=False) or [""])
        r = top_row
        for line in wrapped:
            self._ensure_rows(r+1)
            s = ("**" + line + "**") if bold else line
            s = s[:width_cols]
            for i,ch in enumerate(s):
                c = left_col + i
                if 0 <= c < self.W:
                    self.lines[r][c] = ch
            r += 1

    def draw_ascii_image(self, top_row:int, left_col:int, ascii_art:str, max_w:int):
        if not ascii_art: return
        rows = ascii_art.splitlines()
        for i, row in enumerate(rows):
            self._ensure_rows(top_row + i + 1)
            s = row[:max_w]
            for j, ch in enumerate(s):
                c = left_col + j
                if 0 <= c < self.W:
                    self.lines[top_row + i][c] = ch

    def render(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.lines)

# ---------------- ブラウザセッション ----------------
class BrowserSession:
    def __init__(self):
        self.req = requests.Session()
        self.req.headers.update({"User-Agent": UA_DESKTOP})
        self.width = TERM_WIDTH
        self.js_mode = True          # Use Playwright layout by default
        self.auto_image = True       # Inline ASCII images by default
        self.filter_icons = FILTER_ICONS
        self.current_url = None
        self.full_text = ""
        self.raw_html = ""
        self.links = []
        self.images = []
        self.history = []
        self.future = []
        self.last_query = None
        self.last_results = []
        self.php_proc = None
        self.search_provider = SEARCH_PROVIDER  # auto|cse|playwright
        self.last_search_provider = None

    def _is_icon_link(self, href: str, text: str) -> bool:
        p = urlparse(href).path.lower()
        if any(p.endswith(ext) for ext in (".png",".jpg",".jpeg",".gif",".webp",".svg")):
            if any(k in p for k in ("icon","bnr","banner","logo","sns","insta","fb","tiktok","x_","line")):
                return (not text or len(text) <= 1)
        return False

    def _ascii_for(self, url: str) -> str:
        try:
            data, ctype = fetch_bytes(self.req, url, self.current_url)
            if ("svg" in (ctype or "")) or url.lower().endswith(".svg"):
                if not (HAVE_CAIROSVG and cairosvg):
                    return f"(SVG image skipped: CairoSVG not available) {url}\n"
            return img_bytes_to_ascii(data, width=ASCII_IMG_W)
        except Exception as e:
            return f"(Image fetch error: {e})\n"

    def _should_draw_text_node(self, n: dict) -> bool:
        txt = (n.get("txt") or "").strip()
        if not txt: return False
        tag = (n.get("tag") or "").lower()
        disp = (n.get("disp") or "").lower()
        if tag in ("h1","h2","h3","h4","h5","h6","p","li","figcaption","td","th"):
            return True
        if tag in ("button","label"):
            return True
        if tag == "a":
            return len(txt) >= 5
        if tag in ("div","span","section","article"):
            if disp == "inline":
                return False
            return len(txt) >= 30
        return False

    def _render_fallback_url(self, url: str) -> str:
        # JS を使わないフォールバック（requests + BeautifulSoup）
        r = self.req.get(url, timeout=TIMEOUT_SEC, allow_redirects=True)
        self.current_url = r.url
        html = to_unicode(r.content, r.text)
                # lxml が未導入の場合に備えてフォールバック
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script","style","noscript","svg","iframe"]): tag.decompose()

        imgs, links = [], []
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src: continue
            src = urljoin(self.current_url, src); src, _ = urldefrag(src)
            if src not in imgs:
                imgs.append(src)
        for a in soup.find_all("a"):
            href = a.get("href")
            if not href: continue
            href = urljoin(self.current_url, href); href, _ = urldefrag(href)
            t = a.get_text(strip=True)
            if self.filter_icons and self._is_icon_link(href, t or ""):
                continue
            if href not in links:
                links.append(href)

        text = soup.get_text("\n", strip=True)
        text = compress_blank(text)
        if links:
            text += "\n\n-- Links --\n" + "\n".join(f"[{i}] <{h}>" for i,h in enumerate(links,1))
        if imgs:
            text += "\n\n-- Images --\n" + "\n".join(f"[{i}] <{s}>" for i,s in enumerate(imgs,1))

        if self.auto_image and imgs:
            text += "\n\n-- Images (ASCII, auto) --\n"
            for i, src in enumerate(imgs[:AUTO_IMG_MAX], 1):
                art = self._ascii_for(src)
                text += f"[IMG {i}] {src}\n{art}\n"

        self.full_text = text
        self.links = [{"text":"","href":h} for h in links]
        self.images = [{"src":s,"alt":""} for s in imgs]
        return self.full_text

    def load_url(self, url: str) -> str:
        # history
        if self.current_url:
            self.history.append(self.current_url); self.future.clear()

        if self.js_mode and HAVE_PW:
            try:
                nodes, final_url = snapshot_dom(url, self.req.headers.get("User-Agent", UA_DESKTOP))
            except Exception:
                return self._render_fallback_url(url)

            if not nodes:
                return self._render_fallback_url(url)

            self.current_url = final_url
            vp_w = max([n["x"] + n["w"] for n in nodes] + [1200])
            scale_x = self.width / vp_w
            scale_y = scale_x * ROW_ASPECT

            canvas = TermCanvas(self.width)
            link_map, image_map = [], []
            link_seen, img_seen = set(), set()
            auto_img_count = 0
            placed_text_rects = []

            for n in nodes:
                x = int(n["x"] * scale_x)
                y = int(n["y"] * scale_y)
                w = max(6, int(n["w"] * scale_x))
                h = max(1, int(n["h"] * scale_y))
                bold = (n["fw"] in ("700","800","900","bold") or n["tag"] in ("h1","h2","h3"))

                # image blocks
                if n["tag"] == "img" and n.get("src"):
                    fullsrc = n["src"]
                    if fullsrc not in img_seen:
                        img_seen.add(fullsrc)
                        image_map.append(fullsrc)
                    idx = image_map.index(fullsrc) + 1
                    canvas.draw_text_block(y, x, w, f"[IMG {idx}]")
                    if self.auto_image and auto_img_count < AUTO_IMG_MAX:
                        ascii_art = self._ascii_for(fullsrc)
                        canvas.draw_ascii_image(y+1, x, ascii_art, min(w, ASCII_IMG_W))
                        auto_img_count += 1
                    continue

                # CSS background-image
                if n.get("bg", "").startswith("url("):
                    url_in = n["bg"][4:-1].strip().strip('"').strip("'")
                    full = urljoin(self.current_url or url, url_in)
                    if full not in img_seen:
                        img_seen.add(full)
                        image_map.append(full)
                    idx = image_map.index(full) + 1
                    canvas.draw_text_block(y, x, w, f"[IMG {idx}]")
                    if self.auto_image and auto_img_count < AUTO_IMG_MAX:
                        ascii_art = self._ascii_for(full)
                        canvas.draw_ascii_image(y+1, x, ascii_art, min(w, ASCII_IMG_W))
                        auto_img_count += 1

                # text blocks (with dedup)
                txt = n.get("txt","")
                if txt and self._should_draw_text_node(n):
                    snippet = txt[:24]
                    skip = False
                    for (rx, ry, rw, rh, sn) in placed_text_rects:
                        if rect_iou((x,y,w,h), (rx,ry,rw,rh)) > 0.65 and (snippet in sn or sn in snippet):
                            skip = True
                            break
                    if skip:
                        continue

                    href = n.get("href") or ""
                    if href and not (self.filter_icons and self._is_icon_link(href, txt)):
                        if href not in link_seen:
                            link_seen.add(href)
                            link_map.append(href)
                        idx = link_map.index(href) + 1
                        txt = f"{txt} [{idx}]"

                    canvas.draw_text_block(y, x, w, txt, bold=bold)
                    placed_text_rects.append((x,y,w,h,snippet))

            lines = [f"# {self.current_url}"]
            if link_map:
                lines.append("\n-- Links --")
                for i, href in enumerate(link_map, 1):
                    lines.append(f"[{i}] <{href}>")
            if image_map:
                lines.append("\n-- Images --")
                for i, src in enumerate(image_map, 1):
                    lines.append(f"[{i}] <{src}>")
            body = canvas.render() + "\n" + "\n".join(lines) + "\n"
            self.full_text = compress_blank(body)
            self.links = [{"text":"","href":h} for h in link_map]
            self.images = [{"src":s, "alt":""} for s in image_map]
            return self.full_text

        # fallback (should rarely happen if js_mode ON)
        return self._render_fallback_url(url)

    def open_result(self, n: int) -> str:
        if 1 <= n <= len(self.last_results):
            return self.load_url(self.last_results[n-1]["link"])
        return "（無効な検索結果番号です）\n"

    def open_link(self, n: int) -> str:
        if 1 <= n <= len(self.links):
            return self.load_url(self.links[n-1]["href"])
        return "（無効なリンク番号です）\n"


    def search(self, query: str, num: int = 10) -> list[dict]:
        """
        検索を実行します。

        SEARCH_PROVIDER=auto の場合:
          - GOOGLE_API_KEY と GOOGLE_CSE_ID があれば CSE API
          - なければ Playwright（スクレイピング）
        """
        provider = (self.search_provider or "auto").strip().lower()
        if provider in ("", "auto"):
            provider = "cse" if (GOOGLE_API_KEY and GOOGLE_CSE_ID) else "playwright"

        if provider in ("cse", "google_cse"):
            self.last_search_provider = "cse"
            return google_cse_search(query, num=num, session=self.req, timeout=TIMEOUT_SEC)

        if provider in ("playwright", "pw"):
            self.last_search_provider = "playwright"
            if not HAVE_PW:
                raise RuntimeError("Playwright が見つかりません。pip で playwright を導入し、chromium をインストールしてください。")
            return playwright_google_search(
                query,
                num=num,
                ua=self.req.headers.get("User-Agent", UA_DESKTOP),
                timeout=TIMEOUT_SEC,
            )

        raise RuntimeError(f"不明な SEARCH_PROVIDER: {provider}（auto|cse|playwright）")

    # Optional: PHP helper server
    def php_start(self, directory: str, port: int) -> str:
        if self.php_proc and self.php_proc.poll() is None:
            return "(PHP server already running)\n"
        try:
            creationflags = 0
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            self.php_proc = subprocess.Popen(
                ["php", "-S", f"localhost:{port}", "-t", directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
            return f"PHP server: http://localhost:{port}/  (docroot: {directory})\n"
        except Exception as e:
            return f"(PHP start error: {e})\n"

    def php_stop(self) -> str:
        if self.php_proc and self.php_proc.poll() is None:
            self.php_proc.terminate()
            self.php_proc = None
            return "PHP server stopped.\n"
        return "(No PHP server running)\n"

# ---------------- Telnet Plumbing ----------------
IAC, WILL, WONT, DO, DONT, SE, SB = 255, 251, 252, 253, 254, 240, 250
ECHO, SGA, LINEMODE = 1, 3, 34
def negotiate_echo(wfile):
    try:
        wfile.write(bytes([IAC, WONT, ECHO]))
        wfile.write(bytes([IAC, WILL, SGA]))
        wfile.write(bytes([IAC, WONT, LINEMODE]))
        wfile.flush()
    except Exception:
        pass

class TelnetHandler(socketserver.StreamRequestHandler):
    def send(self, s: str):
        self.wfile.write(s.replace("\n","\r\n").encode("utf-8","ignore"))
    def clear(self):
        try:
            self.wfile.write(CLEAR_SCREEN.encode("ascii","ignore"))
            self.wfile.flush()
        except Exception:
            pass

    def handle(self):
        global ROW_ASPECT, ASCII_IMG_W

        S = BrowserSession()
        try:
            negotiate_echo(self.wfile)
            self.send(
                "=== Telnet テキストブラウザ ===\r\n"
                "検索語を入力して Enter、またはコマンドを入力してください。数字のみ入力すると検索結果/リンクを開きます。\r\n"
                "コマンド一覧（コマンド名は英語のまま）:\r\n"
                "  open N            -> 検索結果 N を開く\r\n"
                "  follow N          -> 現在ページのリンク N を開く\r\n"
                "  goto <url>        -> URL を開く\r\n"
                "  reload            -> 現在ページを再読み込み\r\n"
                "  open-external [N] -> 既定ブラウザで開く（省略時: 現在ページ）\r\n"
                "  save [file]       -> 表示テキストを保存\r\n"
                "  ua pc|mobile      -> User-Agent 切替\r\n"
                "  js on|off         -> Playwright レイアウト ON/OFF（推奨: on）\r\n"
                "  width NN          -> 端末幅（60-200 列）\r\n"
                "  linkfilter on|off -> アイコン/バナー系リンクを除外\r\n"
                "  images on|off     -> ASCII 画像の自動埋め込み\r\n"
                "  img width NN      -> ASCII 画像の幅（列数）\r\n"
                "  img list|all|N    -> 画像一覧/全表示/N 番目表示\r\n"
                "  resolution 640x480|80x30 -> 80x30 端末向けプリセット\r\n"
                "  php start <dir> <port> / php stop -> PHP 簡易サーバの起動/停止\r\n"
                "  help, clear, exit\r\n"
                "\r\n"
            )
            while True:
                state = (f"[W:{S.width} JS:{'ON' if S.js_mode else 'OFF'} IMG:{'ON' if S.auto_image else 'OFF'} "
                         f"Filter:{'ON' if S.filter_icons else 'OFF'}] > ")
                self.send(state); self.wfile.flush()
                line = self.rfile.readline()
                if not line: break
                cmd = line.decode("utf-8","ignore").strip()
                if not cmd: continue
                low = cmd.lower()

                # Exit / Help / Clear
                if low in ("exit","quit","q"): break
                if low in ("help","?"):
                    self.send("上部のコマンド一覧を参照してください。\n\n"); continue
                if low in ("clear","cls"):
                    self.clear(); continue

                # Settings
                if low.startswith("ua "):
                    mode = low.split()[1]
                    S.req.headers["User-Agent"] = UA_DESKTOP if mode in ("pc","desktop") else UA_MOBILE
                    self.send("User-Agent を切り替えました。\n\n"); continue
                if low in ("js on","js off"):
                    S.js_mode = (low.endswith("on"))
                    if S.js_mode and not HAVE_PW:
                        self.send("(Playwright が未導入です: python -m pip install playwright && python -m playwright install chromium)\n\n")
                    else:
                        self.send(f"JS/CSS レイアウト: {'ON' if S.js_mode else 'OFF'}\n\n")

                    continue

                # 検索モード切替（auto|cse|playwright）
                if low.startswith("searchmode "):
                    try:
                        mode = low.split()[1].strip().lower()
                        if mode == "pw":
                            mode = "playwright"
                        if mode not in ("auto", "cse", "playwright"):
                            self.send("Usage: searchmode auto|cse|playwright\\n\\n")
                            continue
                        S.search_provider = mode
                        self.send(f"検索モード: {mode}\\n\\n")
                    except Exception:
                        self.send("Usage: searchmode auto|cse|playwright\\n\\n")
                    continue
                if low.startswith("width "):
                    try:
                        S.width = max(60, min(200, int(cmd.split()[1]))); self.send(f"幅={S.width}\n\n")
                    except Exception: self.send("使い方: width NN（60-200）\n\n")
                    continue
                if low in ("linkfilter on","linkfilter off"):
                    S.filter_icons = low.endswith("on"); self.send(f"リンクフィルタ: {'ON' if S.filter_icons else 'OFF'}\n\n"); continue
                if low in ("images on","images off"):
                    S.auto_image = low.endswith("on"); self.send(f"ASCII 画像自動表示: {'ON' if S.auto_image else 'OFF'}\n\n"); continue

                # ASCII image width
                if low.startswith("img width"):
                    try:
                        n = int(cmd.split()[-1])
                        ASCII_IMG_W = max(20, min(200, n))
                        self.send(f"ASCII 画像幅={ASCII_IMG_W}\n\n")
                    except Exception:
                        self.send("使い方: img width NN\n\n")
                    continue

                # Resolution preset
                if low.startswith("resolution "):
                    try:
                        mode = cmd.split()[1].lower()
                        if mode in ("640x480","80x30"):
                            S.width = 80
                            ROW_ASPECT = 0.5
                            ASCII_IMG_W = 60
                            self.send("プリセット適用: 640x480（80x30）\n\n")
                        else:
                            self.send("使い方: resolution 640x480 | 80x30\n\n")
                    except Exception:
                        self.send("Usage: resolution 640x480\n\n")
                    continue

                # PHP helper
                if low.startswith("php "):
                    parts = cmd.split()
                    if len(parts) >= 2 and parts[1] in ("start","up","run"):
                        try:
                            d = parts[2]; p = int(parts[3])
                            self.send(S.php_start(d, p)); continue
                        except Exception:
                            self.send("使い方: php start <dir> <port>\n\n"); continue
                    if len(parts) >= 2 and parts[1] in ("stop","down","kill"):
                        self.send(S.php_stop()); continue

                # Numeric only => open result or link (result takes precedence)
                if re.fullmatch(r"\d{1,4}", cmd):
                    n = int(cmd); self.clear()
                    if S.last_results and 1 <= n <= len(S.last_results):
                        out = S.open_result(n)
                        self.send(out + ("\n" if not out.endswith("\n") else ""))
                        continue
                    if 1 <= n <= len(S.links):
                        out = S.open_link(n)
                        self.send(out + ("\n" if not out.endswith("\n") else ""))
                        continue
                    self.send("（無効な番号です）\n\n"); continue

                # Explicit
                if low.startswith("open "):
                    try:
                        n = int(cmd.split()[1]); self.clear(); self.send(S.open_result(n) + "\n")
                    except Exception as e: self.send(f"オープンエラー: {e}\n\n")
                    continue
                if low.startswith("follow "):
                    try:
                        n = int(cmd.split()[1]); self.clear(); self.send(S.open_link(n) + "\n")
                    except Exception as e: self.send(f"追従エラー: {e}\n\n")
                    continue

                # Navigation / Reload / External / Save
                if low.startswith("goto "):
                    url = cmd.split(maxsplit=1)[1]; self.clear(); self.send(S.load_url(url) + "\n"); continue
                if low == "reload":
                    if not S.current_url:
                        self.send("（再読み込みするページがありません）\n\n"); continue
                    self.clear(); self.send(S.load_url(S.current_url) + "\n"); continue
                if low.startswith("open-external"):
                    try:
                        url = S.current_url
                        parts = cmd.split()
                        if len(parts)==2 and parts[1].isdigit():
                            n = int(parts[1])
                            if S.last_results and 1<=n<=len(S.last_results):
                                url = S.last_results[n-1]["link"]
                            elif 1<=n<=len(S.links):
                                url = S.links[n-1]["href"]
                        if not url:
                            self.send("（外部ブラウザで開く対象がありません）\n\n"); continue
                        webbrowser.open(url); self.send("既定のブラウザで開きました。\n\n")
                    except Exception as e: self.send(f"外部オープンエラー: {e}\n\n")
                    continue
                if low.startswith("save"):
                    name = (cmd.split(maxsplit=1)[1] if " " in cmd else "page.txt")
                    p = pathlib.Path(name); p.write_text(S.full_text or "", encoding="utf-8", errors="ignore")
                    self.send(f"保存しました: {p.resolve()}\n\n"); continue

                # Image manual show
                if low.startswith("img "):
                    tok = cmd.split(maxsplit=1)[1] if " " in cmd else ""
                    if tok in ("list","ls"):
                        if not S.images: self.send("画像はありません。\n\n"); continue
                        for i, im in enumerate(S.images, 1):
                            self.send(f"[{i}] <{im['src']}>\n")
                        self.send("\n"); continue
                    if tok in ("all","*"):
                        if not S.images: self.send("画像はありません。\n\n"); continue
                        self.clear()
                        for i, im in enumerate(S.images, 1):
                            self.send(f"[IMG {i}] {im['src']}\n{S._ascii_for(im['src'])}\n")
                        continue
                    m = re.fullmatch(r"(\d{1,4})", tok)
                    if m:
                        i = int(m.group(1))
                        if not (1<=i<=len(S.images)): self.send("無効な画像番号です。\n\n"); continue
                        self.clear(); self.send(f"[IMG {i}] {S.images[i-1]['src']}\n{S._ascii_for(S.images[i-1]['src'])}\n"); continue
                    self.send("使い方: img list | img all | img N | img width NN\n\n"); continue

                # ---------- SEARCH (default action) ----------
                try:
                    query = cmd if not low.startswith("search ") else cmd.split(maxsplit=1)[1]
                    S.last_query = query
                    if not HAVE_PW:
                        self.send("(Playwright が未導入です: python -m pip install playwright && python -m playwright install chromium)\n\n")
                        continue
                    S.last_results = S.search(query, num=10)
                    self.clear()
                    if not S.last_results:
                        self.send("結果がありません。\n\n"); continue
                    prov = (S.last_search_provider or "unknown")
                    prov_label = "Google (CSE API)" if prov=="cse" else ("Google (Playwright)" if prov=="playwright" else prov)
                    buf = [f"# [検索結果] {S.last_query}  ({prov_label})"]
                    for i, r in enumerate(S.last_results, 1):
                        buf.append(f"{i}. {r['title']}\n   {r['link']}\n   {r.get('snippet','')}\n")
                    buf.append("ヒント: 数字のみ入力で開く／直接開く場合は goto <url>。")
                    self.send("\n".join(buf) + "\n")
                except Exception as e:
                    self.send(f"検索エラー: {e}\n\n")

        finally:
            try:
                self.send("終了します。\n")
            except Exception:
                pass

# ---------------- Entry ----------------
if __name__ == "__main__":
    host = os.getenv("TELNET_HOST", "127.0.0.1")
    port = int(os.getenv("TELNET_PORT", "2323"))
    with socketserver.ThreadingTCPServer((host, port), TelnetHandler) as server:
        server.daemon_threads = True
        server.allow_reuse_address = True
        print(f"Telnet テキストブラウザを起動しました: {host}:{port} （停止: Ctrl+C）")
        server.serve_forever()
