# Extension / Recipe 개념 재정립과 HCL 설정 전환

작성일: 2026-07-27
상태: 설계 승인 대기 (브레인스토밍 산출물)
범위: 1개 리포 — `unified-amr-adaptor`(어댑터)

## 1. 배경 / 동기

현재 미커밋 상태로 `extension_groups`라는 개념이 들어와 있다. 설정에 정의한
extension 액션들을 순서대로 실행하고 그 묶음 자체를 하나의 VDA5050 액션 타입으로
노출하는 기능이다(`adaptor/extensions/groups/__init__.py`).

두 가지 문제가 있다.

**첫째, 이름이 개념을 담지 못한다.** `group`은 분류·집합의 뜻이 강해서 "정해진
순서로 실행되고, 중간에 실패하면 멈추며, 마무리 처리가 필요한 하나의 동작"을
가리키기에 부족하다.

**둘째, 같은 목적의 메커니즘이 이미 셋으로 갈라져 있다.**

| 메커니즘 | 위치 | 상태 |
|---|---|---|
| `pioScenario` | `extensions/pio/__init__.py:190` | 구현됨. PIO 한정. ACS가 시나리오를 액션 파라미터로 매번 전송 |
| `ASWorkflow` / `EVWorkflow` | `utils/airshower.py`(599줄), `utils/elevator.py`(668줄) | **구현 완료, 호출처 0곳.** 사용법이 docstring 주석으로만 존재 |
| `extension_groups` | `extensions/groups/` | 미커밋 신규 |

`ASWorkflow`/`EVWorkflow`는 페어링 핸드셰이크, solid-on 판정(핀이 깜빡이지 않고 N초
유지되는지), 상태별 분기, 에러 분류를 담은 1,200줄이 넘는 도메인 로직인데 아무도
부르지 않는다. 이대로 두면 현장 요구가 올 때마다 세 메커니즘 중 아무거나 골라
늘어난다.

**셋째, recipe로 엮을 수 있는 액션의 범위가 반쪽이다.** `adapter_jibot.py:4225`의
instant action 분기를 보면 어댑터 내장 액션이 먼저 처리되고 `_action_registry.dispatch`는
마지막 fallback이다. 즉 `core/registry.py:191`의 `_JIBOT_INSTANT_ACTIONS` 22종
(`manualMove`, `jibotMotionRule`, `switchMap`, `gotoNearestNode` …)은 ActionRegistry에
등록되지 않고, `ActionRegistry.execute()`는 `self._specs`만 조회하므로 **recipe의
step으로 주행 액션을 부를 수 없다.** 에어샤워는 IO만으로 끝나지만 엘리베이터는
`호출 → 문 열림 대기 → 탑승 주행 → 층 선택 → 하차 주행`이라 주행이 절차 한가운데
들어간다.

**넷째, 설정 파일이 461줄 한 덩어리다.** `config/config.toml`에 MQTT·차량·주문 같은
어댑터 코어 설정과 `[pio]`, `[air_shower_pio]`, `[elevator_pio]` 같은 extension 설정이
섞여 있다.

## 2. 목표 / 비목표

**목표**

- `extension` / `recipe` / `step` / `cleanup`으로 용어를 확정하고 코드·설정·문서에서
  `group`·`sequence`를 제거한다.
- 설정을 역할별 파일로 분리하고 `robots` + `extensions` + `recipes`를 HCL로 전환한다.
- recipe에 설비 제어에 필요한 실행 의미(항상 실행되는 cleanup, step 단위 timeout,
  명시적 retry)를 넣는다.
- 내장 액션의 실행 로직을 **공통 async primitive**로 분리하고, instant / order / recipe
  세 경로가 각자의 wrapper로 그것을 부르게 한다. 여기에 **취소 계약**을 붙여 timeout이
  대기 중단이 아니라 실제 실행 중단이 되게 한다.
- `ASWorkflow`/`EVWorkflow`를 extension으로 승격해 VDA5050 액션으로 노출하고,
  세 갈래로 갈라진 설비 제어 경로를 하나로 정리한다.

**비목표 (이번에 안 함)**

- recipe 간 중첩, 병렬 실행, 조건 분기.
- `ActionResult`에 데이터 필드를 추가해 recipe 레벨 `until` 조건을 만드는 것.
  현재 `ActionResult`는 `status` + `description` 문자열뿐이라 extension이 값을
  반환하지 못한다. 조건 대기는 대기형 extension(`pioScenario`, 승격된 상태 머신)이
  자기 안에서 처리한다.
- 내장 액션 22종 전부를 registry로 이주하는 것. recipe에서 필요한 것만 선별한다
  (전면 이주는 `2026-06-29-adaptor-action-plugin-registry-design.md`가 이미 점진
  이주로 못박았다).
- `config.toml`을 HCL로 바꾸는 것. 어댑터 코어 설정은 TOML로 남는다.
- WebUI에서 HCL 파일을 편집하는 기능. 읽기 전용이다.

## 3. 개념 모델

| 계층 | 의미 | 예 |
|---|---|---|
| **extension** | 단위 기능. 그 자체로 VDA5050 액션 타입 | `pioInit`, `pioWriteOut`, `ezioReadIn`, `clamp`, `airShowerEnter` |
| **recipe** | 하나의 업무 목적을 가진 실행 단위. 그 자체로 VDA5050 액션 타입 | `elevatorTrip` |
| **step** | recipe 안의 순서 있는 한 단계 | `step "pioWriteOut" { ... }` |
| **cleanup** | 성공·실패·타임아웃과 무관하게 항상 실행되는 마무리 단계 | `cleanup "pioDisconnect" {}` |

이름 변경:

