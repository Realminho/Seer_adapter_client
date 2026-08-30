"""getParameters instant-action 처리: adaptor 설정을 항목 단위 스냅샷으로 MQTT publish.

발행 토픽은 mqtt_client가 prefix(amr/{vda_version}/{serial})를 붙여
amr/v3/{serial}/parameters 가 된다(non-retained). 페이로드는 WCS 설비 파라미터 레지스트리 계약의
EquipmentParameterSnapshot 형태이며 top-level requestId/generatedAt로 요청-응답을 correlate 한다.

설계 원칙 3가지(WCS 스펙 2026-08-01-equipment-parameter-registry-design.md):
  1. 파일 blob을 보내지 않는다. 항목(key/value) 단위로만 보낸다.
  2. key는 "{source}:{section}.{field}" opaque 문자열이며 WCS는 파싱하지 않는다.
  3. secret 성격 필드는 스냅샷에 아예 담지 않는다(마스킹이 아니라 제외).

원칙 1의 예외 — TEXT_EDITABLE_SOURCES(extensions.hcl, recipes.hcl)는 원문을 함께 싣는다.
값 단위 op 로는 recipe/extension 블록을 추가·삭제할 수 없기 때문이다: 없는 항목은 스냅샷에
행이 없어 지목할 key 가 없고, 편집기는 인덱스가 밀리는 컨테이너 삭제를 거부한다. secret 이
없고(원칙 3의 대상 아님) 로더가 의미까지 재검증할 수 있는 이 두 파일에만 연다.
WCS 는 이 문자열도 파싱하지 않는다 — 원칙 2와 같은 취지로 형식은 여전히 설비만 안다.

액션 이름이 getParameters 인 이유: jibot 접두어나 JIBOT.COMMAND_SPECS 이름
(UmGetConfig 등)을 쓰면 adapter_jibot._command_from_jibot_action_type 이 raw JIBOT
command 로 흡수하고 motion 판정까지 받아 BUSY 중 차단된다. getMap/setMap 과 같은
평범한 이름이어야 elif 분기로 안전하게 잡힌다.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import configio, paramstore

# mqtt_client가 prefix를 붙이므로 subtopic만 지정 → amr/v3/{serial}/parameters
PARAMETERS_TOPIC = "parameters"

# 적용 결과는 별도 토픽으로 보낸다. 스냅샷과 같은 토픽에 실으면 수신 측이
# 페이로드 모양(status 필드 유무)으로 종류를 추측해야 해서 조용히 뒤섞인다.
PARAMETERS_RESULT_TOPIC = "parameters-result"

# ConfigScalar.kind(configio._kind_of) → 계약의 EquipmentParameterItem.kind
_KIND = {"str": "string", "int": "number", "num": "number", "bool": "boolean"}

# 스냅샷에서 제외할 secret 성격 키. 부분일치(소문자)로 검사한다.
# 계약상 마스킹이 아니라 제외이므로 WCS는 이 항목의 존재 자체를 모른다.
# snake_case 와 camelCase 를 모두 잡으려고 구분자를 뺀 형태도 함께 둔다
# (TOML 관례는 snake_case 지만 extensions.hcl 은 강제되지 않는다).
_SECRET_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "private_key",
    "privatekey",
)

#: 부분일치가 잡아 버리지만 **자격증명 자체가 아닌 것이 확실한** 키 이름.
#:
#: 왜 필요한가: 부분일치는 안전한 쪽으로 기울어 있어 좋지만, 정책값까지 secret 으로 잡으면
#: 원문 편집이 막힌 진짜 이유가 목록에 묻힌다. 실측(192.168.101.61 config.toml)에서 8건이
#: 걸렸는데 실제 자격증명은 `jibot_client.password` 하나뿐이었고, 화면에는 앞의 오탐 3건만
#: 보여서 운영자에게 "password_min_length 를 web-credentials.toml 로 옮기라"고 읽혔다.
#:
#: 규칙이 아니라 **명단**인 이유: "끝이 토큰이면 secret" 같은 규칙은 `passwords`,
#: `credentials` 같은 복수형을 놓쳐 조용히 새게 만든다. 여기서 한 건을 놓치는 대가는
#: 평문 자격증명이 WCS 리비전 이력에 영구 적재되는 것이라, 모르는 이름은 계속 막는 쪽이 맞다.
#: 그래서 값이 자격증명이 아님을 확인한 이름만 하나씩 적는다.
_SECRET_KEY_EXCEPTIONS = frozenset({
    "credentials_path",           # 자격증명이 아니라 그것이 들어 있는 파일 경로
    "password_min_length",        # 길이 정책(숫자)
    "password_forbidden_tokens",  # 금지어 목록 — 오히려 "쓰면 안 되는" 값들
})

# 항목 수 상한. WCS가 반환값 전체를 콘솔 로그로 stringify 하므로(equipment-remote-control)
# 무한정 커지면 로그 스트림으로 새어나간다. 초과분은 자르지 않고 실패시킨다.
MAX_ITEMS = 2000

#: 원문(파일 전체 텍스트) 편집을 허용하는 source. 네 파일 전부다.
#:
#: 값 단위 set/unset 으로는 블록을 추가·삭제할 수 없다 — 없는 항목은 스냅샷에 행이 없어
#: 지목할 key 자체가 없고, 편집기는 인덱스가 밀리는 컨테이너 삭제를 거부한다. 그래서
#: 원문째로 주고받아 구조 변경을 연다.
#:
#: 쓰기 경로는 source 를 가리지 않는다 — :func:`apply_parameter_changes` 가 replaceText 를
#: 파일 종류와 무관하게 처리하고, 저장은 ``configio.write_config`` 가 락 안에서 하며
#: validate 가 실패하면 원문과 mtime 까지 되돌린다(robots.hcl 은 부팅 로더가 읽지 않는
#: fleet 오버레이라 HCL 문법 검사만, 나머지는 로더 재검증). 그래서 목록만 열면 된다.
#:
#: **config.toml 은 목록에 있어도 대개 secret 검사에서 막힌다.** JIBOT 로그인 password 가
#: 평문으로 들어 있는 배치가 있고, 원문을 실으면 그 값이 WCS 리비전 이력에 영구 적재된다.
#: 열려면 그 값을 ``config/web-credentials.toml`` 또는 ``jibot-config.toml`` 오버레이로
#: 옮겨야 한다. 사유는 :func:`source_text_block_reason` 이 만들어 스냅샷에 실리므로
#: WCS 화면이 "왜 못 고치는지"를 그대로 보여 준다.
#:
#: 이 목록에 있어도 :func:`text_edit_block_reason` 의 secret 검사를 통과해야 실제로 열린다.
TEXT_EDITABLE_SOURCES = paramstore.SOURCES


def secret_paths(source: str, text: str) -> List[str]:
    """텍스트에서 secret 성격 경로를 찾는다.

    항목 스냅샷은 이 경로들을 아예 빼고 보내지만(원칙 3), 원문은 파일을 통째로 싣기 때문에
    같은 필터를 원문에도 걸어야 한다. 안 걸면 값 단위로는 가려지던 값이 원문으로 새어 나간다.

    :param source: 설정 묶음 이름
    :param text: 파일 텍스트
    :returns: secret 로 판정된 opaque key 목록
    """
    try:
        scanned = paramstore.scan(source, text)
    except Exception:  # noqa: BLE001 - 못 읽는 텍스트는 secret 판정을 할 수 없으니 막는 쪽으로
        return ["<parse-failed>"]
    return [
        paramstore.encode_key(source, path)
        for path in scanned
        if any(isinstance(step, str) and is_secret_key(step) for step in path)
    ]


def text_edit_block_reason(source: str, text: str) -> Optional[str]:
    """원문 편집을 막아야 하면 사유를, 열어도 되면 None 을 돌려준다.

    허용 목록만으로 판정하지 않는 이유: 목록은 "지금 이 파일에 secret 이 없다"는 **주장**이라
    나중에 자격증명이 한 줄 추가되면 조용히 틀린 말이 된다. 그때 원문 경로는 그 값을 WCS 로
    올리고 **리비전 이력에 영구 적재**한다(스냅샷 text 가 그대로 저장됨). 그래서 매번 검사한다.

    적용 요청으로 들어오는 새 원문에도 같은 검사를 건다. 파일에 쓰기 전에 막아야 이력에
    남지 않는다 — 쓴 뒤에 닫으면 이미 새어 나간 뒤다.

    :param source: 설정 묶음 이름
    :param text: 검사할 파일 텍스트
    :returns: 막아야 할 사유 문자열, 열어도 되면 None
    """
    if not source:
        return (
            "전체 조회에는 대상 파일이 하나로 정해지지 않아 원문 편집을 열 수 없음 "
            f"— source 를 하나 고르면 열림 (가능: {', '.join(TEXT_EDITABLE_SOURCES)})"
        )
    if source not in TEXT_EDITABLE_SOURCES:
        return (
            f"원문 편집을 지원하지 않는 source: {source} "
            f"(가능: {', '.join(TEXT_EDITABLE_SOURCES)})"
        )
    found = secret_paths(source, text)
    if found:
        return (
            f"secret 성격 항목이 있어 원문 편집을 막음: {', '.join(found[:3])}"
            f"{' 외 %d건' % (len(found) - 3) if len(found) > 3 else ''} "
            "— 해당 값을 web-credentials.toml 로 옮긴 뒤 다시 시도해야 함"
        )
    return None


def _section_dataclasses() -> Dict[str, Any]:
    """섹션명 → dataclass 매핑을 만든다.

    config.get_config 가 ``Settings(**config_dict["settings"])`` 형태로 인라인 조립하고 있어
    기계적으로 도출할 수 없다. 여기 명시 매핑을 두되, 누락된 섹션은 기본값 항목이 안 나올 뿐
    스냅샷 자체는 정상 동작한다(degrade, not break).

    import 를 함수 안에 두는 이유: config 패키지가 이 모듈을 import 하는 경우의 순환을 피하고,
    테스트가 config 전체 의존성 없이 build_parameter_items 를 부를 수 있게 하기 위함.

    Returns: 섹션명 → dataclass 타입 dict. import 실패 시 빈 dict.
    """
    try:
        from config import config as config_module
    except Exception:  # noqa: BLE001 - 기본값 노출은 부가 기능이라 실패해도 스냅샷은 나가야 한다
        return {}
    names = {
        "mqtt_broker": "MqttBrokerConfig",
        "vehicle": "VehicleConfig",
        "ezi": "EziConfig",
        "settings": "Settings",
        "jibot_status": "JibotStatus",
        "sound_settings": "SoundSettings",
        "manual_control": "ManualControlSettings",
        "charge": "ChargeConfig",
        "dock": "DockConfig",
        "charge_circuit": "ChargeCircuitConfig",
        "bms_ros": "BmsRosConfig",
        "pio": "PioConfig",
        "pio_advanced": "PioAdvancedConfig",
        "air_shower_pio": "AirShowerPioConfig",
        "elevator_pio": "ElevatorPioConfig",
    }
    mapping: Dict[str, Any] = {}
    for section, cls_name in names.items():
        cls = getattr(config_module, cls_name, None)
        if cls is not None:
            mapping[section] = cls
    return mapping


#: 섹션명 → dataclass 매핑(모듈 로드 시 1회 해석)
PARAMETER_SECTION_DATACLASSES: Dict[str, Any] = _section_dataclasses()


#: 값이 정해진 목록뿐인 필드의 허용값.
#:
#: 여기 적은 값은 전부 코드/설정에 이미 선언된 것을 옮겨 온 것이고, 아래 테스트가 원본과
#: 대조한다(`test_enum_choices_match_authoritative_sets`). 원본을 고치고 여기를 안 고치면
#: 테스트가 깨지므로, 화면 설명이 조용히 낡는 일이 없다.
#:
#: 직접 import 하지 않는 이유: `extensions.clamp` 는 adapter 를 끌고 들어와 순환이 생기고,
#: 이 모듈은 adapter 없이도 스냅샷을 만들 수 있어야 한다(테스트가 그렇게 쓴다).
_ENUM_CHOICES: Dict[Tuple[str, str], Tuple[Any, ...]] = {
    # config/config.toml:32 주석 — `[[adapter.instances]]` 예시도 이 둘만 쓴다
    ("adapter", "vendor"): ("jibot", "hexplorer"),
    # extensions/clamp/__init__.py 의 CLAMP_SERVO_POLICIES
    ("ezi", "clamp_servo_policy"): ("auto_on_keep_on", "auto_on_auto_off", "manual"),
}

#: 레시피/익스텐션 스텝 파라미터의 허용값. 키는 (스텝 타입, 파라미터명).
#: 레시피 이름은 사용자가 짓기 때문에 (섹션, 필드)로는 못 잡고 스텝 타입으로 잡는다.
#: extensions/pio/__init__.py 가 같은 값을 ActionParameterSpec.choices 로 선언한다.
_ENUM_CHOICES_BY_STEP: Dict[Tuple[str, str], Tuple[Any, ...]] = {
    ("pioWriteOut", "state"): ("on", "off"),
}


def enum_choices_for(source: str, path: paramstore.ValuePath) -> Tuple[Any, ...]:
    """이 항목이 고를 수 있는 값 목록을 돌려준다. 자유 입력이면 빈 튜플.

    운영자가 설명만 보고 값을 지어내면 어댑터가 부팅 때 거부하거나(clamp_servo_policy)
    조용히 무시한다(mode). 그래서 아는 목록은 설명과 `choices` 양쪽에 싣는다.

    :param source: 설정 묶음 이름
    :param path: 항목 경로
    :returns: 허용값 튜플. 목록이 정해져 있지 않으면 빈 튜플
    """
    if not path:
        return ()
    leaf = str(path[-1])

    if len(path) >= 2:
        direct = _ENUM_CHOICES.get((str(path[-2]), leaf))
        if direct:
            return direct

    # motion_rules 는 배열이라 경로에 인덱스가 끼어든다(motion_rules.0.mode).
    # config/config.toml 상단 주석이 dock/move 두 가지만 정의한다.
    if str(path[0]) == "motion_rules" and leaf == "mode":
        return ("dock", "move")

    # `<레시피>.<스텝타입>[#n].parameters.<파라미터>` — 스텝 타입에서 #n 을 떼고 본다
    if len(path) >= 3 and str(path[-2]) == "parameters":
        step = str(path[-3]).split("#", 1)[0]
        by_step = _ENUM_CHOICES_BY_STEP.get((step, leaf))
        if by_step:
            return by_step

    return ()


def describe_choices(choices: Tuple[Any, ...]) -> str:
    """허용값을 설명 뒤에 붙일 문장으로 만든다.

    :param choices: 허용값 튜플
    :returns: ` 가능한 값: a | b` 형태. 비어 있으면 빈 문자열
    """
    if not choices:
        return ""
    return " 가능한 값: " + " | ".join(str(c) for c in choices) + "."


def is_secret_key(key: str) -> bool:
    """키 이름이 secret 성격인지 판정한다.

    모르는 이름은 계속 secret 으로 본다(fail-closed). 아는 정책값만
    :data:`_SECRET_KEY_EXCEPTIONS` 로 빼서, 차단 사유 목록에 진짜 자격증명만 남게 한다.

    Args:
        key: 설정 필드명.

    Returns: secret 으로 취급해 스냅샷에서 제외해야 하면 True.
    """
    lowered = key.lower()
    if lowered in _SECRET_KEY_EXCEPTIONS:
        return False
    return any(token in lowered for token in _SECRET_KEY_TOKENS)


def _source_of(section: str) -> str:
    """섹션이 어느 파일에서 온 것인지 판정해 source 문자열을 만든다.

    extensions.hcl 유래 섹션은 nested(``pio.advanced``) 형태도 있어 첫 마디로 비교한다.

    Args:
        section: config 섹션명.

    Returns: "config.toml" 또는 "extensions.hcl".
    """
    base = section.split(".", 1)[0]
    if section in configio.EXTENSION_SECTIONS or base in configio.EXTENSION_SECTIONS:
        return "extensions.hcl"
    return "config.toml"


def is_overridden_elsewhere(section: str, key: str) -> bool:
    """이 필드가 config.toml 밖에서 최종 결정되는지 판정한다.

    robots.hcl override(configio._ROBOT_OVERRIDE_FIELDS)와 jibot 오버레이
    (configio._JIBOT_OVERLAY_FIELDS) 대상 필드는 get_config 마지막 단계에서 덮어써진다.
    config.toml 을 고쳐도 재시작하면 원래대로 돌아가므로 편집 가능으로 내보내면 안 된다.

    Args:
        section: 설정 섹션명.
        key: 필드명.

    Returns: 다른 파일이 최종값을 결정하면 True.
    """
    pair = (section, key)
    return (
        pair in getattr(configio, "_ROBOT_OVERRIDE_FIELDS", frozenset())
        or pair in getattr(configio, "_JIBOT_OVERLAY_FIELDS", frozenset())
    )


def _item(section: str, key: str, *, kind: str, value: Any, present: bool, editable: bool,
          default_value: Any = None, has_default: bool = False) -> Dict[str, Any]:
    """EquipmentParameterItem 하나를 만든다.

    Args:
        section: 설정 섹션명.
        key: 필드명.
        kind: 계약 kind("string"/"number"/"boolean"/"json").
        value: 현재 값. 미기재 항목은 None.
        present: 설정 파일에 실제로 기재되어 있는지.
        editable: applyConfig 로 변경 가능한지.
        default_value: dataclass 기본값(있을 때만).
        has_default: default_value 를 실을지 여부.

    Returns: EquipmentParameterItem dict.
    """
    overridden = is_overridden_elsewhere(section, key)
    # 섹션이 어느 파일에서 왔는지가 key 와 설명 양쪽을 결정한다. 설명에만 config.toml 을
    # 박아 두면 extensions.hcl 항목이 "config.toml 을 고치라"고 안내해 엉뚱한 파일을 열게 된다.
    item_source = _source_of(section)
    description = configio.field_description(section, key, item_source)
    choices = enum_choices_for(item_source, (section, key))
    description = f"{description}{describe_choices(choices)}"
    if overridden:
        # 운영자가 "왜 안 바뀌지"로 헤매지 않도록 이유를 값 옆에 남긴다.
        description = f"{description} [robots.hcl/jibot 오버레이가 최종값을 결정함 — 여기서 못 고침]"
    item: Dict[str, Any] = {
        "key": f"{item_source}:{section}.{key}",
        "kind": kind,
        "value": value,
        "present": present,
        "editable": editable and not overridden,
        "description": description,
    }
    if choices:
        item["choices"] = list(choices)
    if has_default:
        item["defaultValue"] = default_value
    return item


def _dataclass_defaults(section_dataclasses: Mapping[str, Any], section: str) -> Dict[str, Any]:
    """섹션에 대응하는 dataclass 의 필드 기본값을 반환한다.

    운영자가 "파일에 안 적혀 있지만 이 값으로 동작 중"인 항목까지 보려면 dataclass 기본값이
    필요하다. 대응 dataclass 가 없는 섹션(extensions.hcl 유래 등)은 빈 dict 를 돌려준다.

    Args:
        section_dataclasses: 섹션명 → dataclass 타입 매핑.
        section: 조회할 섹션명.

    Returns: 필드명 → 기본값 dict. 기본값이 MISSING 인 필드는 제외.
    """
    cls = section_dataclasses.get(section)
    if cls is None or not dataclasses.is_dataclass(cls):
        return {}
    defaults: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            try:
                defaults[f.name] = f.default_factory()  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - 기본값 생성 실패는 그 필드만 건너뛴다
                continue
    return defaults


def _kind_of_value(value: Any) -> str:
    """값의 실제 모양에서 계약 kind 를 추론한다.

    **편집 가능 여부와 무관하다.** configio 의 readonly 목록은 "이 항목은 못 고친다"는 뜻이지
    "이 값은 구조체다"라는 뜻이 아니다(configio.py:492 `section not in EXTENSION_SECTIONS`).
    extensions.hcl 유래 섹션은 낱값(int/float/str/bool)이어도 readonly 로 들어오므로,
    거기에 kind='json' 을 찍으면 화면에서 낱값이 덩어리로 잘못 보인다.

    Args:
        value: 항목 값 또는 dataclass 기본값.

    Returns: 계약 kind 문자열. dict/list 만 'json'.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "json"


