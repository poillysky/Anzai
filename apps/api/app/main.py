from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, agent, analysis, auth, health, holdings, market, me, news
from app.core.config import get_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="安崽ETF API", version="0.1.0", lifespan=lifespan)
    origins = list(dict.fromkeys(settings.cors_origin_list))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=(
            r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"
            r"|https://anzai\.605081\.xyz(:\d+)?"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Session for /admin only (must be after CORS in Starlette = added before = outer)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="anzai_admin",
        same_site="lax",
        https_only=False,
        max_age=60 * 60 * 12,
    )
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api")
    app.include_router(holdings.router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(news.router, prefix="/api")
    app.include_router(analysis.router, prefix="/api")
    app.include_router(me.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(admin.router)
    return app


app = create_app()
