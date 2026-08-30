# 원격 config reload / restart — 설계 문서

- 날짜: 2026-08-22
- 상태: **설계 확정, 미구현.** 구현 계획서는 `docs/superpowers/plans/`에 별도.
- 목표: WCS가 EPR(`setParameters`)로 바꾼 설정을 **현장 사람 없이** 반영한다.
  주 용도는 시운전 중 recipes/extensions 값(PIO 타이밍, 엘리베이터 스텝, 핀맵) 반복 튜닝.

---

## 1. 지금 무엇이 문제인가

설정은 **부팅 때 한 번만** 읽는다.

- `get_config()`가 `load_recipes()`로 `recipes.hcl`을 읽어 `Config.recipes`로 굳힌다
  — `adaptor/config/config.py:1088`
- 그 스냅샷을 recipe마다 handler 클로저가 캡처한다
  — `adaptor/extensions/recipes/__init__.py:127`, `adaptor/core/registry.py:375`
- `adaptor/core/configio.py:11`이 이를 명시한다: *"the adaptor reads config only once at boot,
  so applying changes always requires a service restart."*

`setParameters`는 **파일 쓰기 + 부팅 로더 재검증**까지만 한다 (`adaptor/adapter_jibot.py:1948`,
`:2054`). 메모리의 `self.config`는 건드리지 않는다.

그리고 **원격에서 재시작시킬 수단이 아예 없다** — `INSTANT_ACTION_TYPES`
(`adaptor/core/factsheet.py:13`)에 restart 계열이 없다. 결과적으로 원격 파라미터 관리인데
마지막 한 걸음이 현장 사람이다.

---

## 2. 왜 reload가 가능한가 — 이미 있는 스왑 seam

세 지점이 **호출 시점에** 조회하는 형태라 통째 교체가 먹힌다.

| seam | 위치 | 의미 |
|---|---|---|
| 확장이 `adapter.config.*`를 매 호출 읽음 | extensions 전역 31곳 (예: `extensions/pio/__init__.py:791` 핀맵, `:833` output_signals) | `self.config` 스왑이 다음 액션부터 반영 |
| 액션 디스패치가 `self._action_registry`를 매번 조회 | `adapter_jibot.py:4401`, `:4472`, `:5951` | 레지스트리 스왑이 다음 액션부터 반영 |
| `_build_factsheet()`가 config·registry를 live로 읽음 | `adapter_jibot.py:3268` | 재발행하면 WCS도 새 action type을 봄 |

반대로 **부팅 때 값을 복사해 간 소비자**는 스왑해도 안 바뀐다. 이것이 4.4 분류 테이블의 근거다.

| 소비자 | 위치 | 캡처한 것 |
|---|---|---|
| `MQTTClient(config=...)` | `adapter_jibot.py:254` | 브로커 주소·토픽 identity |
| `SoundPlayer(...)` | `:237` | sink/player/경로/타임아웃 |
| `VideoStreamer(config.video, ...)` | `:311` | 비디오 설정 |
| `StateActionController(...)` | `:232` | state_actions |
| `publish_state(interval_sec=...)` | `:582` | state_publish_delay |
| `subscribe_acs_cmd(interval_sec=...)` | `:568` | acs_cmd_subscribe_interval_sec |
| `monitor_jibot_connection(interval_sec=...)` | `:604` | jibot_reconnect_delay |
| `self._jibot_rx_timeout` | `:313` | settings.jibot_rx_timeout |
| 열린 PIO 시리얼 / EZi 소켓 | `main.py:936` 앞뒤에서 부착 | 포트·보드레이트·주소 |

---

## 3. 확정된 결정

| 항목 | 결정 |
|---|---|
| 트리거 | **새 instant action으로 분리.** `setParameters` 자동 적용은 하지 않는다 |
| 범위 | **전체 재로드 + 미반영 필드 보고.** `get_config()`를 통째로 다시 돌리고, 스왑만으로 안 먹는 필드가 실제로 바뀌었으면 "재시작 필요"로 되돌려준다 |
| 안전 게이트 | **IDLE일 때만.** 아니면 REJECT하고 이유를 돌려준다 |
| 재시작 | **`restartAdapter` 액션도 함께.** 별도 명시 호출. reload가 자동으로 재시작하지 않는다 |

