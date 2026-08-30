# WebUi: urobot 재시작 제어 + 모터 상태 표시 — 설계

작성일 2026-06-21. 대상: `adaptor/web/` (서버렌더 HTML WebUi).
2026-06-21 코드리뷰 반영 개정.

운영 중 cam3 스트림이 빈 화면이던 사고를 `sudo systemctl restart urobot.service`로
복구한 데서 출발한다. (1) 그 재시작을 WebUi에서 누를 수 있게 하고, (2) 모터가
멈췄는지(= JIBOT motor power off)를 모니터에서 바로 보이게 한다.

## 배경 / 현재 상태

- **WebUi 위치·실행.** 어댑터와 같은 robot 온보드 PC에서 실행되는 경량 서버렌더
  HTML UI(`adaptor/web/`). 카메라 페이지가 이미 `SystemdController("web_video_server.service")`로
  **robot-host systemd 서비스**를 로컬 `systemctl`로 제어한다. `urobot.service`도
  같은 호스트의 로컬 유닛이므로 동일 패턴이 그대로 적용된다.
- **모니터(상세) 페이지는 이미 control 섹션을 포함한다.** `adapter_detail_page`
  (`render.py:148`)가 `<h3>control</h3>` + `_control_forms(...)`(`render.py:176`)를
  렌더하며, `_control_forms`(`render.py:265`)는 host 재부팅·서비스 verb·instant
  action 폼을 만든다. 따라서 urobot 재시작은 이 control 섹션에 한 줄 더하는 것이다.
  단, `_control_forms`는 상세·control 양쪽에서 **모든 spec에 per-spec로** 호출된다
  (`render.py:176`, `render.py:290`) — jibot, hexplorer, 멀티인스턴스 `jibot:robot-N`
  포함. urobot 폼을 무조건 넣으면 host-wide 버튼이 Hexplorer 페이지에도 샌다(아래 게이트).
- **모터 상태는 이미 시스템에 흐른다(추론값).**
  - 어댑터: `adapter_jibot.py:460-461` — `safety_state.e_stop = AUTOACK if self._vehicle._motor_flag == 0 else NONE`.
    즉 **모터 전원 off(`_motor_flag==0`) → VDA5050 `safetyState.eStop = AUTOACK`** 로 발행.
    JIBOT 어댑터가 발행하는 eStop 값은 사실상 `NONE` 또는 `AUTOACK` 둘뿐이다.
  - 모니터 파싱: `monitor.py:103-110` — `safetyState.eStop`/`activeEmergencyStop`을
    `StateSnapshot.active_emergency_stop`(`monitor.py:37`)로 이미 추출. 상세 페이지
    `_mqtt_live_table`(`render.py:89-90`)에 "emergency stop" / "field violation" 행으로
    **이미 표시**된다.
  - 헬퍼 `_is_emergency_stop_active(snapshot)`(`render.py:106-111`)는 eStop이 `""`/`"NONE"`이
    아니거나 **operating_mode가 emergency 계열**이면 True를 반환한다. 모터 라벨에 그대로
    쓰면 과표시 위험이 있어, 모터 행은 **재사용하지 않고** 별도 보수 규칙을 쓴다(아래 1a).

→ 모터 정보는 **렌더 표현만** 손보면 되고(데이터 수집/파싱 변경 없음), urobot
재시작은 기존 host-reboot 제어 패턴을 복제한다.

## 범위 (확정된 결정)

- 모터 정보: 상세 페이지에 직관적 **"모터(추정)" 행** 추가 + **어댑터 목록 요약**에도
  모터 상태 토큰 추가. 기존 "emergency stop" 행은 프로토콜 정확성을 위해 **유지**.
  데이터는 기존 `active_emergency_stop`만 사용 — 어댑터/JIBOT 데이터 수집 추가 없음.
  모터 상태는 직접 센서값이 아니라 eStop에 얹힌 **추론값**이므로 라벨·조건을 보수적으로 잡는다.
- urobot 제어: 모니터(상세) 페이지의 control 섹션에 **재시작(restart) 단일 동작**.
  확인 체크박스 필수. start/stop/enable/disable은 제공하지 않는다.
- urobot 버튼은 **`spec.manufacturer == "jibot"` spec에서만** 노출한다(config 토글 없음 —
  manufacturer 분기로만 게이트). `_control_forms`가 비-jibot spec에도 렌더되므로,
  host-wide `urobot.service` 버튼이 Hexplorer/비-jibot 페이지에 새는 것을 막기 위함.
  다른 AMR 타입이 자체 vendor 서비스를 가질 때 일반화한다 — 이번 범위 밖.

