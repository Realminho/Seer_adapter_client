# WebUi urobot 재시작 + 모터 상태 표시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** WebUi에 (1) JIBOT `urobot.service` 재시작 버튼과 (2) 운영자 친화 모터 상태 표시를 추가한다.

**Architecture:** 모터 표시는 이미 발행·파싱되는 VDA5050 `safetyState.eStop`을 `adaptor/web/render.py`에서 재표현(백엔드 변경 없음). urobot 재시작은 기존 host-reboot 제어 패턴(SystemdController + POST 라우트 + confirm + polkit per-unit grant)을 복제하되, `_control_forms`가 모든 spec에 렌더되므로 JIBOT spec에서만 노출하도록 게이트한다.

**Tech Stack:** Python 3.11+ (`adaptor/pyproject.toml: requires-python = ">=3.11"`), stdlib `http.server` 기반 서버렌더 HTML, pytest, bash 설치 스크립트, polkit rules.

> **rev2 (2026-06-22, 코드리뷰 반영):** (a) v3 기본 경로에서 모터-off는 `eStop=AUTOACK`이 아니라 **MANUAL**로 발행된다(`_build_v3_emergency_stop`가 AUTOACK→MANUAL 변환) → 모터 판정을 "AUTOACK만"이 아니라 **"NONE이 아님"**으로 바꾸고 **JIBOT spec 전용 게이트**. (b) Python 3.11+. (c) **워크트리에 겹치는 미커밋 WIP가 있어** per-file `git add`가 아니라 **내 hunk만 선택 스테이징**(`git add -p`)해 커밋. (d) v3 페이로드 경로 테스트 추가.

## Global Constraints

- 모터 표시는 **렌더 전용**이다. `adaptor/core/monitor.py`·어댑터·MQTT 파싱은 건드리지 않는다.
- **모터 행은 JIBOT spec에서만** 표시한다(`_runs_urobot(spec)` 재사용). 비-JIBOT(예: dobot/hexplorer)은 eStop 의미가 달라 모터 추론이 무효하므로 행을 넣지 않는다(기존 "emergency stop" 행은 유지).
- JIBOT motor-off 판정: `active_emergency_stop`이 `None`/`""`이면 `알 수 없음`, `"NONE"`이면 `정상(활성)`, **그 외(비-NONE) 전부** `정지(비활성)`. JIBOT 어댑터는 모터-off일 때만 비-NONE eStop을 발행하므로(v2 `AUTOACK`, v3 `MANUAL`) 이 규칙이 두 버전을 모두 포착한다.
- **의존성(주의):** v3에서 모터-off가 eStop=MANUAL로 보이려면 `_build_v3_emergency_stop`의 AUTOACK→MANUAL 변환이 필요하다. 이는 **현재 워크트리 WIP(`adapter_jibot.py`)에만 있고 HEAD에는 없다**(HEAD는 AUTOACK→NONE으로 모터-off가 사라짐). 본 계획은 그 WIP를 커밋하지 않으므로(아래 git 전략), 깨끗한 체크아웃에서 v3 모터-off가 보이려면 WIP의 그 hunk가 별도로 커밋돼야 한다. 이 사실을 PR 설명/리뷰에 명시한다.
- urobot 제어는 **restart 단일 동작**, **confirm 체크박스 필수**, **JIBOT spec에서만** 노출. config 토글은 두지 않는다.
- spec 게이트(모터 행·urobot 버튼 공통)는 `manufacturer`(config로 오버라이드 가능, `registry.py:255`)가 아니라 **`spec.key`**(`"jibot"` 또는 `"jibot:<robot>"`)로 판정한다 — 헬퍼 `_runs_urobot`.
- 모든 POST는 기존대로 CSRF + HTTP Basic 게이트 안에서 동작하고, 제어 결과는 `_audit`로 로깅한다.
- 기존 코드 스타일을 따른다: f-string + `esc()` 단일 escape 헬퍼, 순수 렌더 함수.

