"""Analysis API: profile, recipes, jobs (multi-agent P0)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
