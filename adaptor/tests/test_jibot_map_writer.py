"""Tests for jibot_map_writer — atomic write + backup rotation.

Tests:
- First write (no existing file) creates target with NO backup.
- Repeated writes (12x) produce at most `keep` (10) backups; older ones pruned.
- No leftover .tmp file after write.
- Returned path is the target file.
- Target file holds the LATEST content after all writes.
- Backups are named {map_id}.{timestamp}.json under backup_root.
"""
import json
import unittest
from pathlib import Path


class TestWriteJibotMapFirstWrite(unittest.TestCase):
    """First write — no prior file → no backup created."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.map_dir = Path(self.tmp_dir) / "maps"
        self.backup_root = Path(self.tmp_dir) / "backups"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_first_write_creates_target(self):
        from jibot_map_writer import write_jibot_map

        raw = {"Header": "umcl-map", "MapName": "lab2m", "Objs": {}}
        result = write_jibot_map(
            "m1",
            raw,
            map_dir=self.map_dir,
            backup_root=self.backup_root,
            keep=10,
            timestamp="T1",
        )

        target = self.map_dir / "m1.json"
        self.assertEqual(result, target)
        self.assertTrue(target.exists())
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["MapName"], "lab2m")

    def test_first_write_no_backup_created(self):
        from jibot_map_writer import write_jibot_map

        write_jibot_map(
            "m1",
            {"Header": "umcl-map"},
            map_dir=self.map_dir,
            backup_root=self.backup_root,
            keep=10,
            timestamp="T1",
        )

        # backup_root should not exist (or be empty) since there was no prior file
        if self.backup_root.exists():
            backups = list(self.backup_root.glob("m1.*.json"))
            self.assertEqual(len(backups), 0, "No backup on first write")

    def test_first_write_no_tmp_leftover(self):
        from jibot_map_writer import write_jibot_map

        write_jibot_map(
            "m1",
            {"Header": "umcl-map"},
            map_dir=self.map_dir,
            backup_root=self.backup_root,
            keep=10,
            timestamp="T1",
        )

        tmp_files = list(self.map_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0, "No .tmp leftover after write")


class TestWriteJibotMapBackupRotation(unittest.TestCase):
    """Write 12 times with keep=10 → exactly 10 backups, oldest pruned."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.map_dir = Path(self.tmp_dir) / "maps"
        self.backup_root = Path(self.tmp_dir) / "backups"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _do_writes(self, n=12, keep=10):
        from jibot_map_writer import write_jibot_map

        last_result = None
        for i in range(1, n + 1):
            raw = {"Header": "umcl-map", "seq": i, "MapName": f"lab2m-v{i}"}
            last_result = write_jibot_map(
                "m1",
                raw,
                map_dir=self.map_dir,
                backup_root=self.backup_root,
                keep=keep,
                timestamp=f"T{i:04d}",
            )
        return last_result

    def test_target_holds_latest_content(self):
        self._do_writes(n=12)
        target = self.map_dir / "m1.json"
        loaded = json.loads(target.read_text(encoding="utf-8"))
        # seq=12 is the latest
        self.assertEqual(loaded["seq"], 12)
        self.assertEqual(loaded["MapName"], "lab2m-v12")

    def test_backup_count_capped_at_keep(self):
        self._do_writes(n=12, keep=10)
        backups = sorted(self.backup_root.glob("m1.*.json"))
        self.assertEqual(
            len(backups), 10, f"Expected 10 backups, got {len(backups)}: {backups}"
        )

    def test_oldest_backups_pruned(self):
        """After 12 writes with keep=10, verify oldest backup is pruned.

        Write #1 (T0001): no prior file → no backup created.
        Write #2 (T0002): backs up seq=1 as m1.T0002.json.
        ...
        Write #12 (T0012): 11th backup created (T0002..T0012), prune drops T0002.
        Survivors: T0003..T0012 (10 backups).
        """
        self._do_writes(n=12, keep=10)
        backups = sorted(self.backup_root.glob("m1.*.json"))
        names = [b.name for b in backups]
        # T0002 (oldest backup, created during write #2) should be pruned
        self.assertFalse(
            any("T0002" in n for n in names),
            f"T0002 backup should be pruned. Remaining: {names}",
        )
        # T0003 (created during write #3) should be the oldest surviving backup
        self.assertTrue(
            any("T0003" in n for n in names),
            f"T0003 backup should exist. Remaining: {names}",
        )
        # T0012 (latest backup, created during write #12) should exist
        self.assertTrue(
            any("T0012" in n for n in names),
            f"T0012 backup should exist. Remaining: {names}",
        )

    def test_no_tmp_leftover_after_many_writes(self):
        self._do_writes(n=12)
        tmp_files = list(self.map_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0, "No .tmp leftover after writes")

    def test_returned_path_is_target(self):
        result = self._do_writes(n=3)
        self.assertEqual(result, self.map_dir / "m1.json")

    def test_backup_files_are_named_with_timestamp(self):
        """Backup filenames follow {map_id}.{timestamp}.json pattern."""
        self._do_writes(n=3, keep=10)
        backups = sorted(self.backup_root.glob("m1.*.json"))
        # All backup names should start with 'm1.' and end with '.json'
        for b in backups:
            self.assertTrue(b.name.startswith("m1."))
            self.assertTrue(b.name.endswith(".json"))

    def test_backup_content_is_previous_version(self):
        """Backup created during write #2 (T0002) holds the content from write #1.

        Write #1 (T0001): no prior file → no backup.  Target gets seq=1.
        Write #2 (T0002): prior file exists → backup named m1.T0002.json holds seq=1.
        """
        from jibot_map_writer import write_jibot_map

        write_jibot_map("m1", {"seq": 1}, map_dir=self.map_dir, backup_root=self.backup_root, keep=10, timestamp="T0001")
        write_jibot_map("m1", {"seq": 2}, map_dir=self.map_dir, backup_root=self.backup_root, keep=10, timestamp="T0002")

        backups = sorted(self.backup_root.glob("m1.*.json"))
        # The backup created on write #2 is named T0002 and contains the previous seq=1
        first_backup = next(b for b in backups if "T0002" in b.name)
        loaded = json.loads(first_backup.read_text(encoding="utf-8"))
        self.assertEqual(loaded["seq"], 1)

    def test_keep_fewer_than_writes(self):
        """With keep=3 and 6 writes, only 3 backups survive."""
        self._do_writes(n=6, keep=3)
        backups = sorted(self.backup_root.glob("m1.*.json"))
        self.assertEqual(len(backups), 3)

    def test_no_timestamp_skips_backup(self):
        """When timestamp=None, no backup is created even for subsequent writes."""
        from jibot_map_writer import write_jibot_map

        # First write (creates file)
        write_jibot_map("m1", {"seq": 1}, map_dir=self.map_dir, backup_root=self.backup_root, keep=10, timestamp=None)
        # Second write without timestamp — should overwrite without backing up
        write_jibot_map("m1", {"seq": 2}, map_dir=self.map_dir, backup_root=self.backup_root, keep=10, timestamp=None)

        if self.backup_root.exists():
            backups = list(self.backup_root.glob("m1.*.json"))
            self.assertEqual(len(backups), 0, "No backup when timestamp=None")

        target = self.map_dir / "m1.json"
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["seq"], 2)