**git 전략 (워크트리에 무관한 WIP 공존):**
- 워크트리에는 이 기능과 무관/인접한 미커밋 WIP(MQTT 상태 표시, 도킹 액션, v3 eStop 등)가 `render.py`·`server.py`·`adapter_jibot.py`·테스트·문서에 이미 있다. **건드리거나 커밋하지 않는다.**
- 각 Task 커밋은 `git add -p <file>`로 **내가 추가한 hunk만** 스테이징하고, `git diff --cached`로 내 변경만 들어갔는지 확인한 뒤 커밋한다. `git add <file>` 통째 스테이징 금지.
- 내 추가분은 대부분 새 함수/행/테스트라 별도 hunk로 분리된다. 인접해 한 hunk로 묶이면 `git add -p`의 `s`(split)/`e`(edit)로 내 라인만 고른다.

**테스트 실행 위치:**
- `adaptor/web` 관련 테스트: `cd adaptor` 후 `uv run python -m pytest tests/<file>`.
- 설치 스크립트 회귀 테스트: 리포지토리 루트에서 `python -m pytest tests/<file>`.

---

### Task 1: 모터 상태 표시 (render 전용)

`active_emergency_stop`에서 운영자 친화 모터 라벨을 도출하는 `_motor_label`을 추가하고,
상세 페이지 MQTT live 표에 "모터(추정)" 행과 어댑터 목록 한 줄 요약에 모터 토큰을 추가한다.

**Files:**
- Modify: `adaptor/web/render.py` (`_mqtt_live_table` 부근 `render.py:76-103`, `adapter_list_page` `render.py:53-73`)
- Test: `adaptor/tests/test_web_render.py`

**Interfaces:**
- Consumes: `StateSnapshot.active_emergency_stop`(`adaptor/core/monitor.py:37`) — 값은 `None` / `"NONE"` / `"AUTOACK"` / 기타 문자열.
- Produces: `render._motor_label(snapshot) -> str` (plain text; 호출부가 `esc()`로 감쌈). 반환값: `"알 수 없음"` / `"정상 (활성)"` / `"정지 (비활성)"` / `f"e-stop: {raw}"`.

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_web_render.py` 끝에 추가. v2(AUTOACK)·v3(MANUAL) 모두 모터-off로 잡히는지,
모터 행이 JIBOT에서만 나오는지 검증:

```python
def test_motor_label_states():
    assert render._motor_label(_Snap(active_emergency_stop=None)) == "알 수 없음"
    assert render._motor_label(_Snap(active_emergency_stop="NONE")) == "정상 (활성)"
    # v2 motor-off (AUTOACK) 와 v3 motor-off (MANUAL) 둘 다 "정지"
    assert render._motor_label(_Snap(active_emergency_stop="AUTOACK")) == "정지 (비활성)"
    assert render._motor_label(_Snap(active_emergency_stop="MANUAL")) == "정지 (비활성)"


def test_detail_shows_motor_row_for_jibot():
    spec = _Spec("jibot", "JIBOT")
    out = render.adapter_detail_page(
        spec, _Metrics(), _Snap(active_emergency_stop="MANUAL"), {}
    )
    assert "모터(추정)" in out
    assert "정지 (비활성)" in out


def test_detail_omits_motor_row_for_non_jibot():
    spec = _Spec("hex", "Hexplorer")
    out = render.adapter_detail_page(spec, _Metrics(), _Snap(), {})
    assert "모터(추정)" not in out


def test_list_summary_includes_motor_for_jibot():
    spec = _Spec("jibot", "JIBOT")
    rows = [(spec, _Metrics(), _Snap(active_emergency_stop="MANUAL"))]
    out = render.adapter_list_page(rows)
    assert "모터" in out
    assert "정지 (비활성)" in out
