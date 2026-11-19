"""Configuration.

Two layers:
* ``Settings`` – process-level values from environment variables / ``.env``
  (secrets live here only; they are never written to the database or sent to
  the frontend).
* ``Preferences`` – user-editable, non-secret options persisted in SQLite
  (enabled providers, limits, contact email).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

ALL_PROVIDERS = ("crossref", "openalex", "semanticscholar", "arxiv", "unpaywall")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPERFINDER_",
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    allow_lan: bool = False  # must be explicitly enabled to bind non-loopback
    data_dir: Path = PROJECT_DIR / "data"
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"

    # Optional provider credentials (secrets: env only)
    openalex_api_key: str | None = None
    s2_api_key: str | None = None

    # Optional AI assistant. ANTHROPIC_API_KEY is read by the SDK itself.
    assistant_model: str = "claude-opus-5-5"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperfinder.db"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.downloads_dir, self.temp_dir):
            d.mkdir(parents=True, exist_ok=True)


class Preferences(BaseModel):
    """User-editable, non-secret preferences (persisted locally)."""

    enabled_providers: list[str] = Field(
        default_factory=lambda: ["crossref", "openalex", "semanticscholar", "arxiv", "unpaywall"]
    )
    # Contact email for polite API pools (Crossref/OpenAlex) and required by
    # Unpaywall. Empty by default – nothing personal is sent unless the user sets it.
    contact_email: str = ""
    search_budget_seconds: float = Field(45.0, ge=5, le=180)
    provider_timeout_seconds: float = Field(15.0, ge=2, le=60)
    max_results_per_provider: int = Field(10, ge=1, le=50)
    max_retries: int = Field(2, ge=0, le=4)
    max_pdf_mb: int = Field(50, ge=1, le=500)
    download_timeout_seconds: float = Field(60.0, ge=5, le=300)
    assistant_enabled: bool = True

    @field_validator("enabled_providers")
    @classmethod
    def _known_providers(cls, v: list[str]) -> list[str]:
        unknown = [p for p in v if p not in ALL_PROVIDERS]
        if unknown:
            raise ValueError(f"Unknown providers: {', '.join(unknown)}")
        return list(dict.fromkeys(v))

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if v and ("@" not in v or " " in v or len(v) > 254):
            raise ValueError("Contact email looks invalid")
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    """Used by tests to point the app at a temporary data directory."""
    global _settings
    _settings = s