def to_json_safe(value: Any) -> Any:
    """기본값을 JSON 직렬화 가능한 형태로 변환한다.

    dataclass 기본값에는 중첩 dataclass(예: DockConfig.approach_params = DockApproachParams)
    가 섞여 있다. WCS worker 경계가 JSON round-trip 을 강제하므로 그대로 실으면 발행 자체가
    깨진다. dataclass 는 dict 로 펴고, 그래도 직렬화가 안 되는 값은 문자열로 낮춘다.

    Args:
        value: 임의의 기본값.

    Returns: JSON 직렬화 가능한 값.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: to_json_safe(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    return str(value)


def _source_files(paths: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """읽을 수 있는 source 와 그 텍스트를 순서대로 돌려준다.

    :param paths: source → 파일 경로
    :returns: (source, 텍스트) 목록. 없는 파일은 건너뜀
    """
    out: List[Tuple[str, str]] = []
    for source in paramstore.SOURCES:
        path = paths.get(source)
        if path is None:
            continue
        try:
            out.append((source, Path(path).read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


def _is_multiline_container(value: Any, raw: str) -> bool:
    """여러 줄에 걸친 컨테이너인지 판정한다.

    컨테이너를 통째로 쓰면 편집기는 그 범위를 리터럴 한 덩어리로 갈아 끼운다. 여러 줄
    블록이면 **안쪽 줄마다 붙은 주석과 정렬이 통째로 사라지고** 값 표기까지 바뀐다
    (실측: 롤백이 `advanced = { ... }` 18줄을 한 줄로 뭉개고 주석 6줄을 지웠으며
    `2` 가 `2.0` 이 됨).

    안쪽 항목은 각각 별도 항목으로 이미 편집 가능하므로, 컨테이너 자체를 편집 대상에서
    빼도 도달하지 못하는 값은 생기지 않는다. 한 줄 컨테이너(`input_pins = [0,1,2]`)는
    잃을 주석이 없으므로 그대로 둔다.

    :param value: 파싱된 값
    :param raw: 값의 원문
    :returns: 여러 줄 컨테이너면 True
    """
    return isinstance(value, (dict, list)) and "\n" in (raw or "")


def build_file_items(source: str, text: str) -> List[Dict[str, Any]]:
    """설정 파일 하나에서 EquipmentParameterItem 목록을 만든다.

    편집기가 도달하는 경로를 그대로 항목으로 낸다. 그래서 "화면엔 보이는데 저장은
    실패" 가 구조적으로 생길 수 없다 — 목록의 출처와 편집 대상이 같은 스캔이다.

    :param source: 설정 묶음 이름
    :param text: 파일 텍스트
    :returns: EquipmentParameterItem dict 목록
    """
    items: List[Dict[str, Any]] = []
    for path, found in paramstore.scan(source, text).items():
        # secret 판정은 경로 전체로 한다. 잎만 보면 `password_x[0]` 같은
        # 리스트 원소가 인덱스만 보여 그대로 새어 나간다.
        if any(isinstance(step, str) and is_secret_key(step) for step in path):
            continue
        leaf = path[-1]
        section = ".".join(str(s) for s in path[:-1]) or source
        key_name = str(leaf)
        value = to_json_safe(found.value)
        overridden = is_overridden_elsewhere(section, key_name) or is_overridden_elsewhere(
            str(path[0]) if path else "", key_name
        )
        description = configio.field_description(section, key_name, source)
        choices = enum_choices_for(source, path)
        description = f"{description}{describe_choices(choices)}"
        if overridden:
            description = f"{description} [robots.hcl/jibot 오버레이가 최종값을 결정함 — 여기서 못 고침]"
        multiline = _is_multiline_container(value, getattr(found, "raw", ""))
        if multiline:
            description = f"{description} [여러 줄 블록 — 통째로 쓰면 안쪽 주석이 사라짐. 안쪽 항목을 각각 고침]"
        item: Dict[str, Any] = {
            "key": paramstore.encode_key(source, path),
            "kind": _kind_of_value(value),
            "value": value,
            "present": True,
            "editable": not overridden and not multiline,
            "description": description,
        }
        # 계약의 choices 는 "select 로 그려도 된다"는 신고다. 설명에도 같은 값을 적어 두어
        # select 를 아직 안 그리는 화면에서도 후보를 볼 수 있게 한다.
        if choices:
            item["choices"] = list(choices)
        items.append(item)
    return items


#: config.toml 에 남아 있으면 로더가 부팅을 멈추는 섹션 → extensions.hcl 안의 실제 경로.
#: 2b0ce69 에서 extension 블록으로 옮겨졌고, config/config.py 가 config.toml 에 이
#: 섹션이 있으면 ExtensionsError 로 부팅을 멈춘다(config/extensions.py 의
#: `extension "pio"` 안 advanced 블록 -> [pio_advanced] 승격과 짝이다).
#:
#: dataclass 는 병합이 끝난 뒤 모양이라 섹션명이 그대로 남는다. 되돌리지 않으면
#: 스냅샷이 "쓰면 부팅이 멈추는 자리"를 편집 가능으로 광고한다 — 실제로 실로봇에서
#: 23개 항목이 그렇게 나갔고, 적용하면 로더 재검증에 걸려 되돌려졌다.
MIGRATED_SECTION_PATHS = {
    "pio": ("pio",),
    "pio_advanced": ("pio", "advanced"),
    "air_shower_pio": ("air_shower_pio",),
    "elevator_pio": ("elevator_pio",),
}


def build_default_items(
    raw: Any,
    *,
    section_dataclasses: Optional[Mapping[str, Any]] = None,
    present_keys: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """파일에 없지만 dataclass 기본값으로 동작 중인 항목을 만든다.

    이 항목이 있어야 "기본값으로 되돌리기"(unset)와 "기본값을 파일에 고정"(create)이
    의미를 가진다.

    :param raw: configio.load_raw 결과(섹션 존재 확인용)
    :param section_dataclasses: 섹션명 → dataclass 매핑
    :param present_keys: 이미 파일에 있는 항목 key 집합
    :returns: present=False 항목 목록
    """
    items: List[Dict[str, Any]] = []
    seen = set(present_keys or ())
    for section, cls in (section_dataclasses or {}).items():
        if not isinstance(raw, dict) or section not in raw:
            continue
        for key, default in _dataclass_defaults({section: cls}, section).items():
            if is_secret_key(key):
                continue
            prefix = MIGRATED_SECTION_PATHS.get(section)
            if prefix is None:
                item_source, path = "config.toml", (section, key)
                description = configio.field_description(section, key)
            else:
                item_source, path = "extensions.hcl", prefix + (key,)
                description = (
                    f"extensions.hcl 의 {'.'.join(path)} 설정입니다"
                    f"(config.toml 의 [{section}] 에서 옮겨진 항목). "
                    "여기서 고친 뒤 어댑터 서비스를 재시작해야 반영됩니다."
                )
            item_key = paramstore.encode_key(item_source, path)
            if item_key in seen:
                continue
            safe = to_json_safe(default)
            overridden = is_overridden_elsewhere(section, key)
            items.append({
                "key": item_key,
                "kind": _kind_of_value(safe),
                "value": None,
                "present": False,
                "editable": not overridden,
                "defaultValue": safe,
                "description": description,
            })
    return items


def build_all_items(paths: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """모든 source 를 합쳐 스냅샷 항목을 만든다.

    :param paths: source → 파일 경로
    :returns: EquipmentParameterItem 목록
    :raises ValueError: 항목 수가 MAX_ITEMS 를 초과(자르지 않고 실패시킨다)
    """
    items: List[Dict[str, Any]] = []
    for source, text in _source_files(paths):
        items.extend(build_file_items(source, text))

    config_path = paths.get("config.toml")
    if config_path is not None:
        try:
            raw = configio.load_raw(config_path, extensions_path=paths.get("extensions.hcl"))
        except Exception:  # noqa: BLE001 - 기본값 노출은 부가 기능이라 실패해도 스냅샷은 나간다
            raw = {}
        items.extend(build_default_items(
            raw,
            section_dataclasses=PARAMETER_SECTION_DATACLASSES,
            present_keys={i["key"] for i in items},
        ))

    if len(items) > MAX_ITEMS:
        raise ValueError(
            f"설정 항목이 {len(items)}개로 상한 {MAX_ITEMS}개를 넘음 — source 를 나누어 조회해야 함"
        )
    return items


def group_changes_by_source(changes: Any) -> Dict[str, List[Dict[str, Any]]]:
    """변경분을 key 의 source 별로 나눈다.

    source 마다 파일이 다르고 편집기도 다르므로 한 덩어리로 처리할 수 없다.

    :param changes: ``[{key, op, value?}]``
    :returns: source → 변경 목록. key 형식이 깨진 항목은 빈 문자열 source 로 모음
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for change in changes or []:
        key = str((change or {}).get("key", ""))
        if (change or {}).get("op") == "replaceText":
            # 파일 전체가 대상이라 dotted-path 가 없다. key 가 곧 source 다
            grouped.setdefault(key, []).append(change)
            continue
        try:
            source, _path = paramstore.decode_key(key)
        except ValueError:
            source = ""
        grouped.setdefault(source, []).append(change)
    return grouped


