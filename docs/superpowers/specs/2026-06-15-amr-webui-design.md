# unified-amr-adaptor WebUi — 설계 (Phase 1)

> 작성일: 2026-06-15
> 상태: 설계 rev.2 — 코드 리뷰 반영 완료. 사용자 재검토 후 writing-plans.
> 목적: 현장 운영자가 브라우저로 AMR 어댑터(jibot/hexplorer/멀티인스턴스)를 모니터링·제어한다.
> 방향: **curses TUI(`adaptor/tui/`)를 점진 대체**한다(edge-agent가 WebUi로 간 것과 동일). 이 spec은 그 1단계.

## 1. 배경 / 결정

- **curses TUI 대체(점진).** WebUi가 주 운영 인터페이스가 되고 curses TUI는 이후 폐기한다.
  두 인터페이스를 영구 병행하지 않는다(유지비 이유).
- **edge-agent WebUi 패턴 복제, 코드 공유 안 함.** edge-agent WebUi(`dobot/edge-agent/src/edge_agent/web/`)는
  stdlib `http.server` + Basic 인증 + 단일 프로세스 CSRF 토큰 + 서버렌더 HTML(JS 프레임워크/WebSocket 없음)이다.
  같은 *패턴*을 따르되, repo·도메인이 달라 코드는 공유하지 않는다.
- **TUI 백엔드 재사용.** 조사 결과 TUI의 ~73%(8개 모듈)가 curses 없는 순수 백엔드라 WebUi가 그대로 호출한다.
  WebUi는 사실상 새 HTML view 계층이다.
- **edge-agent는 이 WebUi 대상이 아니다.** edge-agent는 자체 WebUi로 운영하기로 했으므로(별도 결정),
  이 WebUi의 어댑터 목록에서 edge-agent 스펙은 **제외**한다(jibot/hexplorer/멀티인스턴스 jibot만 노출).
  제외 기준: `AdaptorSpec.key`가 `"edge-agent"`이거나 `"edge-agent:"`로 시작하는 스펙
  (registry의 edge-agent 키 규약과 일치).
  **위치:** `build_registry()`는 edge-agent 스펙도 포함해 반환하므로(`registry.py:280` `[*jibot_specs, hexplorer, *edge_agents]`),
  필터는 **WebUi 쪽에서 후처리**로 적용한다(`build_registry`는 수정하지 않음 — TUI와 공유).

## 2. 범위

**Phase 1 (이 spec):**
- Dashboard — 어댑터 목록 + 어댑터별 상세(서비스 상태 + 라이브 VDA5050 상태).
- Control — 서비스 lifecycle verb(start/stop/restart/enable/disable) + MQTT VDA5050 instant action.
- 인증/CSRF, polkit 최소권한 systemctl, `[web_ui]` 설정, `run-web.sh` 런처 + systemd 유닛, 테스트.

**비목표(후속 spec):** Tests(진단 스트리밍), Config(hot-field 편집), Logs(journal tail), curses TUI 폐기.
임의 좌표 조그/파라미터 입력 같은 신규 동작은 범위 밖(WebUi는 기존 TUI 기능의 이식이다).

## 3. 아키텍처

신규 패키지 **`adaptor/web/`** (`adaptor/tui/`와 평행). edge-agent `web/` 구조를 미러:

| 파일 | 역할 |
|---|---|
| `server.py` | `ThreadingHTTPServer` 기반 `WebUi` 클래스. 라우팅 + Basic 인증 + 단일 프로세스 CSRF 토큰 + 모니터/컨트롤러 보유. |
| `render.py` | 서버렌더 HTML(f-string + `esc()` + `_csrf_field()` + `page()`/nav/flash). 외부 자원/JS 없음. |
| `credentials.py` | credential 파일 로드 + 강한 비밀번호 검증(edge-agent 규칙: 12자+, 플레이스홀더 거부). |
| `__main__.py` | 진입점 `python -m web`(=`python -m tui`와 평행). `tui/__main__.py`처럼 **`ADAPTER_ROOT`(=`parents[1]`, `adaptor/`)를 `sys.path`에 삽입**(flat 레이아웃: `config`/`utils`/`protocol` import 해소) 후 config 로드 → `WebUi` 기동. |