| 현재 | 변경 후 |
|---|---|
| `ExtensionGroupConfig` | `RecipeConfig` |
| `ExtensionSequenceStep` | `RecipeStep` |
| `extensions/groups/` | `extensions/recipes/` |
| `Config.extension_groups` | `Config.recipes` |
| `[[extension_groups]]` (TOML) | `recipe "이름" { }` (HCL) |
| `sequence = [...]` | `step "이름" { }` 블록 |

## 4. 설정 파일 구조

```
config/config.toml      어댑터 코어 (TOML 유지)
config/robots.hcl       로봇 인벤토리 (robots.toml 대체)
config/extensions.hcl   extension 설정 + 액션 등록
config/recipes.hcl      recipe 라이브러리
```

**무엇이 어디로 가는가**

| 섹션 | 이동처 | 근거 |
|---|---|---|
| `[pio]`, `[pio_advanced]` | extensions.hcl | PIO extension 전용 |
| `[air_shower_pio]`, `[elevator_pio]` | extensions.hcl | 승격될 설비 extension 전용 |
| `[ezi]` (주소 제외) | extensions.hcl | 트레이 핀·클램프 위치·모터 속도는 extension 튜닝값 |
| `[[action_modules]]`, `[[actions]]` | extensions.hcl | extension 모듈·커스텀 액션 등록 |
| `[[extension_groups]]` | recipes.hcl | recipe로 개명 후 이동 |
| `[mqtt_broker]` `[adapter]` `[vehicle]` `[settings]` `[jibot_status]` `[charge]` `[dock]` `[sound_settings]` `[video]` `[web_ui]` `[bms_ros]` `[jibot_client]` `[factsheet]` `[hexplorer]` | config.toml 잔류 | 어댑터 코어 |
| `[charge_circuit]` | config.toml 잔류 | extension 모듈이 아니다. `main.py:774`에서 어댑터 충전 경로에 직접 주입된다 |
| `[internal_actions]` | config.toml 잔류 | 도킹 상태용 합성 액션 id/type. FMS 계약이며 "변경 금지" 경고가 붙어 있다 |

`ezi_io` / `ezi_motor` 주소는 `[ezi]` 섹션에 리터럴로 존재하지 않고 `EziConfig`의 빈
기본값을 robots 파일의 로봇별 override가 채운다(`config/config.py:26,30`,
`config/fleet.py:34`). 따라서 주소는 `robots.hcl`에 남고, 나머지 EZI 튜닝값만
extensions.hcl로 간다.

**`elevator_motion_rules`** — `[elevator_pio]` 안에 있는 노드 전이 표
(`{ from = "1_05", to = "2_01", mode = "enter", floor_pin = 0, ... }`)는 extension 설정이
아니라 현장 맵 데이터에 가깝다. 이번에는 `extension "elevator"` 블록 안에 그대로
옮기되, 맵 데이터로 분리하는 것은 향후 과제로 남긴다.

### 4.1 robots.hcl

```hcl
robot "HN-SH6-TR-001" {
  vehicle_ip   = "10.0.0.11"
  vehicle_port = 7273
  ezi_io       = "10.8.8.87"
  ezi_motor    = "10.8.8.2"
  mqtt_host    = "192.168.3.108"
  simulator    = false
  extra_args   = []

  extensions = "config/extensions.hcl"   # 생략 시 기본 경로
  recipes    = "config/recipes-sh6.hcl"  # 생략 시 기본 경로
}
```

`robot "ID"`의 라벨은 로더가 `id` 키로 정규화해 넣으므로 `fleet.py` 바깥 호출부는
그대로다. `extensions` / `recipes` 키가 로봇별로 파일을 고른다. 로봇마다 다른 설비
절차는 **파일을 통째로 바꿔** 표현한다.

### 4.2 extensions.hcl

```hcl
extension "pio" {
  enabled      = true
  pio_port     = "/dev/ttyUSB0"
  pio_baudrate = 38400
  media        = 2
  station_id   = "123456"
  channel      = 250
  port         = 0                 # master-client 간 port (pio_port와 다른 값이다)

  advanced {
    init_default_timeout_sec = 2.0
    call_poll_interval_sec   = 0.05
  }
}

extension "airshower" {
  failure                 = 7
  occupied                = 2
  fun_working             = 3
  door_pin                = [0, 1]
  timeout_open_requesting = 3      # minutes
  poll_interval_sec       = 0.2
}

module "custom_actions.air_shower" { enabled = true }
module "extensions.pio"            { enabled = false }

action "customDoorOpen" {
  runner      = "subprocess"
  command     = ["python", "custom_actions/door_open.py"]
  timeout_sec = 10
}

action "pioReadIn" { enabled = false }   # 이 로봇에서 끄기
```

### 4.3 recipes.hcl

```hcl
recipe "elevatorTrip" {
  label       = "Elevator — 층간 이동"
  timeout_sec = 300
  motion      = true

  step "elevatorEnter" {
    parameters = { floor_pin = var.floorPin, station_id = var.stationId }
  }
  step "manualMove" {
    parameters  = { distance = 1.2 }
    timeout_sec = 60
  }
  step "elevatorInside" {
    parameters      = { floor_pin = var.targetPin }
    retry           = 1
    retry_delay_sec = 0.5
  }

  cleanup "pioDisconnect" {}
}
```

## 5. HCL 형식과 파서 계약

`python-hcl2` 8.1.2를 쓴다. 의존성은 `lark`(순수 Python)와 `regex`(C 확장)가 딸려 온다.
현재 런타임 의존성이 `paho-mqtt` 하나뿐이므로 실질적인 증가다. 온보드 PC는
`scripts/update-jibot-adapter-over-ssh.sh --copy-venv` 오프라인 경로가 있어 설치는
가능하지만, `regex`가 C 확장이라 **로봇과 빌드 호스트의 CPU 아키텍처가 같아야 한다**
(스크립트가 이미 같은 제약을 경고한다).