`setParameters` 자동 적용을 뺀 이유: 쓰기와 적용이 분리돼야 WCS가 여러 키를 모아 한 번에
적용하거나, 로봇이 멈춘 순간을 골라 적용할 수 있다.

---

## 4. 설계

### 4.1 새 instant action 2개

`reloadConfig`
- 파라미터: 없음 (v1)
- 성공: `FINISHED`, `resultDescription`에 요약 — 적용된 source, 재시작이 필요한 필드 목록
- 거부: `FAILED` + 사유 (IDLE 아님 / 파일이 로더를 통과 못 함)

`restartAdapter`
- 파라미터: 없음 (v1)
- 성공: `FINISHED` 발행 → connection OFFLINE 발행 → **non-zero 종료**
- 거부: `FAILED` + 사유 (IDLE 아님)

등록 위치:
- `adaptor/core/factsheet.py:13` `INSTANT_ACTION_TYPES`에 두 타입 추가 → factsheet로 WCS에 광고
- `adaptor/adapter_jibot.py:6231~6313` instant-action 분기 체인에 두 분기 추가
- 로컬 control socket(`adapter_jibot.py:1018`)이 같은 분기를 타므로 WebUI에서도 공짜로 쓸 수 있다

### 4.2 config loader 주입 — 이 설계의 필수 전제

**어댑터는 자기 config가 어떻게 만들어졌는지 모른다.** `main.py:936`이 `Adapter(...)`에
넘기는 것은 완성된 `config` 객체와 파일 경로들뿐이고, `robots.hcl`에서 온 **`overrides`는
넘어가지 않는다** (`main.py:760` `get_config_with_fallback(config_path=…, overrides=…)`).

따라서 어댑터가 `get_config(config_path, extensions_path, recipes_path)`를 그대로 다시
부르면 **로봇별 serial_number / vehicle_ip / ezi 주소 / 브로커가 config.toml 기본값으로
조용히 되돌아간다.** 이건 reload 기능이 만들 수 있는 가장 나쁜 사고다.

해결: `main.py`가 **부팅 로드를 그대로 재현하는 callable**을 어댑터에 주입한다.

```python
# main.py — 부팅과 reload가 같은 인자를 쓰도록 한 곳에 묶는다
def make_config_loader(config_path, overrides, extensions_path, recipes_path):
    def load():
        return get_config(
            config_path=config_path, overrides=overrides,
            extensions_path=extensions_path, recipes_path=recipes_path,
        )
    return load

adapter = Adapter(..., config_loader=make_config_loader(...))
```

`config_loader`가 없으면(테스트·구형 호출) reload는 "loader 미주입"으로 REJECT한다 —
overrides 없이 추측해서 읽는 폴백은 두지 않는다.

### 4.3 reload 절차 (원자성)

```
1. IDLE 게이트 검사        — 실패 시 REJECT, 아무것도 건드리지 않음
2. new = self._config_loader()   — 실패(RecipesError/ExtensionsError/…) 시 REJECT
3. new_registry = build_registry_from_config(new.actions, first_party_action_specs(new))
   validate_state_actions(new.state_actions, new_registry)
   validate_joystick_actions(new.joystick, new_registry)   — 실패 시 REJECT
4. changed = 분류 테이블로 old vs new 비교 → restart_required 목록 산출
5. 스왑: self.config = new; self._action_registry = new_registry
6. self.publish_factsheet()
7. FINISHED + 요약 발행
```

1~4단계는 전부 **부작용 없는 준비**다. 어느 단계에서 실패하든 옛 `self.config`가 그대로
살아 있다 — 별도 롤백 코드가 필요 없다. 스왑(5)은 두 줄의 원자적 대입이다.

### 4.4 분류 테이블 + 완전성 테스트

`adaptor/core/config_reload.py`(신규)에 경계를 선언한다. 설계 원칙은 **기본 거부**다 —
명시적으로 "reload 된다"고 적힌 것만 reload로 치고, 나머지는 전부 재시작이 필요한 것으로
본다. 틀렸을 때의 결과가 비대칭이기 때문이다:

- 기본 거부에서 틀리면 → 필요 없는 재시작을 권한다. 성가시지만 안전하다.
- 기본 허용에서 틀리면 → **"FINISHED인데 실제로는 안 먹음"**. 지금(무조건 재시작)보다 나쁘다.

