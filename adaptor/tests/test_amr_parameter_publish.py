"""amr_parameter_publish 단위 테스트.

실제 설정 파일 네 개를 읽어 계약 형태(EquipmentParameterItem[])가 나오는지,
secret 이 제외되는지, 적용 경로가 파일별 편집기로 올바로 갈라지는지 확인한다.

항목 목록의 출처와 편집 대상이 **같은 스캔**(core.paramstore)이므로,
"화면엔 보이는데 저장은 실패" 가 생길 수 없다는 것을 실제 파일로 못박는다.
"""
import json
import sys
import dataclasses
import unittest
from pathlib import Path

ADAPTOR_DIR = Path(__file__).resolve().parent.parent
if str(ADAPTOR_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTOR_DIR))

from amr_parameter_publish import (  # noqa: E402
    MAX_ITEMS,
    PARAMETERS_RESULT_TOPIC,
    PARAMETERS_TOPIC,
    PARAMETER_SECTION_DATACLASSES,
    apply_parameter_changes,
    build_all_items,
    build_apply_result,
    build_default_items,
    build_file_items,
    build_parameter_snapshot,
    group_changes_by_source,
    is_overridden_elsewhere,
    is_secret_key,
    publish_apply_result,
    publish_parameters,
    read_source_text,
    to_json_safe,
)
from core import configio, paramstore  # noqa: E402
import amr_parameter_publish  # noqa: E402

CONFIG_DIR = ADAPTOR_DIR / "config"
PATHS = {name: CONFIG_DIR / name for name in paramstore.SOURCES if (CONFIG_DIR / name).exists()}


class FakeMqtt:
    """publish 인자만 기록하는 최소 스텁."""

    def __init__(self):
        self.calls = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.calls.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


class TestIsSecretKey(unittest.TestCase):
    def test_secret_tokens_detected(self):
        for key in ("password", "PASSWORD", "api_key", "accessKey_x", "private_key", "auth_token"):
            self.assertTrue(is_secret_key(key), key)

    def test_normal_keys_not_secret(self):
        for key in ("port", "host", "speed", "user", "timeout_s"):
            self.assertFalse(is_secret_key(key), key)

    def test_policy_fields_about_credentials_are_not_secret(self):
        """정책값이 목록을 채우면 진짜 자격증명이 묻혀 조치를 못 한다."""
        for key in ("credentials_path", "password_min_length", "password_forbidden_tokens"):
            self.assertFalse(is_secret_key(key), key)

    def test_unknown_names_still_fail_closed(self):
        """모르는 이름은 계속 막는다 — 한 건 놓치면 평문이 이력에 영구 적재된다."""
        for key in ("passwords", "credentials", "api_keys", "mqtt_password", "refresh_token"):
            self.assertTrue(is_secret_key(key), key)

    def test_real_credential_still_detected_next_to_its_policy_fields(self):
        """오탐만 빠지고 실제 자격증명은 남아야 한다(실측 config.toml 조합)."""
        self.assertFalse(is_secret_key("password_min_length"))
        self.assertTrue(is_secret_key("password"))