**직렬화 옵션 고정**

```python
SerializationOptions(
    with_comments=False,      # __comments__ 키 제거
    explicit_blocks=True,     # __is_block__ 마커 유지 (아래 근거)
    strip_string_quotes=True, # "\"값\"" -> "값"
)
```

`with_comments`와 `strip_string_quotes`가 없으면 라벨 키가 `"\"airShowerEnter\""`
형태로 따옴표가 남고 주석 키가 섞인다.

**`explicit_blocks`는 True여야 한다.** 처음엔 마커가 지저분해 False로 두려 했으나,
그러면 로더가 **블록과 일반 객체 값을 구분할 수 없다.** 라벨을 빠뜨린
`robot { vehicle_ip = "10.0.0.1" }`은 유효한 HCL이고, 마커 없이는 이게
`{"robot": [{"vehicle_ip": "10.0.0.1"}]}`로 들어와 `vehicle_ip`가 **로봇 id로 둔갑한다**.
실제로 그렇게 동작했고(`load_fleet` -> `[{'id': 'vehicle_ip'}]`), WebUI의 자유 텍스트
편집기가 따옴표 하나 누락만으로 이 경로를 현장에 노출했다. 마커를 켜면 로더가
블록임을 알고 라벨 개수를 검증해 `HclError`로 거절할 수 있다.

`__is_block__`은 `blocks()`가 정규화 단계에서 벗겨내므로 소비자에게는 보이지 않는다.
중첩된 **속성 값** dict(`parameters = { index = var.doorPin }`)에는 애초에 마커가
붙지 않으므로 recipe의 파라미터도 영향을 받지 않는다. 이 동작은 실제 파싱으로
검증했다.

**블록 → dict 변환 규칙.** `recipe "A" { step "x" {} step "y" {} }`는
`{"recipe": [{"A": {"step": [{"x": {}}, {"y": {}}]}}]}`로 들어오고 **선언 순서가
보존된다.** 공통 로더가 이 형태를 `(라벨, 본문)` 목록으로 정규화한다.

**파라미터 치환.** `index = var.doorPin`은 파서가 평가하지 않고 `"${var.doorPin}"`
문자열로 남긴다. 이 미평가 문자열을 부모 액션 파라미터의 자리표시자로 쓴다.

- 문자열 전체가 `${var.X}`이면 타입을 보존해 치환한다 (`3` → 정수 3).
- 문자열 중간에 섞이면 문자열로 치환한다 (`"dock-${var.n}"` → `"dock-2"`).
- 해당 파라미터가 없으면 그 step에서 FAILED, 메시지에 이름을 담는다.
- 기존 `"$doorPin"` 관례는 폐기한다. 자리표시자 문법은 하나만 둔다.

**구조 직렬화 쓰기는 하지 않는다.** `python-hcl2`는 `strip_string_quotes`와 라운드트립
쓰기를 동시에 지원하지 않으므로, 파싱한 dict를 다시 HCL로 써 내려가는 경로는 만들지
않는다.

다만 WebUI는 fleet 파일을 **원문 텍스트로 저장한다**. `/robots` 화면이 파일 전체를
textarea로 보여주고, 저장 시 `write_text(text)`로 그대로 덮어쓴 뒤 `load_fleet` +
`get_config`로 검증하고 실패하면 원본을 복구한다(`web/server.py:973-998`). 이건 구조
직렬화가 아니라 운영자가 친 텍스트를 그대로 쓰는 것이므로 HCL로 바꿔도 그대로
동작한다. 검증 단계가 `FleetError`뿐 아니라 **HCL 문법 오류도 잡아 원본을 되돌린다**는
점이 오히려 이득이다. `core/configio.py`의 스칼라 편집 경로는 `config.toml` 전용이며
HCL 파일에는 적용하지 않는다.

**실패 정책.**

| 상황 | 동작 |
|---|---|
| `robots.hcl` 없음 / 문법 오류 / `robot` 블록 0개 / id 중복 | `FleetError`로 부팅 실패 (현행 유지) |
| `extensions.hcl` 없음 | **부팅 실패** (아래 근거) |
| 기본 경로의 `recipes.hcl` 없음 | recipe 0개로 계속 (의도된 구성일 수 있다) |
| `robots.hcl`이 가리킨 `recipes` 경로가 없음 | 부팅 실패 — 파일을 지정해 놓고 없는 것은 오타나 배포 누락이다 |
| `extensions.hcl` / `recipes.hcl` 문법 오류 | 부팅 실패 |
| recipe 이름 중복 | 부팅 실패 |
| recipe 이름이 기존 액션 타입과 충돌 | **부팅 실패** |
| recipe step이 없는/비활성 extension 또는 recipe 자신·다른 recipe를 참조 | **부팅 실패** |
| 활성 module이 요구하는 설정 키가 없음 | 그 module만 등록 거부 + 로그 (`discover_action_modules`의 skip-and-log 관례) |

**`extensions.hcl`을 필수로 두는 근거.** `[pio]`·`[ezi]`를 `config.toml`에서 빼면
파일이 없어도 Config 생성은 성공한다 — `PioConfig`/`EziConfig`의 모든 필드에 기본값이
있기 때문이다(`config/config.py:26`). 즉 크래시가 아니라 **PIO가 `pio_port = "COM6"`
같은 기본값으로 조용히 뜨는** 상황이 된다. 설비 제어에서 이런 무증상 오설정이 문법
오류보다 위험하므로, 분리 이후 `extensions.hcl`은 `robots.hcl`과 같은 등급의 필수
파일로 둔다. 없으면 `config/extensions.hcl.example`을 가리키는 에러로 부팅을 멈춘다.

