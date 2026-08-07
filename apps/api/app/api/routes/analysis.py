"""Analysis API: profile, recipes, jobs + SSE multi-agent committee."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.schemas import (
    AnalysisCatalogOut,
    AnalysisDegreeOut,
    AnalysisJobCreate,
    AnalysisJobOut,
    AnalysisModeOut,
    AnalysisProfileOut,
    AnalysisProfileUpdate,
    AnalysisRecipeOut,
    AnalysisReportOut,
)
from app.services import analysis as analysis_svc
from app.services.analysis_recipes import list_degrees, list_modes, list_recipes
from app.services.analysis_tiers import TIER_IDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(require_user)])


def _job_out(job) -> AnalysisJobOut:
    raw = analysis_svc.job_to_out(job)
    report = raw.get("report")
    return AnalysisJobOut(
        id=raw["id"],
        scope=raw["scope"],
        symbols=raw["symbols"],
        recipe_id=raw["recipe_id"],
        degree=raw["degree"],
        status=raw["status"],
        error=raw["error"],
        report=AnalysisReportOut.model_validate(report) if report else None,
        created_at=raw["created_at"],
        finished_at=raw["finished_at"],
    )


@router.get("/catalog", response_model=AnalysisCatalogOut)
def catalog() -> AnalysisCatalogOut:
    return AnalysisCatalogOut(
        degrees=[AnalysisDegreeOut.model_validate(d) for d in list_degrees()],
        modes=[AnalysisModeOut.model_validate(m) for m in list_modes()],
        recipes=[AnalysisRecipeOut.model_validate(r) for r in list_recipes()],
    )


@router.get("/recipes", response_model=list[AnalysisRecipeOut])
def recipes(mode: str | None = Query(default=None)) -> list[AnalysisRecipeOut]:
    return [AnalysisRecipeOut.model_validate(r) for r in list_recipes(mode)]


@router.get("/profile", response_model=AnalysisProfileOut)
def get_profile(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisProfileOut:
    return AnalysisProfileOut.model_validate(analysis_svc.profile_out(db, user.id))


@router.put("/profile", response_model=AnalysisProfileOut)
def put_profile(
    payload: AnalysisProfileUpdate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisProfileOut:
    analysis_svc.set_profile_degree(db, user.id, payload.degree)
    return AnalysisProfileOut.model_validate(analysis_svc.profile_out(db, user.id))


@router.post("/jobs", response_model=AnalysisJobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: AnalysisJobCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisJobOut:
    if payload.degree and payload.degree not in TIER_IDS:
        raise HTTPException(status_code=400, detail="degree 仅支持 light / standard / deep")
    symbols = (
        [{"symbol": s.symbol, "market": s.market, "name": s.name} for s in payload.symbols]
        if payload.symbols
        else None
    )
    try:
        job = analysis_svc.create_and_run_job(
            db,
            user_id=user.id,
            scope=payload.scope,
            symbols=symbols,
            recipe_id=None,
            degree=payload.degree,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_out(job)


@router.post("/jobs/stream")
def create_job_stream(
    payload: AnalysisJobCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> StreamingResponse:
    """SSE: meta → stage / agent_* → report → done."""
    if payload.degree and payload.degree not in TIER_IDS:
        raise HTTPException(status_code=400, detail="degree 仅支持 light / standard / deep")
    symbols = (
        [{"symbol": s.symbol, "market": s.market, "name": s.name} for s in payload.symbols]
        if payload.symbols
        else None
    )
    user_id = user.id

    def event_gen():
        # Leading comment pad helps some proxies flush the first frames sooner.
        yield ": " + (" " * 2048) + "\n\n"
        try:
            for ev in analysis_svc.iter_job_events(
                db,
                user_id=user_id,
                scope=payload.scope,
                symbols=symbols,
                degree=payload.degree,
            ):
                yield "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
        except ValueError as exc:
            yield (
                "data: "
                + json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
                + "\n\n"
            )
            yield "data: " + json.dumps({"type": "done", "status": "failed"}, ensure_ascii=False) + "\n\n"
        except Exception as exc:
            logger.exception("analysis stream failed")
            yield (
                "data: "
                + json.dumps({"type": "error", "message": str(exc)[:400]}, ensure_ascii=False)
                + "\n\n"
            )
            yield "data: " + json.dumps({"type": "done", "status": "failed"}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}", response_model=AnalysisJobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisJobOut:
    job = analysis_svc.get_job(db, job_id, user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_out(job)


@router.get("/latest", response_model=AnalysisJobOut | None)
def latest(
    scope: str | None = Query(default=None, pattern="^(portfolio|symbol)$"),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisJobOut | None:
    job = analysis_svc.latest_job(db, user.id, scope=scope)
    if not job:
        return None
    return _job_out(job)


@router.get("/running", response_model=AnalysisJobOut | None)
def running(
    scope: str | None = Query(default=None, pattern="^(portfolio|symbol)$"),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> AnalysisJobOut | None:
    """In-flight job (analysis page stream or agent-started background)."""
    job = analysis_svc.running_job(db, user.id, scope=scope)
    if not job:
        return None
    return _job_out(job)
