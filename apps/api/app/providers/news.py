"""Market / holdings / interests news — EM + Sina + 同花顺."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_MARKET_CACHE: dict[str, tuple[float, list["NewsItem"]]] = {}
_MARKET_TTL = 60.0
_SYMBOL_CACHE: dict[str, tuple[float, list["NewsItem"]]] = {}
_SYMBOL_TTL = 90.0

_FEED_CAP = 100
_HOLDINGS_SYMBOL_CAP = 12
_PER_SYMBOL_LIMIT = 40
_PER_INTEREST_LIMIT = 100

# Market boards: 要闻 = 7×24 + 多源；其余 = EM 栏目/关键词（并补搜索以丰富来源）
MARKET_BOARDS: list[dict[str, str]] = [
    {"id": "headline", "label": "要闻", "kind": "fast", "value": "102"},
    {"id": "tech", "label": "科技", "kind": "column", "value": "360"},
    {"id": "agri", "label": "农业", "kind": "keyword", "value": "农业"},
    {"id": "auto", "label": "汽车", "kind": "column", "value": "358"},
    {"id": "estate", "label": "地产", "kind": "column", "value": "359"},
    {"id": "energy", "label": "能源", "kind": "column", "value": "356"},
    {"id": "industry", "label": "产经", "kind": "column", "value": "355"},
    {"id": "finance", "label": "金融", "kind": "column", "value": "371"},
    {"id": "company", "label": "公司", "kind": "column", "value": "354"},
]
_BOARD_BY_ID = {b["id"]: b for b in MARKET_BOARDS}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://kuaixun.eastmoney.com/",
}

_EM_TAG_RE = re.compile(r"</?em>", re.I)
_JSONP_RE = re.compile(r"^[^(]+\((.*)\)\s*$", re.S)
_ARTICLE_CODE_RE = re.compile(r"(?:/a/|postid=|=)(\d{10,})", re.I)
_ARTICLE_CACHE: dict[str, tuple[float, "NewsArticle"]] = {}
_ARTICLE_TTL = 300.0


@dataclass
class NewsItem:
    id: str
    title: str
    summary: str
    source: str
    published_at: str
    url: str
    symbols: list[str] = field(default_factory=list)


@dataclass
class NewsArticle:
    id: str
    title: str
    body: str
    source: str
    published_at: str
    url: str
    images: list[str] = field(default_factory=list)


def _strip_em(text: str) -> str:
    return _EM_TAG_RE.sub("", text or "").strip()


def _em_article_code(id_or_url: str) -> str:
    """East Money article codes are long (typically 18 digits). Reject short numeric ids (e.g. 同花顺)."""
    s = (id_or_url or "").strip()
    if not s:
        return ""
    m = _ARTICLE_CODE_RE.search(s)
    if m:
        return m.group(1)
    # Bare id: EM codes are 16+ digits (often YYYYMMDD…)
    if re.fullmatch(r"\d{16,}", s):
        return s
    return ""


def _abs_url(src: str, base: str) -> str:
    s = (src or "").strip()
    if not s or s.startswith("data:"):
        return ""
    if s.startswith("//"):
        return "https:" + s
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/") and base:
        from urllib.parse import urljoin

        return urljoin(base, s)
    return s


_AD_IMG_HINTS = (
    "qrcode",
    "qr_code",
    "ewm",
    "erweima",
    "weibo",
    "weixin",
    "wechat",
    "wxcode",
    "logo",
    "icon",
    "emoji",
    "pixel",
    "sprite",
    "ad_",
    "_ad",
    "/ad/",
    "ads.",
    "banner",
    "promo",
    "share",
    "follow",
    "subscribe",
    "appdown",
    "download",
    "tousu",
    "complaint",
    "vip",
    "member",
    "sinaimg.cn/large/.*qr",
    "n.sinaimg.cn/finance/.*qr",
    ".svg",
    "1x1",
    "blank",
)

_PROMO_HTML_CUTS = (
    r'<div[^>]*(?:class|id)="[^"]*(?:qrcode|ewm|share|weibo|follow|app-down|advertise|ad-box| mag|append|bottom-bar)[^"]*"[^>]*>[\s\S]*?</div>',
    r'<p[^>]*>[\s\S]*?(?:扫码|关注官方|投诉维权|下载APP|点击关注)[\s\S]*?</p>',
)


def _strip_promo_html(html: str) -> str:
    text = html or ""
    for pat in _PROMO_HTML_CUTS:
        text = re.sub(pat, "", text, flags=re.I)
    # Drop blocks that are mostly images with 扫码/关注 nearby
    text = re.sub(
        r"(?:扫码关注|官方微博|投诉维权|下载新浪|关注我们)[\s\S]{0,800}",
        "",
        text,
        flags=re.I,
    )
    return text


def _is_ad_image(tag: str, url: str) -> bool:
    blob = f"{tag} {url}".lower()
    if any(h in blob for h in _AD_IMG_HINTS):
        return True
    # Tiny / icon-sized via attributes
    wm = re.search(r'\bwidth=["\']?(\d+)', tag, flags=re.I)
    hm = re.search(r'\bheight=["\']?(\d+)', tag, flags=re.I)
    if wm and hm:
        try:
            w, h = int(wm.group(1)), int(hm.group(1))
            if w and h and (w < 120 or h < 120):
                return True
            # Mid-size near-square → almost always QR / app promo stickers
            if (
                140 <= w <= 520
                and 140 <= h <= 520
                and abs(w - h) / max(w, h) <= 0.15
            ):
                return True
        except ValueError:
            pass
    alt_m = re.search(r'alt=["\']([^"\']*)["\']', tag, flags=re.I)
    if alt_m:
        alt = alt_m.group(1)
        if any(x in alt for x in ("二维码", "扫码", "微博", "微信", "关注", "下载", "投诉", "广告")):
            return True
    return False


def _extract_images(html: str, base: str) -> list[str]:
    """Keep content photos; drop QR / share / ad banners."""
    cleaned = _strip_promo_html(html)
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"<img\b[^>]*>", cleaned or "", flags=re.I):
        tag = m.group(0)
        sm = re.search(r'(?:data-src|src)=["\']([^"\']+)["\']', tag, flags=re.I)
        if not sm:
            continue
        url = _abs_url(sm.group(1), base)
        if not url or url in seen:
            continue
        if _is_ad_image(tag, url):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 8:
            break
    return out


def get_article(id_or_url: str) -> NewsArticle | None:
    """Fetch article body for in-app reading (EM / Sina / 同花顺)."""
    raw = (id_or_url or "").strip()
    if not raw:
        return None

    em_code = _em_article_code(raw)
    if em_code:
        return _fetch_em_article(em_code)

    lower = raw.lower()
    if "eastmoney.com" in lower:
        # URL without extractable code — give up
        return None
    if "sina.com.cn" in lower or raw.startswith("comos:"):
        return _fetch_sina_article(raw)
    if "10jqka.com.cn" in lower:
        return _fetch_ths_article(raw)
    return None


def _fetch_em_article(code: str) -> NewsArticle | None:
    now = time.time()
    cached = _ARTICLE_CACHE.get(f"em:v2:{code}")
    if cached and now - cached[0] < _ARTICLE_TTL:
        cached_art = cached[1]
        if cached_art.body and not re.search(
            r"[\n\s\u3000]+[\(\uff08]\s*$", cached_art.body
        ):
            return cached_art
        _ARTICLE_CACHE.pop(f"em:v2:{code}", None)

    url = f"https://finance.eastmoney.com/a/{code}.html"
    try:
        headers = {
            **_HEADERS,
            "Referer": "https://finance.eastmoney.com/",
        }
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.exception("Article fetch failed for %s", code)
        return None

    title = ""
    tm = re.search(r"<title>([^<]+)</title>", html, re.I)
    if tm:
        title = re.sub(r"\s*[_\-|].*$", "", tm.group(1)).strip()
        title = _strip_em(title)

    body_html = ""
    bm = re.search(
        r'id="ContentBody"[^>]*>(.*?)(?:</div>\s*<div[^>]*(?:id=|class="(?:reading|comment|relate))|$)',
        html,
        re.S | re.I,
    )
    if bm:
        body_html = bm.group(1)
    else:
        bm2 = re.search(r'class="txtinfos"[^>]*>(.*?)</div>', html, re.S | re.I)
        if bm:
            body_html = bm.group(1)

    body_html = _strip_promo_html(body_html)
    for marker in ("扫码", "下载APP", "特别声明", "免责声明", "责任编辑"):
        idx = body_html.find(marker)
        if idx > 80:
            body_html = body_html[:idx]

    images = _extract_images(body_html, url)
    body = _html_to_text(body_html)
    for marker in ("文章来源：", "责任编辑：", "原标题：", "举报", "我要评论", "相关股票"):
        idx = body.find(marker)
        if idx > 40:
            body = body[:idx].rstrip()
    body = _clean_article_body(body)

    if not body and not title and not images:
        return None

    article = NewsArticle(
        id=code,
        title=title or code,
        body=body,
        source="东方财富",
        published_at="",
        url=url,
        images=images,
    )
    _ARTICLE_CACHE[f"em:v2:{code}"] = (now, article)
    return article


def _fetch_sina_article(id_or_url: str) -> NewsArticle | None:
    url = id_or_url.strip()
    if url.startswith("comos:"):
        return None
    if not url.startswith("http"):
        return None
    now = time.time()
    cache_key = f"sina:v2:{url}"
    cached = _ARTICLE_CACHE.get(cache_key)
    if cached and now - cached[0] < _ARTICLE_TTL:
        return cached[1]

    try:
        headers = {**_HEADERS, "Referer": "https://finance.sina.com.cn/"}
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.exception("Sina article fetch failed")
        return None

    title = ""
    tm = re.search(r"<title>([^<]+)</title>", html, re.I)
    if tm:
        title = re.sub(r"\s*[_\-|].*$", "", tm.group(1)).strip()

    body_html = ""
    for pat in (
        r'id="artibody"[^>]*>(.*?)</div>\s*<div[^>]+(?:id|class)="[^"]*(?:bottom|share|comment)',
        r'id="article"[^>]*>(.*?)</div>\s*<div',
        r'class="article[^"]*"[^>]*>(.*?)</div>\s*<div[^>]+class="[^"]*append',
    ):
        bm = re.search(pat, html, re.S | re.I)
        if bm:
            body_html = bm.group(1)
            break
    if not body_html:
        bm = re.search(r'id="artibody"[^>]*>(.*?)</div>', html, re.S | re.I)
        if bm:
            body_html = bm.group(1)

    body_html = _strip_promo_html(body_html)
    # Cut at common Sina footers before image/text extract
    for marker in (
        "扫码关注",
        "官方微博",
        "投诉维权",
        "特别声明",
        "海量资讯",
        "新浪声明",
        "责任编辑",
    ):
        idx = body_html.find(marker)
        if idx > 80:
            body_html = body_html[:idx]

    images = _extract_images(body_html, url)
    body = _clean_article_body(_html_to_text(body_html))
    if not body and not images:
        return None

    article = NewsArticle(
        id=url,
        title=title or "新浪财经",
        body=body,
        source="新浪财经",
        published_at="",
        url=url,
        images=images,
    )
    _ARTICLE_CACHE[cache_key] = (now, article)
    return article


def _fetch_ths_article(url: str) -> NewsArticle | None:
    raw = url.strip()
    if not raw.startswith("http"):
        return None
    now = time.time()
    cache_key = f"ths:v2:{raw}"
    cached = _ARTICLE_CACHE.get(cache_key)
    if cached and now - cached[0] < _ARTICLE_TTL:
        return cached[1]

    try:
        headers = {**_HEADERS, "Referer": "https://news.10jqka.com.cn/"}
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(raw)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.exception("THS article fetch failed")
        return None

    title = ""
    tm = re.search(r"<title>([^<]+)</title>", html, re.I)
    if tm:
        title = re.sub(r"\s*[_\-|].*$", "", tm.group(1)).strip()

    body_html = ""
    for pat in (
        r'class="[^"]*news-content[^"]*article-content[^"]*"[^>]*>(.*?)</div>\s*<div',
        r'class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>\s*<div',
        r'id="contentApp"[^>]*>(.*?)</div>',
        r'class="main-text[^"]*"[^>]*>(.*?)</div>\s*<div',
    ):
        bm = re.search(pat, html, re.S | re.I)
        if bm:
            body_html = bm.group(1)
            break
    if not body_html:
        bm = re.search(
            r'class="[^"]*news-content[^"]*"[^>]*>(.*?)</div>',
            html,
            re.S | re.I,
        )
        if bm:
            body_html = bm.group(1)

    body_html = _strip_promo_html(body_html)
    for marker in ("扫码", "下载APP", "特别声明", "免责声明", "责任编辑", "风险提示"):
        idx = body_html.find(marker)
        if idx > 80:
            body_html = body_html[:idx]

    images = _extract_images(body_html, raw)
    body = _clean_article_body(_html_to_text(body_html))
    if not body and not images:
        return None

    article = NewsArticle(
        id=raw,
        title=title or "同花顺",
        body=body,
        source="同花顺",
        published_at="",
        url=raw,
        images=images,
    )
    _ARTICLE_CACHE[cache_key] = (now, article)
    return article


# Back-compat alias used nowhere else after rename
def _article_code(id_or_url: str) -> str:
    return _em_article_code(id_or_url)


def _html_to_text(html: str) -> str:
    from html import unescape

    text = re.sub(r"<script[\s\S]*?</script>", "", html or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    # Keep link text; drop empty quote widgets that leave orphan "("
    text = re.sub(r"<a\b[^>]*>([\s\S]*?)</a>", r"\1", text, flags=re.I)
    text = re.sub(r'<span\b[^>]*id="quote_[^"]*"[^>]*>[\s\S]*?</span>', "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_article_body(body: str) -> str:
    text = (body or "").strip()
    # Trailing orphan punctuation from stripped tags / share widgets
    # Includes fullwidth （ U+FF08 which EM often leaves behind
    text = re.sub(r"[\s\n\u3000]*[\(\uff08\[【〈《]+[\s\n\u3000]*$", "", text)
    text = re.sub(r"[\s\n]+[\(\uff08]\s*$", "", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and re.fullmatch(r"[\(\uff08\[【〈《\)\uff09\]]+", lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def _article_url(code: str, fallback: str | None = None) -> str:
    if fallback and fallback.startswith("http"):
        return fallback
    code = (code or "").strip()
    if not code:
        return fallback or ""
    return f"https://finance.eastmoney.com/a/{code}.html"


def list_market_boards() -> list[dict[str, str]]:
    return [{"id": b["id"], "label": b["label"]} for b in MARKET_BOARDS]


def news_age_label(published_at: object) -> str:
    """今日 / N天前 — Agent 与分析席共用，避免把旧闻当突发。"""
    raw = str(published_at or "").strip()
    if not raw:
        return "时间未知"
    from datetime import date

    from app.providers.cn_calendar import parse_as_of_date, shanghai_today

    d = parse_as_of_date(raw)
    if d is None:
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", raw)
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                d = None
    if d is None:
        return "时间未知"
    delta = (shanghai_today() - d).days
    if delta <= 0:
        return "今日"
    if delta == 1:
        return "1天前"
    return f"{delta}天前"


def format_news_digest(
    items: list[NewsItem],
    *,
    title: str,
    limit: int = 8,
) -> str:
    """Compact Chinese digest for Agent tools (title + age + related)."""
    lim = max(1, min(int(limit or 5), 12))
    lines = [
        f"【{title}】",
        "引用规则：优先「今日」；≥3天前只当背景，勿当今天突发驱动；无条目就说没有。",
    ]
    if not items:
        lines.append("（暂无条目）")
        return "\n".join(lines)
    for it in items[:lim]:
        age = news_age_label(it.published_at)
        src = (it.source or "").strip()
        summary = (it.summary or "").strip().replace("\n", " ")[:100]
        syms = ",".join((it.symbols or [])[:4])
        bit = f"- [{age}] {it.title or '（无标题）'}"
        if src:
            bit += f" · {src}"
        if syms:
            bit += f" · 关联 {syms}"
        if summary:
            bit += f" — {summary}"
        lines.append(bit)
    return "\n".join(lines)


def _fmt_unix(ts: object) -> str:
    try:
        t = int(float(str(ts)))
        if t > 1_000_000_000_000:
            t //= 1000
        if t <= 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except Exception:
        return str(ts or "").strip()


def _dedupe_key(item: NewsItem) -> str:
    if item.url:
        return item.url.strip()
    if item.id:
        return f"id:{item.id}"
    return f"t:{item.title}"


def _merge_news(batches: list[list[NewsItem]], limit: int) -> list[NewsItem]:
    merged: dict[str, NewsItem] = {}
    for batch in batches:
        for item in batch:
            key = _dedupe_key(item)
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
                continue
            for sym in item.symbols:
                if sym not in existing.symbols:
                    existing.symbols.append(sym)
            if (not existing.source or existing.source == "东方财富") and item.source:
                existing.source = item.source

    def sort_key(n: NewsItem) -> str:
        return n.published_at or ""

    out = sorted(merged.values(), key=sort_key, reverse=True)
    return out[: max(1, min(limit, _FEED_CAP))]


def get_market_news(limit: int = _FEED_CAP, board: str = "headline") -> tuple[str, list[NewsItem]]:
    """Return (board_title, items) for a market board — up to ~100, multi-source."""
    meta = _BOARD_BY_ID.get(board) or _BOARD_BY_ID["headline"]
    board_id = meta["id"]
    title = meta["label"]
    need = max(1, min(limit, _FEED_CAP))
    now = time.time()
    cached = _MARKET_CACHE.get(board_id)
    if cached and now - cached[0] < _MARKET_TTL:
        return title, cached[1][:need]

    kind = meta["kind"]
    value = meta["value"]
    batches: list[list[NewsItem]] = []

    if kind == "fast":
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(_fetch_fast_news, value, need),
                pool.submit(_fetch_sina_roll, need),
                pool.submit(_fetch_ths_push, need),
            ]
            for fut in as_completed(futs):
                try:
                    batches.append(fut.result() or [])
                except Exception:
                    logger.exception("Headline multi-source worker failed")
    elif kind == "column":
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [
                pool.submit(_fetch_column_news, value, need),
                pool.submit(_fetch_keyword_news, title, need),
            ]
            for fut in as_completed(futs):
                try:
                    batches.append(fut.result() or [])
                except Exception:
                    logger.exception("Column multi-source worker failed")
    elif kind == "keyword":
        batches.append(_fetch_keyword_news(value, limit=need))
    else:
        batches = []

    items = _merge_news(batches, need)
    _MARKET_CACHE[board_id] = (now, items)
    return title, items[:need]


def _fetch_fast_news(fast_column: str, limit: int = _FEED_CAP) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": fast_column,
            "sortEnd": "",
            "pageSize": str(min(max(limit, 1), _FEED_CAP)),
            "req_trace": str(int(time.time() * 1000)),
        }
        with httpx.Client(timeout=12.0, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
        for row in data.get("fastNewsList") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip()
            title = _strip_em(str(row.get("title") or ""))
            if not title:
                continue
            items.append(
                NewsItem(
                    id=code or f"m-{len(items)}",
                    title=title,
                    summary=_strip_em(str(row.get("summary") or "")),
                    source="东方财富快讯",
                    published_at=str(row.get("showTime") or ""),
                    url=_article_url(code),
                    symbols=[],
                )
            )
    except Exception:
        logger.exception("Fast news fetch failed col=%s", fast_column)
    return items[:limit]


def _fetch_column_news(column: str, limit: int = _FEED_CAP) -> list[NewsItem]:
    """EM column list returns ~10/page — paginate until limit."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    page_no = 1
    max_pages = max(1, (min(limit, _FEED_CAP) + 9) // 10)
    try:
        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        with httpx.Client(timeout=12.0, headers=_HEADERS, follow_redirects=True) as client:
            while page_no <= max_pages and len(items) < limit:
                params = {
                    "client": "web",
                    "biz": "web_news_col",
                    "column": column,
                    "pageSize": "20",
                    "pageNo": str(page_no),
                    "req_trace": str(int(time.time() * 1000)),
                }
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                rows = data.get("list") or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("code") or "").strip()
                    title = _strip_em(str(row.get("title") or ""))
                    if not title:
                        continue
                    raw_url = str(row.get("uniqueUrl") or row.get("url") or "").strip() or None
                    key = raw_url or code or title
                    if key in seen:
                        continue
                    seen.add(key)
                    media = _strip_em(str(row.get("mediaName") or "")) or "东方财富"
                    items.append(
                        NewsItem(
                            id=code or f"c-{len(items)}",
                            title=title,
                            summary=_strip_em(str(row.get("summary") or "")),
                            source=media,
                            published_at=str(row.get("showTime") or ""),
                            url=_article_url(code, raw_url),
                            symbols=[],
                        )
                    )
                    if len(items) >= limit:
                        break
                if len(rows) < 5:
                    break
                page_no += 1
    except Exception:
        logger.exception("Column news fetch failed col=%s", column)
    return items[:limit]


