"""Seed A-share terminology knowledge cards into Postgres (idempotent by id)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import reload_settings
from app.services import knowledge as k

SOURCE = "安崽经验库·术语"
DATE = "2026-08-06"

CARDS: list[dict[str, str]] = [
    {
        "id": "term-limit-up-down",
        "title": "涨停跌停是啥意思",
        "tags": "术语,涨停,跌停",
        "body": (
            "涨停/跌停是当日价格到了交易规则允许的涨跌上限，常见主板约±10%，"
            "创业板/科创板等规则不同（别背死数字，以交易所规则为准）。"
            "涨停不等于明天必涨，跌停也不等于明天必跌；封板能否成交看买卖盘。"
            "没行情工具就别断言「已经板了」。"
        ),
    },
    {
        "id": "term-t-plus-one",
        "title": "T加一是啥",
        "tags": "术语,T+1,交易规则",
        "body": (
            "A股股票多数是 T+1：今天买的，最早明天才能卖。"
            "所以日内冲进去又反悔，当天往往出不来。跟对方讲计划时要提醒这一点，"
            "别按「随时进出」的美股直觉给建议。"
        ),
    },
    {
        "id": "term-turnover-rate",
        "title": "换手率怎么理解",
        "tags": "术语,换手率,成交",
        "body": (
            "换手率大致是「这段时间有多少筹码换了手」，偏高常见于分歧大、情绪热；"
            "偏低可能交投清淡。高低没有万能阈值，要对照它自己历史和板块。"
            "安崽没工具数字时别随口说「换手爆炸/地量」。"
        ),
    },
    {
        "id": "term-amplitude",
        "title": "振幅是啥",
        "tags": "术语,振幅,波动",
        "body": (
            "振幅看的是当日最高到最低晃了多大，不是收盘涨跌幅。"
            "振幅大=当天拉扯狠，容易把追涨杀跌的人两边打脸。"
            "可以说「今天晃得厉害」，具体百分比以行情为准。"
        ),
    },
    {
        "id": "term-market-cap",
        "title": "总市值和流通市值",
        "tags": "术语,市值,流通",
        "body": (
            "总市值≈股价×全部股本；流通市值≈股价×能自由买卖的那部分股本。"
            "聊「盘子大小、好不好进出」时更常看流通市值；别把两者混着甩数字。"
            "没查到就说没实数，别编造几百亿几千亿。"
        ),
    },
    {
        "id": "term-pb-simple",
        "title": "市净率大概怎么说",
        "tags": "术语,市净率,估值",
        "body": (
            "市净率（PB）粗看「股价相对净资产」贵不贵，银行地产等账面重的行业更常拿来聊。"
            "PB低不一定便宜，资产质量差也能压低。没可靠数据时别报具体 PB，"
            "也别单靠一个估值倍数下买卖结论。"
        ),
    },
    {
        "id": "term-dividend-yield",
        "title": "股息率是啥",
        "tags": "术语,股息率,分红",
        "body": (
            "股息率≈每股分红/股价，像「按现价买进去，分红能带来多高的现金回报感」。"
            "高股息不等于稳赚，还要看分红能不能持续、股价会不会先跌一截。"
            "提醒：除权除息后股价常下调，别只盯红包数字。"
        ),
    },
    {
        "id": "term-ex-rights",
        "title": "除权除息复权咋回事",
        "tags": "术语,除权,复权",
        "body": (
            "分红、送转后交易所常做除权除息，K线价格会往下调一截，不是公司一夜蒸发。"
            "复权是把历史价按规则接回去，方便看长期走势。聊「腰斩/翻倍」时要问清看的是复权还是不复权，"
            "别拿除权缺口吓人。"
        ),
    },
    {
        "id": "term-auction",
        "title": "集合竞价开盘收盘",
        "tags": "术语,集合竞价,开盘",
        "body": (
            "开盘/收盘前后有一段集合竞价：大家先挂单，再撮合成一个开盘价或收盘价。"
            "集合竞价里的「突然拉高/砸低」不一定代表全天方向，容易被情绪单带节奏。"
            "没开盘实价时别把竞价意愿说成已成交事实。"
        ),
    },
    {
        "id": "term-support-resistance",
        "title": "支撑位阻力位白话",
        "tags": "术语,支撑,阻力,技术",
        "body": (
            "支撑像「以前多次在这附近有人接」，阻力像「涨到这附近常有人抛」。"
            "这是经验位置不是防火墙，跌破/突破可以假突破。安崽说话用「附近有人关注的区域」，"
            "少装神弄鬼画死线；没有行情图就别点具体价位。"
        ),
    },
    {
        "id": "term-moving-average",
        "title": "均线大概是啥",
        "tags": "术语,均线,MA,技术",
        "body": (
            "均线是过去一段时间收盘价的平均，短均线更敏感、长均线更钝。"
            "价格在均线上方偏强、下方偏弱只是简化说法，不是买卖圣旨。"
            "别把「金叉必涨、死叉必跌」当纪律；具体均线数值以行情工具为准。"
        ),
    },
    {
        "id": "term-macd-rsi-caution",
        "title": "MACD、RSI这类指标咋用",
        "tags": "术语,MACD,RSI,技术指标",
        "body": (
            "MACD、RSI 等是把价格波动加工后的参考信号，滞后且会假信号，"
            "适合辅助看超买超卖或动能变化，不适合单独当买卖开关。"
            "安崽没打开指标就别编「MACD金叉了」；有也不要说成必涨必跌。"
        ),
    },
    {
        "id": "term-etf-premium",
        "title": "ETF溢价折价",
        "tags": "术语,ETF,溢价,折价",
        "body": (
            "ETF 市价相对净值（一篮子资产估算值）贵了叫溢价，便宜了叫折价。"
            "高溢价追买容易多付「情绪税」，大幅折价也要问清能不能顺畅套利、流动性怎样。"
            "没净值/溢价数据就别断言「严重溢价」。"
        ),
    },
    {
        "id": "term-boards-ashare",
        "title": "主板创业板科创板粗分",
        "tags": "术语,主板,创业板,科创板",
        "body": (
            "主板偏相对成熟公司；创业板、科创板门槛与涨跌规则、投资者适当性更不一样，"
            "波动往往更大。跟用户说话时用「板块规则不同、波动可能更猛」，"
            "别背错涨跌幅限制；不确定就说以交易所规则为准。"
        ),
    },
    {
        "id": "term-margin-short",
        "title": "融资和融券别混",
        "tags": "术语,融资,融券,两融",
        "body": (
            "融资是借钱买股票（做多加杠杆）；融券是借股票先卖、指望低价买回（做空方向）。"
            "两融都有利息、担保和强平风险。个人默认不鼓励加杠杆；"
            "对方问融券时说清机制和风险，别教人裸奔式做空。"
        ),
    },
    {
        "id": "term-northbound",
        "title": "北向资金怎么听",
        "tags": "术语,北向,外资",
        "body": (
            "北向资金常指境外投资者经互联互通买 A 股的流向，媒体爱当情绪温度计。"
            "单日流入流出噪音大，别把一天北向说成「外资全面看多/看空」。"
            "没权威数据就别编造净买入金额。"
        ),
    },
    {
        "id": "term-volume-price",
        "title": "量价配合白话",
        "tags": "术语,量价,成交量",
        "body": (
            "价涨量增常被说成有人愿意追；价涨量缩可能后劲不足；价跌量增压力大——"
            "都是经验口诀，不是定律。安崽用「涨的时候有没有量跟」白话讲，"
            "具体成交量必须来自行情工具。"
        ),
    },
    {
        "id": "term-position-words",
        "title": "满仓半仓空仓啥意思",
        "tags": "术语,仓位,满仓",
        "body": (
            "满仓≈资金基本都买成了票；半仓≈大约一半在票里；空仓≈基本持币观望。"
            "仓位是风险旋钮：越满，波动对账户伤害越大。建议先问对方睡不睡得着，"
            "再谈加减，别默认人人都该满仓。"
        ),
    },
    {
        "id": "term-beta-vol",
        "title": "波动和大盘敏感度",
        "tags": "术语,波动,Beta,风险",
        "body": (
            "有的票跟大盘同涨同跌很紧（敏感度高），有的更「独自发癫」。"
            "白话说「它平时比大盘晃得更厉害/更钝」即可，不必硬甩 Beta 术语。"
            "没数据别编波动率数字；仓位建议要匹配对方承受力。"
        ),
    },
    {
        "id": "term-stop-take-profit",
        "title": "止损和止盈术语",
        "tags": "术语,止损,止盈",
        "body": (
            "止损是「错了认栽、把亏控制住」；止盈是「对了也落袋、别把利润坐电梯」。"
            "关键不是百分比魔术数字，而是买入理由是否失效、还能不能睡得着。"
            "禁止保证「设了止损就一定少亏」——极端行情可能跳空或流动性差。"
        ),
    },
]


def _write_markdown() -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    out.mkdir(parents=True, exist_ok=True)
    for c in CARDS:
        text = (
            "---\n"
            f"id: {c['id']}\n"
            f"title: {c['title']}\n"
            f"tags: {c['tags']}\n"
            f"source: {SOURCE}\n"
            f"date: {DATE}\n"
            "---\n"
            f"{c['body']}\n"
        )
        (out / f"{c['id']}.md").write_text(text, encoding="utf-8")


def main() -> None:
    reload_settings()
    k.reset_embed_cooldown()
    _write_markdown()
    ok = 0
    for c in CARDS:
        result = k.save_card(
            card_id=c["id"],
            title=c["title"],
            tags=c["tags"],
            source=SOURCE,
            card_date=DATE,
            body=c["body"],
            path="admin-seed",
            reembed=True,
        )
        print(
            ("OK" if result.get("embedded") else "WARN"),
            result.get("id"),
            "embedded=",
            result.get("embedded"),
        )
        ok += 1
    from app.services.knowledge_pg import count_cards

    total, with_emb = count_cards()
    print(f"done {ok} cards; db total={total} with_embedding={with_emb}")


if __name__ == "__main__":
    main()
