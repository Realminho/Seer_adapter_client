"""원문 교체의 **실제 파일 왕복** 통합 테스트.

:func:`_apply_text_replacement` 단위 테스트는 순수 함수만 본다 — 문자열이 들어가고 문자열이
나온다. 실제로 위험한 구간은 그 뒤다: ``write_config`` 가 락을 잡고 임시파일로 갈아끼운 뒤
부팅 로더로 재검증하고, 실패하면 원문과 mtime 까지 되돌리는 부분.

여기서 그 구간을 실제 파일로 돌린다. 로봇에 올리기 전에 확인할 수 있는 마지막 경계다:

  1. recipe 블록 추가가 파일에 실제로 쓰이고 로더를 통과하는가
  2. 블록 삭제가 실제로 반영되는가
  3. **부팅이 멈추는 원문**(중복 recipe 이름)이 거부되고 파일이 원래대로 돌아오는가
  4. 실패한 적용이 mtime 을 앞으로 밀지 않는가(밀면 열려 있는 스냅샷이 전부 STALE 이 됨)
"""

import shutil
import sys
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from adapter_jibot import _validate_parameter_sources  # noqa: E402
from amr_parameter_publish import (  # noqa: E402
    apply_parameter_changes,
    read_source_text,
)
from core import configio  # noqa: E402

CONFIG_DIR = ADAPTOR_DIR / "config"
SOURCES = ("config.toml", "extensions.hcl", "recipes.hcl", "robots.hcl")

NEW_RECIPE = '''
recipe "e2eElevatorClose" {
  label       = "E2E — 문 닫기"
  timeout_sec = 60

  step "pioInit" {}
}
'''


