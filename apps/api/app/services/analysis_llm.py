"""Analysis committee chat completion (sync) — uses dedicated analysis LLM config."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import reload_settings
from app.services import analysis_connection as analysis_conn_svc

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")

_MAX_RETRIES = 4
_RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


def friendly_llm_error(exc: BaseException) -> str:
    """Short user-facing reason — never dump raw JSON bodies into the report UI."""
    msg = str(exc or "").strip()
    low = msg.lower()
    if "429" in msg or "rate_limit" in low or "rate limit" in low:
        return "模型限流，请稍后再试"
    if "401" in msg or "403" in msg or "invalid api" in low or "incorrect api" in low:
        return "分析模型鉴权失败，请检查 /admin/analysis-llm"
    if "未配置" in low or "api key" in low:
        return "未配置分析模型，请到 /admin/analysis-llm"
    if "timeout" in low or "timed out" in low:
        return "分析模型响应超时"
    if "未返回 json" in low or "json" in low and "模型" in msg:
        return "模型返回格式异常"
    if msg.startswith("LLM HTTP"):
        code = msg.split(":", 1)[0].replace("LLM HTTP", "").strip()
        return f"分析模型服务异常（{code or 'HTTP'}）"
    if len(msg) > 48:
        return msg[:48].rstrip() + "…"
    return msg or "分析模型调用失败"


def _retry_after_seconds(res: httpx.Response, attempt: int) -> float:
    raw = (res.headers.get("retry-after") or "").strip()
    if raw:
        try:
            return max(1.0, min(60.0, float(raw)))
        except ValueError:
            pass
    return min(30.0, 2.0 ** attempt)


def chat_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.35,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """One completion → parsed JSON object. Retries 429/5xx. Raises on hard failures."""
    reload_settings()
    conn = analysis_conn_svc.resolve_creds()
    if not conn.get("api_key"):
        raise RuntimeError("未配置分析 LLM API Key（请到 /admin/analysis-llm 配置）")

    url = urljoin(conn["base_url"], "chat/completions")
    model = conn["model"]
    body: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if "gemini" in model.lower():
        body["max_tokens"] = max(max_tokens, 4096)
        body["reasoning_effort"] = "low"

    headers = {
        "Authorization": f"Bearer {conn['api_key']}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(90.0, connect=15.0)
    last_err: Exception | None = None

    with httpx.Client(timeout=timeout) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                res = client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last_err = RuntimeError("模型响应超时")
                if attempt >= _MAX_RETRIES:
                    raise last_err from exc
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            except httpx.HTTPError as exc:
                last_err = RuntimeError(f"模型网络异常：{type(exc).__name__}")
                if attempt >= _MAX_RETRIES:
                    raise last_err from exc
                time.sleep(min(30.0, 2.0 ** attempt))
                continue

            if res.status_code in _RETRYABLE:
                wait = _retry_after_seconds(res, attempt)
                last_err = RuntimeError(
                    f"LLM HTTP {res.status_code}: {(res.text or '')[:240]}"
                )
                if attempt >= _MAX_RETRIES:
                    raise last_err
                logger.warning(
                    "analysis LLM %s (attempt %s/%s), sleep %.1fs",
                    res.status_code,
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            if res.status_code >= 400:
                raise RuntimeError(
                    f"LLM HTTP {res.status_code}: {(res.text or '')[:240]}"
                )

            data = res.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            if not choices:
                raise RuntimeError("模型无返回")
            content = ((choices[0] or {}).get("message") or {}).get("content") or ""
            return parse_json_object(str(content))

    raise last_err or RuntimeError("模型调用失败")


def parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = _JSON_RE.search(raw)
    if not m:
        raise RuntimeError("模型未返回 JSON")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise RuntimeError("JSON 根节点必须是对象")
    return obj


def normalize_stance(raw: Any) -> str:
    s = str(raw or "").strip()
    if s in {"偏多", "中性", "偏空", "数据不足"}:
        return s
    # Prefer insufficiency before bare 多/空 (e.g. 「多空不明」「数据不足」)
    if any(k in s for k in ("不足", "缺失", "未知", "无法判断", "缺数据", "不明")):
        return "数据不足"
    if any(k in s for k in ("偏多", "看多", "逢低买", "偏强")):
        return "偏多"
    if any(k in s for k in ("偏空", "看空", "宜减", "偏弱")):
        return "偏空"
    if any(k in s for k in ("涨", "上行", "加仓")):
        return "偏多"
    if any(k in s for k in ("跌", "下行", "减仓", "卖出")):
        return "偏空"
    if "中性" in s or "观望" in s:
        return "中性"
    return "中性"


def clamp_confidence(raw: Any, default: float = 0.5) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, v)), 2)