**재사용(무수정) 백엔드 모듈:**

| 모듈 | WebUi가 쓰는 것 |
|---|---|
| `tui/registry.py` | `build_registry(config)` → `List[AdaptorSpec]`(edge-agent **포함** 반환; WebUi가 후처리 필터, §1), `AdaptorSpec`, `InstantAction` |
| `tui/monitor.py` | `MqttMonitor`(start/stop/get_snapshot/publish_json), `StateSnapshot` |
| `tui/systemd.py` | `SystemdController`(poll/start/stop/restart/enable/disable). **`SystemdController(spec.unit, use_sudo=False)`로 생성**(생성자 기본은 `use_sudo=True`, `systemd.py:246`; polkit 인가 위해 명시적으로 False), `ServiceMetrics` |
| `tui/control.py` | `build_instant_actions(...)` → VDA5050 instantActions dict |
| `tui/status.py` | 메트릭 → 표시용 상태 합성(순수 로직) |

> 참고: 위 모듈은 현재 `tui/` 패키지에 있다. Phase 1에서는 **`tui/`에서 import해 재사용**한다(이동/리네임 없음).
> curses TUI 폐기(후속)가 확정되면 `tui/`에서 공용 백엔드를 `core/` 같은 패키지로 분리하는 리팩터를 별도로 다룬다.

## 4. 라우트 (Phase 1)

