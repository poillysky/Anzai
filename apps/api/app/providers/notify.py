"""WeChat push bridges: Server酱 / PushPlus / WxPusher."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

NotifyChannel = Literal["serverchan", "pushplus", "wxpusher"]


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    channel: str
    detail: str = ""


def channel_configured(channel: str, token: str, *, wxpusher_uid: str = "") -> bool:
    ch = (channel or "").strip().lower()
    tok = (token or "").strip()
    if not ch or not tok:
        return False
    if ch == "wxpusher" and tok.upper().startswith("AT") and not (wxpusher_uid or "").strip():
        # appToken usually needs UID; SPT does not
        return False
    return ch in {"serverchan", "pushplus", "wxpusher"}


def send_wechat_notify(
    *,
    channel: str,
    token: str,
    title: str,
    content: str,
    wxpusher_uid: str = "",
) -> NotifyResult:
    """Send via explicit credentials (per-user)."""
    ch = (channel or "").strip().lower()
    tok = (token or "").strip()
    title = (title or "安崽").strip()[:100]
    content = (content or "").strip()
    if not content:
        return NotifyResult(False, ch, "正文为空")
    if not channel_configured(ch, tok, wxpusher_uid=wxpusher_uid):
        return NotifyResult(False, ch, "通道未配置完整")

    try:
        if ch == "serverchan":
            return _send_serverchan(tok, title, content)
        if ch == "pushplus":
            return _send_pushplus(tok, title, content)
        if ch == "wxpusher":
            return _send_wxpusher(tok, title, content, uid=(wxpusher_uid or "").strip())
        return NotifyResult(False, ch, f"未知通道 {ch!r}")
    except Exception as exc:
        logger.exception("notify send failed (%s)", ch)
        return NotifyResult(False, ch, str(exc)[:300])


def _send_serverchan(sendkey: str, title: str, content: str) -> NotifyResult:
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, data={"title": title, "desp": content})
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        return NotifyResult(False, "serverchan", "响应异常")
    if data.get("code") not in (0, "0"):
        return NotifyResult(False, "serverchan", str(data.get("message") or data)[:300])
    return NotifyResult(True, "serverchan", "ok")


def _send_pushplus(token: str, title: str, content: str) -> NotifyResult:
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "txt",
    }
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
    if isinstance(data, dict) and data.get("code") not in (200, "200"):
        return NotifyResult(False, "pushplus", str(data.get("msg") or data)[:300])
    return NotifyResult(True, "pushplus", "ok")


def _send_wxpusher(token: str, title: str, content: str, *, uid: str = "") -> NotifyResult:
    body = f"{title}\n\n{content}" if title else content
    if token.upper().startswith("SPT") and not uid:
        url = "https://wxpusher.zjiecode.com/api/send/message/simple-push"
        payload: dict = {"content": body, "contentType": 1, "spt": token}
    else:
        url = "https://wxpusher.zjiecode.com/api/send/message"
        payload = {
            "appToken": token,
            "content": body,
            "summary": title[:20] if title else "安崽",
            "contentType": 1,
        }
        if uid:
            payload["uids"] = [uid]
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
    if isinstance(data, dict) and data.get("code") not in (1000, 0, "1000", "0"):
        if data.get("success") is True:
            return NotifyResult(True, "wxpusher", "ok")
        return NotifyResult(False, "wxpusher", str(data.get("msg") or data)[:300])
    return NotifyResult(True, "wxpusher", "ok")
