"""CJK-aware token estimate + newest-first context trim (BrewStory-style)."""

from __future__ import annotations

from typing import Any

# Default model context window for packing (leave headroom for gateway quirks).
DEFAULT_MAX_CONTEXT = 24_000
DEFAULT_TOKEN_PADDING = 64


def estimate_tokens(text: str) -> int:
    """Heuristic: CJK ≈ 1.1 tok/char; latin ≈ 4 chars/tok."""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        c = ord(ch)
        if (
            (0x2E80 <= c <= 0x9FFF)
            or (0xF900 <= c <= 0xFAFF)
            or (0xFF00 <= c <= 0xFFEF)
            or (0x3400 <= c <= 0x4DBF)
        ):
            cjk += 1
        else:
            other += 1
    return max(1, int(cjk * 1.1 + other / 4 + 0.999))


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, list):
        text = "".join(
            (p.get("text") if isinstance(p, dict) else str(p)) or "" for p in content
        )
    else:
        text = str(content or "")
    return estimate_tokens(text) + 4


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def trim_messages_to_budget(
    messages: list[dict[str, Any]],
    max_context: int,
    reserve_for_reply: int,
    *,
    token_padding: int = DEFAULT_TOKEN_PADDING,
) -> list[dict[str, Any]]:
    """Keep leading contiguous system block; fill the rest newest-first until budget."""
    padding = max(0, int(token_padding))
    budget = max(512, int(max_context) - int(reserve_for_reply) - padding - 3)
    if estimate_messages_tokens(messages) <= budget:
        return messages

    i = 0
    prefix: list[dict[str, Any]] = []
    while i < len(messages) and (messages[i].get("role") or "") == "system":
        prefix.append(messages[i])
        i += 1
    rest = messages[i:]

    remaining = budget - estimate_messages_tokens(prefix)
    if remaining < 32:
        tail = rest[-2:] if rest else []
        return [*prefix, *tail]

    kept: list[dict[str, Any]] = []
    for j in range(len(rest) - 1, -1, -1):
        cost = estimate_message_tokens(rest[j])
        if cost <= remaining:
            kept.insert(0, rest[j])
            remaining -= cost
        else:
            break

    if not kept and rest:
        return [*prefix, *rest[-min(2, len(rest)) :]]
    return [*prefix, *kept]
