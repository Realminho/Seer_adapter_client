"""configio.write_config 단위 테스트.

config.toml 에는 writer 가 여러 갈래 있고 전부 read -> 문자열 수정 -> write 형태다.
각자 write 만 하면 마지막 writer 가 남의 수정을 통째로 되돌린다. 이 파일은 그 상황과
쓰기 도중 중단 상황을 고정한다.
"""
import multiprocessing
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parent.parent
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from core import configio  # noqa: E402

REAL_CONFIG = ADAPTOR_DIR / "config" / "config.toml"


def _append_marker(job):
    """다른 프로세스에서 실행되는 writer. 읽기와 쓰기 사이에 일부러 틈을 둔다."""
    path_str, tag, delay = job
    sys.path.insert(0, str(ADAPTOR_DIR))
    from core import configio as cio

    def transform(text):
        time.sleep(delay)
        return text + f"\n# marker {tag}\n"

    cio.write_config(Path(path_str), transform)


class WriteConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "config.toml"
        shutil.copy(REAL_CONFIG, self.path)
        self.original = self.path.read_text(encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_transform_result_is_saved(self):
        returned = configio.write_config(self.path, lambda text: text + "\n# added\n")
        self.assertTrue(returned.endswith("# added\n"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), returned)

    def test_transform_sees_current_text(self):
        """transform 은 호출 시점이 아니라 락 안에서 다시 읽은 텍스트를 받는다."""
        seen = {}

        def transform(text):
            seen["text"] = text
            return text

        configio.write_config(self.path, transform)
        self.assertEqual(seen["text"], self.original)

    def test_transform_failure_leaves_file_untouched(self):
        def boom(_text):
            raise ValueError("bad edit")

        with self.assertRaises(ValueError):
            configio.write_config(self.path, boom)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original)

    def test_validate_failure_restores_original(self):
        """검증 실패 시 원본으로 되돌린다.

        되돌리지 않으면 adaptor 가 다음 기동에서 아예 뜨지 못한다.
        """
        with self.assertRaises(ValueError) as ctx:
            configio.write_config(
                self.path,
                lambda text: "broken = = =\n",
                validate=lambda: (False, "parse error"),
            )
        self.assertIn("parse error", str(ctx.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original)

    def test_validate_success_keeps_new_text(self):
        configio.write_config(
            self.path,
            lambda text: text + "\n# ok\n",
            validate=lambda: (True, "ok"),
        )
        self.assertIn("# ok", self.path.read_text(encoding="utf-8"))

    def test_concurrent_writers_do_not_lose_each_other(self):
        """서로 다른 프로세스의 read-modify-write 가 겹쳐도 둘 다 남아야 한다.

        write 만 배타적으로 해서는 부족하다. 읽기부터 쓰기까지가 한 덩어리여야
        나중 writer 가 앞 writer 의 수정을 덮지 않는다.
        """
        jobs = [(str(self.path), "alpha", 0.4), (str(self.path), "beta", 0.4)]
        with multiprocessing.Pool(2) as pool:
            pool.map(_append_marker, jobs)

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# marker alpha", text)
        self.assertIn("# marker beta", text)

    def test_no_temp_or_partial_file_left_behind(self):
        configio.write_config(self.path, lambda text: text + "\n# done\n")
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_lock_timeout_raises_instead_of_clobbering(self):
        """락을 못 얻으면 그냥 쓰지 않고 실패한다.

        타임아웃 후 강행하면 락이 있으나 마나가 된다.
        """
        import fcntl

        lock_path = self.path.with_name(self.path.name + ".lock")
        with open(lock_path, "a+") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            with self.assertRaises(TimeoutError):
                configio.write_config(
                    self.path, lambda text: text + "\n# never\n", lock_timeout=0.2
                )
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.original)



class ApplyThroughWriteConfigTestCase(unittest.TestCase):
    """apply_parameter_changes + write_config 통합. 실제 파일에 반영되는지 본다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "config.toml"
        self.path.write_text(
            "[dock]\ntimeout_s = 30      # 도킹 대기\nretry = 3\n", encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _apply(self, changes, validate=None):
        from amr_parameter_publish import apply_parameter_changes

        outcome = {}

        def transform(text):
            new_text, applied, failures = apply_parameter_changes(
                text, changes, editable_keys={"config.toml:dock.timeout_s", "config.toml:dock.retry"}
            )
            outcome["applied"] = applied
            outcome["failures"] = failures
            return new_text

        configio.write_config(self.path, transform, validate=validate)
        return outcome

    def test_applied_change_is_on_disk(self):
        outcome = self._apply([{"key": "config.toml:dock.timeout_s", "op": "set", "value": 45}])
        self.assertEqual(outcome["failures"], [])
        self.assertIn("timeout_s = 45", self.path.read_text(encoding="utf-8"))

    def test_revision_changes_after_write(self):
        """리비전(mtime µs)이 실제로 증가해야 낙관적 동시성이 성립한다."""
        before = int(self.path.stat().st_mtime_ns // 1000)
        time.sleep(0.01)
        self._apply([{"key": "config.toml:dock.retry", "op": "set", "value": 9}])
        after = int(self.path.stat().st_mtime_ns // 1000)
        self.assertGreater(after, before)
        self.assertLessEqual(after, 2 ** 53 - 1)

    def test_validation_failure_rolls_back_applied_change(self):
        """검증이 실패하면 적용됐던 변경도 되돌아간다."""
        original = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            self._apply(
                [{"key": "config.toml:dock.retry", "op": "set", "value": 9}],
                validate=lambda: (False, "loader 거부"),
            )
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

if __name__ == "__main__":
    unittest.main()


class RestorePreservesMtimeTestCase(unittest.TestCase):
    """검증 실패로 되돌렸으면 mtime 도 되돌려야 한다.

    EPR 리비전은 설정 파일 mtime 의 최댓값이다. 실패한 적용이 mtime 을 밀면
    열려 있는 모든 스냅샷이 STALE 이 되어, 아무도 아무것도 적용하지 못한다.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "config.toml"
        self.path.write_text('a = 1\n')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_failed_validate_restores_content_and_mtime(self):
        before_text = self.path.read_text()
        before_mtime = self.path.stat().st_mtime_ns
        with self.assertRaises(ValueError):
            configio.write_config(
                self.path,
                lambda text: text + "b = 2\n",
                validate=lambda: (False, "nope"),
            )
        self.assertEqual(self.path.read_text(), before_text)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)

    def test_successful_write_moves_mtime(self):
        before = self.path.stat().st_mtime_ns
        configio.write_config(self.path, lambda text: text + "b = 2\n")
        self.assertNotEqual(self.path.stat().st_mtime_ns, before)