```

(v3 발행 경로 자체 — `safetyState.activeEmergencyStop` → snapshot — 검증은 Task 1b에서 `extract_state`로 별도 추가.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_render.py::test_motor_label_states -v`
Expected: FAIL — `AttributeError: module 'web.render' has no attribute '_motor_label'`

- [ ] **Step 3: `_runs_urobot` + `_motor_label` 추가**

`adaptor/web/render.py`에 두 헬퍼를 추가한다. `_runs_urobot`은 Task 2에서도 쓰므로 모듈 상단부
(예: `_mqtt_live_table` 위)에 둔다. (워크트리 WIP로 `_is_emergency_stop_active`는 이미
`_manual_test_block_reason`으로 바뀌어 있을 수 있으니, 라인 번호가 아니라 함수 위치 기준으로 삽입.)

```python
def _runs_urobot(spec) -> bool:
    """JIBOT spec(key 'jibot' 또는 'jibot:<robot>')만 vendor urobot 스택을 구동한다."""
    key = getattr(spec, "key", "") or ""
    return key == "jibot" or key.startswith("jibot:")


def _motor_label(snapshot) -> str:
    """eStop에서 도출한 운영자 친화 모터 전원 뷰(직접 센서값 아님, JIBOT 전용).

    JIBOT 어댑터는 모터 전원 off(_motor_flag==0)일 때만 비-NONE eStop을 발행한다
    (v2: AUTOACK, v3: MANUAL). 그래서 NONE만 '정상', 그 외 비-NONE은 모두 '정지'로 본다.
    """
    raw = getattr(snapshot, "active_emergency_stop", None)
    if raw is None or str(raw) == "":
        return "알 수 없음"
    if str(raw).upper() == "NONE":
        return "정상 (활성)"
    return "정지 (비활성)"
```

- [ ] **Step 4: 상세 표 행 추가 (JIBOT 게이트)**

`_mqtt_live_table`의 "emergency stop" 행 **바로 위**에, JIBOT일 때만 모터 행을 넣는다.
(`s = snapshot`, `spec`은 같은 함수에서 바인딩됨.) 해당 행 문자열을 다음으로 교체/삽입:

```python
        f"{('<tr><td>모터(추정)</td><td>' + esc(_motor_label(s)) + '</td></tr>') if _runs_urobot(spec) else ''}"
        f"<tr><td>emergency stop</td><td>{esc(getattr(s, 'active_emergency_stop', None))}</td></tr>"
```

- [ ] **Step 5: 목록 요약 토큰 추가 (JIBOT 게이트)**

`adapter_list_page`의 `live` 문자열을 다음으로 교체(JIBOT spec에만 모터 토큰 추가):

```python
            live = (
                f"{esc(snap.connection_state)} / {esc(snap.operating_mode)} / "
                f"{esc(snap.battery_soc)}% / pos ({esc(snap.x)}, {esc(snap.y)}, {esc(snap.theta)}) / err {errc}"
            )
            if _runs_urobot(spec):
                live += f" / 모터 {esc(_motor_label(snap))}"
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_render.py -v`
Expected: PASS (신규 4개 포함 전체 그린)

- [ ] **Step 7: 내 hunk만 선택 커밋**

```bash
# 내가 추가한 hunk만 스테이징 (WIP hunk는 건너뛴다)
git add -p adaptor/web/render.py adaptor/tests/test_web_render.py
git diff --cached            # 내 변경만 들어갔는지 확인 (motor/_runs_urobot 만)
git commit -m "feat(webui): show inferred motor state (JIBOT) on detail page and adapter list"
```

---

### Task 2: urobot 재시작 폼 + JIBOT 게이트 (render 전용)

`urobot.service` 재시작 폼과 JIBOT 판정 헬퍼를 추가하고, `_control_forms`가 JIBOT spec에서만
(그리고 활성화됐을 때만) urobot 섹션을 렌더하도록 한다. server 와이어링은 Task 3에서 한다 —
이 시점엔 `urobot_restart_enabled` 기본값 `False`라 화면 변화는 없다(테스트로만 검증).