## 변경 1 — 모터 상태 표시 (`adaptor/web/render.py` 단일 파일)

데이터 흐름·백엔드 변경 없음. 순수 렌더 함수만 수정한다.

### 1a. 상세 페이지 "모터(추정)" 행

`_mqtt_live_table`(`render.py:76`)에 "emergency stop" 행 근처(가독성상 위쪽)로
**모터 행**을 추가한다. `_is_emergency_stop_active`(operating_mode까지 봄)는 쓰지 않고
전용 `_motor_label(snapshot) -> str`을 둔다. **AUTOACK만 motor off**로 보수적으로 해석:

| `active_emergency_stop` (대문자화) | 표시 |
| --- | --- |
| `None` (state 미수신) | `알 수 없음` |
| `"NONE"` | `정상 (활성)` |
| `"AUTOACK"` | `정지 (비활성)` — JIBOT 모터 전원 off |
| 그 외(`MANUAL`/`REMOTE` 등) | `e-stop: <값>` — 물리 e-stop, 모터 전원은 단정 안 함 |

라벨을 "모터(추정)"로 두어 직접 센서값이 아니라 eStop 기반 추론임을 드러낸다. 기존
"emergency stop"·"field violation" 행은 그대로 둔다(모터 행은 운영자 친화 뷰, eStop
행은 VDA5050 원값).

### 1b. 어댑터 목록 요약

`adapter_list_page`(`render.py:53`)의 한 줄 요약(`render.py:60-62`, 현재
`연결 / 모드 / 배터리% / pos / err N`)에 **모터 토큰**을 덧붙인다. 1a의 `_motor_label`을
공유해 짧게: `motor 정상` / `motor 정지` / `motor e-stop:<값>` / `motor ?`.

## 변경 2 — urobot 재시작 제어

host reboot 경로(`/adapter/<key>/host/reboot` → `_post_host_reboot`)를 그대로 본뜬다.

### 2a. 컨트롤러 와이어링 — `adaptor/web/main.py`

`WebUi(...)` 생성 시 인자 추가:

```python
urobot_controller=SystemdController("urobot.service", use_sudo=False)
```

`camera_controller` / `host_controller` 와 동일하게 단일 host-wide 인스턴스로 전달한다.

### 2b. 라우트·핸들러 — `adaptor/web/server.py`

- `__init__`에 `self._urobot_controller = urobot_controller` 저장(`server.py:62-64` 인접).
- `do_POST` 디스패치(`server.py:352` host-reboot 분기 인접)에 추가:
  `parts == ["adapter", <key>, "urobot", "restart"]` → `self._post_urobot_restart(h, key, form)`.
- `_post_urobot_restart`는 `_post_host_reboot`(`server.py:420-435`)를 복제:
  - 컨트롤러 `None`이면 `rejected:not-configured` audit + err redirect.
  - `confirm` 미체크면 `rejected:unconfirmed` audit + err redirect.
  - `ok, msg = self._urobot_controller.restart()` 호출.
  - `self._audit(h, key, "urobot:restart", f"ok={ok} {msg}")` 기록.
  - 원래 페이지로 flash redirect.
- urobot 서비스 메트릭 polling은 추가하지 않는다(상태 표기 범위 밖, 아래 YAGNI).
  재시작 명령 결과는 flash 메시지로 확인한다.

### 2c. 폼 렌더 — `adaptor/web/render.py`

