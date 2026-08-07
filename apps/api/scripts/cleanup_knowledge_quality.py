"""Audit cleanup: delete low-value/redundant cards, upgrade merge targets."""

from __future__ import annotations

from pathlib import Path

from app.core.config import reload_settings
from app.services import knowledge as k
from app.services import knowledge_pg as pg

# Delete: redundant after merge, or too thin / low agent value
DELETE_IDS: list[str] = [
    # folded into cash-flow / FCF
    "kb-ocf-vs-profit",
    "kb-fcf-yield",
    # folded into ROE
    "kb-dupont-roe",
    # folded into boards / ST
    "kb-limit-20-boards",
    "kb-limit-st-five",
    # folded into leverage default
    "compliance-leverage-warning",
    # folded into revenge trading
    "mkt-martingale-danger",
    # folded into FOMO
    "kb-herding",
    # folded into thesis kill / cash position / main-force / diversify / tips / PE / core-sat / knife
    "kb-order-checklist",
    "kb-pre-mortem",
    "mkt-dry-powder",
    "mkt-chip-distribution-caution",
    "mkt-crisis-correlation",
    "compliance-conflict-tone",
    "mkt-equal-vs-cap-weight",
    "kb-barbell-alloc",
    "mkt-peg-rough",
    "kb-gambler-fallacy",
    # low substance / edge noise
    "mkt-soe-vs-private",
    "mkt-fibonacci-caution",
    "kb-futures-basis",
    "term-beta-vol",
    "mkt-price-time-priority",
    "kb-partial-fill",  # merge into cancel-replace upgrade as order mechanics
]

