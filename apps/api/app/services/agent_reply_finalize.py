"""Post-stream reply cleanup — strip leaks / reasoning / incomplete tails."""

from __future__ import annotations

import re

_REASONING_BLOCKS = (
    re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE),
    re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE),
    re.compile(r"<reasoning>[\s\S]*?</reasoning>", re.IGNORECASE),
    re.compile(r"<redacted_reasoning>[\s\S]*?</redacted_reasoning>", re.IGNORECASE),
)
_REASONING_OPEN = re.compile(
    r"<(?:think|thinking|reasoning|redacted_reasoning)\b[^>]*>[\s\S]*\Z",
    re.IGNORECASE,
)

_TOOL_LEAKS = (
    re.compile(r"```(?:json|tool|tools)?\s*[\s\S]*?```", re.IGNORECASE),
    re.compile(
        r"(?:^|\n)\s*(?:tool_call|function_call|tool_calls)\s*[:=].*(?:\n|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\n)\s*\{[^{}]{0,40}\"(?:name|arguments|tool_calls)\"[\s\S]{0,800}?\}(?:\n|$)"
    ),
)

_SYSTEM_LEAK_MARKERS = (
    "【本轮实时查询】",
    "【本轮分析已完成",
    "【强制行情】",
    "【分析师 Skill",
    "【会话记忆】",
    "【口径提示】",
    "【数据说明】",
)

_STOP_STRINGS = (
    "<|endoftext|>",
    "<|im_end|>",
    "<|eot_id|>",
    "\nuser:",
    "\nUser:",
    "\nHuman:",
    "\n【本轮",
)

_SENTENCE_END = set(".!?。！？…\"”）」』】*")


def _strip_reasoning(text: str) -> str:
    out = text
    for pat in _REASONING_BLOCKS:
        out = pat.sub("", out)
    out = _REASONING_OPEN.sub("", out)
    return out


def _strip_tool_leaks(text: str) -> str:
    out = text
    for pat in _TOOL_LEAKS:
        out = pat.sub("\n", out)
    return out


def _cut_system_leak(text: str) -> str:
    out = text
    for marker in _SYSTEM_LEAK_MARKERS:
        idx = out.find(marker)
        if idx >= 0:
            # Keep text before accidental dump of prompt guts
            if idx > 20:
                out = out[:idx].rstrip()
            else:
                # Entire reply is a dump — drop marker lines
                lines = [
                    ln
                    for ln in out.splitlines()
                    if not any(m in ln for m in _SYSTEM_LEAK_MARKERS)
                ]
                out = "\n".join(lines)
    return out


def _apply_stop_strings(text: str) -> str:
    out = text
    for stop in _STOP_STRINGS:
        pos = out.find(stop)
        if pos >= 0:
            out = out[:pos]
    # Partial stop at end (e.g. trailing "<|endof")
    for stop in _STOP_STRINGS:
        for j in range(len(stop), 0, -1):
            if out.endswith(stop[:j]) and j < len(stop):
                out = out[: -j]
                break
    return out


def _strip_markdown_bold(text: str) -> str:
    # **x** / __x__ → x（Skill 禁止加粗，收尾再清一遍）
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    out = re.sub(r"__([^_]+)__", r"\1", out)
    return out


def trim_to_end_sentence(text: str) -> str:
    """Trim incomplete trailing fragment back to last sentence end / emoji."""
    if not text:
        return ""
    chars = list(text)
    last = -1
    for i in range(len(chars) - 1, -1, -1):
        ch = chars[i]
        # rough emoji / CJK punctuation
        if ch in _SENTENCE_END or (ord(ch) > 0x1F300 and ord(ch) < 0x1FAFF):
            if ch not in _SENTENCE_END and i > 0 and chars[i - 1] in " \t\n":
                last = i - 1
            else:
                last = i
            break
    if last < 0:
        return text.rstrip()
    return "".join(chars[: last + 1]).rstrip()


def finalize_assistant_text(
    raw: str,
    *,
    trim_incomplete: bool = True,
) -> str:
    """Clean model output before persist / UI replace."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _strip_reasoning(text)
    text = _strip_tool_leaks(text)
    text = _cut_system_leak(text)
    text = _apply_stop_strings(text)
    text = _strip_markdown_bold(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[^\S\r\n]+$", "", text, flags=re.MULTILINE)
    text = text.strip()
    if trim_incomplete and text and text[-1] not in _SENTENCE_END:
        # Only trim when there is a clear incomplete tail (≥4 chars after last end)
        trimmed = trim_to_end_sentence(text)
        if trimmed and len(trimmed) >= max(8, int(len(text) * 0.55)):
            text = trimmed
    return text.strip()