class TestBuildAllItems(unittest.TestCase):
    def setUp(self):
        self.items = build_all_items(PATHS)

    def test_items_produced(self):
        self.assertGreater(len(self.items), 0)

    def test_key_is_source_prefixed_and_decodable(self):
        for item in self.items:
            source, path = paramstore.decode_key(item["key"])
            self.assertIn(source, paramstore.SOURCES, item["key"])
            self.assertGreater(len(path), 0, item["key"])

    def test_kind_is_contract_value(self):
        for item in self.items:
            self.assertIn(item["kind"], {"string", "number", "boolean", "json"}, item["key"])

    def test_required_fields_present(self):
        for item in self.items:
            for field in ("key", "kind", "value", "present", "editable"):
                self.assertIn(field, item, f"{item.get('key')} missing {field}")

    def test_secrets_excluded(self):
        """자격증명 자체는 빠지되, 그것을 **설명하는** 정책값까지 지우지는 않는다.

        전에는 "password 라는 글자가 어디에도 없을 것"으로 검사해서 길이 정책·금지어
        목록까지 스냅샷에서 사라졌다. 그러면 운영자가 화면에서 그 값을 볼 수도 고칠 수도 없다.
        """
        keys = [i["key"] for i in self.items]
        leaked = [k for k in keys if k.rsplit(".", 1)[-1].lower() in {"password", "passwd"}]
        self.assertFalse(leaked, leaked)
        self.assertIn("config.toml:web_ui.password_min_length", keys)

    def test_all_four_sources_present(self):
        sources = {paramstore.decode_key(i["key"])[0] for i in self.items}
        for source in PATHS:
            self.assertIn(source, sources, source)

    def test_items_are_json_serialisable(self):
        json.dumps(self.items)

    def test_kind_reflects_value_shape(self):
        for item in self.items:
            value = item["value"]
            if value is None:
                continue
            if isinstance(value, bool):
                expected = "boolean"
            elif isinstance(value, (int, float)):
                expected = "number"
            elif isinstance(value, str):
                expected = "string"
            else:
                expected = "json"
            self.assertEqual(item["kind"], expected, f"{item['key']} value={value!r}")

    def test_override_fields_are_not_editable(self):
        """robots.hcl / jibot 오버레이가 최종값을 결정하는 필드는 편집 불가여야 한다.

        config.toml 을 고쳐도 기동 마지막 단계에서 덮어써지므로, 편집 가능으로
        내보내면 "APPLIED 라고 했는데 재시작하면 원래 값" 이 된다.
        """
        checked = 0
        for item in self.items:
            source, path = paramstore.decode_key(item["key"])
            if source != "config.toml" or len(path) < 2:
                continue
            section, key = str(path[-2]), str(path[-1])
            if is_overridden_elsewhere(section, key):
                checked += 1
                self.assertFalse(item["editable"], item["key"])
        self.assertGreater(checked, 0, "override 대상이 하나도 없으면 검사가 무의미함")

    def test_default_items_carry_default_value(self):
        absent = [i for i in self.items if not i["present"]]
        self.assertGreater(len(absent), 0)
        for item in absent:
            self.assertIsNone(item["value"])
            self.assertIn("defaultValue", item)

    def test_max_items_raises_instead_of_truncating(self):
        import amr_parameter_publish

        original = amr_parameter_publish.MAX_ITEMS
        try:
            amr_parameter_publish.MAX_ITEMS = 1
            with self.assertRaises(ValueError):
                build_all_items(PATHS)
        finally:
            amr_parameter_publish.MAX_ITEMS = original


class TestEditableItemsAreWritable(unittest.TestCase):
    """편집 가능으로 내보낸 항목 **전부**에 실제 쓰기를 시도한다.

    "화면엔 입력란이 보이는데 저장하면 실패" 가 가장 나쁜 실패다.
    항목 목록과 편집기가 어긋나면 여기서 잡힌다.
    """

    def test_every_editable_item_writes(self):
        texts = {s: p.read_text(encoding="utf-8") for s, p in PATHS.items()}
        checked = 0
        for item in build_all_items(PATHS):
            if not item["editable"] or not item["present"]:
                continue
            source, path = paramstore.decode_key(item["key"])
            paramstore.set_value(source, texts[source], path, item["value"])
            checked += 1
        self.assertGreater(checked, 0)

    def test_rewriting_same_scalar_changes_nothing(self):
        texts = {s: p.read_text(encoding="utf-8") for s, p in PATHS.items()}
        checked = 0
        for item in build_all_items(PATHS):
            if not item["editable"] or not item["present"]:
                continue
            if isinstance(item["value"], (dict, list)):
                continue
            source, path = paramstore.decode_key(item["key"])
            out = paramstore.set_value(source, texts[source], path, item["value"])
            self.assertEqual(out, texts[source], item["key"])
            checked += 1
        self.assertGreater(checked, 0)