**Files:**
- Modify: `adaptor/web/render.py` (`_host_reboot_form` 부근 `render.py:277-303`, `adapter_detail_page` `render.py:172-199`, `control_page` `render.py:306-315`)
- Test: `adaptor/tests/test_web_render.py`

**Interfaces:**
- Produces:
  - `render._runs_urobot(spec) -> bool` — `spec.key == "jibot"` 또는 `spec.key.startswith("jibot:")`.
  - `render._urobot_restart_form(spec_key: str, csrf: str, return_to: str = "") -> str`.
  - `render._control_forms(..., urobot_restart_enabled: bool = False)` 시그니처 확장.
  - `render.adapter_detail_page(..., urobot_restart_enabled: bool = False)` 및 `render.control_page(..., urobot_restart_enabled: bool = False)` 확장.

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_web_render.py`에 추가:

```python
def test_runs_urobot_only_for_jibot_specs():
    assert render._runs_urobot(_Spec("jibot", "JIBOT")) is True
    assert render._runs_urobot(_Spec("jibot:ROBOT-A", "JIBOT A")) is True
    assert render._runs_urobot(_Spec("hex", "Hexplorer")) is False


def test_control_forms_shows_urobot_for_jibot_when_enabled():
    out = render._control_forms(
        _Spec("jibot", "JIBOT"), "tok", urobot_restart_enabled=True
    )
    assert "restart urobot" in out
    assert 'action="/adapter/jibot/urobot/restart"' in out
    assert "confirm" in out


def test_control_forms_hides_urobot_for_non_jibot():
    out = render._control_forms(
        _Spec("hex", "Hexplorer"), "tok", urobot_restart_enabled=True
    )
    assert "restart urobot" not in out


def test_control_forms_hides_urobot_when_disabled():
    out = render._control_forms(
        _Spec("jibot", "JIBOT"), "tok", urobot_restart_enabled=False
    )
    assert "restart urobot" not in out


def test_detail_renders_urobot_button_when_enabled():
    out = render.adapter_detail_page(
        _Spec("jibot", "JIBOT"), _Metrics(), _Snap(), {}, csrf="tok",
        urobot_restart_enabled=True,
    )
    assert "restart urobot" in out
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_render.py::test_runs_urobot_only_for_jibot_specs -v`
Expected: FAIL — `AttributeError: module 'web.render' has no attribute '_runs_urobot'`

- [ ] **Step 3: `_urobot_restart_form` 추가**

(`_runs_urobot`은 Task 1 Step 3에서 이미 추가됨 — 재정의하지 않는다.)
`adaptor/web/render.py`의 `_host_reboot_form` 바로 아래에 추가:

```python
def _urobot_restart_form(spec_key: str, csrf: str, return_to: str = "") -> str:
    return (
        f'<form class="btn" method="post" action="/adapter/{esc(spec_key)}/urobot/restart">'
        f"{_csrf_field(csrf)}{_return_to_field(return_to)}"
        '<label><input type="checkbox" name="confirm"> confirm</label> '
        "<button>restart urobot</button>"
        ' <span class="err">nav/perception 스택 재기동 — 로봇 정지 시에만</span></form>'
    )
