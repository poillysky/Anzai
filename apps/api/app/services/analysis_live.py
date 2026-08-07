"""In-process live event bus for analysis jobs (page SSE + agent background attach)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_HISTORY: dict[int, list[dict[str, Any]]] = {}
_SUBS: dict[int, list[queue.Queue[dict[str, Any] | None]]] = {}
_CLOSED: set[int] = set()
_CLOSED_AT: dict[int, float] = {}
_MAX_HISTORY = 400
# After done, keep replay briefly for a second tab attach, then free RAM
_DISCARD_AFTER_SEC = 45.0


def ensure_job(job_id: int) -> None:
    jid = int(job_id)
    with _LOCK:
        _HISTORY.setdefault(jid, [])
        _SUBS.setdefault(jid, [])


def publish(job_id: int, event: dict[str, Any]) -> None:
    jid = int(job_id)
    ev = dict(event)
    schedule_discard = False
    with _LOCK:
        hist = _HISTORY.setdefault(jid, [])
        hist.append(ev)
        if len(hist) > _MAX_HISTORY:
            del hist[: len(hist) - _MAX_HISTORY]
        for q in list(_SUBS.get(jid, [])):
            try:
                q.put_nowait(ev)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass
        if ev.get("type") == "done":
            _CLOSED.add(jid)
            _CLOSED_AT[jid] = time.time()
            schedule_discard = True
            # wake waiters that might be blocked
            for q in list(_SUBS.get(jid, [])):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
    if schedule_discard:
        _schedule_discard(jid, _DISCARD_AFTER_SEC)
    sweep_closed()


def is_closed(job_id: int) -> bool:
    with _LOCK:
        return int(job_id) in _CLOSED


def history(job_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_HISTORY.get(int(job_id), []))


def discard(job_id: int) -> None:
    jid = int(job_id)
    with _LOCK:
        _HISTORY.pop(jid, None)
        subs = _SUBS.pop(jid, [])
        _CLOSED.discard(jid)
        _CLOSED_AT.pop(jid, None)
    for q in subs:
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


def sweep_closed(*, max_age_sec: float = _DISCARD_AFTER_SEC) -> None:
    """Drop finished jobs with no subscribers after max_age_sec."""
    now = time.time()
    doomed: list[int] = []
    with _LOCK:
        for jid, at in list(_CLOSED_AT.items()):
            if now - at < max_age_sec:
                continue
            if _SUBS.get(jid):
                continue
            doomed.append(jid)
    for jid in doomed:
        discard(jid)


def _schedule_discard(job_id: int, delay_sec: float) -> None:
    def _run() -> None:
        try:
            with _LOCK:
                if job_id not in _CLOSED:
                    return
                if _SUBS.get(job_id):
                    # still attached — try again later
                    _schedule_discard(job_id, min(delay_sec, 15.0))
                    return
            discard(job_id)
        except Exception:
            logger.exception("analysis_live discard failed for job %s", job_id)

    t = threading.Timer(max(0.5, float(delay_sec)), _run)
    t.daemon = True
    t.start()


def subscribe(job_id: int, *, idle_sec: float = 25.0) -> Iterator[dict[str, Any]]:
    """Yield historical events then live until type=done (or bus closed)."""
    jid = int(job_id)
    q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=256)
    with _LOCK:
        hist = list(_HISTORY.get(jid, []))
        closed = jid in _CLOSED
        _SUBS.setdefault(jid, []).append(q)

    try:
        for ev in hist:
            yield ev
            if ev.get("type") == "done":
                return
        if closed:
            return
        while True:
            try:
                item = q.get(timeout=idle_sec)
            except queue.Empty:
                # keepalive comment handled by route; yield a ping progress skip
                yield {"type": "ping", "ts": time.time()}
                with _LOCK:
                    if jid in _CLOSED:
                        return
                continue
            if item is None:
                with _LOCK:
                    if jid in _CLOSED:
                        return
                continue
            yield item
            if item.get("type") == "done":
                return
    finally:
        with _LOCK:
            subs = _SUBS.get(jid) or []
            if q in subs:
                subs.remove(q)
            empty = not subs and jid in _CLOSED
        if empty:
            # Second tab may still attach briefly; discard after grace
            _schedule_discard(jid, _DISCARD_AFTER_SEC)
