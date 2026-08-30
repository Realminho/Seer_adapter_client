from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class HexplorerMapSyncResult:
    source_dir: Path
    target_dir: Path
    backup_dir: Path | None
    copied: list[Path]


def sync_hexplorer_maps(
    *,
    source_dir: Path,
    target_dir: Path,
    backup_root: Path,
    include_names: Iterable[str],
) -> HexplorerMapSyncResult:
    source_dir = source_dir.expanduser()
    target_dir = target_dir.expanduser()
    backup_root = backup_root.expanduser()
    names = tuple(include_names)
    if not names:
        raise ValueError("include_names must contain at least one map item")
    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"Hexplorer map source directory does not exist: {source_dir}"
        )

    existing_targets = [
        target_dir / name for name in names if (target_dir / name).exists()
    ]
    backup_dir = None
    if existing_targets:
        backup_dir = _next_backup_dir(backup_root)
        backup_dir.mkdir(parents=True, exist_ok=False)
        for existing_target in existing_targets:
            shutil.copytree(existing_target, backup_dir / existing_target.name)

    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in names:
        source_path = source_dir / name
        if not source_path.exists():
            raise FileNotFoundError(f"Hexplorer map item does not exist: {source_path}")
        target_path = target_dir / name
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(source_path, target_path)
        copied.append(target_path)

    return HexplorerMapSyncResult(
        source_dir=source_dir,
        target_dir=target_dir,
        backup_dir=backup_dir,
        copied=copied,
    )


def _next_backup_dir(backup_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = backup_root / timestamp
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"{timestamp}-{suffix:02d}"
        suffix += 1
    return candidate