```

- [ ] **Step 4: `_control_forms` 확장**

`_control_forms`(`render.py:286-303`)를 다음으로 교체:

```python
def _control_forms(
    spec,
    csrf: str,
    host_reboot_enabled: bool = False,
    return_to: str = "",
    urobot_restart_enabled: bool = False,
) -> str:
    verbs = "".join(_verb_form(spec.key, v, csrf, return_to=return_to) for v in _VERBS)
    actions = "".join(_action_form(spec.key, a, csrf, return_to=return_to) for a in spec.instant_actions)
    host = (
        f"<h4>host</h4>{_host_reboot_form(spec.key, csrf, return_to=return_to)}"
        if host_reboot_enabled
        else ""
    )
    urobot = (
        f"<h4>robot (urobot)</h4>{_urobot_restart_form(spec.key, csrf, return_to=return_to)}"
        if urobot_restart_enabled and _runs_urobot(spec)
        else ""
    )
    return (
        f"<h4>service</h4>{verbs}"
        f"<h4>actions</h4>{actions or '(none)'}"
        f"{host}"
        f"{urobot}"
    )
```

- [ ] **Step 5: `adapter_detail_page` 시그니처·호출 확장**

`adapter_detail_page`(`render.py:172-181`) 시그니처에 파라미터 추가:

```python
def adapter_detail_page(
    spec,
    metrics,
    snapshot,
    q: dict,
    csrf: str = "",
    runner=None,
    video_url: str = "",
    host_reboot_enabled: bool = False,
    urobot_restart_enabled: bool = False,
) -> str:
```

그리고 본문의 `_control_forms` 호출(`render.py:197`)을 교체:

```python
        f"<h3>control</h3>{_control_forms(spec, csrf, host_reboot_enabled, return_to='dashboard', urobot_restart_enabled=urobot_restart_enabled)}"
```

- [ ] **Step 6: `control_page` 시그니처·호출 확장**

`control_page`(`render.py:306`) 시그니처에 파라미터 추가:

```python
def control_page(spec, metrics, csrf: str, q: dict, snapshot=None, host_reboot_enabled: bool = False, urobot_restart_enabled: bool = False) -> str:
```

본문의 `_control_forms` 호출(`render.py:311`)을 교체:

```python
        f"{_control_forms(spec, csrf, host_reboot_enabled, urobot_restart_enabled=urobot_restart_enabled)}"
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_render.py -v`
Expected: PASS (전체 그린)

- [ ] **Step 8: 커밋**

```bash
git add adaptor/web/render.py adaptor/tests/test_web_render.py
git commit -m "feat(webui): add urobot restart form gated to JIBOT specs (render layer)"
```

---

### Task 3: server/main 와이어링 — urobot 컨트롤러·라우트·핸들러

`urobot.service` 컨트롤러를 `WebUi`에 주입하고, GET에서 게이트 플래그를 넘기고,
`/adapter/<key>/urobot/restart` POST 라우트와 `_post_urobot_restart` 핸들러를 추가한다.

**Files:**
- Modify: `adaptor/web/server.py` (`__init__` `server.py:68-79`, GET detail/control `server.py:319-350`, `_dispatch_post` `server.py:377-401`, `_post_host_reboot` 인근 `server.py:454-471`)
- Modify: `adaptor/web/main.py` (`WebUi(...)` 생성 `main.py:64-72`)
- Test: `adaptor/tests/test_web_server.py`

**Interfaces:**
- Consumes: `render._runs_urobot`(Task 2), `SystemdController.restart() -> (bool, str)`(`adaptor/core/systemd.py:309`).
- Produces: `WebUi(..., urobot_controller=None)` 파라미터; POST 엔드포인트 `/adapter/<key>/urobot/restart`.

- [ ] **Step 1: 실패하는 테스트 작성**

`adaptor/tests/test_web_server.py`에 추가(파일 상단의 `FakeController`·`_post`·`ui` 픽스처 재사용):

```python
def test_urobot_restart_requires_confirm():
    spec = FakeSpec("jibot", "JIBOT")
    urobot = FakeController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=urobot,
    )
    web.start()
    try:
        _post(web, "/adapter/jibot/urobot/restart", {"csrf_token": web._csrf})
        assert "restart" not in urobot.calls  # confirm 없음 -> 미호출
        _post(web, "/adapter/jibot/urobot/restart",
              {"csrf_token": web._csrf, "confirm": "on"})
        assert "restart" in urobot.calls       # confirm -> 호출
    finally:
        web.stop()


