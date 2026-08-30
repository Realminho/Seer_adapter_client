"""Utilities for copying JIBOT map and route parameter files."""

from __future__ import annotations

import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


PARAM_DIR_NAMES = ("map", "routes")
DEFAULT_SOURCE_DIR = Path("/usr/local/urobot/params")
DEFAULT_TARGET_DIR = Path(__file__).resolve().parent / "jibot" / "params"
DEFAULT_BACKUP_ROOT = Path(__file__).resolve().parent / "backups" / "jibot-params"


@dataclass(frozen=True)
class JibotParamsSyncResult:
    copied: List[Path]
    backup_dir: Optional[Path]
    target_dir: Path


def _next_backup_dir(backup_root: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = backup_root / stamp
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"{stamp}-{suffix}"
        suffix += 1
    return candidate


def _existing_param_dirs(target_dir: Path, names: Iterable[str]) -> List[Path]:
    return [target_dir / name for name in names if (target_dir / name).exists()]


def sync_jibot_params(
    source_dir: Path = DEFAULT_SOURCE_DIR,
    target_dir: Path = DEFAULT_TARGET_DIR,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
) -> JibotParamsSyncResult:
    """Copy JIBOT map/routes files after backing up existing target dirs."""
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    backup_root = Path(backup_root)

    missing_sources = [
        str(source_dir / name)
        for name in PARAM_DIR_NAMES
        if not (source_dir / name).is_dir()
    ]
    if missing_sources:
        raise FileNotFoundError(
            "Missing JIBOT parameter source directories: "
            + ", ".join(missing_sources)
        )

    target_dir.mkdir(parents=True, exist_ok=True)

    backup_dir = None
    existing_dirs = _existing_param_dirs(target_dir, PARAM_DIR_NAMES)
    if existing_dirs:
        backup_dir = _next_backup_dir(backup_root)
        backup_dir.mkdir(parents=True, exist_ok=False)
        for existing_dir in existing_dirs:
            shutil.copytree(existing_dir, backup_dir / existing_dir.name)

    copied = []
    for name in PARAM_DIR_NAMES:
        source = source_dir / name
        target = target_dir / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        copied.append(target)

    return JibotParamsSyncResult(
        copied=copied,
        backup_dir=backup_dir,
        target_dir=target_dir,
    )