class TestBuildFileItems(unittest.TestCase):
    def test_recipes_items_are_editable(self):
        text = (CONFIG_DIR / "recipes.hcl").read_text(encoding="utf-8")
        items = build_file_items("recipes.hcl", text)
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertTrue(item["key"].startswith("recipes.hcl:"))
            self.assertTrue(item["present"])

    def test_enum_choices_appear_in_description(self):
        """설명만 보고 값을 지어내면 어댑터가 부팅 때 거부하거나 조용히 무시한다."""
        text = 'recipe "r" {\n  step "pioWriteOut" {\n    parameters = { state = "on" }\n  }\n}\n'
        items = {i["key"]: i for i in build_file_items("recipes.hcl", text)}
        state = next(i for k, i in items.items() if k.endswith(".parameters.state"))
        self.assertIn("가능한 값: on | off", state["description"])
        self.assertEqual(state["choices"], ["on", "off"])

    def test_enum_choices_survive_repeated_step_suffix(self):
        """같은 스텝이 여러 번 나오면 경로에 #2 가 붙는다. 그때도 후보를 붙여야 한다."""
        self.assertEqual(
            amr_parameter_publish.enum_choices_for(
                "recipes.hcl", ("r", "pioWriteOut#2", "parameters", "state")
            ),
            ("on", "off"),
        )

    def test_motion_rules_mode_choices_ignore_array_index(self):
        self.assertEqual(
            amr_parameter_publish.enum_choices_for("config.toml", ("motion_rules", 0, "mode")),
            ("dock", "move"),
        )

    def test_free_text_fields_have_no_choices(self):
        self.assertEqual(
            amr_parameter_publish.enum_choices_for("config.toml", ("mqtt_broker", "host")), ()
        )

    def test_enum_choices_match_authoritative_sets(self):
        """원본을 고치고 표를 안 고치면 화면 설명이 조용히 낡는다."""
        from extensions.clamp import CLAMP_SERVO_POLICIES

        self.assertEqual(
            set(amr_parameter_publish.enum_choices_for("config.toml", ("ezi", "clamp_servo_policy"))),
            set(CLAMP_SERVO_POLICIES),
        )

    def test_description_names_the_file_the_field_lives_in(self):
        """설명이 늘 config.toml 을 가리키면 운영자가 엉뚱한 파일을 연다."""
        text = (CONFIG_DIR / "recipes.hcl").read_text(encoding="utf-8")
        for item in build_file_items("recipes.hcl", text):
            self.assertNotIn("config.toml", item["description"], item["key"])
            self.assertIn("recipes.hcl", item["description"], item["key"])
        extension_items = build_file_items(
            "extensions.hcl", 'extension "x" {\n  port = 1\n}\n'
        )
        self.assertIn("extensions.hcl", extension_items[0]["description"])

    def test_descriptions_are_korean(self):
        """WCS 화면은 전부 한국어라 설명만 영문이면 그 열만 읽히지 않는다."""
        import re

        text = (CONFIG_DIR / "recipes.hcl").read_text(encoding="utf-8")
        items = build_file_items("recipes.hcl", text)
        self.assertGreater(len(items), 0)
        for item in items:
            self.assertRegex(item["description"], r"[가-힣]", item["key"])
        # 영문 원문이 남아 있으면 번역이 빠진 것이다
        english = [i["key"] for i in items if "Save changes here" in i["description"]]
        self.assertFalse(english, english)
        self.assertTrue(re.search(r"[가-힣]", configio.field_description("x", "y", "config.toml")))

    def test_secret_key_excluded_per_file(self):
        items = build_file_items(
            "extensions.hcl", 'extension "x" {\n  api_key = "abc"\n  port = 1\n}\n'
        )
        self.assertEqual([i["key"] for i in items], ["extensions.hcl:x.port"])

    def test_state_action_uses_epr_opaque_paths(self):
        text = '''state_action "DRIVING" {
  start = {
    action = "warningOn"
    parameters = { signal = "lamp" }
  }
  end = { action = "warningOff" }
}
'''
        items = {item["key"]: item for item in build_file_items("extensions.hcl", text)}
        self.assertEqual(items["extensions.hcl:DRIVING.start.action"]["value"], "warningOn")
        self.assertTrue(items["extensions.hcl:DRIVING.start.action"]["editable"])
        self.assertEqual(
            items["extensions.hcl:DRIVING.start.parameters.signal"]["value"],
            "lamp",
        )
        self.assertEqual(items["extensions.hcl:DRIVING.end.action"]["value"], "warningOff")


class TestBuildDefaultItems(unittest.TestCase):
    def test_skips_keys_already_in_file(self):
        raw = configio.load_raw(PATHS["config.toml"], extensions_path=PATHS.get("extensions.hcl"))
        present = {i["key"] for i in build_file_items(
            "config.toml", PATHS["config.toml"].read_text(encoding="utf-8"))}
        defaults = build_default_items(
            raw, section_dataclasses=PARAMETER_SECTION_DATACLASSES, present_keys=present)
        self.assertFalse(set(i["key"] for i in defaults) & present)


class TestGroupChangesBySource(unittest.TestCase):
    def test_groups_by_key_source(self):
        grouped = group_changes_by_source([
            {"key": "config.toml:dock.retry", "op": "set", "value": 1},
            {"key": "extensions.hcl:pio.media", "op": "set", "value": 3},
            {"key": "config.toml:dock.timeout_s", "op": "unset"},
        ])
        self.assertEqual(sorted(grouped), ["config.toml", "extensions.hcl"])
        self.assertEqual(len(grouped["config.toml"]), 2)

    def test_malformed_key_goes_to_empty_source(self):
        grouped = group_changes_by_source([{"key": "nocolon", "op": "set", "value": 1}])
        self.assertIn("", grouped)