def test_urobot_restart_rejected_for_non_jibot_spec():
    spec = FakeSpec("hex", "Hexplorer")
    urobot = FakeController()
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"hex": FakeController()},
        monitors={"hex": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=urobot,
    )
    web.start()
    try:
        _post(web, "/adapter/hex/urobot/restart",
              {"csrf_token": web._csrf, "confirm": "on"})
        assert "restart" not in urobot.calls   # 비-jibot -> 거부
    finally:
        web.stop()


def test_detail_renders_urobot_button_when_controller_configured():
    spec = FakeSpec("jibot", "JIBOT")
    cred = WebUiCredentials("admin", "a-strong-pass-123")
    web = WebUi(
        specs=[spec], controllers={"jibot": FakeController()},
        monitors={"jibot": FakeMonitor()},
        credentials=cred, host="127.0.0.1", port=0,
        urobot_controller=FakeController(),
    )
    web.start()
    try:
        body = _get(web, "/adapter/jibot").read().decode()
        assert "restart urobot" in body
        assert 'action="/adapter/jibot/urobot/restart"' in body
    finally:
        web.stop()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_server.py::test_urobot_restart_requires_confirm -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'urobot_controller'`

- [ ] **Step 3: `__init__`에 컨트롤러 파라미터 추가**

`server.py`의 `WebUi.__init__` 시그니처(`server.py:69-72`) 끝에 파라미터를 추가하고, 본문에 저장한다.

시그니처 마지막 줄(`server.py:72`)을 교체:

```python
                 camera_controller=None, camera_config=None, host_controller=None,
                 urobot_controller=None):
```

`self._host_controller = host_controller`(`server.py:79`) 바로 아래에 추가:

```python
        self._urobot_controller = urobot_controller
```

- [ ] **Step 4: GET detail/control에 게이트 플래그 전달**

`_dispatch_get`의 detail 분기 `render.adapter_detail_page(...)` 호출(`server.py:329`,
`host_reboot_enabled=...` 줄) 다음에 인자 추가:

```python
                    host_reboot_enabled=self._host_controller is not None,
                    urobot_restart_enabled=self._urobot_controller is not None,
```

control 분기 `render.control_page(...)` 호출(`server.py:348`, `host_reboot_enabled=...` 줄) 다음에 인자 추가:

```python
                    host_reboot_enabled=self._host_controller is not None,
                    urobot_restart_enabled=self._urobot_controller is not None,
```

- [ ] **Step 5: POST 라우트 추가**

`_dispatch_post`(`server.py:377`)의 host-reboot 분기(`server.py:386-388`) **바로 아래**에 추가:

```python
        if len(parts) == 4 and parts[0] == "adapter" and parts[2] == "urobot" and parts[3] == "restart":
            self._post_urobot_restart(h, parts[1], form)
            return
```

- [ ] **Step 6: `_post_urobot_restart` 핸들러 추가**

`_post_host_reboot`(`server.py:454-471`) **바로 아래**에 추가:

```python
    def _post_urobot_restart(self, h, key: str, form: dict):
        spec = self._specs.get(key)
        if spec is None:
            h._html(404, render.page("not found", "<p>unknown adapter</p>"))
            return
        target = self._adapter_post_target(key, "control", form)
        if self._urobot_controller is None or not render._runs_urobot(spec):
            self._audit(h, key, "urobot:restart", "rejected:not-available")
            h._redirect(f"{target}?err={urllib.parse.quote('urobot restart is not available')}")
            return
        if not _confirmed(form):
            self._audit(h, key, "urobot:restart", "rejected:unconfirmed")
            h._redirect(f"{target}?err={urllib.parse.quote('confirm required')}")
            return
        ok, msg = self._urobot_controller.restart()
        self._audit(h, key, "urobot:restart", f"ok={ok} {msg}")
        flash = "msg" if ok else "err"
        h._redirect(f"{target}?{flash}={urllib.parse.quote(msg)}")