def _apply_text_replacement(
    text: str,
    changes: List[Dict[str, Any]],
    *,
    source: str,
) -> Tuple[str, List[str], List[Dict[str, str]]]:
    """`replaceText` 변경을 처리한다(순수 함수. 파일은 안 건드린다).

    값 단위 편집과 달리 파싱하지 않고 문자열을 그대로 새 파일 내용으로 삼는다. 이 텍스트가
    실제로 로더를 통과하는지는 저장 후 재검증(write_config 의 validate)이 판정하고,
    실패하면 원문과 mtime 까지 되돌아간다. 그래서 여기서 미리 파싱하지 않는다 —
    같은 검사를 두 번 하게 되고, 둘의 판정이 어긋나면 어느 쪽이 진실인지 알 수 없다.

    :param text: 원본 파일 텍스트
    :param changes: 이 source 의 변경 목록. replaceText 를 최소 1건 포함함
    :param source: 대상 source
    :returns: (새 텍스트, 적용된 key 목록, 실패 목록)
    """
    if len(changes) > 1:
        # 원문 교체가 값 편집을 통째로 덮어써서 무엇이 남는지 예측할 수 없다.
        # 순서에 따라 조용히 사라지느니 요청 전체를 거부한다
        return text, [], [
            {
                "key": str((c or {}).get("key", "")),
                "reason": f"원문 교체와 값 편집을 같은 요청에 섞을 수 없음: {source} (원문만 따로 적용 필요)",
            }
            for c in changes
        ]

    change = changes[0] or {}
    key = str(change.get("key", ""))
    blocked = text_edit_block_reason(source, text)
    if blocked is not None:
        return text, [], [{"key": key, "reason": blocked}]
    if key != source:
        return text, [], [
            {"key": key, "reason": f"replaceText 의 key 는 source 이름이어야 함: {source}"}
        ]

    value = change.get("value")
    if not isinstance(value, str) or not value.strip():
        return text, [], [
            {"key": key, "reason": "replaceText 의 value 가 비어 있거나 문자열이 아님"}
        ]
    if value == text:
        # 파일을 건드리면 mtime 이 바뀌어 리비전만 헛돈다. 목표 상태에는 이미 도달해 있다
        return text, [], []

    # 들어온 원문도 검사한다. 쓴 뒤에 막으면 이미 파일과 리비전 이력에 secret 이 박힌 뒤다
    incoming_blocked = text_edit_block_reason(source, value)
    if incoming_blocked is not None:
        return text, [], [{"key": key, "reason": f"적용하려는 원문이 거부됨 — {incoming_blocked}"}]
    return value, [key], []