여기에 더해 각 first-party module은 spec을 만들 때 자기가 요구하는 설정 키가 채워졌는지
검증하고, 아니면 등록을 거부한다. 이 층은 `discover_action_modules`가 이미 쓰는
skip-and-log를 따른다 — 한 module의 설정 누락이 어댑터 전체를 죽이지는 않게.

**충돌·오타를 부팅 실패로 올리는 근거.** `build_registry_from_config`는 설정
`[[actions]]`가 내장 액션을 가릴 때 skip-and-log로 넘어간다. 그건 외부/현장이 떨군
플러그인이 메인 어댑터를 죽이지 못하게 하려는 의도적 선택이다
(`2026-06-29-adaptor-action-plugin-registry-design.md`). recipe는 성격이 다르다.
운영자가 직접 쓴 첫 파티 설정이고, 조용히 건너뛰면 ACS가 부르는 액션 타입이 **존재하지
않는 채로** 운영에 들어간다. 그래서 recipe 관련 구성 오류는 부팅 실패로 올린다.

파일이 없는 것은 의도일 수 있지만 깨진 문법과 존재하지 않는 참조는 명백한 실수다.

**경로 해석.** `robot` 블록의 `extensions` / `recipes` 상대 경로는 **`robots.hcl`
파일의 부모 디렉터리**를 기준으로 푼다. 배포 위치가 바뀌어도 상대 경로가 안정적이고,
프로세스 CWD(서비스와 수동 실행이 다르다)에 의존하지 않는다. 절대 경로도 허용하고,
symlink는 따르며, `..`도 막지 않는다 — 온보드 설정은 운영자 소유라 샌드박스가 목적이
아니다. 대신 로더는 **해석된 절대 경로를 부팅 로그에 찍어** 어느 파일이 실제로 읽혔는지
남긴다.

파싱 결과 캐시는 두지 않는다. `run_multi.py`가 `subprocess.Popen`으로 로봇마다 별도
프로세스를 띄우므로(`run_multi.py:106`) 여러 로봇이 같은 파일을 가리켜도 프로세스가
분리돼 있고, 한 프로세스 안에서는 부팅 시 한 번만 읽는다.

## 6. recipe 실행 의미

- **직렬 실행, fail-fast.** 첫 실패에서 중단한다. 병렬·분기는 없다.
- **cleanup은 항상 실행한다.** 성공·실패·타임아웃 모두. 선언 순서대로 돈다.
- **중첩 금지.** recipe는 다른 recipe를 step으로 부를 수 없다 (현행 유지).
- **retry.** 명시할 때만 동작한다. `retry = 2`는 최초 1회 + 재시도 2회(최대 3회),
  `retry_delay_sec` 기본 0. 쓰기 계열 재시도는 위험하므로 기본은 재시도 없음이다.
  **재시도 전에 직전 attempt가 실제로 종료됐음을 확인한다** (7.3절의 취소 계약).
- **delay_sec.** step이 **성공한** 뒤 다음 step 전까지 쉰다. 기본 0. momentary
  출력의 pulse 폭처럼 "쓰고 나서 유지"가 필요한 곳에 쓴다. 남은 본문 예산
  (cleanup이면 cleanup 예산) 안에서만 쉬므로 delay가 예산을 늘리지 못한다.
  step이 실패하면 쉬지 않고 곧바로 cleanup으로 간다.
- **조건 대기는 step으로 표현한다.** recipe 문법에는 조건·분기가 없다. "입력이
  기대 상태가 될 때까지 기다리고 아니면 실패"는 그 판정을 가진 액션
  (`ezioWaitIn`, `pioScenario`의 `in` step, 설비 상태 머신)을 step으로 부른다.

### 6.1 cleanup 실패의 결과 반영

| 본문 | cleanup | 최종 결과 |
|---|---|---|
| 성공 | 성공 | FINISHED |
| 성공 | 실패 | **FAILED** |
| 실패 | 성공 | FAILED — 본문 오류 유지 |
| 실패 | 실패 | FAILED — 본문 오류 + cleanup 오류 병기 |

원칙은 "**이미 실패한 원인을 cleanup 실패로 덮어쓰지 않는다**"이다. 본문이 성공했는데
cleanup이 실패하면 FAILED로 내린다 — 릴레이가 켜진 채, 포트가 열린 채, 설비 자원을
쥔 채 성공을 보고하는 것이 설비 제어에서 가장 위험한 거짓말이기 때문이다.

**결과 형식.** `elevatorTrip failed at step 2 (manualMove): <detail>`, cleanup이
실패했으면 `; cleanup pioDisconnect failed: <detail>`을 덧붙인다. 본문 성공 +
cleanup 실패는 `elevatorTrip body ok but cleanup pioDisconnect failed: <detail>`.

### 6.2 시간 예산

recipe spec은 `ActionSpec.timeout_sec = 0`으로 등록하고 핸들러가 데드라인을 직접
관리한다. 근거는 "취소되면 cleanup이 안 돈다"가 **아니다** — `asyncio.wait_for`가
취소해도 `try/finally`의 `finally`는 진입하고 그 안의 `await`도 완주한다(Python
3.12.3에서 실행 확인). 실제 이유는 넷이다.

1. registry는 recipe의 전체 timeout 정책을 알지 못한다.
2. cleanup에 **별도 시간 예산**을 주기 어렵다. `wait_for`는 취소 후 대상이 끝날 때까지
   기다리므로, cleanup이 매달리면 recipe timeout을 넘겨 무한정 붙잡힌다.