UPGRADES: list[dict[str, str]] = [
    {
        "id": "mkt-cash-flow-matters",
        "title": "现金流、利润和自由现金流",
        "tags": "现金流,自由现金流,利润质量,财报",
        "body": (
            "净利润是会计结果；经营现金流看主业现金进没进来；自由现金流再扣必要资本开支，看还剩多少真钱。"
            "长期「利润很好、经营现金/自由现金流很差」要警惕；某季波动也可能正常。"
            "安崽没报表就别报具体数字，有就白话讲差额方向与可持续性。"
        ),
    },
    {
        "id": "kb-free-cash-flow",
        "title": "自由现金流和FCF收益率",
        "tags": "自由现金流,FCF收益率,估值,财报",
        "body": (
            "自由现金流粗看经营现金扣掉维持与发展所需资本开支后的剩余。"
            "用它相对市值/企业价值，可粗看「真现金回报厚度」，但波动大的公司这个比率会晃。"
            "强调口径与可持续，不编造 FCF；也不单靠一个收益率下买卖结论。"
        ),
    },
    {
        "id": "mkt-roe-simple",
        "title": "ROE和杜邦拆解",
        "tags": "ROE,杜邦,杠杆,财报",
        "body": (
            "ROE 粗看股东权益赚钱效率。杜邦可拆成利润率、周转、杠杆：同样 ROE，靠加杠杆堆出来的通常更脆。"
            "长期稳定且质量干净的 ROE 更有参考，单季暴增要查是否一次性。"
            "没财报别编 ROE，更别神化单一指标。"
        ),
    },
    {
        "id": "term-boards-ashare",
        "title": "主板创业板科创板与涨跌幅",
        "tags": "主板,创业板,科创板,涨跌幅,术语",
        "body": (
            "主板偏相对成熟公司；创业板、科创板门槛、适当性与涨跌幅规则不同，后两者单日弹性通常更大（常讨论约±20%，以现行规则为准）。"
            "别拿主板经验硬套。权限没开通别硬冲；具体限制查交易所规则，安崽不背死数字吓人。"
        ),
    },
    {
        "id": "compliance-st-risk",
        "title": "ST、退市与更窄涨跌停",
        "tags": "合规,ST,退市,涨跌停",
        "body": (
            "ST、*ST 等提示财务或规范风险，波动与退市可能更大；风险警示股涨跌幅限制也常更窄（常见讨论约±5%，以规则为准）。"
            "窄限制不等于更安全，低价也不等于捡便宜。不鼓励博重组。"
            "是否 ST、退市进程与交易安排以交易所/公告为准，禁止编造。"
        ),
    },
    {
        "id": "no-leverage-default",
        "title": "杠杆、融资与借钱炒股",
        "tags": "杠杆,融资,借钱,风险,合规",
        "body": (
            "个人账户默认不鼓励融资/加杠杆：收益放大时亏损与强平风险也放大，还有利息成本。"
            "消费贷、信用卡、场外配资拿去炒股更危险——场外配资尤其要劝停。"
            "对方没提杠杆别主动教；提了就说清风险，禁止怂恿满仓融资或借钱翻本。"
        ),
    },
    {
        "id": "mkt-revenge-trading",
        "title": "亏了想翻本和越亏越加倍",
        "tags": "报复交易,马丁格尔,心理,纪律",
        "body": (
            "刚亏损就加码搏一把，或「越亏越加倍摊回」，常把小伤变成爆仓式伤害——那是赌徒进度条，不是策略。"
            "正确节奏：停一停、复盘错在哪、仓位降下来再谈。禁止怂恿当场加倍。"
        ),
    },
    {
        "id": "mkt-fomo",
        "title": "怕踏空和从众抢热点",
        "tags": "FOMO,从众,心理,纪律",
        "body": (
            "怕踏空、看周围都在买，最容易在拥挤处下手；错过一段涨幅不是末日，满仓接最后一棒更痛。"
            "热点可以研究，但要有自己的理由与仓位上限，别把人群当风控。"
            "安崽话术：机会还有下一班车，先保账户留在牌桌上。"
        ),
    },
    {
        "id": "kb-thesis-kill-criteria",
        "title": "买入逻辑、失效条件和下单清单",
        "tags": "投资逻辑,失效条件,清单,纪律",
        "body": (
            "买入时写清逻辑，并写清什么情况证明自己错了（份额丢失、价格战、债务爆雷等）；触发就减，不改口编新故事。"
            "下单前再问：逻辑还在吗？仓位超限吗？是不是情绪单？查过公告/行情吗？"
            "也可做尸检预演：假设一年后亏很惨，最可能死在哪——写不出来就先别下重仓。"
        ),
    },
    {
        "id": "mkt-cash-is-position",
        "title": "现金、子弹也是仓位",
        "tags": "现金,子弹,仓位,纪律",
        "body": (
            "持币不是无作为，而是保留购买力与容错；子弹是为下跌或计划内机会留的子弹，不是逼自己花掉的死钱。"
            "没好价格或逻辑不清时，现金可以是正确答案。别被「必须做事」逼进场。"
        ),
    },
    {
        "id": "mkt-main-force-myth",
        "title": "主力控盘和筹码图别神化",
        "tags": "主力,筹码,神话,纪律",
        "body": (
            "口头「主力控盘」常被用来解释一切涨跌，缺公开证据时只是故事；软件筹码图是模型估算，不是交易所真人持仓名单。"
            "安崽少用控盘叙事，多用公告、成交、估值与仓位纪律。禁止帮人算「庄家成本」或「筹码峰决定明天必涨」。"
        ),
    },
    {
        "id": "mkt-correlation-diversify",
        "title": "分散和危机时一起跌",
        "tags": "分散,相关性,危机,风险",
        "body": (
            "分散能降低单一公司暴雷，但不保证组合不回撤：压力时期抢流动性，股票债券商品等可能同步承压。"
            "别把「买了很多只」当成绝对安全。极端情景先谈生存与杠杆，再谈抄底故事。"
        ),
    },
    {
        "id": "compliance-no-tip-stocks",
        "title": "不荐牛股、不做带单老师",
        "tags": "合规,荐股,定位,纪律",
        "body": (
            "安崽是工具型助手：查行情、理组合、讲纪律与公开信息；不是收费荐股群、不是带单老师。"
            "禁止点名「必涨牛股」「内部消息票」「跟庄名单」。对方求暗号/仓位模板时拉回："
            "可以分析你提出的标的与风险，不提供神秘信号。"
        ),
    },
    {
        "id": "valuation-pe-simple",
        "title": "市盈率和PEG怎么听",
        "tags": "市盈率,PEG,估值,基础",
        "body": (
            "市盈率粗看「用多少年利润回本」的感觉，高不一定贵、低不一定便宜，要结合成长与行业。"
            "PEG 想把 PE 和增长放一起，增长假设错了就失效，别背「PEG小于1就买」。"
            "没可靠数据时别随口报 PE/PEG，也别单靠倍数下买卖结论。"
        ),
    },
    {
        "id": "mkt-core-satellite",
        "title": "核心卫星和哑铃配置",
        "tags": "配置,核心卫星,哑铃,基础",
        "body": (
            "核心用宽基/稳健仓打底，卫星用少量资金表达主题或弹性；也有人用「一头极稳现金短债、一头少量高风险」的哑铃结构。"
            "卫星/高风险一头亏光不伤筋骨是目标。别把卫星做成事实上的全仓赌博；先有风险预算再谈结构。"
        ),
    },
    {
        "id": "catching-falling-knife",
        "title": "大跌了就能抄底吗",
        "tags": "抄底,接飞刀,赌徒谬误,纪律",
        "body": (
            "急跌不等于便宜；「都跌这么多了该反弹了」也可能是赌徒谬误——跌深只说明已经跌了，不证明下一步必涨。"
            "没看清情绪宣泄还是基本面坏掉之前，默认别一把梭。可以分批小仓或等跌势缓和再谈。"
            "「腰斩必翻倍」是口号不是纪律；没问买卖就不要硬推加仓。"
        ),
    },
    {
        "id": "mkt-index-weight-bias",
        "title": "市值加权、等权和指数偏科",
        "tags": "指数,权重,等权,基础",
        "body": (
            "市值加权指数里越大权重越大，常被龙头带着走；等权让小权重声音更大，风格不同。"
            "所以「指数涨了你的股票不涨」可以发生。选 ETF 看清跟踪哪类加权；没说明书别瞎猜。"
        ),
    },
    {
        "id": "kb-cancel-replace-order",
        "title": "部分成交、撤单和改单",
        "tags": "下单,部分成交,撤单,交易",
        "body": (
            "限价单可能只成交一部分，剩余继续挂着或需撤改；没成交前通常可撤，改价常等于撤了重挂、时间优先级可能重置。"
            "流动性差或剧烈波动时，别默认「点了就按心理价全成」，想撤也可能来不及。"
            "规则与回报以券商界面为准。"
        ),
    },
]


