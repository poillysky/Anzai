"""Seed basic A-share market knowledge cards into Postgres (idempotent by id)."""

from __future__ import annotations

from app.core.config import reload_settings
from app.services import knowledge as k

CARDS: list[dict[str, str]] = [
    {
        "id": "stop-loss-discipline",
        "title": "亏了怎么办，要不要死扛",
        "tags": "止损,风险,纪律",
        "body": (
            "账户已经明显偏离买入理由时，先问「逻辑还在不在」，别只问「会不会涨回来」。"
            "死扛常把小亏拖成大坑。可以说：仓位还能睡得着就留着观察，睡不着或逻辑破了，"
            "宜减不宜加。禁止保证「扛回去一定赚钱」。点位以行情工具为准。"
        ),
    },
    {
        "id": "catching-falling-knife",
        "title": "大跌了是不是抄底机会",
        "tags": "抄底,接飞刀,纪律",
        "body": (
            "急跌不等于便宜。没看清是情绪宣泄还是基本面坏掉之前，默认别一把梭抄底。"
            "更稳的说法：可以分批、先小仓试错，或等跌势缓和再谈；"
            "「腰斩必翻倍」是口号不是纪律。没问买卖就不要硬推加仓。"
        ),
    },
    {
        "id": "etf-vs-stock",
        "title": "ETF 和个股有啥不一样",
        "tags": "ETF,个股,基础",
        "body": (
            "ETF 更像一篮子资产，波动通常比单一个股钝；个股弹性大，也更容易被消息打穿。"
            "聊配置时：想跟大盘或行业、又怕单票暴雷，可以说更偏向指数/行业 ETF；"
            "想押某一家公司才聊个股。别把 ETF 涨跌直接说成「这只股票」。"
        ),
    },
    {
        "id": "index-vs-stock-strength",
        "title": "个股和大盘怎么比强弱",
        "tags": "相对强弱,大盘,基础",
        "body": (
            "大盘跌它也跌，不一定是它不行；大盘平它还跌，才更值得警惕。"
            "说话时用「跟指数比偏强/偏弱」，少甩「相对强弱」术语。"
            "必须先有指数和个股行情工具数字，再下结论；没有就说没比。"
        ),
    },
    {
        "id": "liquidity-basics",
        "title": "成交清淡要注意啥",
        "tags": "流动性,成交,基础",
        "body": (
            "成交很稀的时候，报价容易跳、买卖难成交，看着涨跌幅唬人。"
            "仓位大或想进出自由时，要提醒对方注意流动性，别按「想卖就能卖那个价」算。"
            "具体量能看工具，别编造成交额。"
        ),
    },
    {
        "id": "valuation-pe-simple",
        "title": "市盈率大概怎么理解",
        "tags": "估值,市盈率,基础",
        "body": (
            "市盈率粗看「用多少年利润回本」的感觉，高不一定贵、低不一定便宜，"
            "要结合成长和行业。安崽没查到可靠估值数据时，别随口报 PE/PB。"
            "可以说「估值这块我这会儿没实数，先别当买卖主因」。"
        ),
    },
    {
        "id": "rumor-vs-fact",
        "title": "听说有利好能不能追",
        "tags": "消息,传闻,纪律",
        "body": (
            "朋友圈、群传闻默认当噪音。没有新闻工具里的今日条目和行情数字，"
            "别把「听说」讲成确定催化。就算有新闻，也要分清是已定价还是突发；"
            "旧闻别当今天突发。倾向：消息不清时观望或轻仓，别满仓赌传闻。"
        ),
    },
    {
        "id": "bull-bear-choppy",
        "title": "牛市熊市震荡怎么说话",
        "tags": "牛市,熊市,震荡,基础",
        "body": (
            "短短几天涨跌定不了牛熊。更稳妥：用「这段偏强/偏弱/来回震荡」描述体感，"
            "并挂上指数工具数字。震荡市里追涨杀跌最容易两头挨打，"
            "可以说降低交易频率、仓位别打满。禁止断言「牛市开始了/熊市结束了」。"
        ),
    },
    {
        "id": "sector-rotation-simple",
        "title": "板块轮动怎么跟人讲",
        "tags": "板块,轮动,基础",
        "body": (
            "板块今天强不代表明天还强。先看板块工具涨跌和个股是否跟得上，"
            "再白话说「这方向这几天更热/更冷」。别编造资金流向或龙虎榜数字。"
            "仓里已经很重单一板块时，热度更高反而更要提醒集中度风险。"
        ),
    },
    {
        "id": "long-vs-short-horizon",
        "title": "长线和短线别混着聊",
        "tags": "周期,长线,短线,基础",
        "body": (
            "对方问今天追不追，就别用「十年长牛」糊弄；问长期配置，就别被分时风吹草动带跑。"
            "回答先对齐时间尺度：短线看位置和节奏，长线看逻辑和仓位承受力。"
            "两套标准混用，建议会自相矛盾。"
        ),
    },
    {
        "id": "dividend-mindset",
        "title": "分红和股价别算糊涂账",
        "tags": "分红,基础",
        "body": (
            "分红到账不等于白捡：股权登记后股价常除权除息，总资产要合并看。"
            "别把「高股息」直接说成稳赚；公司能不能持续分红，安崽没财报工具就别打包票。"
            "有仓聊分红时，提醒看的是总回报，不是红包数字本身。"
        ),
    },
    {
        "id": "no-leverage-default",
        "title": "融资加杠杆默认怎么劝",
        "tags": "杠杆,融资,风险",
        "body": (
            "个人账户默认不鼓励融资加杠杆：赚的时候爽，跌的时候可能被逼平仓。"
            "对方没提杠杆就不要主动教加杠杆；提了就说清风险，倾向「现有仓位先弄明白再谈」。"
            "禁止怂恿满仓融资博反弹。"
        ),
    },
]


def main() -> None:
    reload_settings()
    k.reset_embed_cooldown()
    ok = 0
    for c in CARDS:
        result = k.save_card(
            card_id=c["id"],
            title=c["title"],
            tags=c["tags"],
            source="安崽经验库·基础常识",
            card_date="2026-08-06",
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
    total, with_emb = __import__(
        "app.services.knowledge_pg", fromlist=["count_cards"]
    ).count_cards()
    print(f"done {ok} cards; db total={total} with_embedding={with_emb}")


if __name__ == "__main__":
    main()