`Config`는 최상위 28개 필드다. 하나하나를 세 갈래 중 하나로 둔다.

```python
# 통째로 reload 된다 — 확장·레지스트리·디스패치가 실행 시점에 읽는다
RELOADABLE_WHOLE = frozenset({
    "recipes", "actions", "action_modules",
    "pio_advanced", "air_shower_config", "elevator_config",
    "motion_rules", "dock", "charge", "manual_control", "jibot_status", "factsheet",
})

# 통째로 재시작이 필요하다 — 부팅 때 값을 복사해 간 소비자가 있다(2절 표)
RESTART_WHOLE = frozenset({
    "mqtt_broker", "vehicle", "settings", "ezi_config", "sound_settings",
    "charge_circuit", "bms_ros", "hexplorer", "video", "adapter",
    "jibot_client", "web_ui", "internal_actions", "state_actions", "joystick",
})

# 한 섹션 안에서 갈린다 — 여기 적힌 하위 필드만 reload 되고, 나머지는 기본 거부
RELOADABLE_SUBFIELDS = {
    "pio_config": frozenset({
        "input_pins", "output_pins", "output_pin_map", "output_signals",
    }),
}
```

분류 근거 몇 가지(구현 시 나머지도 같은 방식으로 확인):

| 필드 | 갈래 | 근거 |
|---|---|---|
| `internal_actions` | 재시작 | `__init__`에서 `_docking_status_action_id/_type`로 복사 — `adapter_jibot.py:378`, `:383` |
| `settings` | 재시작 | `map_id`가 `_current_map_id`로 복사(`:392`), 주기값들이 루프 인자로 캡처(2절 표) |
| `manual_control` | reload | `utils/joystick_runtime.py:206`, `:409`가 실행 시점에 읽는다 |
| `dock` | reload | `adapter_jibot.py:4960`, `:4967`, `:4977` 실행 시점 참조 |
| `factsheet` | reload | `core/factsheet.py:116`이 발행할 때 읽는다 → 재발행으로 반영 |
| `pio_config` 핀맵 | reload | `extensions/pio/__init__.py:791`, `:812`, `:822`, `:833` 실행 시점 참조 |
| `pio_config` 포트·보드레이트 | 재시작 | 이미 열린 시리얼이 산다 (`extensions/pio/__init__.py:138`) |
| `ezi_config` | 재시작(v1) | 주소(`ezi_io`/`ezi_motor`)와 핀 값이 섞여 있다. 기본 거부로 통째 재시작에 두고, 근거가 모이면 하위 분리 |

**완전성 테스트** — 최상위 28개가 세 갈래를 정확히 한 번씩 덮는지 검사한다.

```
set(fields(Config)) == RELOADABLE_WHOLE | RESTART_WHOLE | set(RELOADABLE_SUBFIELDS)
그리고 세 집합은 서로소
```

새 설정 **섹션**이 추가되면 이 테스트가 깨지고 개발자가 갈래를 정해야 통과한다. 기존 섹션에
새 **하위 필드**가 추가되면 테스트는 안 깨지지만 기본 거부라 "재시작 필요"로 분류된다 —
거짓 성공이 아니라 과한 신중이므로 안전한 방향으로 틀린다.

비교는 dataclass 값 동등성(`==`)으로 한다. 재시작이 필요한 항목 중 **실제로 값이 바뀐 것만**
결과에 싣는다 — 안 바뀐 항목은 보고하지 않는다.

```python
def restart_required_changes(old: Config, new: Config) -> list[str]:
    """스왑으로 반영되지 않는 항목 중 값이 실제로 달라진 경로를 정렬해 돌려준다."""
```

### 4.5 IDLE 게이트

reload/restart 공통. 아래 중 하나라도 참이면 거부한다.

| 조건 | 근거 |
|---|---|
| `self.order is not None` 이고 `_order_motion_in_flight()` | 주행 중 |
| `self.order_queue.empty()`가 거짓 또는 `current_order_step is not None` | 주문 처리 중 |
| `self._work_in_progress is not None` | loading/unloading BUSY (`adapter_jibot.py:295`) |
| 실행 중 액션 있음 (`_order_background_action_tasks`, `_order_exclusive_background_action_tasks`, `_active_action_steps`) | 액션 도중 값이 섞인다 |
| `self._manual_control_active` | 수동 조작 중 |