3. 외부 취소(주문 취소·종료)와 recipe 자체 timeout을 구분할 수 없다.
4. 자식 작업이 분리된 task라면 recipe 코루틴을 취소해도 멈추지 않는다 (7.3절).

**계산식**

```
effective step timeout
    = min(step.timeout_sec 또는 extension 기본 timeout, recipe 본문 잔여 예산)

cleanup budget
    = 본문 deadline과 무관한 별도 예산
      cleanup.timeout_sec (블록별) / 미지정 시 recipe.cleanup_timeout_sec 기본값
```

cleanup 예산을 본문 deadline 밖에 두는 이유는, recipe가 timeout으로 실패한 바로 그
순간이 cleanup이 가장 필요한 순간인데 본문 예산은 이미 0이기 때문이다.

```hcl
cleanup "pioDisconnect" {
  timeout_sec = 5
}
```

## 7. 내장 액션의 registry 등록

recipe step에서 주행 액션을 부르려면 `ActionRegistry.execute()`가 찾을 수 있어야 한다.

**등록 대상 (선별).** `manualMove`, `manualStop`, `jibotMotionRule`, `switchMap`,
`gotoNearestNode`. 엘리베이터가 층을 넘을 때 맵 전환이 필요하므로 `switchMap`을 포함한다.

### 7.1 instant 핸들러를 재사용하면 안 된다

기존 instant 핸들러를 그대로 호출하는 `ActionSpec`을 등록하면 **order recipe에서 주행이
전부 차단된다.**

`_handle_manual_move_instant_action`은 첫 줄에서 `_manual_blocked_reason()`을 부르고
(`adapter_jibot.py:4317`), 그 함수는 `self.order is not None`이면
`"order in progress; cancel order first"`를 돌려준다(`:4255`). recipe가 node/edge order
action으로 실행될 때는 정의상 order가 있으므로 **항상 실패한다.** 같은 게이트가
`jibotMotionRule`에도 걸려 있고(`:4390`), `gotoNearestNode`는 `_work_in_progress`(`:4535`)와
`order_worker_task`(`:4541`)로 따로 막는다.

여기에 `manualStop`은 별도 문제가 있다. `_handle_manual_stop_instant_action`은
`um_stop()`을 스케줄하기 **전에** FINISHED를 기록한다(`:4457-4467`). 브리지에 그대로
연결하면 "정지 완료"가 아니라 "정지 task 예약 완료"를 뜻하게 되어, recipe가 아직 움직이는
로봇을 세웠다고 믿고 다음 step으로 넘어간다.

그래서 **핸들러 재사용이 아니라 실행 primitive를 분리한다.**

```
공통 async primitive          run_manual_move(adapter, params, *, owner)
  ├─ instant action wrapper   기존 게이트 유지 (owner=None)
  ├─ order action wrapper     order worker가 소유
  └─ recipe extension wrapper owner = 부모 recipe의 order_id
```

primitive는 실제 완료까지 `await`하고 `ActionResult`를 반환한다. `manualStop`의 경우
`await vehicle.um_stop()` **이후에** FINISHED를 돌려주도록 primitive 안에서 순서를
바로잡는다. instant wrapper는 기존 "먼저 FINISHED" 동작을 유지해도 되지만, 정확성을
위해 함께 고치는 편이 낫다.

**소유권 검사.** 무조건적인 `allow_during_order=True`는 다른 order와의 충돌을 숨긴다.
대신 primitive에 소유자를 넘긴다.

```python
def _motion_blocked_reason(self, *, owner_order_id: Optional[str]) -> Optional[str]:
    if not self.config.manual_control.enabled:
        return "manual control disabled"
    if self._work_in_progress is not None:
        return f"busy with {self._work_in_progress} work"
    if self.order is not None and self.order.order_id != owner_order_id:
        return "order in progress; cancel order first"
    return None
```

recipe step은 자기 부모 order의 `order_id`를 넘기므로 **자기 order 안에서는 통과하고
다른 order가 잡고 있으면 여전히 막힌다.** `owner_order_id=None`(instant 경로)은 기존
동작과 동일하다.

**어댑터 사다리는 그대로 둔다.** `adapter_jibot.py:4225`의 if/elif는 instant wrapper를
계속 부르므로 instant action 경로의 동작은 바뀌지 않는다(회귀 0).

### 7.2 완료 브리지

primitive가 `await` 가능해도, 어댑터 루프에 스케줄되는 기존 경로를 감싸는 wrapper는
여전히 완료 신호가 필요하다. 내장 핸들러는 결과를 반환하지 않고
`_update_instant_action_status(action_id, ...)`로 상태를 갱신하는데, 이 메서드는
`self.state.instant_action_states`를 순회해 id로 찾으므로(`adapter_jibot.py:5792`)
recipe step의 합성 id는 매칭되지 않는다. 엔트리를 만들어주면 이번엔 합성 액션이
VDA5050 state로 새어 나간다.

그래서 상태 테이블을 건드리지 않는 완료 대기표를 둔다.

```python
# adapter
self._action_completions: Dict[str, asyncio.Future] = {}
self._synthetic_action_ids: Set[str] = set()   # 은퇴한 합성 id (tombstone)

def _update_instant_action_status(self, action_id, action_status, result_description=None):
    future = self._action_completions.get(action_id)
    if future is not None:
        if self._is_terminal_action_status(action_status):
            self._action_completions.pop(action_id, None)
            self._synthetic_action_ids.add(action_id)
            if not future.done():
                future.set_result(ActionResult(action_status, result_description or ""))
        return   # 합성 액션은 VDA5050 state에 반영하지 않는다
    if action_id in self._synthetic_action_ids:
        print(f"[ACTION BRIDGE LATE] {action_id} -> {action_status}")
        return   # 폐기된 attempt의 늦은 완료
    # ... 기존 로직 그대로 ...
```

