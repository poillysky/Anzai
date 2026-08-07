"""Gold board: domestic (浙商/民生) / international (伦敦/纽约) / shop (门店).

Holdable: A-share gold ETFs + 浙商/民生积存金 (market=JD, shares=克).
Jicunjin quotes via JD Gold public APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from app.providers.macro import freshness_label, get_macro_quotes
from app.providers.quote import get_quotes

logger = logging.getLogger(__name__)

_JD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://m.jdjygold.com/",
    "Accept": "application/json, text/plain, */*",
}

# Holdable A-share gold ETFs on App「股票→黄金」国内栏（可入仓）
# 其它金 ETF 仍可被 short_bias 识别时用扩展集合
GOLD_ETFS: list[tuple[str, str, str]] = [
    ("159937", "SZ", "博时黄金ETF"),
    ("518660", "SH", "工银瑞信黄金ETF"),
]

# 识别用（持仓里可能有历史录入的华安/易方达等）
GOLD_ETF_ALIASES: list[tuple[str, str, str]] = [
    *GOLD_ETFS,
    ("518880", "SH", "黄金ETF华安"),
    ("518800", "SH", "黄金ETF易方达"),
    ("159934", "SZ", "黄金ETF易方达"),
]

# kind: gds | jd | etf | na
# 国内：AU9999（上金所）+ 浙商 / 民生积存金（可入仓，按克）+ 黄金 ETF
DOMESTIC_CATALOG: list[dict] = [
    {
        "id": "au9999",
        "name": "AU9999",
        "kind": "gds",
        "sina": "gds_AU9999",
        "em_secid": "118.AU9999",
        "unit": "元/克",
        "note": "上金所现货，仅查阅",
    },
    {
        "id": "icbc-jcj",
        "name": "工行积存金",
        "kind": "jd",
        "sku": "2005453243",
        "unit": "元/克",
        "note": "积存金，可入仓（按克）",
    },
    {
        "id": "zs-jcj",
        "name": "浙商积存金",
        "kind": "jd",
        "sku": "1961543816",
        "unit": "元/克",
        "note": "积存金，可入仓（按克）",
    },
    {
        "id": "ms-jcj",
        "name": "民生积存金",
        "kind": "jd",
        "sku": "21001001000001",
        "unit": "元/克",
        "note": "积存金，可入仓（按克）",
    },
    {
        "id": "bosera-etf",
        "name": "博时黄金ETF",
        "kind": "etf",
        "symbol": "159937",
        "market": "SZ",
        "unit": "元",
        "note": "场内ETF，可入仓",
    },
    {
        "id": "ruixin-etf",
        "name": "工银瑞信黄金ETF",
        "kind": "etf",
        "symbol": "518660",
        "market": "SH",
        "unit": "元",
        "note": "场内ETF，可入仓",
    },
]

# 门店金价：品牌金店零售参考（优先顺序）
SHOP_BRAND_ORDER: tuple[str, ...] = (
    "周大福",
    "周生生",
    "老凤祥",
    "六福珠宝",
    "菜百首饰",
    "金至尊",
    "周大生",
    "老庙黄金",
    "潮宏基",
    "中国黄金",
)

_SHOP_PRICE_URL = "https://openapi.dwo.cc/api/jinjia"


@dataclass
class GoldEtfQuote:
    symbol: str
    market: str
    name: str
    price: float
    change_pct: float | None = None
    prev_close: float | None = None


@dataclass
class GoldBoardItem:
    id: str
    name: str
    section: str  # domestic | international | shop
    price: float | None = None
    change_pct: float | None = None
    prev: float | None = None
    unit: str = "元/克"
    freshness: str = ""
    note: str = ""
    holdable: bool = False
    symbol: str = ""
    market: str = ""
    chart: list[float] = field(default_factory=list)
    # Parallel HH:mm labels for chart points (AU9999 EM); empty → client synthesizes
    chart_times: list[str] = field(default_factory=list)
    # Expected full-session slot count (e.g. JD pointCount=1000 for day24)
    chart_slots: int = 0
    # Sparkline session axis: cn | us | day24
    chart_session: str = ""


@dataclass
class GoldBoardSection:
    id: str
    title: str
    subtitle: str = ""
    items: list[GoldBoardItem] = field(default_factory=list)


@dataclass
class GoldBoard:
    sections: list[GoldBoardSection]
    note: str = ""


def list_gold_etfs() -> list[GoldEtfQuote]:
    pairs = [(sym, mkt) for sym, mkt, _ in GOLD_ETFS]
    quotes = get_quotes(pairs)
    out: list[GoldEtfQuote] = []
    for sym, mkt, fallback in GOLD_ETFS:
        q = quotes.get(sym)
        out.append(
            GoldEtfQuote(
                symbol=sym,
                market=mkt,
                name=(q.name if q and q.name else fallback),
                price=q.price if q else 0.0,
                change_pct=q.change_pct if q else None,
                prev_close=q.prev_close if q else None,
            )
        )
    return out


def _parse_pct(raw: object) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_price(raw: object) -> float | None:
    try:
        v = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _fetch_jd_latest(sku: str) -> dict:
    """Return datas dict from JD stdLatestPrice / latestPrice."""
    urls = [
        f"https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice?productSku={sku}",
        "https://api.jdjygold.com/gw/generic/hj/h5/m/latestPrice?reqData={}",
    ]
    with httpx.Client(timeout=8.0, headers=_JD_HEADERS, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json() or {}
                datas = ((payload.get("resultData") or {}).get("datas")) or {}
                if datas.get("price") and (
                    not datas.get("productSku") or str(datas.get("productSku")) == str(sku)
                    or sku == "21001001000001"
                ):
                    # latestPrice always returns 民生 default — only accept if sku matches or is 民生
                    if "latestPrice" in url and str(datas.get("productSku")) != str(sku):
                        continue
                    return datas
            except Exception:
                logger.exception("JD latest price failed %s", url)
    return {}


def _fetch_jd_chart(sku: str) -> tuple[str, list[float], int]:
    """Name + intraday line + full-day slot count (JD pointCount)."""
    name, pts, slots, _meta = _fetch_jd_product_page(sku)
    return name, pts, slots


def _fetch_jd_product_page(sku: str) -> tuple[str, list[float], int, dict]:
    """Product page: name, chart, slots, raw meta (minimumPriceValue / rateValue …).

    Used as quote fallback when stdLatestPrice has no data (e.g. 工行积存金).
    """
    url = "https://api.jdjygold.com/gw2/generic/CreatorSer/newh5/m/getFirstRelatedProductInfo"
    params = {
        "reqData": (
            '{"circleId":"13245","invokeSource":5,'
            f'"productId":"{sku}"}}'
        )
    }
    try:
        with httpx.Client(timeout=8.0, headers=_JD_HEADERS, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = ((resp.json() or {}).get("resultData") or {}).get("data") or {}
    except Exception:
        logger.exception("JD product page failed %s", sku)
        return "", [], 0, {}
    if not isinstance(data, dict):
        return "", [], 0, {}
    name = str(data.get("goldName") or data.get("productName") or "")
    chart_obj = data.get("icLineChart") or {}
    line = chart_obj.get("lineDataList") or []
    try:
        point_count = int(chart_obj.get("pointCount") or 0)
    except (TypeError, ValueError):
        point_count = 0
    pts: list[float] = []
    for x in line:
        try:
            pts.append(float(x))
        except (TypeError, ValueError):
            continue
    if point_count > 0 and len(pts) > point_count * 2:
        pts = pts[:point_count]
    slots = point_count if point_count > 0 else len(pts)
    return name, pts, slots, data


def _jd_quote_bundle(sku: str) -> dict:
    """Unified JD quote: prefer stdLatestPrice, else product-page minimumPriceValue."""
    datas = _fetch_jd_latest(sku)
    name, chart, slots, meta = _fetch_jd_product_page(sku)
    price = _parse_price(datas.get("price")) or _parse_price(meta.get("minimumPriceValue"))
    if price is None and chart:
        price = chart[-1]
    change = _parse_pct(datas.get("upAndDownRate")) or _parse_pct(meta.get("rateValue"))
    prev = _parse_price(datas.get("yesterdayPrice"))
    if prev is None and price and change is not None:
        denom = 1 + change / 100.0
        if denom != 0:
            prev = round(price / denom, 2)
    if name:
        # Prefer page name (工行积存金) over empty latest
        pass
    return {
        "name": name,
        "price": price,
        "prev": prev,
        "change_pct": change,
        "chart": chart,
        "chart_slots": slots,
    }


def _fetch_em_trends_chart(secid: str) -> tuple[list[float], list[str], int]:
    """东财分时 trends → prices + HH:mm + trendsTotal（整段槽位数）。"""
    from app.providers.eastmoney import EM_HEADERS, em_float, host_label, trends_urls

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1",
    }
    data: dict = {}
    with httpx.Client(timeout=8.0, headers=EM_HEADERS, follow_redirects=True) as client:
        for url in trends_urls():
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                payload = (resp.json() or {}).get("data") or {}
                if payload.get("trends"):
                    data = payload
                    break
                logger.info("EM trends empty %s from %s", secid, host_label(url))
            except Exception:
                logger.exception("EM trends miss %s %s", secid, host_label(url))
    trends = data.get("trends") or []
    prices: list[float] = []
    times: list[str] = []
    for row in trends:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        t = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]
        if len(t) >= 5:
            t = t[:5]
        close = em_float(parts[2]) if len(parts) > 2 else None
        open_px = em_float(parts[1]) if len(parts) > 1 else None
        price = close if close and close > 0 else (open_px if open_px and open_px > 0 else None)
        if price is None:
            continue
        prices.append(price)
        times.append(t)
    # COMEX/伦敦金全日约 23h（06:00→次日05:00）；缺省用 trendsTotal
    total = 0
    try:
        total = int(data.get("trendsTotal") or 0)
    except (TypeError, ValueError):
        total = 0
    slots = total if total >= 60 else 23 * 60
    return prices, times, slots


def _fetch_em_au9999_chart(secid: str = "118.AU9999") -> tuple[list[float], list[str], int]:
    """兼容旧调用：AU9999 分时。"""
    return _fetch_em_trends_chart(secid)


def _catalog_items(catalog: list[dict], section: str) -> list[GoldBoardItem]:
    etf_map = {e.symbol: e for e in list_gold_etfs()}
    gds_codes = [str(r["sina"]) for r in catalog if r.get("kind") == "gds" and r.get("sina")]
    gds_raw: dict[str, str] = {}
    if gds_codes:
        try:
            from app.providers.macro import _fetch_raw, _parse_gds

            gds_raw = _fetch_raw(gds_codes)
        except Exception:
            logger.exception("AU9999 / gds fetch failed")
            gds_raw = {}

    out: list[GoldBoardItem] = []
    for row in catalog:
        kind = row["kind"]
        item = GoldBoardItem(
            id=row["id"],
            name=row["name"],
            section=section,
            unit=row.get("unit") or "元/克",
            note=str(row.get("note") or ""),
        )
        if kind == "gds":
            code = str(row.get("sina") or "")
            raw = gds_raw.get(code) or ""
            q = None
            if raw:
                try:
                    from app.providers.macro import _parse_gds

                    q = _parse_gds(code, raw, row["name"], item.unit)
                except Exception:
                    logger.exception("parse gds %s failed", code)
            item.holdable = False
            item.market = "GDS"
            item.symbol = "AU9999"
            item.chart_session = "day24"
            if q:
                item.price = q.price
                item.prev = q.prev
                item.change_pct = q.change_pct
                item.freshness = "上金所"
            else:
                item.freshness = "暂无报价"
                item.note = item.note or "上金所报价暂不可用"
            # 分时：东财 118.AU9999（夜盘+日盘）
            em_secid = str(row.get("em_secid") or "118.AU9999")
            try:
                prices, times, slots = _fetch_em_au9999_chart(em_secid)
                if prices:
                    item.chart = prices
                    item.chart_times = times
                    item.chart_slots = slots
                    # Prefer live EM last if sina stale
                    if not item.price:
                        item.price = prices[-1]
            except Exception:
                logger.exception("AU9999 chart failed")
        elif kind == "jd":
            sku = str(row["sku"])
            bundle = _jd_quote_bundle(sku)
            if bundle.get("name"):
                item.name = str(bundle["name"])
            item.symbol = str(row["id"])
            item.market = "JD"
            item.holdable = True
            item.price = bundle.get("price")  # type: ignore[assignment]
            item.prev = bundle.get("prev")  # type: ignore[assignment]
            item.change_pct = bundle.get("change_pct")  # type: ignore[assignment]
            if item.change_pct is None and item.price and item.prev:
                item.change_pct = round((item.price - item.prev) / item.prev * 100, 2)
            item.chart = list(bundle.get("chart") or [])
            item.chart_slots = int(bundle.get("chart_slots") or 0)
            item.chart_session = "day24"
            item.freshness = "积存金实时" if item.price else "暂无报价"
            if not item.price:
                item.note = item.note or "暂无公开实时报价"
        elif kind == "etf":
            sym = str(row["symbol"])
            mkt = str(row["market"])
            q = etf_map.get(sym)
            item.symbol = sym
            item.market = mkt
            item.holdable = True
            item.chart_session = "cn"
            if q:
                item.price = q.price or None
                item.prev = q.prev_close
                item.change_pct = q.change_pct
            item.freshness = "场内ETF" if item.price else "暂无报价"
        else:
            item.freshness = "暂无报价"
            item.note = item.note or "暂无公开实时报价"
        out.append(item)
    return out


def _intl_freshness(as_of: str | None, *, market_name: str) -> str:
    """Prefer 「纽约今日」over bare 「今日」."""
    raw = freshness_label(as_of, venue="us")
    if raw == "今日":
        return f"{market_name}今日"
    if raw.startswith("20") and "非今日" in raw:
        return f"{market_name}·{raw}"
    return f"{market_name}·{raw}" if raw else market_name


def _fill_em_chart(item: GoldBoardItem, secid: str) -> None:
    try:
        prices, times, slots = _fetch_em_trends_chart(secid)
        if prices:
            item.chart = prices
            item.chart_times = times
            item.chart_slots = slots
            # 国际金：整段轴开→收（非「铺满到最新时刻」）
            item.chart_session = "comex"
            if not item.price:
                item.price = prices[-1]
    except Exception:
        logger.exception("intl gold chart failed %s", secid)


def _international_items() -> list[GoldBoardItem]:
    """仅纽约金 + 伦敦金（东财分时：COMEX GC / XAU）。"""
    from app.providers.macro import _fetch_raw, _parse_hf

    out: list[GoldBoardItem] = []
    _topic, quotes, _err = get_macro_quotes("gold")
    for q in quotes:
        if q.key != "hf_GC" and "GC" not in q.key:
            continue
        item = GoldBoardItem(
            id=q.key,
            name="纽约金",
            section="international",
            price=q.price,
            change_pct=q.change_pct,
            prev=q.prev,
            unit=q.unit or "美元/盎司",
            freshness=_intl_freshness(q.as_of, market_name="纽约"),
            chart_session="comex",
        )
        _fill_em_chart(item, "101.GC00Y")
        out.append(item)

    try:
        raw_map = _fetch_raw(["hf_XAU"])
    except Exception:
        raw_map = {}
    if raw_map.get("hf_XAU"):
        q = _parse_hf("hf_XAU", raw_map["hf_XAU"], "伦敦金", "美元/盎司")
        if q:
            item = GoldBoardItem(
                id=q.key,
                name="伦敦金",
                section="international",
                price=q.price,
                change_pct=q.change_pct,
                prev=q.prev,
                unit=q.unit or "美元/盎司",
                freshness=_intl_freshness(q.as_of, market_name="伦敦"),
                chart_session="comex",
            )
            _fill_em_chart(item, "122.XAU")
            out.append(item)

    # 纽约金在前
    out.sort(key=lambda x: 0 if "GC" in x.id else 1)
    return out


def _slug_brand(name: str) -> str:
    mapping = {
        "周大福": "ctf",
        "周生生": "css",
        "老凤祥": "lfx",
        "六福珠宝": "lfjb",
        "菜百首饰": "cb",
        "金至尊": "jzz",
        "周大生": "zds",
        "老庙黄金": "lm",
        "潮宏基": "chj",
        "中国黄金": "cng",
    }
    return mapping.get(name) or name.encode("utf-8", "ignore").hex()[:10]


def _shop_items() -> list[GoldBoardItem]:
    """品牌门店零售/回收参考价（周大福等）。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    try:
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            resp = client.get(_SHOP_PRICE_URL)
            resp.raise_for_status()
            payload = resp.json() or {}
    except Exception:
        logger.exception("shop gold price fetch failed")
        return [
            GoldBoardItem(
                id="shop-na",
                name="门店金价",
                section="shop",
                unit="元/克",
                freshness="暂无报价",
                note="品牌门店报价暂不可用",
            )
        ]

    data = payload.get("data") if isinstance(payload, dict) else None
    shops = (data or {}).get("shops") if isinstance(data, dict) else None
    if not isinstance(shops, list) or not shops:
        return [
            GoldBoardItem(
                id="shop-na",
                name="门店金价",
                section="shop",
                unit="元/克",
                freshness="暂无报价",
                note="品牌门店报价暂不可用",
            )
        ]

    board_asof = str((data or {}).get("update_time") or "")
    by_name: dict[str, dict] = {}
    for row in shops:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            by_name[name] = row

    ordered_names = [n for n in SHOP_BRAND_ORDER if n in by_name]
    ordered_names.extend(sorted(n for n in by_name if n not in SHOP_BRAND_ORDER))

    out: list[GoldBoardItem] = []
    for name in ordered_names:
        row = by_name[name]
        retail = _parse_price(row.get("retail_price"))
        recycle = _parse_price(row.get("exchange_price"))
        asof = str(row.get("update_time") or board_asof or "")
        note = f"回收 {recycle:g}" if recycle else "零售参考"
        freshness = f"门店 · {asof[:16]}" if asof else "门店参考价"
        out.append(
            GoldBoardItem(
                id=f"shop-{_slug_brand(name)}",
                name=name,
                section="shop",
                price=retail,
                unit="元/克",
                freshness=freshness,
                note=note,
            )
        )
    return out