def apply_parameter_changes(
    text: str,
    changes: Any,
    *,
    editable_keys: Any,
    source: str = "config.toml",
    creatable_keys: Any = (),
) -> Tuple[str, List[str], List[Dict[str, str]]]:
    """한 source 의 변경분을 그 파일 텍스트에 적용한다(순수 함수. 파일은 안 건드린다).

    한 건이 실패해도 나머지는 적용한다. 호출자는 applied/failures 로 APPLIED·PARTIAL·
    REJECTED 를 가른다. 실제 저장은 configio.write_config 가 락 안에서 수행한다.

    ``replaceText`` 가 섞여 있으면 값 단위 경로를 타지 않고 :func:`_apply_text_replacement`
    로 넘긴다. 구조 변경(블록 추가·삭제)은 값 단위 op 로 표현할 수 없어 원문 교체가 유일한
    경로이며, 두 방식을 한 요청에 섞는 것은 거기서 거부한다.

    editable 이 아닌 key 를 거르는 게 핵심이다. robots.hcl / jibot 오버레이가 최종값을
    결정하는 필드는 config.toml 을 고쳐도 기동 시 덮어써져서, 그냥 쓰면 "APPLIED 라고
    했는데 재시작하면 원래 값" 이 된다.

    :param text: 대상 파일의 원본 텍스트
    :param changes: 이 source 에 속한 ``[{key, op, value?}]``
    :param editable_keys: 편집 허용 key 집합(스냅샷의 editable=True 항목)
    :param source: 대상 source. 편집기 선택과 key 대조에 쓴다
    :param creatable_keys: 파일에 줄이 없어 새로 적어 넣어도 되는 key 집합
        (스냅샷의 present=False 항목). 이 목록 밖의 없는 키는 오타로 보고 거부한다 —
        그냥 만들면 로더가 모르는 설정이 파일에 조용히 쌓인다
    :returns: (새 텍스트, 적용된 key 목록, 실패 목록)
    """
    editable = set(editable_keys)
    creatable = set(creatable_keys)
    applied: List[str] = []
    failures: List[Dict[str, str]] = []
    current = text

    changes = list(changes or [])
    if any((c or {}).get("op") == "replaceText" for c in changes):
        return _apply_text_replacement(current, changes, source=source)

    for change in changes:
        key = str((change or {}).get("key", ""))
        op = (change or {}).get("op")
        try:
            key_source, path = paramstore.decode_key(key)
        except ValueError as exc:
            failures.append({"key": key, "reason": str(exc)})
            continue

        if key not in editable:
            failures.append({"key": key, "reason": "편집 불가 항목(설비가 editable=false 로 신고함)"})
            continue
        if key_source != source:
            # 다른 파일의 key 를 이 텍스트에 쓰면 파일이 깨진다
            failures.append({"key": key, "reason": f"source 불일치: 이 파일은 {source} 임"})
            continue

        try:
            if op == "set":
                current = paramstore.set_value(
                    source, current, path, (change or {}).get("value"),
                    create=key in creatable,
                )
            elif op == "unset":
                try:
                    current = paramstore.remove_value(source, current, path)
                except KeyError:
                    # 이미 없으면 목표 상태에 도달해 있다. 재시도가 실패로 뒤집히지 않게 성공 처리
                    pass
            else:
                raise ValueError(f"알 수 없는 op: {op!r}")
        except (KeyError, ValueError, TypeError) as exc:
            failures.append({"key": key, "reason": str(exc)})
            continue
        applied.append(key)

    return current, applied, failures


