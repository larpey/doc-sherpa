"""First-run setup wizard — persist core config to a YAML the runtime reads.

`runtime.yaml` lives next to the SQLite DB. When present, it overrides
the env-var defaults on next startup. When absent, the inbox redirects
to `/setup` so the operator can fill it in once.

We deliberately keep the schema tiny: watch folder, destination root,
which industry packs to activate, and whether to auto-create folders.
Everything else stays in env / code defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class WizardConfig:
    """The minimum the operator must set on first run."""

    watch_dir: Path
    destination_root: Path
    active_packs: tuple[str, ...]
    auto_create_folders: bool = True


def _config_path(data_dir: Path) -> Path:
    return data_dir / "runtime.yaml"


def is_complete(data_dir: Path) -> bool:
    """Has the operator completed first-run setup?"""
    return _config_path(data_dir).exists()


def load(data_dir: Path) -> WizardConfig | None:
    """Read the persisted config, or None if not yet completed."""
    path = _config_path(data_dir)
    if not path.exists():
        return None
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return WizardConfig(
        watch_dir=Path(raw["watch_dir"]).expanduser(),
        destination_root=Path(raw["destination_root"]).expanduser(),
        active_packs=tuple(raw.get("active_packs", ())),
        auto_create_folders=bool(raw.get("auto_create_folders", True)),
    )


def save(data_dir: Path, cfg: WizardConfig) -> None:
    """Persist the config. Creates `data_dir` if missing.

    Restricts file permissions to the owner only (0600 on POSIX). On
    Windows the chmod is a no-op for these bits, but file ACLs from the
    parent dir provide equivalent protection.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "watch_dir": str(cfg.watch_dir),
        "destination_root": str(cfg.destination_root),
        "active_packs": list(cfg.active_packs),
        "auto_create_folders": cfg.auto_create_folders,
    }
    config_path = _config_path(data_dir)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    try:
        config_path.chmod(0o600)
    except OSError:
        # Best-effort on filesystems that don't support POSIX modes.
        pass


def validate_paths(cfg: WizardConfig) -> list[str]:
    """Return a list of human-readable problems, or empty if all good.

    Tests writability rather than just existence — many operators point
    at a network share that exists but isn't writable, and finding out
    at first classification time is a worse experience than the wizard
    flagging it now.
    """
    problems: list[str] = []
    for label, p in (("watch folder", cfg.watch_dir), ("destination root", cfg.destination_root)):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"{label} not creatable at {p}: {exc}")
            continue
        probe = p / ".sherpa-write-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{label} not writable: {exc}")
    return problems