```

- [ ] **Step 7: main.py에서 컨트롤러 주입**

`adaptor/web/main.py`의 `WebUi(...)` 생성(`main.py:64-72`) 마지막 인자 줄(`host_controller=HostController(use_sudo=False)`)을 교체:

```python
                camera_config=video, host_controller=HostController(use_sudo=False),
                urobot_controller=SystemdController("urobot.service", use_sudo=False))
```

(`SystemdController`는 `main.py`에서 이미 import·사용 중 — `main.py:51,71`.)

- [ ] **Step 8: 테스트 통과 확인**

Run: `cd adaptor && uv run python -m pytest tests/test_web_server.py -v`
Expected: PASS (신규 3개 포함 전체 그린)

- [ ] **Step 9: 커밋**

```bash
git add adaptor/web/server.py adaptor/web/main.py adaptor/tests/test_web_server.py
git commit -m "feat(webui): wire urobot.service restart route, handler, controller"
```

---

### Task 4: polkit — `urobot.service` per-unit restart 허용 + 회귀 테스트

설치 스크립트가 렌더하는 polkit managed-units에 `urobot.service`를 추가(DO_JIBOT 게이트)하고,
이를 검증하는 기존 회귀 테스트에 assert를 더한다.

**Files:**
- Modify: `scripts/setup-adaptor-service.sh` (`render_webui_polkit` `:223-249`)
- Test: `tests/test_update_jibot_adapter_over_ssh.py` (`:171`)

**Interfaces:**
- Consumes: 없음(셸 텍스트 렌더).
- Produces: 렌더된 `__MANAGED_UNITS__`에 `"urobot.service"` 포함(DO_JIBOT일 때).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_update_jibot_adapter_over_ssh.py`의
`test_setup_script_renders_webui_polkit_units_from_selected_targets`(`:171-178`)에 assert 한 줄 추가:

```python
    assert '[[ $DO_JIBOT -eq 1 ]] && polkit_units+=("jibot-adapter.service")' in text
    assert '[[ $DO_JIBOT -eq 1 ]] && polkit_units+=("urobot.service")' in text
    assert '[[ $DO_CAMERA -eq 1 ]] && polkit_units+=("web_video_server.service")' in text
    assert '[[ $DO_HEXPLORER -eq 1 ]] && polkit_units+=("hexplorer-adapter.service")' in text
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py::test_setup_script_renders_webui_polkit_units_from_selected_targets -v`
Expected: FAIL — `assert '...urobot.service...' in text`

- [ ] **Step 3: 설치 스크립트에 urobot 유닛 추가**

`scripts/setup-adaptor-service.sh`의 `render_webui_polkit()`(`:224-227`)에서 jibot-adapter 줄 다음에 추가:

```bash
  local polkit_units=()
  [[ $DO_JIBOT -eq 1 ]] && polkit_units+=("jibot-adapter.service")
  [[ $DO_JIBOT -eq 1 ]] && polkit_units+=("urobot.service")
  [[ $DO_HEXPLORER -eq 1 ]] && polkit_units+=("hexplorer-adapter.service")
  [[ $DO_CAMERA -eq 1 ]] && polkit_units+=("web_video_server.service")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -v`
Expected: PASS (전체 그린)

- [ ] **Step 5: 커밋**

```bash
git add scripts/setup-adaptor-service.sh tests/test_update_jibot_adapter_over_ssh.py
git commit -m "feat(webui): grant polkit restart for urobot.service on JIBOT hosts"
```

---

### Task 5: 운영 문서 갱신 (`docs/guide/web-ui.md`)

모터 행과 urobot 제어, polkit 권한 확대를 운영 매뉴얼에 반영한다.

**Files:**
- Modify: `docs/guide/web-ui.md` (§5 화면 `:89-109`, §6 polkit `:111-134`)

