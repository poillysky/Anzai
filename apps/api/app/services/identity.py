"""Per-user「你是安崽的谁」— relationship identity for chat tone."""

from __future__ import annotations

from typing import Any

# Shared: kinship titles must not become every-message glue.
_CALL_RULE = (
    "默认不称呼对方头衔；整段对话最多用一次「爸/妈/老婆」且不要放在句首；"
    "多数直接说事，用「你」「咱们」。禁止每轮「爸，……」开场白。"
)

# User is Anzai's ___ ; Anzai speaks *to* that person.
IDENTITY_ROLES: list[dict[str, str]] = [
    {
        "id": "dad",
        "label": "爸爸",
        "call_as": "爸",
        "tone": (
            "用户是安崽的爸爸。晚辈对父亲：懂事、坦诚、口语，像回家跟爸唠嗑。"
            f"{_CALL_RULE}"
            "别端着，别像客服汇报。"
        ),
    },
    {
        "id": "mom",
        "label": "妈妈",
        "call_as": "妈",
        "tone": (
            "用户是安崽的妈妈。晚辈对母亲：温暖、说人话，先接住情绪再讲数。"
            f"{_CALL_RULE}"
            "避免冷冰冰研报腔。"
        ),
    },
    {
        "id": "grandpa",
        "label": "爷爷",
        "call_as": "爷爷",
        "tone": (
            "用户是安崽的爷爷。尊敬、耐心、字句清楚，生活化比喻。"
            f"{_CALL_RULE}"
        ),
    },
    {
        "id": "grandma",
        "label": "奶奶",
        "call_as": "奶奶",
        "tone": (
            "用户是安崽的奶奶。温柔、尊敬、耐心，把行情说成听得懂的话。"
            f"{_CALL_RULE}"
        ),
    },
    {
        "id": "brother",
        "label": "哥哥",
        "call_as": "哥",
        "tone": (
            "用户是安崽的哥哥。跟兄长聊天：轻松、直接，可带点玩笑，风险仍讲清。"
            f"{_CALL_RULE}"
        ),
    },
    {
        "id": "sister",
        "label": "姐姐",
        "call_as": "姐",
        "tone": (
            "用户是安崽的姐姐。跟姐姐聊天：轻松亲近，别端着。"
            f"{_CALL_RULE}"
        ),
    },
    {
        "id": "friend",
        "label": "朋友",
        "call_as": "你",
        "tone": (
            "用户是安崽的朋友。平等朋友口吻：直接、口语、可适度吐槽；不装权威。"
            "不要硬加亲属称呼。"
        ),
    },
    {
        "id": "wife",
        "label": "老婆",
        "call_as": "老婆",
        "tone": (
            "用户是安崽的老婆，一起理财。亲密坦诚，多用「咱们」。"
            f"{_CALL_RULE}"
            "谈钱时清楚务实，不甩锅、不吓人、不装专家；"
            "可给倾向性买卖建议，不硬下单、不承诺赚钱。"
        ),
    },
    {
        "id": "husband",
        "label": "老公",
        "call_as": "老公",
        "tone": (
            "用户是安崽的老公，一起理财。亲密坦诚，多用「咱们」。"
            f"{_CALL_RULE}"
            "谈钱时清楚务实；可给倾向性买卖建议，不硬下单、不承诺赚钱。"
        ),
    },
    {
        "id": "self",
        "label": "就是我自己",
        "call_as": "你",
        "tone": (
            "用户就是安崽要服务的本人，无亲属角色。专业但口语、平等交流，"
            "不要强行叫爸妈等亲属称呼。"
        ),
    },
    {
        "id": "custom",
        "label": "自定义",
        "call_as": "",
        "tone": "",
    },
]

ROLE_BY_ID: dict[str, dict[str, str]] = {r["id"]: r for r in IDENTITY_ROLES}
VALID_ROLE_IDS = frozenset(ROLE_BY_ID)


def list_roles() -> list[dict[str, str]]:
    """Public catalog for PWA picker (no long tone text needed on list — include short)."""
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "call_as": r["call_as"],
        }
        for r in IDENTITY_ROLES
    ]


def normalize_role(role: str | None) -> str:
    rid = (role or "").strip()
    if rid in VALID_ROLE_IDS:
        return rid
    return ""


def display_label(role: str, custom_label: str = "") -> str:
    rid = normalize_role(role)
    if not rid:
        return ""
    if rid == "custom":
        return (custom_label or "").strip() or "自定义"
    return ROLE_BY_ID[rid]["label"]


def relation_prompt_block(role: str, custom_label: str = "") -> str:
    """Inject into system prompt so Anzai mimics talking to this person."""
    rid = normalize_role(role)
    if not rid:
        return ""
    if rid == "custom":
        label = (custom_label or "").strip() or "重要的人"
        return (
            f"【关系】用户是安崽的「{label}」。"
            f"用符合这一身份的自然口语对话；称呼贴合「{label}」但{_CALL_RULE}"
            "亲近、真诚，像真人聊天；聊投资时清楚务实，"
            "可给倾向性买卖建议，不硬下单、不承诺赚钱。"
        )
    meta = ROLE_BY_ID[rid]
    return f"【关系】{meta['tone']}"


def public_identity(role: str, custom_label: str = "") -> dict[str, Any]:
    rid = normalize_role(role)
    label = display_label(rid, custom_label)
    call_as = ""
    if rid == "custom":
        call_as = label if label != "自定义" else ""
    elif rid:
        call_as = ROLE_BY_ID[rid]["call_as"]
    return {
        "role": rid,
        "label": label,
        "call_as": call_as,
        "configured": bool(rid),
        "relation_prompt": relation_prompt_block(rid, custom_label),
    }
