"""Runtime configuration via environment variables.

Why pydantic-settings rather than a config.ini: the training service is
exposed over HTTP, and config tends to leak into more places (rate limits,
upload caps, paths). pydantic-settings gives us validation + a single
typed object instead of `os.environ.get(...)` scattered across the codebase.

POC defaults are friendly: storage under the package, no auth token, no API
key required. Production deployments override via env vars or `.env`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_storage_dir() -> Path:
    """Default to ./training-service/storage relative to the project root.

    The CWD when running `uvicorn training_service.main:app --app-dir
    training-service` is the project root (uvicorn's `--app-dir` only
    affects module resolution, not CWD), so this resolves correctly.
    """
    return Path("training-service") / "storage"


class Settings(BaseSettings):
    """All runtime knobs for the training service. Read once at startup."""

    model_config = SettingsConfigDict(
        env_prefix="TRAINING_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- security ----------------------------------------------------------
    auth_token: SecretStr | None = Field(
        default=None,
        description=(
            "Optional shared-secret token. When set, all routes require an "
            "Authorization: Bearer <token> header. Phase 1 POC default is "
            "None (LAN-only operation)."
        ),
    )

    # --- AI ---------------------------------------------------------------
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Anthropic API key for the regex-synthesis call. Not needed for "
            "upload/labeling, so phase 1 ships without it set. Phase 1 "
            "deliverable 4 (training) will require this."
        ),
    )

    # --- storage ----------------------------------------------------------
    storage_dir: Path = Field(
        default_factory=_default_storage_dir,
        description="Filesystem root for uploaded PDFs and the SQLite DB.",
    )
    database_filename: str = Field(
        default="training.db",
        description="Name of the SQLite file inside `storage_dir`.",
    )

    # --- caps -------------------------------------------------------------
    max_upload_size_mb: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Per-file upload cap. Larger PDFs get rejected with 413.",
    )
    max_documents_per_session: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Cap on documents per session. Real training runs use 20–30 "
            "samples; the cap is twice that to give operators headroom."
        ),
    )
    session_ttl_hours: int = Field(
        default=24,
        ge=1,
        le=24 * 30,
        description="Sessions are eligible for cleanup after this many hours.",
    )

    # --- knowledge base ---------------------------------------------------
    packs_dir: Path | None = Field(
        default=None,
        description=(
            "Override the packs directory. Default is `<project>/packs/` "
            "relative to the package."
        ),
    )
    active_packs: tuple[str, ...] = Field(
        default=("logistics", "healthcare"),
        description=(
            "Industry packs to load alongside _base.yaml. Vocabulary in the "
            "labeling dropdown comes from this set."
        ),
    )

    @property
    def database_path(self) -> Path:
        """Absolute path to the SQLite file. Created on first access by `db.init_db`."""
        return self.storage_dir / self.database_filename


settings = Settings()
