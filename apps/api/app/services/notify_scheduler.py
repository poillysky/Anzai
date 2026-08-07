"""Background minute ticker for per-user WeChat digests."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.services.notify_digest import run_due_digests

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_stop = threading.Event()
_thread: threading.Thread | None = None
_last_fire_key = ""


def start_notify_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="anzai-notify", daemon=True)
    _thread.start()
    logger.info("notify scheduler started (per-user WeChat digest)")


def stop_notify_scheduler() -> None:
    _stop.set()


def _loop() -> None:
    global _last_fire_key
    while not _stop.is_set():
        try:
            now = datetime.now(_CST)
            key = now.strftime("%Y-%m-%d %H:%M")
            if key != _last_fire_key:
                _last_fire_key = key
                _fire(now)
        except Exception:
            logger.exception("notify scheduler tick failed")
        _stop.wait(20.0)


def _fire(now: datetime) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        results = run_due_digests(db, now)
        for r in results:
            if r.get("skipped"):
                logger.info("notify user=%s skipped: %s", r.get("user_id"), r.get("reason"))
            elif r.get("ok"):
                logger.info(
                    "notify user=%s sent via %s job=%s",
                    r.get("user_id"),
                    r.get("channel"),
                    r.get("job_id"),
                )
            else:
                logger.warning(
                    "notify user=%s failed: %s",
                    r.get("user_id"),
                    r.get("reason") or r.get("detail"),
                )
    finally:
        db.close()