class TestApplyParameterChanges(unittest.TestCase):
    TOML = "[dock]\ntimeout_s = 30      # 도킹 대기\nretry = 3\n"
    HCL = 'extension "pio" {\n  media        = 2\n  pins = [0, 1]\n}\n'

    def test_sets_toml_scalar(self):
        text, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.timeout_s", "op": "set", "value": 45}],
            editable_keys={"config.toml:dock.timeout_s"}, source="config.toml")
        self.assertEqual(failures, [])
        self.assertIn("timeout_s = 45", text)
        self.assertIn("# 도킹 대기", text)

    def test_sets_hcl_scalar(self):
        text, applied, failures = apply_parameter_changes(
            self.HCL, [{"key": "extensions.hcl:pio.media", "op": "set", "value": 7}],
            editable_keys={"extensions.hcl:pio.media"}, source="extensions.hcl")
        self.assertEqual(failures, [])
        self.assertIn("media        = 7", text)

    def test_sets_state_action_through_epr_path(self):
        text = '''state_action "DRIVING" {
  start = { action = "warningOn", parameters = { signal = "lamp" } }
}
'''
        key = "extensions.hcl:DRIVING.start.action"
        changed, applied, failures = apply_parameter_changes(
            text,
            [{"key": key, "op": "set", "value": "otherWarningOn"}],
            editable_keys={key},
            source="extensions.hcl",
        )
        self.assertEqual(failures, [])
        self.assertEqual(applied, [key])
        self.assertIn('action = "otherWarningOn"', changed)

    def test_sets_hcl_list_element(self):
        text, applied, failures = apply_parameter_changes(
            self.HCL, [{"key": "extensions.hcl:pio.pins[1]", "op": "set", "value": 9}],
            editable_keys={"extensions.hcl:pio.pins[1]"}, source="extensions.hcl")
        self.assertEqual(failures, [])
        self.assertIn("[0, 9]", text)

    def test_unset_removes_key(self):
        text, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.retry", "op": "unset"}],
            editable_keys={"config.toml:dock.retry"}, source="config.toml")
        self.assertEqual(failures, [])
        self.assertNotIn("retry", text)

    def test_unset_already_absent_is_success(self):
        """이미 없으면 목표 상태에 도달해 있으므로 성공으로 본다(재시도 안전)."""
        once, _a, _f = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.retry", "op": "unset"}],
            editable_keys={"config.toml:dock.retry"}, source="config.toml")
        _t, applied, failures = apply_parameter_changes(
            once, [{"key": "config.toml:dock.retry", "op": "unset"}],
            editable_keys={"config.toml:dock.retry"}, source="config.toml")
        self.assertEqual(failures, [])
        self.assertEqual(applied, ["config.toml:dock.retry"])

    def test_non_editable_key_rejected_without_writing(self):
        text, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.timeout_s", "op": "set", "value": 45}],
            editable_keys=set(), source="config.toml")
        self.assertEqual(applied, [])
        self.assertEqual(text, self.TOML)

    def test_wrong_source_rejected(self):
        """다른 파일의 key 를 이 텍스트에 쓰면 파일이 깨진다."""
        text, applied, failures = apply_parameter_changes(
            self.HCL, [{"key": "config.toml:dock.retry", "op": "set", "value": 1}],
            editable_keys={"config.toml:dock.retry"}, source="extensions.hcl")
        self.assertEqual(applied, [])
        self.assertEqual(text, self.HCL)

    def test_malformed_key_fails(self):
        _t, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "nocolon", "op": "set", "value": 1}],
            editable_keys={"nocolon"}, source="config.toml")
        self.assertEqual(applied, [])
        self.assertEqual(len(failures), 1)

    def test_unknown_op_fails(self):
        _t, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.retry", "op": "delete"}],
            editable_keys={"config.toml:dock.retry"}, source="config.toml")
        self.assertEqual(applied, [])
        self.assertEqual(len(failures), 1)

    def test_partial_success_keeps_the_good_one(self):
        text, applied, failures = apply_parameter_changes(
            self.TOML, [
                {"key": "config.toml:dock.retry", "op": "set", "value": 9},
                {"key": "config.toml:dock.nope", "op": "set", "value": 1},
            ],
            editable_keys={"config.toml:dock.retry", "config.toml:dock.nope"},
            source="config.toml")
        self.assertEqual(applied, ["config.toml:dock.retry"])
        self.assertEqual(len(failures), 1)
        self.assertIn("retry = 9", text)

    def test_unknown_key_is_not_silently_created(self):
        """creatable 목록 밖의 없는 키는 오타로 보고 거부한다.

        그냥 만들면 로더가 모르는 설정이 파일에 조용히 쌓인다.
        """
        text, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.nope", "op": "set", "value": 1}],
            editable_keys={"config.toml:dock.nope"}, source="config.toml")
        self.assertEqual(applied, [])
        self.assertEqual(text, self.TOML)

    def test_creatable_key_is_written_into_the_file(self):
        """스냅샷이 present=False 로 신고한 항목은 새로 적어 넣는다."""
        import tomllib

        text, applied, failures = apply_parameter_changes(
            self.TOML, [{"key": "config.toml:dock.fail_timeout_sec", "op": "set", "value": 45}],
            editable_keys={"config.toml:dock.fail_timeout_sec"},
            creatable_keys={"config.toml:dock.fail_timeout_sec"},
            source="config.toml")
        self.assertEqual(failures, [])
        self.assertEqual(tomllib.loads(text)["dock"]["fail_timeout_sec"], 45)

    def test_creatable_hcl_key_is_inserted_into_block(self):
        import hcl2

        text, applied, failures = apply_parameter_changes(
            self.HCL, [{"key": "extensions.hcl:pio.new_knob", "op": "set", "value": 3}],
            editable_keys={"extensions.hcl:pio.new_knob"},
            creatable_keys={"extensions.hcl:pio.new_knob"},
            source="extensions.hcl")
        self.assertEqual(failures, [])
        self.assertIn("new_knob = 3", text)
        self.assertIn("media        = 2", text)
        self.assertTrue(hcl2.loads(text))


