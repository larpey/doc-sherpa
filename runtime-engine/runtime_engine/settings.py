"""Runtime engine configuration.

`RUNTIME_ENGINE_*` env vars. Read once at startup; downstream modules
import the singleton. Reload via `importlib.reload` in tests when the
env changes (mirrors the training-service pattern).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_watch_dir() -> Path:
    return Path("runtime-engine") / "incoming"


def _default_destination_root() -> Path:
    return Path("runtime-engine") / "classified"


def _default_db_path() -> Path:
    return Path("runtime-engine") / "data" / "runtime.db"


class RuntimeEngineSettings(BaseSettings):
    """Knobs the operator sets via env / first-run wizard."""

    model_config = SettingsConfigDict(
        env_prefix="RUNTIME_ENGINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- paths ------------------------------------------------------------
    watch_dir: Path = Field(default_factory=_default_watch_dir)
    destination_root: Path = Field(default_factory=_default_destination_root)
    db_path: Path = Field(default_factory=_default_db_path)
    packs_dir: Path | None = Field(default=None)

    # --- KB ---------------------------------------------------------------
    active_packs: tuple[str, ...] = Field(
        default=("logistics", "healthcare"),
        description="Industry packs to load alongside _base.yaml.",
    )

    # --- routing ----------------------------------------------------------
    confidence_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    auto_create_folders: bool = Field(default=True)
    routing_template: str = Field(
        default="{doc_type}/{year}/{vendor}_{identifier}.pdf",
    )
    unclassified_template: str = Field(
        default="unclassified/{year}/{original_filename}",
    )

    # --- watcher ----------------------------------------------------------
    poll_interval_seconds: float = Field(default=2.0, gt=0.0)
    stability_check_seconds: float = Field(
        default=1.0,
        description=(
            "How long a file's size must remain unchanged before we "
            "trust the writer is done. Guards against picking up "
            "half-written PDFs."
        ),
    )

    # --- after-place behavior --------------------------------------------
    delete_source_after_place: bool = Field(
        default=False,
        description=(
            "If True, the source file is removed once it's been copied "
            "to the destination. Default False — operators are nervous "
            "about deletion in early use; they can toggle this on."
        ),
    )


settings = RuntimeEngineSettings()
