import tempfile
import unittest
from pathlib import Path

from hexplorer_client.map_files import sync_hexplorer_maps


class SyncHexplorerMapsTest(unittest.TestCase):
    def test_copies_selected_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            backup = root / "backup"
            (source / "record_001").mkdir(parents=True)
            (source / "record_001" / "metadata.yaml").write_text(
                "bag: one\n",
                encoding="utf-8",
            )

            result = sync_hexplorer_maps(
                source_dir=source,
                target_dir=target,
                backup_root=backup,
                include_names=("record_001",),
            )

            self.assertEqual(
                (target / "record_001" / "metadata.yaml").read_text(
                    encoding="utf-8"
                ),
                "bag: one\n",
            )
            self.assertEqual(result.copied, [target / "record_001"])
            self.assertIsNone(result.backup_dir)

    def test_backs_up_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            backup = root / "backup"
            (source / "record_002").mkdir(parents=True)
            (source / "record_002" / "metadata.yaml").write_text(
                "bag: new\n",
                encoding="utf-8",
            )
            (target / "record_002").mkdir(parents=True)
            (target / "record_002" / "metadata.yaml").write_text(
                "bag: old\n",
                encoding="utf-8",
            )

            result = sync_hexplorer_maps(
                source_dir=source,
                target_dir=target,
                backup_root=backup,
                include_names=("record_002",),
            )

            self.assertEqual(
                (target / "record_002" / "metadata.yaml").read_text(
                    encoding="utf-8"
                ),
                "bag: new\n",
            )
            self.assertIsNotNone(result.backup_dir)
            assert result.backup_dir is not None
            self.assertEqual(
                (result.backup_dir / "record_002" / "metadata.yaml").read_text(
                    encoding="utf-8"
                ),
                "bag: old\n",
            )


if __name__ == "__main__":
    unittest.main()