class TestBuildApplyResult(unittest.TestCase):
    def result(self, applied, failures):
        return build_apply_result(
            request_id="r1", equipment_id="eq", source="", revision=7,
            applied=applied, failures=failures, generated_at="t")

    def test_status_applied(self):
        self.assertEqual(self.result(["a"], [])["status"], "APPLIED")

    def test_status_partial(self):
        self.assertEqual(self.result(["a"], [{"key": "b", "reason": "x"}])["status"], "PARTIAL")

    def test_status_rejected(self):
        self.assertEqual(self.result([], [{"key": "b", "reason": "x"}])["status"], "REJECTED")

    def test_result_is_json_serialisable(self):
        json.dumps(self.result(["a"], []))


class TestBuildParameterSnapshot(unittest.TestCase):
    def setUp(self):
        self.items = build_all_items(PATHS)

    def test_envelope_fields(self):
        snap = build_parameter_snapshot(
            equipment_id="HN-SH6-TR-002", source="", request_id="req-1",
            generated_at="2026-08-03T00:00:00.000Z", revision=123, items=self.items)
        self.assertEqual(snap["requestId"], "req-1")
        self.assertEqual(snap["equipmentId"], "HN-SH6-TR-002")
        self.assertEqual(snap["revision"], 123)
        self.assertEqual(len(snap["items"]), len(self.items))

    def test_source_filters_items(self):
        snap = build_parameter_snapshot(
            equipment_id="eq", source="recipes.hcl", request_id="r",
            generated_at="t", revision=1, items=self.items)
        self.assertGreater(len(snap["items"]), 0)
        for item in snap["items"]:
            self.assertTrue(item["key"].startswith("recipes.hcl:"))

    def test_snapshot_is_json_serialisable(self):
        json.dumps(build_parameter_snapshot(
            equipment_id="eq", source="", request_id="r",
            generated_at="t", revision=1, items=self.items))


class TestToJsonSafe(unittest.TestCase):
    def test_dataclass_becomes_dict(self):
        import dataclasses

        @dataclasses.dataclass
        class Inner:
            a: int = 1

        @dataclasses.dataclass
        class Outer:
            inner: Inner = dataclasses.field(default_factory=Inner)

        self.assertEqual(to_json_safe(Outer()), {"inner": {"a": 1}})

    def test_scalars_pass_through(self):
        for value in (None, True, 1, 1.5, "x"):
            self.assertEqual(to_json_safe(value), value)

    def test_tuple_becomes_list(self):
        self.assertEqual(to_json_safe((1, 2)), [1, 2])


