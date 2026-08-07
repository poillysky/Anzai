"""Track analysis jobs the chat should proactively announce when done."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "data" / "analysis_pending.json"


def _load() -> dict[str, Any]:
    try:
        if _PATH.is_file():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        logger.exception("load analysis_pending failed")
    return {}


def _save(data: dict[str, Any]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("save analysis_pending failed")


def mark_pending(user_id: int, job_id: int) -> None:
    data = _load()
    data[str(int(user_id))] = {"job_id": int(job_id)}
    _save(data)


def peek_pending_job_id(user_id: int) -> int | None:
    row = _load().get(str(int(user_id)))
    if not isinstance(row, dict):
        return None
    try:
        return int(row.get("job_id"))
    except (TypeError, ValueError):
        return None


def clear_pending(user_id: int) -> None:
    data = _load()
    if str(int(user_id)) in data:
        data.pop(str(int(user_id)), None)
        _save(data)


def consume_if_ready(db: Any, user_id: int) -> Any | None:
    """
    If a pending job has finished (done/failed), return that job row and clear pending.
    If still running, return None and keep pending.
    """
    from app.services import analysis as analysis_svc

    jid = peek_pending_job_id(user_id)
    if jid is None:
        return None
    if analysis_svc.running_job(db, user_id) is not None:
        return None
    job = analysis_svc.get_job(db, jid, user_id)
    if job is None:
        clear_pending(user_id)
        return None
    if str(job.status or "") not in {"done", "failed"}:
        return None
    clear_pending(user_id)
    return job