def _fetch_keyword_news(keyword: str, limit: int = _FEED_CAP) -> list[NewsItem]:
    """EM article search — paginate (~40/page) until limit."""
    kw = keyword.strip()
    if not kw:
        return []
    now = time.time()
    cache_key = f"kw:{kw}:{limit}"
    cached = _SYMBOL_CACHE.get(cache_key)
    if cached and now - cached[0] < _SYMBOL_TTL:
        return cached[1][:limit]

    items: list[NewsItem] = []
    seen: set[str] = set()
    page_index = 1
    page_size = 40
    max_pages = max(1, (min(limit, _FEED_CAP) + page_size - 1) // page_size)
    try:
        headers = {
            **_HEADERS,
            "Referer": f"https://so.eastmoney.com/news/s?keyword={quote(kw)}",
        }
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            while page_index <= max_pages and len(items) < limit:
                inner = {
                    "uid": "",
                    "keyword": kw,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": page_index,
                            "pageSize": page_size,
                            "preTag": "<em>",
                            "postTag": "</em>",
                        }
                    },
                }
                cb = f"jQuery3510_{int(time.time() * 1000)}"
                params = {
                    "cb": cb,
                    "param": json.dumps(inner, ensure_ascii=False),
                    "_": str(int(time.time() * 1000)),
                }
                resp = client.get(url, params=params)
                resp.raise_for_status()
                text = resp.text.strip()
                match = _JSONP_RE.match(text)
                if not match:
                    break
                payload = json.loads(match.group(1))
                rows = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("code") or "").strip()
                    title = _strip_em(str(row.get("title") or ""))
                    if not title:
                        continue
                    raw_url = str(row.get("url") or "").strip() or None
                    key = raw_url or code or title
                    if key in seen:
                        continue
                    seen.add(key)
                    media = _strip_em(str(row.get("mediaName") or "")) or "东方财富"
                    items.append(
                        NewsItem(
                            id=code or f"kw-{len(items)}",
                            title=title,
                            summary=_strip_em(str(row.get("content") or "")),
                            source=media,
                            published_at=str(row.get("date") or ""),
                            url=_article_url(code, raw_url),
                            symbols=[],
                        )
                    )
                    if len(items) >= limit:
                        break
                if len(rows) < page_size:
                    break
                page_index += 1
    except Exception:
        logger.exception("Keyword news fetch failed for %s", kw)

    _SYMBOL_CACHE[cache_key] = (now, items)
    return items[:limit]