class TestPublish(unittest.TestCase):
    def test_snapshot_publish_uses_qos1_no_retain(self):
        mqtt = FakeMqtt()
        self.assertTrue(publish_parameters(mqtt, {"requestId": "r", "items": []}))
        self.assertEqual(mqtt.calls[0]["topic"], PARAMETERS_TOPIC)
        self.assertEqual(mqtt.calls[0]["qos"], 1)
        self.assertFalse(mqtt.calls[0]["retain"])

    def test_result_publish_uses_separate_topic(self):
        """스냅샷과 같은 토픽에 실으면 수신 측이 payload 모양으로 종류를 추측해야 한다."""
        mqtt = FakeMqtt()
        self.assertTrue(publish_apply_result(mqtt, {"requestId": "r"}))
        self.assertEqual(mqtt.calls[0]["topic"], PARAMETERS_RESULT_TOPIC)
        self.assertNotEqual(PARAMETERS_RESULT_TOPIC, PARAMETERS_TOPIC)

    def test_publish_failure_returns_false(self):
        class Broken:
            def publish(self, *a, **k):
                raise RuntimeError("boom")

        self.assertFalse(publish_parameters(Broken(), {"requestId": "r"}))
        self.assertFalse(publish_apply_result(Broken(), {"requestId": "r"}))


if __name__ == "__main__":
    unittest.main()


class MigratedSectionTestCase(unittest.TestCase):
    """extensions.hcl 로 옮겨진 섹션을 config.toml 자리로 광고하면 안 된다.

    config/config.py 는 config.toml 에 [pio]/[pio_advanced]/[air_shower_pio]/
    [elevator_pio] 가 있으면 ExtensionsError 로 부팅을 멈춘다. 그 자리를 편집
    가능으로 내보내면 적용이 로더 재검증에 걸려 되돌려지고 리비전만 헛돈다.
    """

    def test_default_items_point_at_extensions_hcl(self):
        raw = {"pio_advanced": {}}
        items = amr_parameter_publish.build_default_items(
            raw,
            section_dataclasses={"pio_advanced": _PioAdvancedStub},
            present_keys=set(),
        )
        keys = [i["key"] for i in items]
        self.assertTrue(keys, "기본값 항목이 하나도 안 나옴")
        for key in keys:
            self.assertFalse(
                key.startswith("config.toml:"),
                f"부팅을 멈추는 자리를 config.toml 로 광고함: {key}",
            )
        self.assertIn("extensions.hcl:pio.advanced.ping_default_timeout_sec", keys)

    def test_untouched_sections_stay_on_config_toml(self):
        raw = {"settings": {}}
        items = amr_parameter_publish.build_default_items(
            raw,
            section_dataclasses={"settings": _PioAdvancedStub},
            present_keys=set(),
        )
        self.assertIn("config.toml:settings.ping_default_timeout_sec",
                      [i["key"] for i in items])

    def test_migration_table_matches_loader_guard(self):
        """로더가 막는 섹션과 리다이렉트 표가 어긋나면 조용히 되살아난다."""
        from config.config import _PIO_SECTIONS

        self.assertEqual(
            set(_PIO_SECTIONS), set(amr_parameter_publish.MIGRATED_SECTION_PATHS)
        )