def build_apply_result(
    *,
    request_id: str,
    equipment_id: str,
    source: str,
    revision: int,
    applied: List[str],
    failures: List[Dict[str, str]],
    generated_at: str,
) -> Dict[str, Any]:
    """WCS 계약의 EquipmentParameterApplyResult envelope 을 만든다.

    :param request_id: WCS 가 발급한 상관 id
    :param equipment_id: 설비 id
    :param source: 대상 묶음
    :param revision: 적용 후 리비전. 거부됐으면 baseRevision 과 같음
    :param applied: 실제로 적용된 key 목록
    :param failures: 실패 목록
    :param generated_at: 설비 기준 처리 시각(ISO)
    :returns: 결과 envelope
    """
    if not failures:
        status = "APPLIED"
    elif applied:
        status = "PARTIAL"
    else:
        status = "REJECTED"
    return {
        "requestId": request_id,
        "equipmentId": equipment_id,
        "source": source,
        "status": status,
        "revision": revision,
        "appliedKeys": list(applied),
        "failures": [dict(f) for f in failures],
        "generatedAt": generated_at,
    }


def build_parameter_snapshot(
    *,
    equipment_id: str,
    source: str,
    request_id: str,
    generated_at: str,
    revision: int,
    items: List[Dict[str, Any]],
    text: Optional[str] = None,
    text_block_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """WCS 계약의 EquipmentParameterSnapshot envelope 을 만든다.

    Args:
        equipment_id: WCS 설비 식별자(= vehicle serial number).
        source: 요청된 설정 묶음 단위. 빈 문자열이면 전체를 담는다.
        request_id: 요청 correlation id(echo).
        generated_at: 생성 시각(ISO8601 UTC, ...Z).
        revision: 설정 파일 기준 리비전. 낙관적 동시성 판정에 쓴다.
        items: build_parameter_items 결과.
        text: 이 source 파일의 원문 전체. 넣으면 WCS 가 replaceText 로 구조 변경을
            할 수 있다는 신고가 된다. 원문 편집을 허용하지 않는 source 는 None 이다.
        text_block_reason: 원문을 안 실은 사유. text 가 None 일 때만 의미가 있다.
            WCS 화면이 "구조 변경 불가" 대신 조치 가능한 문장을 보여 주는 근거다.

    Returns: EquipmentParameterSnapshot dict.
    """
    if source:
        items = [i for i in items if str(i["key"]).startswith(f"{source}:")]
    snapshot = {
        "requestId": request_id,
        "equipmentId": equipment_id,
        "source": source,
        "revision": revision,
        "generatedAt": generated_at,
        "items": items,
    }
    if text is not None:
        snapshot["text"] = text
    elif text_block_reason:
        snapshot["textBlockReason"] = text_block_reason
    return snapshot


def read_source_text_with_reason(
    paths: Mapping[str, Any], source: str
) -> Tuple[Optional[str], Optional[str]]:
    """원문과, 못 실을 때의 사유를 함께 돌려준다.

    사유를 버리지 않는 이유: text 를 빼기만 하면 WCS 화면에는 "구조 변경 불가" 넉 자만
    남는다. 운영자는 미지원 source 라서 막힌 건지, 자격증명이 들어 있어서 막힌 건지,
    파일을 못 읽는 건지 구분할 수 없고 조치도 못 한다. 사유는 스냅샷에 실어 보낸다.

    :param paths: source → 파일 경로
    :param source: 대상 source. 빈 문자열(전체 조회)이면 대상 파일이 하나로 정해지지 않음
    :returns: (파일 텍스트, 사유). 열리면 (텍스트, None), 막히면 (None, 사유)
    """
    if not source or source not in TEXT_EDITABLE_SOURCES:
        # 텍스트 없이도 판정되는 사유다. 빈 문자열을 넘겨 목록 검사만 태운다
        return None, text_edit_block_reason(source, "")
    path = paths.get(source)
    if path is None:
        return None, f"설정 파일 경로가 없음: {source} — adaptor 에 이 source 가 주입되지 않음"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"설정 파일을 읽을 수 없음: {source} ({exc})"
    # 스냅샷은 secret 항목을 아예 빼고 보낸다(원칙 3). 원문에 그 값이 들어 있으면
    # 항목으로는 가려진 값이 통째로 새어 나가므로 원문 자체를 안 싣는다
    blocked = text_edit_block_reason(source, text)
    if blocked is not None:
        return None, blocked
    return text, None


def read_source_text(paths: Mapping[str, Any], source: str) -> Optional[str]:
    """원문 편집이 허용된 source 의 파일 텍스트를 읽는다.

    허용 목록 밖이거나, secret 이 들어 있거나, 파일을 못 읽으면 None 이다. None 이면 스냅샷에
    text 가 실리지 않고, WCS 는 그 source 를 원문 편집 불가로 본다.

    :param paths: source → 파일 경로
    :param source: 대상 source. 빈 문자열(전체 조회)이면 대상 파일이 하나로 정해지지 않음
    :returns: 파일 텍스트 또는 None
    """
    return read_source_text_with_reason(paths, source)[0]


def publish_parameters(mqtt: Any, snapshot: Dict[str, Any]) -> bool:
    """설정 스냅샷을 config 토픽에 publish 한다.

    Args:
        mqtt: MQTTClient 인스턴스(토픽 prefix 를 스스로 붙인다).
        snapshot: build_parameter_snapshot 결과.

    Returns: 발행 성공 여부. 실패는 예외를 삼키고 False 로 알린다(map 과 동일 정책).
    """
    try:
        mqtt.publish(PARAMETERS_TOPIC, snapshot, qos=1, retain=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[getParameters] publish failed for request {snapshot.get('requestId')}: {exc}")
        return False
    return True


def publish_apply_result(mqtt: Any, result: Dict[str, Any]) -> bool:
    """적용 결과를 parameters-result 토픽에 publish 한다.

    :param mqtt: MQTTClient 인스턴스(토픽 prefix 를 스스로 붙인다)
    :param result: build_apply_result 결과
    :returns: 발행 성공 여부. 실패는 예외를 삼키고 False 로 알린다(스냅샷과 동일 정책)
    """
    try:
        mqtt.publish(PARAMETERS_RESULT_TOPIC, result, qos=1, retain=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[setParameters] publish failed for request {result.get('requestId')}: {exc}")
        return False
    return True