def _write_md(card: dict[str, str], source: str) -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    out.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"id: {card['id']}\n"
        f"title: {card['title']}\n"
        f"tags: {card['tags']}\n"
        f"source: {source}\n"
        f"date: 2026-08-06\n"
        "---\n"
        f"{card['body']}\n"
    )
    (out / f"{card['id']}.md").write_text(text, encoding="utf-8")


def main() -> None:
    reload_settings()
    k.reset_embed_cooldown()
    existing = {r["id"] for r in pg.list_cards()}
    deleted = 0
    missing = []
    for cid in DELETE_IDS:
        if cid not in existing:
            missing.append(cid)
            continue
        if k.delete_card(cid):
            deleted += 1
            md = Path(__file__).resolve().parents[1] / "data" / "knowledge" / f"{cid}.md"
            if md.exists():
                md.unlink()
            print("DEL", cid)
        else:
            print("FAIL_DEL", cid)

    upgraded = 0
    for c in UPGRADES:
        if c["id"] not in existing and c["id"] not in DELETE_IDS:
            # may still exist if not deleted
            pass
        row = pg.get_card(c["id"])
        source = (row or {}).get("source") or "安崽经验库·整理"
        result = k.save_card(
            card_id=c["id"],
            title=c["title"],
            tags=c["tags"],
            source=str(source),
            card_date="2026-08-06",
            body=c["body"],
            path="admin-cleanup",
            reembed=True,
        )
        _write_md(c, str(source))
        upgraded += 1
        print(
            ("OK" if result.get("embedded") else "WARN"),
            "UP",
            c["id"],
            "embedded=",
            result.get("embedded"),
        )

    total, with_e = pg.count_cards()
    print(
        f"done deleted={deleted} missing_delete={len(missing)} "
        f"upgraded={upgraded}; db total={total} with_embedding={with_e}"
    )
    if missing:
        print("missing:", ", ".join(missing))


if __name__ == "__main__":
    main()