class TestReplaceText(unittest.TestCase):
    """원문 교체 — 값 단위 op 로는 못 하는 블록 추가·삭제의 유일한 경로."""

    RECIPES = 'recipe "a" {\n  label = "A"\n}\n'

    def apply(self, changes, source="recipes.hcl", text=None):
        return apply_parameter_changes(
            self.RECIPES if text is None else text,
            changes,
            editable_keys=set(),
            source=source,
        )

    def change(self, value, key="recipes.hcl"):
        return {"key": key, "op": "replaceText", "value": value}

    def test_adds_recipe_block(self):
        added = self.RECIPES + 'recipe "b" {\n  label = "B"\n}\n'
        text, applied, failures = self.apply([self.change(added)])
        self.assertEqual(failures, [])
        self.assertEqual(applied, ["recipes.hcl"])
        self.assertIn('recipe "b"', text)

    def test_deletes_recipe_block(self):
        remaining = 'recipe "a" {\n  label = "A"\n}\n'
        text, applied, failures = self.apply(
            [self.change(remaining)],
            text=remaining + 'recipe "b" {\n  label = "B"\n}\n',
        )
        self.assertEqual(failures, [])
        self.assertNotIn('recipe "b"', text)

    def test_editable_keys_not_required(self):
        """항목 게이팅은 값 단위 축이라 원문 교체에는 적용되지 않는다(source 허용목록이 대신 판정)."""
        _t, applied, failures = self.apply([self.change(self.RECIPES + "\n# note\n")])
        self.assertEqual(failures, [])
        self.assertEqual(applied, ["recipes.hcl"])

    def test_rejects_source_outside_allowlist(self):
        """허용 목록은 네 설정 파일뿐이다. 그 밖의 이름은 파일이 없으니 받지 않는다."""
        _t, applied, failures = self.apply(
            [self.change("x = 1", key="notes.txt")], source="notes.txt")
        self.assertEqual(applied, [])
        self.assertIn("원문 편집을 지원하지 않는", failures[0]["reason"])

    def test_rejects_incoming_text_with_secret(self):
        """쓴 뒤에 막으면 이미 파일과 리비전 이력에 secret 이 박힌 뒤다."""
        leaked = self.RECIPES + 'recipe "b" {\n  api_key = "AKIA-LEAK"\n}\n'
        _t, applied, failures = self.apply([self.change(leaked)])
        self.assertEqual(applied, [])
        self.assertIn("적용하려는 원문이 거부됨", failures[0]["reason"])
        self.assertIn("recipes.hcl:b.api_key", failures[0]["reason"])
        # 사유에 값 자체가 실리면 로그로 새어 나간다. 경로만 남아야 한다
        self.assertNotIn("AKIA-LEAK", failures[0]["reason"])

    def test_rejects_when_existing_text_has_secret(self):
        """파일에 이미 secret 이 있으면 원문 축 자체를 닫는다(조회에서도 text 가 안 실림)."""
        tainted = 'recipe "a" {\n  password = "p"\n}\n'
        _t, applied, failures = self.apply([self.change(tainted + "\n")], text=tainted)
        self.assertEqual(applied, [])
        self.assertIn("secret 성격 항목이 있어", failures[0]["reason"])

    def test_rejects_key_that_is_not_source(self):
        _t, applied, failures = self.apply([self.change("recipe \"z\" {}\n", key="recipes.hcl:a.label")])
        self.assertEqual(applied, [])
        self.assertIn("key 는 source 이름", failures[0]["reason"])

    def test_rejects_empty_text(self):
        for value in ("", "   ", None, 42):
            _t, applied, failures = self.apply([self.change(value)])
            self.assertEqual(applied, [], value)
            self.assertIn("비어 있거나 문자열이 아님", failures[0]["reason"])

    def test_rejects_mixing_with_value_edit(self):
        """원문 교체가 값 편집을 덮어써서 무엇이 남는지 예측할 수 없으므로 요청째 거부한다."""
        _t, applied, failures = self.apply([
            self.change(self.RECIPES + "\n"),
            {"key": "recipes.hcl:a.label", "op": "set", "value": "Z"},
        ])
        self.assertEqual(applied, [])
        self.assertEqual(len(failures), 2)
        self.assertIn("섞을 수 없음", failures[0]["reason"])

    def test_identical_text_is_noop(self):
        """같은 내용을 다시 쓰면 mtime 만 바뀌어 리비전이 헛돈다."""
        text, applied, failures = self.apply([self.change(self.RECIPES)])
        self.assertEqual((applied, failures), ([], []))
        self.assertEqual(text, self.RECIPES)

    def test_grouping_uses_key_as_source(self):
        grouped = group_changes_by_source([self.change("x", key="recipes.hcl")])
        self.assertEqual(list(grouped), ["recipes.hcl"])