`_manual_blocked_reason()`(`adapter_jibot.py:6360`)와 같은 모양의 `_reload_blocked_reason()`을
새로 두고, 사유 문자열을 그대로 `resultDescription`에 싣는다. WCS는 잠시 뒤 다시 부르면 된다.

게이트가 필요한 이유는 recipe 클로저가 아니라 **확장이다**: 확장은 `adapter.config`를 실행
중에 live로 읽으므로(2절), 액션 중간에 스왑하면 한 액션이 옛 핀맵과 새 핀맵을 섞어 쓸 수 있다.

### 4.6 restartAdapter 절차

재시작에 systemctl 권한은 필요 없다. **비정상 종료 → systemd가 되살림** 패턴이 이미
확립돼 있고(`e5b3615`, `scripts/setup-adaptor-service.sh:868` `Restart=on-failure`,
`RestartSec=3`), `_terminate_after_task_loss()`(`adapter_jibot.py:544`)가 그 구현이다.

다만 그 함수는 `os._exit`라 인터프리터 정리를 건너뛴다 — 루프가 이미 죽은 장애 상황용이라
그렇다. **의도된 재시작은 기존 graceful shutdown 경로를 타야 한다**: `main.py:1002~1017`의
finally가 connection OFFLINE(retained) 발행 → MQTT 해제 → vehicle 해제를 이미 한다.

```
1. IDLE 게이트 + 최소 가동시간 검사 — 실패 시 REJECT
2. instantActionStates에 FINISHED 발행
3. adapter.request_restart(reason) — asyncio.Event 세트
4. main.py의 while 루프가 Event를 보고 break → 기존 finally 실행
   (OFFLINE retained 발행 / MQTT·vehicle 해제)
5. sys.exit(75) — 비영 코드. systemd가 3초 뒤 되살린다
```

75를 쓰는 이유는 "의도된 재시작"을 journal에서 크래시와 구분하기 위해서다. `main()`의
`except Exception`은 `SystemExit`(BaseException)을 잡지 않으므로 finally 이후에 그대로
빠져나간다.

**폭주 방지.** systemd 기본값은 `StartLimitBurst=5` / `StartLimitIntervalSec=10s`이고 두 유닛
생성기 모두 이를 재정의하지 않는다. 재시작이 빠르게 반복되면 유닛이 failed로 **주저앉아
사람이 갈 때까지 안 뜬다** — 원격 기능이 만들 수 있는 최악의 결과다. 어댑터 기동 시각은
이미 `self._adapter_started_at`(`adapter_jibot.py:252`)에 있으므로, **기동 후 최소 가동시간
(기본 30초) 미만이면 restartAdapter를 거부**한다. 재시작을 넘어 살아남는 상태가 필요 없는
게이트라 프로세스가 죽어도 그대로 성립한다.

**멀티로봇 blast radius를 결과에 명시한다.** `run_multi.py`는 자식 하나가 죽으면 나머지를
정리하고 non-zero로 빠진다 → systemd가 **그룹 전체**를 재시작한다. 즉 AMR 1대 재시작이
같은 그룹 3대 재시작이 된다. v1에서는 이 동작을 바꾸지 않고, `resultDescription`에
"group restart"임을 싣는다. (개별 재시작은 후속 과제 — 6절)

### 4.7 결과 보고

`reloadConfig` / `restartAdapter` 모두 `instantActionStates`의 `resultDescription`으로
보고한다. `EquipmentParameterApplyResult` envelope(`amr_parameter_publish.py:801`)은
**건드리지 않는다** — WCS와의 계약이고, 이 두 액션은 그 계약 밖의 별개 액션이다.

`resultDescription` 형식(예):
```
reloaded: recipes=14, actions=…; restart required for: mqtt_broker, settings.state_publish_delay
```

---

## 5. 하지 않는 것 (YAGNI)

- `setParameters` 성공 시 자동 reload — 3절 결정
- reload 실패 시 자동 재시작 — 원격이 의도치 않은 재시작을 유발한다
- 프로세스 내 소프트 재시작(루프·SoundPlayer 재생성) — order 큐/시퀀스/진행 상태 이어붙이기가
  전부 새 문제가 된다. 재시작의 깨끗함만 잃는다
