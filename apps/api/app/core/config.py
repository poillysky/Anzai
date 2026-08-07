from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: str = "sqlite:///./anzai.db"
    # Knowledge / pgvector only (business data stays on database_url / SQLite)
    knowledge_database_url: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    # Analysis committee LLM (separate from 安崽 chat) — also in data/analysis_connection.json
    llm_analysis_base_url: str = ""
    llm_analysis_api_key: str = ""
    llm_analysis_model: str = ""
    # Embedding connection (separate from chat LLM) — also in data/embedding_connection.json
    llm_embedding_base_url: str = ""
    llm_embedding_api_key: str = ""
    llm_embedding_model: str = "text-embedding-v4"
    app_password: str = ""
    # Local reminder / docs — not used for auth; real password is hashed in DB
    demo_username: str = ""
    demo_password: str = ""
    jwt_secret: str = ""
    jwt_expire_hours: int = 24 * 14
    quote_provider: str = "sina"
    cors_origins: str = (
        "http://localhost:3515,http://127.0.0.1:3515,"
        "https://anzai.605081.xyz:16666,https://anzai.605081.xyz"
    )
    # Session secret for /admin cookie (optional; falls back to app_password hash material)
    admin_session_secret: str = ""

    # WeChat digest — prefer per-user settings in App（安崽→设置→微信日报）
    # Legacy global env kept for ignore; scheduler always runs and reads preferences.
    notify_enabled: bool = False
    notify_channel: str = ""
    notify_serverchan_sendkey: str = ""
    notify_pushplus_token: str = ""
    notify_wxpusher_token: str = ""
    notify_wxpusher_uid: str = ""
    notify_hour: int = 15
    notify_minute: int = 10
    notify_weekdays: str = "0,1,2,3,4"
    notify_user: str = ""
    notify_degree: str = "light"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def session_secret(self) -> str:
        if self.admin_session_secret:
            return self.admin_session_secret
        if self.jwt_secret:
            return f"anzai-admin:{self.jwt_secret}"
        if self.app_password:
            return f"anzai-admin:{self.app_password}"
        return "anzai-admin-dev-only-change-me"

    @property
    def jwt_signing_key(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        if self.app_password:
            return f"anzai-jwt:{self.app_password}"
        return "anzai-jwt-dev-only-change-me-32b+"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Clear cache after .env write so subsequent reads see new values."""
    get_settings.cache_clear()
    # Re-bind env_file in case .env was just created
    return get_settings()
