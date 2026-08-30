"""robots.toml -> robots.hcl 일회성 변환기."""

import importlib.util
import re
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "convert-robots-toml-to-hcl.py"


def _load():
    spec = importlib.util.spec_from_file_location("convert_robots", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_converts_ids_scalars_and_lists():
    module = _load()
    out = module.convert(
        textwrap.dedent(
            """
            [[robot]]
            id = "ROBOT-A"
            vehicle_ip = "10.0.0.11"
            vehicle_port = 7273
            simulator = true
            extra_args = ["--x", "--y"]

            [[robot]]
            id = "ROBOT-B"
            mqtt_port = 12000
            """
        )
    )

    # 스크립트가 키를 ljust로 정렬 패딩하므로 `키 = 값` 사이 공백 폭이 가변이다.
    # 그래도 키와 값은 반드시 같은 줄에 묶여 있어야 한다 — 값이 뒤바뀌는 회귀를
    # 잡으려면 줄 단위 정규식으로 결합을 검증해야 한다.
    def line(key, value):
        return re.search(
            rf"^\s*{re.escape(key)}\s*=\s*{re.escape(value)}\s*$", out, re.M
        )

    assert 'robot "ROBOT-A" {' in out
    assert line("vehicle_ip", '"10.0.0.11"')
    assert line("vehicle_port", "7273")
    assert line("simulator", "true")
    assert line("extra_args", '["--x", "--y"]')
    assert 'robot "ROBOT-B" {' in out
    assert line("mqtt_port", "12000")


def test_output_parses_as_valid_fleet():
    module = _load()
    out = module.convert('[[robot]]\nid = "R-1"\nvehicle_ip = "10.0.0.1"\n')

    import sys

    sys.path.insert(0, str(REPO_ROOT / "adaptor"))
    try:
        from config import hcl

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "robots.hcl"
            path.write_text(out, encoding="utf-8")
            data = hcl.load_hcl(path)
        label, body = hcl.blocks(data, "robot")[0]
        assert label == "R-1"
        assert body["vehicle_ip"] == "10.0.0.1"
    finally:
        sys.path.remove(str(REPO_ROOT / "adaptor"))


def test_robot_id_is_escaped_in_the_block_label():
    """따옴표가 든 id를 그대로 라벨에 넣으면 깨진 HCL이 나온다."""
    module = _load()
    out = module.convert('[[robot]]\nid = \'A"B\'\nvehicle_ip = "10.0.0.1"\n')

    assert 'robot "A\\"B" {' in out

    import sys, tempfile

    sys.path.insert(0, str(REPO_ROOT / "adaptor"))
    try:
        from config import hcl

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "robots.hcl"
            path.write_text(out, encoding="utf-8")
            # 핵심은 이게 파싱된다는 것이다. escaping 전에는 `robot "A"B" {`가
            # 나와 문법 오류였다. python-hcl2는 `\"`를 되돌리지 않으므로 라벨에
            # 역슬래시가 남지만, 따옴표가 든 로봇 id는 현실적이지 않으므로
            # 로더에 unescape를 넣지 않는다.
            data = hcl.load_hcl(path)
        labels = [label for label, _ in hcl.blocks(data, "robot")]
        assert len(labels) == 1
        assert "A" in labels[0] and "B" in labels[0]
    finally:
        sys.path.remove(str(REPO_ROOT / "adaptor"))


def test_refuses_to_overwrite_without_force(tmp_path):
    module = _load()
    src = tmp_path / "robots.toml"
    src.write_text('[[robot]]\nid = "R-1"\n', encoding="utf-8")
    dest = tmp_path / "robots.hcl"
    dest.write_text("existing\n", encoding="utf-8")

    try:
        module.main([str(src), "-o", str(dest)])
    except SystemExit as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected SystemExit")

    assert dest.read_text(encoding="utf-8") == "existing\n"

    module.main([str(src), "-o", str(dest), "--force"])
    assert 'robot "R-1" {' in dest.read_text(encoding="utf-8")


def test_nested_table_is_rejected_with_a_named_error():
    """중첩 테이블은 조용히 파이썬 repr 문자열이 되면 안 된다.

    fleet.py의 _FIELD_TYPES에 dict 타입 필드가 없어 HCL 객체로 변환해도
    load_fleet이 unknown field로 거부한다. 운영자가 고칠 수 있는 지점은
    변환 시점이므로 여기서 실패시킨다.
    """
    module = _load()
    try:
        module.convert('[[robot]]\nid = "R-1"\n\n[robot.extra]\nfoo = 1\n')
    except SystemExit as exc:
        message = str(exc)
        assert "R-1" in message
        assert "extra" in message
    else:
        raise AssertionError("expected SystemExit")


def test_nested_table_inside_a_list_is_rejected():
    module = _load()
    try:
        module.convert('[[robot]]\nid = "R-1"\nextra_args = [{ a = 1 }]\n')
    except SystemExit as exc:
        assert "extra_args" in str(exc)
    else:
        raise AssertionError("expected SystemExit")