class TextRoundtripTest(unittest.TestCase):
    """MW 적용 경로와 동일하게 write_config + 부팅 로더 재검증을 태운다."""

    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = {}
        for name in SOURCES:
            src = CONFIG_DIR / name
            if src.exists():
                dst = self.tmp / name
                shutil.copy2(src, dst)
                self.paths[name] = dst
        self.recipes = self.paths["recipes.hcl"]

    def apply_text(self, new_text: str):
        """adapter_jibot 의 적용 경로와 같은 순서로 원문을 적용한다.

        :param new_text: 적용할 파일 전문
        :returns: (성공 여부, 실패 목록, 저장 실패 사유)
        """
        change = {"key": "recipes.hcl", "op": "replaceText", "value": new_text}
        outcome = {}

        def _transform(current: str) -> str:
            text, applied, failures = apply_parameter_changes(
                current, [change], editable_keys=set(), source="recipes.hcl"
            )
            outcome["applied"] = applied
            outcome["failures"] = failures
            if not applied:
                raise _NoChange()
            return text

        try:
            configio.write_config(
                self.recipes,
                _transform,
                validate=lambda: _validate_parameter_sources(self.paths),
            )
        except _NoChange:
            return False, outcome.get("failures", []), None
        except ValueError as exc:
            return False, outcome.get("failures", []), str(exc)
        return True, outcome.get("failures", []), None

    def test_baseline_config_is_valid(self):
        ok, message = _validate_parameter_sources(self.paths)
        self.assertTrue(ok, message)

    def test_adds_recipe_block_and_loader_accepts_it(self):
        original = self.recipes.read_text(encoding="utf-8")
        ok, failures, error = self.apply_text(original + NEW_RECIPE)

        self.assertTrue(ok, f"failures={failures} error={error}")
        saved = self.recipes.read_text(encoding="utf-8")
        self.assertIn('recipe "e2eElevatorClose"', saved)
        self.assertTrue(_validate_parameter_sources(self.paths)[0])

        # 추가된 recipe 가 스냅샷 항목으로도 보여야 한다 — 안 보이면 그 뒤 값 편집이 불가능하다
        from core import paramstore

        keys = {paramstore.encode_key("recipes.hcl", p) for p in paramstore.scan("recipes.hcl", saved)}
        self.assertIn("recipes.hcl:e2eElevatorClose.label", keys)

    def test_removes_recipe_block(self):
        original = self.recipes.read_text(encoding="utf-8")
        added_ok, _f, _e = self.apply_text(original + NEW_RECIPE)
        self.assertTrue(added_ok)

        ok, failures, error = self.apply_text(original)
        self.assertTrue(ok, f"failures={failures} error={error}")
        self.assertNotIn('recipe "e2eElevatorClose"', self.recipes.read_text(encoding="utf-8"))

    def test_duplicate_recipe_is_rejected_and_file_restored(self):
        """문법은 맞고 부팅만 멈추는 원문. 값 편집에서는 안 나타나던 실패 모양이다."""
        original = self.recipes.read_text(encoding="utf-8")
        broken = original + '\nrecipe "pioElevatorOpen1f" {\n  label = "dup"\n}\n'

        ok, _failures, error = self.apply_text(broken)

        self.assertFalse(ok)
        self.assertIsNotNone(error)
        self.assertIn("duplicate recipe", error)
        # 파일이 원래대로 돌아와야 한다. 안 돌아오면 다음 재시작에서 어댑터가 못 뜬다
        self.assertEqual(self.recipes.read_text(encoding="utf-8"), original)
        self.assertTrue(_validate_parameter_sources(self.paths)[0])

    def test_failed_apply_does_not_move_mtime(self):
        """실패한 적용 1건이 mtime 을 밀면 열려 있는 모든 스냅샷이 STALE 이 된다."""
        original = self.recipes.read_text(encoding="utf-8")
        before = self.recipes.stat().st_mtime_ns

        ok, _failures, _error = self.apply_text(
            original + '\nrecipe "pioElevatorOpen1f" {\n  label = "dup"\n}\n'
        )

        self.assertFalse(ok)
        self.assertEqual(self.recipes.stat().st_mtime_ns, before)

    def test_broken_syntax_is_rejected_and_file_restored(self):
        original = self.recipes.read_text(encoding="utf-8")
        ok, _failures, error = self.apply_text('recipe "a" {\n  label = \n')

        self.assertFalse(ok)
        self.assertIsNotNone(error)
        self.assertEqual(self.recipes.read_text(encoding="utf-8"), original)

    def test_secret_in_incoming_text_is_rejected_before_write(self):
        """secret 은 파일에 닿기 전에 막혀야 한다. 쓴 뒤에는 이미 리비전 이력에 박힌다."""
        original = self.recipes.read_text(encoding="utf-8")
        leaked = original + '\nrecipe "leak" {\n  api_key = "AKIA-LEAK"\n}\n'

        ok, failures, _error = self.apply_text(leaked)

        self.assertFalse(ok)
        self.assertIn("적용하려는 원문이 거부됨", failures[0]["reason"])
        self.assertNotIn("AKIA-LEAK", self.recipes.read_text(encoding="utf-8"))

    def test_snapshot_text_matches_file_after_apply(self):
        """적용 후 재조회 원문이 파일과 정확히 같아야 롤백이 성립한다."""
        original = self.recipes.read_text(encoding="utf-8")
        self.assertTrue(self.apply_text(original + NEW_RECIPE)[0])

        published = read_source_text(self.paths, "recipes.hcl")
        self.assertEqual(published, self.recipes.read_text(encoding="utf-8"))

    def test_rollback_text_restores_exactly(self):
        """롤백은 보관 원문을 되쓰는 것이다. 바이트가 정확히 돌아와야 한다."""
        before = read_source_text(self.paths, "recipes.hcl")
        self.assertIsNotNone(before)
        self.assertTrue(self.apply_text(before + NEW_RECIPE)[0])
        self.assertNotEqual(read_source_text(self.paths, "recipes.hcl"), before)

        self.assertTrue(self.apply_text(before)[0])
        self.assertEqual(read_source_text(self.paths, "recipes.hcl"), before)


class _NoChange(Exception):
    """적용할 변경이 없을 때. write_config 안에서 던져 파일을 안 건드리게 한다."""


if __name__ == "__main__":
    unittest.main()