**attempt마다 다른 합성 id를 쓴다.** retry가 있으므로 id는 step 단위가 아니라 attempt
단위여야 한다.

```
{parent_action_id}:step{n}:try{k}
```

같은 id를 재사용하면 다음이 성립한다.

1. attempt 1 timeout → future 제거
2. attempt 2가 같은 id로 새 future 등록
3. attempt 1의 늦은 FINISHED가 **attempt 2의 future를 완료**

이건 실제 오완료다. attempt별 고유 id가 이 경로를 닫는다.

**tombstone의 목적은 오완료 방지가 아니라 진단이다.** id가 고유하면 늦은 완료가 다른
future를 건드릴 수 없고, 대기표에 없는 id는 기존 경로로 내려가도 `instant_action_states`
순회에서 매칭 실패로 조용히 반환되므로(`adapter_jibot.py:5792-5800`) state 유출도 없다.
tombstone은 그 "조용한 무시"를 **로그가 남는 폐기**로 바꿔, 타임아웃된 설비 동작이
뒤늦게 끝났다는 사실을 운영자가 볼 수 있게 한다. 무한 증가를 막기 위해 recipe 종료 시
그 recipe가 만든 id를 일괄 제거한다.

### 7.3 취소 계약 — timeout은 대기 중단이 아니라 실행 중단이어야 한다

future를 버리는 것만으로는 아무것도 멈추지 않는다. `_handle_manual_move_instant_action`은
`_run_on_adapter_loop(_run)`로 독립 코루틴을 띄우고(`adapter_jibot.py:4380`) 그 안에서
`move_distance()`가 계속 돈다(`:4358`). 대기만 끊으면 이렇게 된다.

```
manualMove step timeout
  → recipe 실패, cleanup 진입
  → 실제 move_distance는 계속 주행
  → 다음 step 재시도나 다른 액션과 경합
```

그래서 registry에 취소 계약을 추가한다.

```python
@dataclass(frozen=True)
class RunningAction:
    completion: Awaitable[ActionResult]
    cancel: Callable[[], Awaitable[None]]
```

또는 `ActionSpec`에 취소 핸들러를 명시한다.

```python
ActionSpec(
    action_type="manualMove",
    handler=run_manual_move,
    cancel_handler=stop_manual_move,   # task 취소 + um_stop()
    motion=True,
)
```

**계약 내용**

| 항목 | 규정 |
|---|---|
| task 취소 | recipe는 timeout·실패·외부 취소 시 `cancel()`을 반드시 호출한다 |
| motion extension | `cancel()`은 task 취소로 끝나지 않는다. **`await vehicle.um_stop()`까지 보장**해야 한다. task를 죽여도 차량은 이미 받은 이동 명령을 계속 수행하기 때문이다 |
| 비-motion extension | `cancel()` 기본 구현은 task 취소 + 자원 반납(연결 종료 등). 제공하지 않으면 취소 불가로 간주하고, recipe는 그 step의 종료를 **기다린 뒤** 실패 처리한다 |
| retry | 다음 attempt를 시작하기 전에 직전 attempt의 `cancel()` 완료를 `await`한다. 확인 없이 재시도하지 않는다 |
| cleanup | cleanup step도 같은 계약을 따르며, cleanup 예산(6.2)을 넘기면 취소된다 |

취소를 제공하지 않는 extension이 있어도 설계가 성립하도록, "취소 불가 = 종료를 기다린다"를
기본값으로 둔다. 조용히 방치된 채 다음 step으로 넘어가는 경우는 만들지 않는다.

**motion 플래그.** 등록하는 내장 액션은 `motion=True`로 둔다. 주행을 포함하는 recipe도
`motion = true`로 선언해야 WebUI의 확인 키 게이트가 유지된다.

## 8. 상태 머신의 extension 승격

`ASWorkflow`/`EVWorkflow`를 `extensions/facility/`로 올려 VDA5050 액션으로 노출한다.

| 액션 타입 | 대응 |
|---|---|
| `airShowerEnter` / `airShowerInside` / `airShowerPassed` | `ASWorkflow(door_open_pin, action="ENTER"/"INSIDE"/"PASSED")` |
| `elevatorEnter` / `elevatorInside` / `elevatorPassed` | `EVWorkflow(floor_pin, pio_station_id, action=...)` |

`WORKFLOW_SEQUENCE`(`utils/airshower.py:83`)가 이미 ENTER/INSIDE/PASSED 세 갈래를
정의하고 있으므로 액션 타입은 여기에 1:1로 대응시킨다.

**필수 선결 조건 — 클라이언트 공유.** `ASWorkflow.__init__`은 `get_config()`를 직접
부르고(`utils/airshower.py:135`) 자기 `PIOMaster`/`EZIIOClient`를 새로 만든다. 반면
`extensions/pio`는 어댑터가 소유한 `adapter._pio_client`를 쓴다
(`extensions/pio/__init__.py:46`). 그대로 승격하면 **같은 시리얼 포트를 두 번 여는
충돌**이 난다. 승격 시 두 워크플로는 생성자에서 `adapter`를 받아
`get_pio_client(adapter)`를 쓰도록 바꾼다. 설정도 `get_config()` 직접 호출 대신
주입받은 config를 쓴다.

**결과 매핑.** `workflow_sequence()`는 `(result, error_state, error_message)`를
돌려준다. `result`가 True면 FINISHED, 아니면 FAILED로 매핑하고 `error_state.value`와
`error_message`를 `ActionResult.description`에 담는다. 에러 enum이 이미 사람이 읽을
수 있는 문자열이라 그대로 쓸 수 있다.