def jd_sku_by_symbol(symbol: str) -> str | None:
    """Map holding symbol (catalog id or sku) → JD productSku."""
    key = (symbol or "").strip().lower()
    if not key:
        return None
    for row in DOMESTIC_CATALOG:
        if row.get("kind") != "jd":
            continue
        if str(row.get("id", "")).lower() == key or str(row.get("sku", "")) == key:
            return str(row["sku"])
    return None


def jd_name_by_symbol(symbol: str) -> str:
    key = (symbol or "").strip().lower()
    for row in DOMESTIC_CATALOG:
        if row.get("kind") != "jd":
            continue
        if str(row.get("id", "")).lower() == key or str(row.get("sku", "")) == key:
            return str(row.get("name") or key)
    return key


def get_jd_holding_quotes(symbols: list[str]) -> dict[str, "Quote"]:
    """Live quotes for 积存金 holdings (market=JD). Keyed by requested symbol."""
    from app.providers.quote import Quote

    out: dict[str, Quote] = {}
    for raw in symbols:
        sym = (raw or "").strip().lower()
        if not sym:
            continue
        sku = jd_sku_by_symbol(sym)
        if not sku:
            out[sym] = Quote(
                symbol=sym,
                name=jd_name_by_symbol(sym),
                market="JD",
                price=0.0,
                live=False,
            )
            continue
        bundle = _jd_quote_bundle(sku)
        price = float(bundle.get("price") or 0.0)
        prev = bundle.get("prev")
        chg = bundle.get("change_pct")
        name = str(bundle.get("name") or "") or jd_name_by_symbol(sym)
        out[sym] = Quote(
            symbol=sym,
            name=name,
            market="JD",
            price=price,
            change_pct=float(chg) if chg is not None else None,
            prev_close=float(prev) if prev is not None else None,
            as_of=None,
            live=price > 0,
        )
    return out