class TestSnapshotText(unittest.TestCase):
    """원문 동봉 — 이게 있어야 WCS 가 구조 변경 가능 여부를 안다."""

    def test_text_included_for_allowed_source(self):
        text = read_source_text(PATHS, "recipes.hcl")
        self.assertIsNotNone(text)
        snap = build_parameter_snapshot(
            equipment_id="eq", source="recipes.hcl", request_id="r",
            generated_at="t", revision=1, items=[], text=text)
        self.assertIn('recipe "', snap["text"])

    def test_text_included_for_robots_hcl(self):
        """robots.hcl 도 원문 편집을 연다. 값 편집이 이미 열려 있으므로 원문만 닫는 건 앞뒤가 안 맞는다."""
        self.assertIsNotNone(read_source_text(PATHS, "robots.hcl"))

    def test_text_absent_for_config_toml_with_plaintext_secret(self):
        """허용 목록에 넣어도 평문 자격증명이 있으면 secret 검사가 계속 막아야 한다."""
        text, reason = amr_parameter_publish.read_source_text_with_reason(PATHS, "config.toml")
        self.assertIsNone(text)
        self.assertIn("secret 성격 항목이 있어", reason)
        self.assertIn("web-credentials.toml", reason)

    def test_block_reason_tells_operator_to_pick_a_source(self):
        """전체 조회에는 대상 파일이 없다. '불가' 가 아니라 '고르면 열림' 이라고 말해야 한다."""
        text, reason = amr_parameter_publish.read_source_text_with_reason(PATHS, "")
        self.assertIsNone(text)
        self.assertIn("source 를 하나 고르면 열림", reason)

    def test_snapshot_carries_block_reason_when_text_absent(self):
        """사유를 버리면 화면에는 '구조 변경 불가' 넉 자만 남아 조치를 못 한다."""
        snap = build_parameter_snapshot(
            equipment_id="eq", source="config.toml", request_id="r",
            generated_at="t", revision=1, items=[],
            text=None, text_block_reason="secret 성격 항목이 있어 원문 편집을 막음: config.toml:jibot.password")
        self.assertNotIn("text", snap)
        self.assertIn("password", snap["textBlockReason"])

    def test_snapshot_omits_block_reason_when_text_present(self):
        """원문이 실렸으면 사유 필드는 의미가 없다. 남기면 화면이 '열렸는데 막힘' 으로 읽힌다."""
        snap = build_parameter_snapshot(
            equipment_id="eq", source="recipes.hcl", request_id="r",
            generated_at="t", revision=1, items=[],
            text='recipe "a" {}\n', text_block_reason="지워져야 함")
        self.assertNotIn("textBlockReason", snap)

    def test_config_toml_is_blocked_by_secret_scan_not_only_by_allowlist(self):
        """config.toml 을 닫아 둔 이유가 주석이 아니라 검사로 남아 있어야 한다."""
        text = PATHS["config.toml"].read_text(encoding="utf-8")
        found = amr_parameter_publish.secret_paths("config.toml", text)
        self.assertGreater(len(found), 0, "secret 매칭이 0이면 allowlist 만 남아 근거가 약해짐")

    def test_text_absent_when_file_gains_a_secret(self, ):
        """허용 목록에 있어도 secret 이 생기면 원문 축이 자동으로 닫혀야 한다."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipes.hcl"
            path.write_text('recipe "a" {\n  label = "A"\n}\n', encoding="utf-8")
            self.assertIsNotNone(read_source_text({"recipes.hcl": path}, "recipes.hcl"))

            path.write_text('recipe "a" {\n  auth_token = "t"\n}\n', encoding="utf-8")
            self.assertIsNone(read_source_text({"recipes.hcl": path}, "recipes.hcl"))

    def test_text_absent_for_whole_snapshot_request(self):
        """전체 조회는 대상 파일이 하나로 정해지지 않는다."""
        self.assertIsNone(read_source_text(PATHS, ""))

    def test_key_omitted_when_no_text(self):
        snap = build_parameter_snapshot(
            equipment_id="eq", source="config.toml", request_id="r",
            generated_at="t", revision=1, items=[], text=None)
        self.assertNotIn("text", snap)


@dataclasses.dataclass
class _PioAdvancedStub:
    ping_default_timeout_sec: float = 5.0


class MultilineContainerTestCase(unittest.TestCase):
    """여러 줄 컨테이너를 통째로 쓰면 안쪽 주석이 사라진다. 편집 대상에서 뺀다."""

    HCL = (
        'extension "pio" {\n'
        "  advanced = {\n"
        "    init_timeout_sec = 2.0   # pioInit 기본 대기\n"
        "    poll_interval_sec = 0.05 # polling 주기\n"
        "  }\n"
        "  input_pins = [0, 1, 2]\n"
        "}\n"
    )

    def _by_key(self, source, text):
        return {i["key"]: i for i in amr_parameter_publish.build_file_items(source, text)}

    def test_multiline_map_is_not_editable(self):
        items = self._by_key("extensions.hcl", self.HCL)
        self.assertFalse(items["extensions.hcl:pio.advanced"]["editable"])
        self.assertIn("주석", items["extensions.hcl:pio.advanced"]["description"])

    def test_leaves_stay_editable(self):
        items = self._by_key("extensions.hcl", self.HCL)
        self.assertTrue(items["extensions.hcl:pio.advanced.init_timeout_sec"]["editable"])
        self.assertTrue(items["extensions.hcl:pio.advanced.poll_interval_sec"]["editable"])

    def test_single_line_list_stays_editable(self):
        items = self._by_key("extensions.hcl", self.HCL)
        self.assertTrue(items["extensions.hcl:pio.input_pins"]["editable"])

    def test_toml_multiline_array_is_not_editable(self):
        text = "pins = [\n  1,\n  2,\n]\ninline = [1, 2]\n"
        items = self._by_key("config.toml", text)
        self.assertFalse(items["config.toml:pins"]["editable"])
        self.assertTrue(items["config.toml:inline"]["editable"])