**print 정리.** 두 파일은 `print()`로 진행 상황을 찍는다. 승격 시 어댑터 로그 관례에
맞추고, 긴 대기 구간에서는 `InlineActionContext.report()`로 RUNNING 설명을 갱신한다.

## 9. pioScenario와의 관계

문서와 주석에 경계를 명시해 세 갈래를 정리한다.

| | 소유자 | 범위 | 용도 |
|---|---|---|---|
| `pioScenario` | ACS (액션 파라미터로 전송) | PIO 한정 | 현장 실험·임시 시퀀스 |
| 설비 extension | 코드 (상태 머신) | 설비 프로토콜 | 페어링·조건 대기 등 분기 있는 절차 |
| recipe | 로봇 설정 (HCL) | extension 조합 | 여러 extension과 주행을 엮는 상위 절차 |

recipe는 조건 대기가 필요하면 대기형 extension을 step으로 부른다. `ActionResult`가
값을 반환하지 못하는 현 구조에서 이것이 유일한 경로다.

## 10. 코드 구조와 로딩 순서

```
config/hcl.py         HCL 로딩·정규화 공통 (라벨 블록 -> (label, body) 목록)
config/fleet.py       robots.hcl 파싱. load_fleet/find_robot/robot_overrides/robot_ids API 유지
config/extensions.py  extensions.hcl 파싱
config/recipes.py     recipes.hcl 파싱 -> List[RecipeConfig]
config/config.py      Config에 recipes / extension 설정 병합
core/motion_primitives.py 주행 primitive (run_manual_move / stop_manual_move 등)
core/action_bridge.py     완료 대기표 + 합성 id 발급 + 취소 계약
extensions/recipes/       recipe 실행기 (extensions/groups 대체)
extensions/facility/      승격된 ASWorkflow / EVWorkflow
```

**로딩 순서**

1. `robots.hcl` 로드 → 대상 로봇 엔트리 선택
2. `config.toml` 로드
3. 로봇 엔트리의 `extensions` 경로(없으면 기본)로 `extensions.hcl` 로드 → config dict에 병합
4. 로봇 override를 병합된 dict에 적용
5. `recipes` 경로로 `recipes.hcl` 로드 → `Config.recipes`
6. `first_party_action_specs(config)`가 extension spec + 내장 브리지 spec + recipe spec을 조립

`ezi_io`처럼 extension 설정을 겨냥하는 로봇 override가 있으므로 **3번이 4번보다 먼저**
와야 한다. `_OVERRIDE_KEYS`의 대상 경로도 이에 맞춰 바꾼다.

`_deep_merge`(`config/config.py:545`)는 dict만 재귀 병합하고 list는 교체한다. 이 규칙은
유지하되, 파일이 나뉘어 각 파일이 자기 영역을 온전히 소유하므로 부분 override 문제가
생기지 않는다.

**부팅 시 recipe 검증.** 5번 단계 이후, 6번 단계에서 registry를 조립하기 전에 모든
recipe의 step·cleanup 참조를 검사한다. 없는 extension, 비활성 extension, 다른 recipe,
자기 자신, 중복 이름, 기존 액션 타입과의 충돌은 모두 부팅 실패다. 이 검사는 registry가
완성된 뒤에야 가능한 정보(어떤 액션 타입이 실제로 등록됐는지)를 쓰므로, extension spec을
먼저 모으고 recipe spec을 나중에 붙이는 순서를 지킨다.

**WebUI 검증 컨텍스트.** `core/configio.py`는 `get_config(path)`를 다시 돌려 편집 결과를
검증하는데(`core/configio.py:317`), 설정이 나뉜 뒤에는 `config.toml` 하나만으로 완전한
Config가 나오지 않는다. WebUI는 이미 `load_fleet`/`robot_overrides`를 임포트하므로
(`web/server.py:26`), 검증 시 **현재 보고 있는 로봇의 `extensions`/`recipes` 경로를 함께
병합**해 같은 조합으로 검증한다. 로봇 컨텍스트가 없으면 fleet의 첫 로봇을 쓰고, 어느
조합으로 검증했는지 화면에 표시한다. 편집 대상은 여전히 `config.toml`뿐이며 HCL 파일은
읽기 전용이다.

## 11. 마이그레이션 범위

HCL 전용 즉시 전환이다. `robots.toml`은 더 이상 읽지 않는다.

**의존성** — `pyproject.toml`에 `python-hcl2>=8,<9` 추가.

**변환 스크립트** — `scripts/convert-robots-config-to-hcl.py`. 기존
`robots.toml` + `config.toml`을 읽어 `robots.hcl` / `extensions.hcl`을 생성한다.
일회성이며 현장 로봇 이전을 마치면 제거한다.

**코드** — `config/fleet.py`, `config/config.py`, `core/action_modules.py`,
`core/action_registry.py`, `adapter_jibot.py`, `main.py`, `run_multi.py`,
`config/adapter_dispatch.py`, `web/main.py`, `web/render.py`, `web/server.py`.

**스크립트** — `run-adapter.sh`, `run-multi.sh`, `scripts/setup-adaptor-service.sh`,
`scripts/update-jibot-adapter-over-ssh.sh`(`--robots-toml-mode` → `--robots-hcl-mode`,
rsync exclude, 보존/덮어쓰기 로직), `scripts/update-jibot-adapter-config.sh`,
`scripts/systemd/amr-adaptor@.service`.

**테스트** — `adaptor/tests/test_fleet_registry.py`, `test_adapter_dispatch.py`,
`test_config.py`, `test_web_main.py`, `test_web_render.py`, `test_web_server.py`,
`tests/test_adaptor_cli.py`, `tests/test_adaptor_service_scripts.py`,
`tests/test_update_jibot_adapter_over_ssh.py`. `test_extension_groups.py`는
`test_recipes.py`로 대체한다.