def get_gold_board() -> GoldBoard:
    domestic = _catalog_items(DOMESTIC_CATALOG, "domestic")
    international = _international_items()
    shop = _shop_items()

    return GoldBoard(
        sections=[
            GoldBoardSection(
                id="domestic",
                title="国内金价",
                subtitle="AU9999 · 工行 / 浙商 / 民生积存金 · 黄金 ETF",
                items=domestic,
            ),
            GoldBoardSection(
                id="international",
                title="国际金价",
                subtitle="纽约金 / 伦敦金",
                items=international,
            ),
            GoldBoardSection(
                id="shop",
                title="门店金价",
                subtitle="周大福 / 老凤祥等品牌零售参考",
                items=shop,
            ),
        ],
        note=(
            "AU9999 为上金所现货参考；工行/浙商/民生积存金参考京东金融可入仓；"
            "博时/瑞信为场内黄金ETF可入仓；"
            "门店为品牌金店零售/回收参考价，以门店公示为准。非投资建议。"
        ),
    )


def format_gold_board_text() -> str:
    """Agent-facing text aligned with App「股票→黄金」三栏看板。"""
    from app.providers.macro import calendar_clock_line

    try:
        board = get_gold_board()
    except Exception:
        logger.exception("format_gold_board_text failed")
        return "黄金看板暂时拉不到（勿编造）。"

    lines = [
        "【黄金 · 与 App「股票→黄金」页一致】国内 / 国际 / 门店；无报价禁止编造。",
        "只许用下列品种名与数字。禁止说「沪金99」「沪金连续」「黄金连续合约」"
        "「纽约黄金」等页面没有的叫法；国内现货称 AU9999，国际称纽约金/伦敦金。",
        "引用方式：嵌进口语短句，不要复述成项目符号看板。",
        calendar_clock_line(),
    ]
    if board.note:
        lines.append(f"说明：{board.note}")

    spoken: list[str] = []
    for sec in board.sections:
        bits: list[str] = []
        items = sec.items
        # 门店只取前 4 个，避免刷屏
        if sec.id == "shop":
            items = items[:4]
        for it in items:
            if it.price is None:
                continue
            chg = f"{it.change_pct:+.2f}%" if it.change_pct is not None else "—"
            tag = (it.freshness or "").strip() or "时间未知"
            unit = it.unit or "元/克"
            extra = f"，{it.note}" if it.note else ""
            bits.append(f"{it.name} {it.price} {unit}（{chg}，{tag}{extra}）")
            if it.change_pct is not None and len(spoken) < 4 and sec.id != "shop":
                direction = (
                    "涨了" if it.change_pct > 0 else ("跌了" if it.change_pct < 0 else "差不多平盘")
                )
                spoken.append(
                    f"{it.name}大概 {it.price}{unit}，{direction}约 {abs(it.change_pct):.2f}%"
                )
        if bits:
            lines.append(f"{sec.title}：{'；'.join(bits)}。")
        else:
            lines.append(f"{sec.title}：暂无报价。")

    if spoken:
        lines.append("口语参考：" + "。".join(spoken) + "。")
    return "\n".join(lines)