- `_host_reboot_form`(`render.py:253`) 패턴으로 `_urobot_restart_form(spec_key, csrf, return_to)`
  추가: action `/adapter/<key>/urobot/restart`, CSRF 필드, **확인 체크박스 필수**,
  버튼 라벨 `restart urobot`, 옆에 경고 문구("nav/perception 스택 재기동 — 로봇 정지
  시에만").
- `_control_forms`(`render.py:265`)에서 **`spec.manufacturer == "jibot"`일 때만**
  `<h4>robot (urobot)</h4>` + 위 폼을 삽입한다(Hexplorer·비-jibot spec엔 미표시).
  `_control_forms`는 상세·control 페이지 공용이므로 jibot 모니터 페이지에 자동 노출된다.

### 2d. polkit 권한

- `scripts/systemd/amr-webui-polkit.rules`: start/stop/restart per-unit 허용 목록(managed
  units, `action.lookup("unit")` 매칭, `__MANAGED_UNITS__` 플레이스홀더)에 `urobot.service`가
  포함되도록 설치 스크립트가 렌더한다.
- `scripts/setup-adaptor-service.sh:render_webui_polkit()`(`:223`)의 `polkit_units`
  배열에 `[[ $DO_JIBOT -eq 1 ]] && polkit_units+=("urobot.service")` 추가(`:225-227`
  인접 — urobot은 jibot 로봇 호스트에 존재). 이 변경으로 **WebUi 실행 사용자는
  `urobot.service`의 start/stop/restart 권한을 갖는다**(per-unit 그랜트가 세 verb를 함께
  허용; UI는 restart만 노출). 이 권한 확대는 §안전 경계에 명시한다.
- 회귀: `tests/test_update_jibot_adapter_over_ssh.py`의
  `test_setup_script_renders_webui_polkit_units_from_selected_targets`(`:171`)가
  `polkit_units` 라인을 직접 검증하므로 urobot 라인 assert를 함께 추가한다.
- 규칙 미설치 호스트에선 기존 동작대로 권한 거부 → flash "restart failed"로 표시(폼은
  그대로 보임).

## 안전 경계

- urobot 재시작은 nav·perception·localization·motion 스택 전체를 잠깐 내린다. host
  reboot와 동일 등급으로 취급: **확인 체크박스 필수 + 경고 문구 + audit 로그**.
- WebUi는 기존대로 HTTP Basic + CSRF + 기본 localhost 게이트 안에서만 동작한다.
- **권한 확대 명시:** polkit 변경으로 WebUi 실행 사용자는 `urobot.service`에 대해
  start/stop/restart 권한을 얻는다(UI는 restart만 노출). 기존 어댑터/카메라 유닛과 동일
  등급의 per-unit 그랜트이며 root 전체 sudo는 아니다.
- WebUi는 소프트웨어 게이트일 뿐 안전 PLC/비상정지를 대체하지 않는다(기존 문구 유지).

## 테스트

`render.py`는 순수 함수라 단위 테스트가 쉽다. `adaptor/tests/`의 기존 패턴
(`test_monitor.py`, `test_video.py`) 활용:

- 모터 행/요약: `active_emergency_stop` 이 `None` / `"NONE"` / `"AUTOACK"` / `"MANUAL"`
  각각에 대해 `_motor_label`·`_mqtt_live_table`·`adapter_list_page` 출력 검증. 특히
  `"MANUAL"`은 `정지`가 아니라 `e-stop:` 로 나오는지(과표시 방지).
- urobot 폼: `_urobot_restart_form` 이 올바른 action·CSRF·확인 체크박스를 포함하는지,
  `_control_forms`가 **jibot spec에선 섹션을 렌더하고 비-jibot(예: hexplorer) spec에선
  렌더하지 않는지**.
- `_post_urobot_restart`: confirm 미체크 거부 / 컨트롤러 None 거부 / 정상 시 `restart()`
  호출 + audit 기록(스파이/스텁).
- installer/polkit 회귀: `tests/test_update_jibot_adapter_over_ssh.py`의
  `test_setup_script_renders_webui_polkit_units_from_selected_targets`에 urobot.service
  라인 assert 추가.

## 변경 파일 요약

- `adaptor/web/render.py` — `_motor_label`, 모터 행·요약 토큰, `_urobot_restart_form`,
  `_control_forms`에 `manufacturer=="jibot"` 게이트 섹션.
- `adaptor/web/server.py` — `_urobot_controller`, `/adapter/<key>/urobot/restart` 라우트,
  `_post_urobot_restart`.
- `adaptor/web/main.py` — `urobot_controller=SystemdController("urobot.service")` 주입.
- `scripts/systemd/amr-webui-polkit.rules` + `scripts/setup-adaptor-service.sh` — managed
  units에 `urobot.service`(`DO_JIBOT` 게이트).
- `docs/guide/web-ui.md` — §5(화면) 모터 행·urobot 제어, §6(polkit) urobot 항목·권한 확대 반영.
- `adaptor/tests/` (render/server) + `tests/test_update_jibot_adapter_over_ssh.py` (polkit 회귀).

## 범위 밖 (YAGNI)

- urobot start/stop/enable/disable (재시작만).
- 실제 로봇 state(`Stopped` 등)·per-axis EziMotor 정보 등 어댑터 데이터 수집 추가.
- urobot 서비스 상태(active/enabled) 표기 — 재시작 결과는 flash로 확인.
- urobot 버튼 config 토글(manufacturer 분기로 충분; 다른 AMR 타입이 vendor 서비스를 가질 때 일반화).
- 모든 제어를 모니터 페이지로 통합하는 UX 개편.