def _fetch_sina_roll(limit: int = _FEED_CAP) -> list[NewsItem]:
    """Sina finance roll feed — secondary source for 要闻."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    page = 1
    per = 50
    max_pages = max(1, (min(limit, _FEED_CAP) + per - 1) // per)
    try:
        headers = {
            **_HEADERS,
            "Referer": "https://finance.sina.com.cn/",
        }
        url = "https://feed.mix.sina.com.cn/api/roll/get"
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            while page <= max_pages and len(items) < limit:
                params = {
                    "pageid": "153",
                    "lid": "2516",
                    "k": "",
                    "num": str(per),
                    "page": str(page),
                }
                resp = client.get(url, params=params)
                resp.raise_for_status()
                rows = ((resp.json() or {}).get("result") or {}).get("data") or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    title = _strip_em(str(row.get("title") or ""))
                    raw_url = str(row.get("url") or "").strip()
                    if not title or not raw_url:
                        continue
                    if raw_url in seen:
                        continue
                    seen.add(raw_url)
                    media = _strip_em(str(row.get("media_name") or "")) or "新浪财经"
                    doc_id = str(row.get("docid") or row.get("oid") or "").strip()
                    items.append(
                        NewsItem(
                            id=doc_id or f"sina-{len(items)}",
                            title=title,
                            summary=_strip_em(str(row.get("intro") or row.get("summary") or "")),
                            source=media,
                            published_at=_fmt_unix(row.get("ctime") or row.get("mtime")),
                            url=raw_url,
                            symbols=[],
                        )
                    )
                    if len(items) >= limit:
                        break
                if len(rows) < per:
                    break
                page += 1
    except Exception:
        logger.exception("Sina roll news fetch failed")
    return items[:limit]


def _fetch_ths_push(limit: int = _FEED_CAP) -> list[NewsItem]:
    """同花顺 flash push — secondary source for 要闻."""
    items: list[NewsItem] = []
    seen: set[str] = set()
    page = 1
    per = 20
    max_pages = max(1, (min(limit, _FEED_CAP) + per - 1) // per)
    try:
        headers = {
            **_HEADERS,
            "Referer": "https://news.10jqka.com.cn/",
        }
        url = "https://news.10jqka.com.cn/tapp/news/push/stock/"
        with httpx.Client(timeout=12.0, headers=headers, follow_redirects=True) as client:
            while page <= max_pages and len(items) < limit:
                params = {"page": str(page), "tag": "", "track": "website"}
                resp = client.get(url, params=params)
                resp.raise_for_status()
                rows = ((resp.json() or {}).get("data") or {}).get("list") or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    title = _strip_em(str(row.get("title") or ""))
                    raw_url = str(row.get("url") or row.get("shareUrl") or "").strip()
                    if not title:
                        continue
                    key = raw_url or str(row.get("id") or title)
                    if key in seen:
                        continue
                    seen.add(key)
                    media = _strip_em(str(row.get("source") or "")) or "同花顺"
                    nid = str(row.get("id") or row.get("seq") or "").strip()
                    items.append(
                        NewsItem(
                            id=nid or f"ths-{len(items)}",
                            title=title,
                            summary=_strip_em(str(row.get("digest") or "")),
                            source=media,
                            published_at=_fmt_unix(row.get("ctime") or row.get("rtime")),
                            url=raw_url,
                            symbols=[],
                        )
                    )
                    if len(items) >= limit:
                        break
                if len(rows) < per:
                    break
                page += 1
    except Exception:
        logger.exception("THS push news fetch failed")
    return items[:limit]


def _fetch_symbol_news(symbol: str) -> list[NewsItem]:
    sym = symbol.strip()
    if not sym:
        return []
    now = time.time()
    cached = _SYMBOL_CACHE.get(f"sym:{sym}")
    if cached and now - cached[0] < _SYMBOL_TTL:
        return cached[1]

    rows = _fetch_keyword_news(sym, limit=_PER_SYMBOL_LIMIT)
    items = [
        NewsItem(
            id=r.id,
            title=r.title,
            summary=r.summary,
            source=r.source,
            published_at=r.published_at,
            url=r.url,
            symbols=[sym],
        )
        for r in rows
    ]
    _SYMBOL_CACHE[f"sym:{sym}"] = (now, items)
    return items


def get_holdings_news(symbols: list[str], limit: int = _FEED_CAP) -> list[NewsItem]:
    """Fetch & merge news for unique symbols (capped). Sorted newest-first."""
    unique: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        sym = (s or "").strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        unique.append(sym)
        if len(unique) >= _HOLDINGS_SYMBOL_CAP:
            break

    if not unique:
        return []

    batches: list[list[NewsItem]] = []
    with ThreadPoolExecutor(max_workers=min(6, len(unique))) as pool:
        futures = {pool.submit(_fetch_symbol_news, sym): sym for sym in unique}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                batches.append(fut.result() or [])
            except Exception:
                logger.exception("Holdings news worker failed for %s", sym)

    return _merge_news(batches, limit)


def get_interests_news(keywords: list[str], limit: int = _FEED_CAP) -> list[NewsItem]:
    """Fetch & merge news for user interest keywords. Sorted newest-first."""
    unique: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        kw = (raw or "").strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        unique.append(kw)
        if len(unique) >= 8:
            break

    if not unique:
        return []

    batches: list[list[NewsItem]] = []
    per = max(_PER_INTEREST_LIMIT // max(len(unique), 1), 40)
    per = min(per, _FEED_CAP)
    with ThreadPoolExecutor(max_workers=min(6, len(unique))) as pool:
        futures = {
            pool.submit(_fetch_keyword_news, kw, per): kw for kw in unique
        }
        for fut in as_completed(futures):
            kw = futures[fut]
            try:
                rows = fut.result() or []
            except Exception:
                logger.exception("Interests news worker failed for %s", kw)
                continue
            tagged: list[NewsItem] = []
            for item in rows:
                tagged.append(
                    NewsItem(
                        id=item.id,
                        title=item.title,
                        summary=item.summary,
                        source=item.source,
                        published_at=item.published_at,
                        url=item.url,
                        symbols=[kw, *item.symbols],
                    )
                )
            batches.append(tagged)

    return _merge_news(batches, limit)