**Interfaces:** 없음(문서).

- [ ] **Step 1: §5 라이브(VDA5050) 항목에 모터 행 명시**

`docs/guide/web-ui.md`의 "라이브(VDA5050)" 줄(`:92`)을 교체:

```markdown
- **라이브(VDA5050)**: 연결, 운전모드, **모터(추정: eStop 기반)**, 배터리, 위치(x/y/θ), localization, 오더 진행, 에러.
  (MQTT 모니터가 없는 어댑터는 라이브 영역이 생략된다.)
  > "모터(추정)"는 직접 센서값이 아니라 VDA5050 `safetyState.eStop`에서 도출한다.
  > JIBOT은 모터 전원 off일 때 `eStop=AUTOACK`을 보내므로 그때만 "정지(비활성)"로 표시한다.
```

- [ ] **Step 2: §5 제어 항목에 urobot 재시작 추가**

"제어 ( `/adapter/<key>/control` )" 절의 **Host** 항목(`:101`) 다음에 한 줄 추가:

```markdown
- **Robot(urobot)**: JIBOT 어댑터 페이지에 한해 `urobot.service` **재시작** 버튼을 제공한다.
  nav/perception 스택 전체를 잠깐 내리므로 **확인 체크박스 필수**이며, 로봇이 정지 상태일 때만 사용한다.
  (모니터/상세 페이지의 control 섹션에 노출된다.)
```

- [ ] **Step 3: §6 polkit 권한 범위에 urobot 명시**

"권한 범위:" 목록(`:128-132`)의 `start`/`stop`/`restart` 항목(`:129`)을 교체:

```markdown
- `start` / `stop` / `restart` → **대상 어댑터 유닛 + (JIBOT 호스트) `urobot.service`에 한해** 허용(per-unit 최소권한).
  → 이로써 WebUi 실행 사용자는 `urobot.service`의 start/stop/restart 권한을 갖는다(UI는 restart만 노출).
```

- [ ] **Step 4: 커밋**

```bash
git add docs/guide/web-ui.md
git commit -m "docs(webui): document motor row, urobot restart control, polkit grant"
```

---

## 최종 검증 (모든 Task 후)

- [ ] **adaptor 테스트 전체 그린**

Run: `cd adaptor && uv run python -m pytest tests/test_web_render.py tests/test_web_server.py tests/test_web_main.py -v`
Expected: PASS (회귀 없음)

- [ ] **설치 스크립트 테스트 전체 그린**

Run: `python -m pytest tests/test_update_jibot_adapter_over_ssh.py -v`
Expected: PASS

- [ ] **(선택) 실기 확인** — 로봇 정지 상태에서 WebUi `/adapter/jibot` 진입 → "모터(추정)" 행과 "restart urobot" 버튼 확인, 확인 체크 후 재시작 → flash 결과 확인. polkit 미설치 시 "restart failed" flash가 뜨는지 확인.

## Self-Review 결과

- **Spec 커버리지:** 모터 행(Task 1)·목록 요약(Task 1)·urobot 재시작 폼/게이트(Task 2)·라우트·핸들러·main 주입(Task 3)·polkit+회귀 테스트(Task 4)·문서(Task 5) — 스펙 §변경1/§변경2/§2d/§테스트/§변경 파일 요약 모두 대응.
- **플레이스홀더:** 없음(모든 코드/명령/기대 출력 명시). Task 1 Step 3의 오타 유발 코드는 Step 3b 정정본으로 대체하도록 명시.
- **타입 일관성:** `_motor_label`(Task 1)·`_runs_urobot`/`_urobot_restart_form`/`_control_forms`(Task 2)·`urobot_controller`/`_post_urobot_restart`(Task 3) 시그니처가 후속 Task의 호출과 일치. 게이트는 전 구간 `spec.key` 기준으로 통일.