- IDLE 대기 큐("나중에 자동 적용") — 결과 보고 시점이 미끄러진다
- `force` 파라미터 — 게이트를 우회하는 순간 4.5의 근거가 무의미해진다
- reload 가능 필드의 부분 적용(선택 키만 reload) — 전체 재로드 + 보고로 충분하다

---

## 6. 후속 과제 (이번 범위 밖)

- 멀티로봇 개별 재시작: `run_multi.py`가 죽은 자식만 되살리도록 감독 구조 변경
- PIO 포트·보드레이트 변경 시 재시작 대신 `pioDisconnect` → `pioInit` 재연결로 흡수
- `state_actions` / `joystick`을 컨트롤러 재생성으로 reloadable 쪽으로 옮기기

---

## 7. 테스트 계획

| 테스트 | 확인하는 것 |
|---|---|
| 분류 완전성 | `Config` 최상위 28개 필드가 세 갈래를 정확히 한 번씩 덮고 서로소다 (4.4) |
| reload 성공 | recipes.hcl을 바꾸고 reloadConfig → 새 recipe가 registry에 보이고 factsheet가 재발행된다 |
| reload 원자성 | 깨진 recipes.hcl로 reloadConfig → REJECT, `self.config`·registry가 이전 값 그대로 |
| loader 미주입 | `config_loader=None`이면 REJECT (config.toml 기본값으로 되돌아가지 않는다) |
| overrides 보존 | robots.hcl override가 있는 로드에서 reload 후 serial_number/broker가 유지된다 |
| 재시작 필요 보고 | RESTART_REQUIRED 필드를 바꾸고 reload → 그 필드만 목록에 실린다. 안 바뀐 건 안 실린다 |
| IDLE 게이트 | order/BUSY/액션 실행 중 각각에서 reload·restart가 REJECT되고 사유가 실린다 |
| restart 절차 | FINISHED 발행 → restart Event 세트 → main 루프 break → OFFLINE 발행 → exit 75 |
| 최소 가동시간 | 기동 직후 restartAdapter는 REJECT, 임계 시간 경과 후에는 수락 |
| factsheet 광고 | 두 타입이 `INSTANT_ACTION_TYPES`와 발행된 factsheet에 있다 |

---

## 8. 변경 대상 파일

| 파일 | 변경 |
|---|---|
| `adaptor/core/config_reload.py` | **신규** — 분류 테이블, 비교 함수 |
| `adaptor/core/factsheet.py` | `INSTANT_ACTION_TYPES`에 2개 추가 |
| `adaptor/adapter_jibot.py` | `config_loader` 인자, `_reload_blocked_reason()`, 두 핸들러, 분기 2개, 의도된 종료 함수 |
| `adaptor/main.py` | `make_config_loader()` + `Adapter(config_loader=…)` 주입, 루프의 restart Event 감시와 `sys.exit(75)` |
| `adaptor/tests/test_config_reload.py` | **신규** — 7절 |
| `adaptor/core/registry.py` | (필요 시) WebUI 버튼 노출 |
| `adaptor/readme.md` | reload/restart 액션과 "재시작 필요" 개념 문서화 |

---

## 9. 리스크

| 리스크 | 완화 |
|---|---|
| 분류 오판 → "FINISHED인데 안 먹음" | 기본 거부 설계 + 최상위 완전성 테스트 + 구현 시 필드별 실사(4.4) |
| overrides 유실로 로봇 정체성 붕괴 | loader 주입 강제, 미주입 시 REJECT, 전용 테스트(7절) |
| 액션 중 스왑으로 값 섞임 | IDLE 게이트, force 없음 |
| 원격 재시작이 그룹 전체를 내림 | 결과에 명시, 개별 재시작은 후속 과제 |
| 재시작 반복 → systemd start limit으로 유닛이 주저앉음 | 최소 가동시간 게이트(4.6). 상태를 파일에 남기지 않아 재시작을 넘어 성립 |
| 새 액션이 WCS plugin에 없음 | factsheet 광고 → WCS 쪽 대응 필요. 배포 순서를 운영과 합의 |