전 라우트 HTTP Basic 인증. 전 POST에 CSRF 토큰 검증.

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/` | 어댑터 목록. 각 행: `display_name`, systemd `active_state`, 라이브 요약(연결/모드/배터리/에러 수). edge-agent 제외. |
| GET | `/adapter/<key>` | Dashboard 상세. `ServiceMetrics`(active/enabled/uptime/cpu%/mem/restarts) + `StateSnapshot`(연결, 운전모드, 배터리 게이지, 위치 x/y/θ, localization, 오더 진행, 에러). `?refresh=<sec>` meta 자동새로고침(기본 5s, `refresh=0`이면 끔). |
| GET | `/adapter/<key>/control` | 서비스 verb 버튼(start/stop/restart/enable/disable) + spec의 instant action 버튼. 파괴적 동작(stop/restart/disable)·motion 액션은 confirm 체크박스 표시. |
| POST | `/adapter/<key>/control` | (CSRF) `SystemdController.<verb>()` 실행. 결과를 flash로 담아 `/adapter/<key>/control`로 리다이렉트(PRG 패턴). |
| POST | `/adapter/<key>/action` | (CSRF) `control.build_instant_actions(...)` → `MqttMonitor.publish_json("instantActions", payload)`. **motion 액션은 confirm 체크박스 필수.** flash 후 리다이렉트. |

알 수 없는 `<key>` → 404. 모니터 미보유 어댑터(monitor_kind=none) → 상세에서 라이브 상태 영역 생략, 서비스 메트릭만 표시.

> `<key>`는 URL 경로 세그먼트로 쓴다. 멀티인스턴스 키(`jibot:robot-1`)의 `:`는 경로에서 허용 문자이나,
> 링크 생성/매칭 시 일관되게 URL-encode/decode 한다.

## 5. 데이터 흐름 / 런타임 상태

- **MqttMonitor 수명:** `WebUi` 기동 시 `monitor_kind="vda5050"`인 어댑터마다 `MqttMonitor`를 하나씩 생성·`start()`하고
  `{adapter_key: MqttMonitor}`로 보관한다(TUI의 데몬 스레드 모델과 동일). 각 요청은 `get_snapshot()`만 읽는다(요청 무상태, 스레드 안전).
- **SystemdController 수명:** 어댑터마다 하나씩 보관한다(`{adapter_key: SystemdController}`). `cpu_percent`는 직전 poll 대비 델타이므로
  컨트롤러가 직전 표본을 유지해야 한다. 새로고침 간격이 길면 cpu%가 거칠 수 있음 — 허용(주: 메모리/uptime은 정확).
- **인증 상태:** credential은 기동 시 1회 로드. CSRF는 프로세스 단일 토큰(`secrets.token_urlsafe(32)`), 모든 POST에서 비교.
- **instant action 페이로드 생성:** `build_instant_actions(*, header_id, timestamp, version, manufacturer, serial_number, action_type, action_id, ...)`는 키워드 인자가 필요하다(`control.py:15`). WebUi가 다음을 생성·공급한다 —
  `header_id`=어댑터별 프로세스 단조 증가 카운터, `timestamp`=요청 시각 ISO8601 UTC, `action_id`=`uuid4`,
  `version`/`manufacturer`/`serial_number`=해당 `AdaptorSpec`의 `vda_full_version`/`manufacturer`/`serial`.

## 6. 보안 모델

- **인증:** HTTP Basic(전 라우트). 실패 시 401 + `WWW-Authenticate`, 실패 IP/경로 로그.
- **CSRF:** 프로세스 단일 토큰, 전 POST 폼 hidden 필드로 검증. 불일치 → 403.
- **바인드:** 기본 `127.0.0.1`. LAN 노출은 `[web_ui].host`를 명시 설정해야만(평문 HTTP는 격리 설비 LAN 전제).
- **systemctl = polkit 최소권한:**
  - `SystemdController(spec.unit, use_sudo=False)`로 생성 → `sudo` 없이 `systemctl <verb> <unit>` 실행, polkit이 인가한다(생성자 기본 `use_sudo=True`이므로 반드시 명시적 False).
  - **polkit action 2종을 분리해 허용**(verb 성격이 다름):
    - start/stop/restart → `org.freedesktop.systemd1.manage-units` — **대상 어댑터 유닛에 한해** 허용(per-unit 스코프).
    - enable/disable → `org.freedesktop.systemd1.manage-unit-files` — systemd가 이 action엔 polkit에 `unit` 디테일을 주지 않아(파일 경로 전달) **per-unit 스코프 불가**. **결정(2026-06-15, "간단·5 verb 전부 동작" 우선): `amr-webui` 서비스 사용자에게 허용**(유닛 무관). per-unit start/stop/restart보다 넓은 권한이나 root 전체 sudo는 아니며 WebUi는 Basic+CSRF+localhost로 보호됨.
    어느 경우든 root 전체 sudo는 부여하지 않는다.
  - **배치:** 기존 systemd 자산이 `scripts/systemd/`(`jibot-adapter.service`, `hexplorer-adapter.service`,
    `jibot-adapter@.service`, 웹서비스 선례 `web_video_server.service`)에 있고 설치는 `scripts/setup-adaptor-service.sh`이다.
    → polkit 규칙은 `scripts/systemd/`에 템플릿으로 두고(예: `scripts/systemd/amr-webui-polkit.rules`),
    설치는 `scripts/setup-adaptor-service.sh`를 확장한다. **`deploy/`는 만들지 않는다**(현 repo 규약 따름).
    규칙의 유닛명은 위 실제 유닛(`jibot-adapter.service` 등, 멀티인스턴스는 `jibot-adapter@*.service`)에 맞춘다.
- **confirm 게이트:** motion instant action + 파괴적 서비스 동작(stop/restart/disable)은 confirm 체크박스 필수
  (`_confirmed(form)` 패턴). 미체크 시 거부 + 안내 flash.
- **audit 로그:** 모든 control/action POST는 서버 로그에 `{시각, client IP, basic-auth 사용자, 어댑터 key, verb/action_type, 결과}`를 남긴다(enable/disable·motion은 사고 범위가 커 추적 필수).
- **credential:** `[web_ui].credentials_path`가 가리키는 별도 파일(username/password). edge-agent와 동일한
  강한 비밀번호 검증(12자+, `set-me`/`admin`/`password`/`changeme` 등 거부). VCS 커밋 금지.

## 7. 설정 / 실행

- **`config.toml`에 `[web_ui]`:** `enabled`(bool), `host`(기본 127.0.0.1), `port`, `credentials_path`.
  `web_ui.enabled=false`(기본)면 WebUi 미기동.
- **런처 `run-web.sh`** (`adaptor/`): 다른 run 스크립트와 동일하게 **`cd "$SCRIPT_DIR"`로 `adaptor/`에서 실행**(flat 레이아웃 전제),
  `.venv` 우선 → venvJIBOT → system python3, `python -m web` exec.
- **systemd 유닛**: `scripts/systemd/`에 둔다(예: `scripts/systemd/amr-webui.service`; `web_video_server.service` 선례와 동일 위치).
  WebUi 자체를 서비스로(어댑터 유닛과 별개). `WorkingDirectory=.../adaptor`, 웹 서비스 사용자 = polkit 규칙 대상. 설치는 `scripts/setup-adaptor-service.sh` 확장.
- 포트는 어댑터/브로커와 분리.

## 8. 에러 처리

- 라우트 디스패치: 알 수 없는 경로 404, CSRF 불일치 403, 인증 실패 401.
- `SystemdController` verb 실패(`(False, msg)`) → flash에 msg, 5xx 아님(PRG 유지).
- `publish_json` 실패(브로커 끊김) → flash 경고.
- 백엔드 예외는 500 + 최소 메시지(스택 노출 금지), 서버 로그에 상세.

## 9. 테스트 (하드웨어/브로커 없음)

edge-agent web 테스트 패턴 미러:
- 라우트 테스트: mock `SystemdController`/`MqttMonitor` 주입, 실 systemctl/MQTT 없이 GET 렌더 + POST 동작/리다이렉트 검증.
- 인증 테스트: 무자격 401, 잘못된 자격 401, 정상 200.
- CSRF 테스트: 토큰 없음/불일치 POST → 403.
- confirm 게이트: motion/파괴적 동작 미확인 → 거부.
- credential 검증: 약한 비밀번호/플레이스홀더 거부.
- render: edge-agent 제외 필터, monitor_kind=none 어댑터의 라이브 영역 생략.
- 실행: `uv run pytest`(uv 환경 사용).

백엔드 8모듈은 기존 테스트를 보유하므로 신규 테스트는 `web/`에 집중한다.

## 10. 단계 / 향후

- **Phase 1 (이 spec):** Dashboard(목록+상세) + Control + 인증/CSRF + polkit + `[web_ui]` 설정 + `run-web.sh` + systemd 유닛 + 테스트.
- **Phase 2:** Tests view — `runner.StreamProcess`로 진단/테스트 subprocess 출력을 브라우저에 스트리밍(폴링 새로고침).
- **Phase 3:** Config view — `tui/configio.py` hot-field 편집(검증 후 저장, 재시작 안내). *주: TUI configio는 `config.toml`용이므로 그대로 재사용.*
- **Phase 4:** Logs view — journal tail. 이후 백엔드 `tui/`→`core/` 분리 리팩터 + **curses TUI 폐기**.

## 11. 리스크 / 미해결

- **polkit 규칙 정확성:** 유닛명 매칭(멀티인스턴스 키 `jibot:robot-N` → 실제 유닛 `jibot-adapter@robot-N.service`)·웹 서비스 사용자 식별을 현장 systemd 설정과 맞춰야 함. 규칙은 템플릿으로 제공하고 설치 시 유닛/사용자 치환. **검증 항목:** start/stop/restart(`manage-units`)와 enable/disable(`manage-unit-files`) 두 action이 각각 인가되는지 현장에서 별도 확인.
- **멀티인스턴스 모니터 수 / fleet 규모:** 어댑터마다 MqttMonitor 스레드 1개 + 브로커 연결 1개. **Phase 1 가정: 동시 모니터링 어댑터 소규모(≈16대 이하).** 실제 현장 fleet 규모(N)를 확인해 N개 연결이 수용 가능한지 검증하고, 초과 시 lazy-start(요청 시 모니터 기동) 또는 연결 풀링으로 후속 최적화.
- **cpu% 정확도:** 새로고침 간격 의존(§5).
- **TUI 백엔드 import 경로:** Phase 1은 `web/`가 `tui/`를 import. `tui/` 폐기 전까지 결합 유지(의도적), Phase 4에서 `core/` 분리.