**문서** — `README.md`, `adaptor/readme.md`, `docs/guide/adaptor-tui.md`,
`docs/guide/jibot-onboard-quick-guide.md`, `docs/guide/simulator.md`,
`docs/manual/jibot-adapter-ssh-update.md`, `docs/reference/jibot-onboard-access.md`.
`config/robots.toml.example` → `config/robots.hcl.example`, `extensions.hcl.example`,
`recipes.hcl.example`을 추가한다.

## 12. 테스트 전략

- **HCL 로더** — 라벨 정규화, step 순서 보존, 중첩 객체·리스트, `${var.X}` 미평가
  유지, 문법 오류 시 파일명·줄 번호가 담긴 예외.
- **fleet** — `robot` 블록 0개 / id 중복 / id 누락에서 `FleetError`, override 매핑,
  로봇별 `extensions`·`recipes` 경로 선택.
- **recipe 실행** — 순서대로 실행, 파라미터 치환(타입 보존/문자열 삽입/누락),
  첫 실패에서 중단, **실패·타임아웃에도 cleanup 실행**, step timeout override,
  retry 횟수와 지연, recipe 중첩 거부.
- **cleanup 결과 정책** — 본문 성공 + cleanup 실패 → **FAILED**, 본문 실패 + cleanup 실패
  → 본문 오류가 유지되고 cleanup 오류가 병기됨, cleanup 자체 timeout이 본문 예산과
  독립적으로 동작함.
- **부팅 검증** — 없는 extension / 비활성 extension / 다른 recipe / 자기 자신 참조,
  recipe 이름 중복, 액션 타입 충돌에서 각각 부팅 실패. `extensions.hcl` 부재도 부팅 실패.
- **내장 브리지** — 합성 액션이 `state.instant_action_states`에 나타나지 않음, terminal
  상태에서 future 완료, 타임아웃 시 대기표 정리, recipe 종료 시 tombstone 일괄 제거.
- **late completion 격리** — 첫 attempt timeout 후 늦은 FINISHED가 도착해도 **두 번째
  attempt의 future를 완료하지 않음**, 늦은 완료가 로그를 남기고 폐기됨, 타임아웃된 합성
  액션이 VDA5050 state에 노출되지 않음.
- **소유권 게이트** — `owner_order_id`가 부모 order와 일치하면 order 중에도 주행 primitive
  통과, **다른 order가 잡고 있으면 차단**, `owner_order_id=None`(instant 경로)은 기존
  차단 동작 유지.
- **취소 계약** — timeout 시 `cancel()` 호출됨, motion primitive의 `cancel()`이
  `um_stop()`을 부름, 취소 핸들러가 없는 extension은 종료를 기다린 뒤 실패 처리됨,
  **재시도 전 직전 attempt의 종료가 확인됨**.
- **manualStop 순서** — primitive가 `um_stop()` 완료 **후에** FINISHED를 반환함
  (예약 시점이 아님).
- **설비 extension** — 승격된 워크플로가 어댑터의 PIO 클라이언트를 재사용하는지(새
  `PIOMaster`를 만들지 않는지), 결과·에러 매핑.
- **회귀** — 기존 PIO/order/action 테스트가 그대로 통과.

## 13. 하지 않는 것 / 향후

- recipe 중첩, 병렬, 조건 분기
- `ActionResult` 데이터 필드와 recipe 레벨 `until` 조건 — 이게 생기면 recipe가 대기형
  extension 없이도 조건 대기를 표현할 수 있다
- `variable "doorPin" { default = 1 }` 블록을 통한 factsheet 파라미터 광고
- `elevator_motion_rules`를 맵 데이터로 분리
- 내장 액션 22종 전면 registry 이주
- WebUI에서 HCL 편집

## 14. 리스크

| 리스크 | 완화 |
|---|---|
| 현장 로봇이 `robots.toml`을 그대로 들고 있으면 기동 실패 | 변환 스크립트 제공, 배포 스크립트에 파일 부재 시 명확한 에러 메시지 |
| `regex` C 확장 아키텍처 불일치 | `--copy-venv` 경로가 이미 동일 아키텍처를 요구하고 경고한다. 배포 문서에 명시 |
| 상태 머신 승격 시 시리얼 포트 이중 오픈 | 클라이언트 공유를 승격의 선결 조건으로 못박고 테스트로 고정 |
| 설정 파일이 4개로 늘어 운영자가 헷갈림 | 각 파일 머리말에 "무엇을 담는가"를 한 문단으로 명시. 기존 config.toml 주석 밀도를 유지 |
| primitive 분리가 주행 경로를 건드린다 | instant wrapper가 기존 게이트를 그대로 유지하고(`owner_order_id=None`), 사다리는 손대지 않는다. primitive 추출 단계에서 기존 instant 액션 회귀 테스트를 먼저 통과시킨 뒤 recipe wrapper를 붙인다 |
| 취소 계약이 없는 extension이 남아 있음 | "취소 불가 = 종료를 기다린다"를 기본값으로 두어, 계약 미구현이 방치된 주행으로 이어지지 않게 한다 |
| 한 번에 바뀌는 범위가 큼 | 구현 계획에서 (1) HCL 로더 + robots 전환, (2) extensions 분리, (3) recipe 개명·실행 의미(cleanup 정책·시간 예산 포함), (4) 주행 primitive 분리 + 소유권 게이트, (5) 완료 브리지 + 취소 계약, (6) 상태 머신 승격 순으로 쪼갠다. 각 단계가 독립적으로 통과 가능하다. (4)와 (5)는 recipe 없이도 자체 회귀 테스트로 검증된다 |
