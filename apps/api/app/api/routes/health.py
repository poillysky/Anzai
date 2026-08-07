from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas import HealthOut
from app.services.quote import provider_name

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(status="ok", app="anzai", quote_provider=provider_name() or settings.quote_provider)
