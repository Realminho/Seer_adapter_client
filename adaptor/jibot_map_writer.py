"""실로봇 JIBOT 맵 파일 쓰기(원자쓰기 + 백업 rotation).

Atomic write pattern: write to `{target}.tmp` then `Path.replace()` — same as
`jibot_map_store.save_map_snapshot`.

Backup naming: `{map_id}.{timestamp}.json` under `backup_root` — timestamp is
injected by the caller (keeps tests deterministic), analogous to the
`%Y%m%d-%H%M%S` stamp used by `jibot_params._next_backup_dir`.

Backup rotation: after copying the current file to the backup, call
`_prune_backups` to delete the oldest entries so at most `keep` backups remain.
Backups are sorted lexicographically by filename; because the timestamp is the
middle component (`{map_id}.{ts}.json`) and callers use ISO-style stamps, lexicographic
order == chronological order, so `backups[:-keep]` gives the oldest entries.
"""

import json
import shutil
from pathlib import Path

DEFAULT_UROBOT_MAP_WRITE_DIR = Path("/usr/local/urobot/params/map")
DEFAULT_MAP_BACKUP_ROOT = Path("/usr/local/urobot/params/map/.backups")
MAX_BACKUPS = 10


def _prune_backups(backup_root: Path, map_id: str, keep: int) -> None:
    """Delete the oldest backups for *map_id* so that at most *keep* remain.

    Backups are glob-matched as ``{map_id}.*.json`` and sorted lexicographically
    (chronological when timestamps use sortable strings like ``YYYYMMDD-HHMMSS``
    or zero-padded counters).  The oldest ``len(backups) - keep`` files are
    removed.

    @param backup_root: Directory that contains backup files.
    @param map_id: Map identifier used as the filename prefix.
    @param keep: Maximum number of backup files to retain.
    """
    backups = sorted(backup_root.glob(f"{map_id}.*.json"))
    excess = len(backups) - keep
    if excess > 0:
        for old in backups[:excess]:
            old.unlink(missing_ok=True)


def write_jibot_map(
    map_id: str,
    raw_map: dict,
    *,
    map_dir: Path = DEFAULT_UROBOT_MAP_WRITE_DIR,
    backup_root: Path = DEFAULT_MAP_BACKUP_ROOT,
    keep: int = MAX_BACKUPS,
    timestamp: str | None = None,
) -> Path:
    """Write a raw JIBOT map dict to ``{map_dir}/{map_id}.json`` atomically.

    If the target file already exists **and** a *timestamp* is provided, the
    existing file is copied to ``{backup_root}/{map_id}.{timestamp}.json`` before
    being overwritten.  Old backups beyond *keep* are pruned (oldest first).

    The write is atomic: data is written to a sibling ``.tmp`` file first, then
    renamed over the target via :py:meth:`pathlib.Path.replace`, which is atomic
    on POSIX filesystems within the same directory.  No ``.tmp`` file is left
    behind on success.

    @param map_id: Identifier for the map; used as the filename stem.
    @param raw_map: JIBOT map dict to serialise as JSON.
    @param map_dir: Directory where the live map file is written.
        Defaults to ``/usr/local/urobot/params/map``.
    @param backup_root: Directory where timestamped backups are stored.
        Defaults to ``{map_dir}/.backups``.
    @param keep: Maximum number of backup files to retain per map_id.
    @param timestamp: Opaque string injected by the caller, appended to the
        backup filename.  When *None*, no backup is created even if the target
        already exists (useful for overwrite-only flows).
    @returns: :py:class:`~pathlib.Path` of the written target file.
    """
    map_dir = Path(map_dir)
    backup_root = Path(backup_root)

    map_dir.mkdir(parents=True, exist_ok=True)

    # Remove any stale .tmp from a prior crash before the new atomic write
    (map_dir / f"{map_id}.json.tmp").unlink(missing_ok=True)

    target = map_dir / f"{map_id}.json"

    # Back up the existing file before overwriting (only when timestamp given)
    if target.exists() and timestamp is not None:
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_root / f"{map_id}.{timestamp}.json")
        _prune_backups(backup_root, map_id, keep)

    # Atomic write: write to .tmp then rename over target
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(
        json.dumps(raw_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)

    return target
