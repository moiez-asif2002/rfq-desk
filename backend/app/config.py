from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://rfq:rfq@localhost:5434/rfq_desk"

    gemini_backend: Literal["vertex", "api_key"] = "vertex"
    gemini_model: str = "gemini-2.5-flash"
    gcp_project: str | None = None
    gcp_location: str = "us-central1"
    google_application_credentials: str | None = None
    gemini_api_key: str | None = None

    inbound_webhook_secret: str = "dev-inbound-secret"
    outbound_webhook_url: str | None = None
    outbound_webhook_secret: str = "dev-outbound-secret"
    outbound_max_attempts: int = 3

    gmail_client_secrets_file: str = "../secrets/gmail-oauth-client.json"
    gmail_redirect_uri: str = "http://localhost:8000/api/gmail/oauth/callback"
    token_encryption_key: str | None = None

    frontend_url: str = "http://localhost:3001"

    # Review gating: anything below these goes to a human with a reason attached.
    review_confidence_threshold: float = 0.8
    match_accept_score: float = 0.75
    match_min_score: float = 0.3
    # LLM re-ranking over the SQL candidate pool; only this confident a pick is auto-selected.
    enable_rerank: bool = True
    rerank_accept_confidence: float = 0.8


@lru_cache
def get_settings() -> Settings:
    return Settings()