class TestWriteJibotMapTmpOrphanCleanup(unittest.TestCase):
    """A pre-existing stale .tmp file is cleaned up before the new atomic write."""

    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.map_dir = Path(self.tmp_dir) / "maps"
        self.backup_root = Path(self.tmp_dir) / "backups"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_stale_tmp_is_removed_and_write_succeeds(self):
        """A pre-existing {map_id}.json.tmp is cleaned up and the write still succeeds."""
        from jibot_map_writer import write_jibot_map

        # Ensure map_dir exists so we can plant the stale .tmp
        self.map_dir.mkdir(parents=True, exist_ok=True)
        stale_tmp = self.map_dir / "m1.json.tmp"
        stale_tmp.write_text("stale orphan content", encoding="utf-8")
        self.assertTrue(stale_tmp.exists(), "Precondition: stale .tmp must exist")

        raw = {"Header": "umcl-map", "MapName": "lab2m", "seq": 99}
        result = write_jibot_map(
            "m1",
            raw,
            map_dir=self.map_dir,
            backup_root=self.backup_root,
            keep=10,
            timestamp="T1",
        )

        # Stale .tmp must be gone
        self.assertFalse(stale_tmp.exists(), "Stale .tmp must be removed before/during write")

        # Target file must exist with correct content
        target = self.map_dir / "m1.json"
        self.assertTrue(target.exists())
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["seq"], 99)
        self.assertEqual(result, target)

    def test_no_tmp_leftover_after_write_with_stale_tmp(self):
        """No .tmp file remains after a write that started with a stale .tmp."""
        from jibot_map_writer import write_jibot_map

        self.map_dir.mkdir(parents=True, exist_ok=True)
        (self.map_dir / "m1.json.tmp").write_text("stale", encoding="utf-8")

        write_jibot_map(
            "m1",
            {"seq": 1},
            map_dir=self.map_dir,
            backup_root=self.backup_root,
            keep=10,
            timestamp="T1",
        )

        tmp_files = list(self.map_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0, f"No .tmp leftover: {tmp_files}")


if __name__ == "__main__":
    unittest.main()
