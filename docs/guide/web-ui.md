# AMR Adaptor WebUi 운영 매뉴얼

브라우저로 JIBOT/Hexplorer VDA5050 어댑터를 **모니터링·제어**하는 경량 HTML UI.
어댑터 프로세스와 별개로 같은 호스트에서 뜨며, 서버렌더 HTML만 쓴다(JS 프레임워크/WebSocket 없음).

`adaptor/tui/`의 curses TUI([adaptor-tui.md](adaptor-tui.md))와 같은 백엔드(systemd 제어·MQTT 모니터·VDA5050 instant action)를
재사용하되, 터미널 대신 브라우저로 쓴다. **WebUi가 TUI를 점진적으로 대체**한다(현재 Phase 1: Dashboard + Control).

대상: 현장 IPC에서 실행 중인 어댑터를 브라우저로 보고 제어해야 하는 운영자/엔지니어.

## 1. 사전 준비

1. **개발/실행 환경(uv).** `adaptor/`에서:
   ```bash
   cd adaptor
   uv sync           # .venv 생성 + 의존성 설치
   ```
2. **어댑터가 systemd 서비스로 설치돼 있어야** 제어(start/stop/restart 등)가 동작한다
   (`amr-adaptor.service`, 멀티인스턴스 `amr-adaptor@<id>.service`; vendor는 `config.toml [adapter].vendor`로 결정).
   설치: `scripts/setup-adaptor-service.sh` 참고.
3. **통신 모델(JIBOT).** WebUi는 **MQTT 브로커에 의존하지 않는다.** 같은 호스트의
   tmpfs 파일로 JIBOT 어댑터와 직접 통신한다:
   - 상태: 어댑터가 `/run/amr-adaptor/<serial>/state.json`·`health.json`을 쓰고, WebUi가
     렌더 시 읽는다. `state.json`이 오래되면(stale) **"adapter offline/stale"**, 어댑터의
     ACS 브로커 연결 여부는 `health.json`으로 **"ACS broker connected/disconnected"** 로
     **분리 표시**한다.
   - 명령: WebUi가 어댑터의 Unix domain socket `/run/amr-adaptor/<serial>/control.sock`로
     instant action을 보내고 **delivered(접수) 응답**을 받는다. 어댑터가 죽어 있으면 즉시
     "not delivered"(fail-closed — 큐잉/지연 발사 없음). action별 결과는 상세 페이지의
     instant action 행(state의 `instantActionStates`)으로 확인한다.
   - `/run/amr-adaptor` 루트는 `scripts/systemd/tmpfiles.d/amr-adaptor.conf`로 생성된다
     (설치 스크립트가 배포 사용자로 렌더). 어댑터가 `<serial>/` 하위를 런타임에 만든다.
   - **MQTT는 ACS/VDA5050 외부 통신 전용**이다(`[mqtt_broker]`). 비-JIBOT 어댑터
     (hexplorer 등)는 아직 MQTT 모니터를 쓴다.

## 2. 활성화

`adaptor/config/config.toml`에 `[web_ui]` 섹션을 추가한다(기본은 비활성).

```toml
[web_ui]
enabled = true
host = "127.0.0.1"          # LAN 노출이 필요할 때만 0.0.0.0 / 특정 NIC IP
port = 9000
credentials_path = "web-credentials.toml"   # adaptor/ 기준 상대경로 또는 절대경로
```

- `host: 127.0.0.1` — IPC 로컬에서만 접속(기본·권장).
- `host: 0.0.0.0` — 설비 LAN 접속이 필요할 때만. 인터넷/사무망 노출 금지.
- `enabled`가 없거나 `false`면 WebUi는 뜨지 않는다.

`scripts/setup-adaptor-service.sh`로 설치하면 자격증명 파일이 없을 때 기본값(`admin` / `labtomarket1231`)으로 생성된다.
수동 실행하거나 운영 비밀번호를 바꿀 때는 자격증명 파일을 별도로 만든다(TOML, **VCS에 커밋 금지**).

```toml
# adaptor/web-credentials.toml
username = "admin"
password = "12자-이상의-강한-비밀번호"
```

- 비밀번호는 **12자 이상**이어야 한다.
- `set-me` / `changeme` / `password` / `admin` 같은 기본·플레이스홀더 문자열이 포함되면 로드가 거부된다.
- 운영 파일 권한은 제한한다: `chmod 600 adaptor/web-credentials.toml`.

## 3. 기동 및 접속

`adaptor/`에서 실행한다.

```bash
cd adaptor
./run-web.sh                 # .venv → venvJIBOT → system python3 순으로 자동 선택
# 또는
uv run python -m web
```

정상 기동 시 다음과 같은 줄이 출력된다.

```text
web_ui on http://127.0.0.1:9000/
```

브라우저로 접속한다: `http://127.0.0.1:9000/`
원격 PC에서 접속하려면 `[web_ui].host`를 `0.0.0.0`(또는 특정 NIC IP)로 바꾸고 방화벽에서 해당 포트만 허용한다.

systemd 서비스로 상시 띄우려면 `scripts/systemd/amr-webui.service`를 설치한다(아래 §6).

## 4. 로그인

모든 페이지가 HTTP Basic 인증을 요구한다. 브라우저 기본 로그인 창에 `web-credentials.toml`의 `username`/`password`를 입력한다.
인증 실패 시 서버는 `401`을 반환하고 실패 IP·경로를 로그에 남긴다.

## 5. 화면

### 어댑터 목록 ( `/` )

발견된 어댑터(jibot / hexplorer / 멀티인스턴스 `jibot:robot-N`)를 한 줄씩 보여준다.
각 행: 표시 이름(링크), systemd 서비스 상태(`active_state`), 라이브 요약(연결/운전모드/배터리%/에러 수).

> edge-agent는 자체 WebUi로 운영하므로 이 목록에 **표시되지 않는다**.

### 어댑터 상세 ( `/adapter/<key>` )

- **서비스 메트릭**: active/enabled, uptime, CPU%, 메모리, 재시작 횟수.
- **라이브(VDA5050)**: 연결, 운전모드, **모터**, 배터리, 위치(x/y/θ), localization, 오더 진행, 에러.
  (MQTT 모니터가 없는 어댑터는 라이브 영역이 생략된다.)
  > **모터** 행은 **JIBOT 어댑터에서만** 나오고 어댑터 목록 요약에도 같은 토큰이 붙는다. 값의 출처는 둘:
  > - **"모터"(실값)**: 어댑터가 `UmGetMotorState` 폴링으로 실제로 가져온 모터 플래그를 state의
  >   `information.jibotMotorState`(`stopped`/`running`)로 발행한 것 — 권위 있는 값.
  > - **"모터(추정)"**: 위 값이 없을 때의 폴백. VDA5050 `safetyState.eStop`에서 추론한다(JIBOT은 모터
  >   off일 때만 비-NONE eStop(v2 `AUTOACK`, v3 `MANUAL`)을 보냄 → 비-NONE이면 "정지(비활성)").
- 기본 5초 자동 새로고침. `/adapter/<key>?refresh=0`으로 끄거나 `?refresh=<초>`로 간격 변경.

### 진행 ( `/adapter/<key>/progress` )

오더 하나를 **체크리스트로 소진해 나가는 화면**이다. 상세 페이지 우상단 `progress` 버튼으로 들어간다.
데이터는 전부 어댑터가 이미 발행하는 VDA5050 state(`state.json`)에서 오고, 이 화면은 아무것도 지시하지 않는다(읽기 전용).

- **상단 요약**: 클리어한 액션 수 / 전체(진행 막대), 단계(`workingState` + `workingStateDetail`),
  지금 도는 액션(`activeActionType`)과 recipe step(`activeStepActionType`), last node, 위치(x/y/θ, map).
- **오더 액션**: 오더의 **전 액션**이 순서대로 나오고 `FINISHED`는 취소선·회색, 진행 중은 강조, `FAILED`는
  빨간 테두리에 실패 사유가 붙는다. 어댑터가 오더 접수 때 전 액션을 `WAITING`으로 만들어 두고 끝나도
  배열에서 빼지 않으므로, 한 오더가 끝날 때까지 목록이 유지된다. **오더 취소 시에는 목록이 비워진다.**
- **남은 경로**: `nodeStates`/`edgeStates`를 `sequenceId` 순으로 합쳐 보여준다. 어댑터가 **지나온 노드를
  지우므로 "앞으로 갈 곳"만 보인다** — 목록이 위에서부터 줄어드는 것이 곧 주행 진행이다. 지나온 경로를
  회색으로 남기려면 오더 원본을 별도로 보관해야 하고, 지금은 하지 않는다.
- 액션에는 노드 정보(`nodeId`)가 없어 **노드별로 묶어 보여주지 못한다**(VDA5050 `ActionState` 한계).
- 상세 페이지와 같은 `?refresh=<초>` 자동 갱신을 쓴다(0 = 끔).

### 제어 ( `/adapter/<key>/control` )

- **서비스 verb**: `start` / `stop` / `restart` / `enable` / `disable`.
  파괴적 동작(`stop` / `restart` / `disable`)은 **실행 확인 체크박스**가 필요하다.
- **Instant action**: 어댑터 spec에 정의된 VDA5050 instant action 버튼. **motion 액션은 확인 체크박스 필수**.
- **Host**: OS 재부팅 버튼. **확인 체크박스 필수**이며, 원격 장애 복구용으로만 사용한다.
- **Robot(urobot)**: **JIBOT 어댑터 페이지에 한해** `urobot.service` **재시작** 버튼을 제공한다(상세/모니터
  페이지의 control 섹션에 노출). nav/perception 스택 전체를 잠깐 내리므로 **확인 체크박스 필수**이며,
  로봇이 정지 상태일 때만 사용한다. (카메라 스트림이 빈 화면이거나 vendor 스택이 멈췄을 때 복구용.)
- 모든 제어는 POST + CSRF 토큰으로 처리되고, 결과는 플래시 메시지로 표시된다.
- 모든 제어/액션 요청은 서버 로그에 기록된다(시각·접속 IP·로그인 사용자·어댑터·동작·결과).

### 수동 컨트롤 ( `/adapter/<key>/manual` ) — JIBOT 전용

운영자가 로봇을 직접 운전한다. 두 가지 방식:
- **조그(덱맨)**: 화살표키 또는 화면 ▲▼◀▶ 패드를 **누르는 동안** 주행, **떼면 즉시 정지**.
  내부적으로 `manualDrive`(UmDrive 연속 속도) instant action을 ~300ms마다 보내고, 어댑터
  워치독이 명령 단절 시 자동 정지한다. **Arm 토글을 켜야** 동작한다.
- **거리 이동**: distance(±mm)·speed 입력 후 Go → `manualMove`(상대 거리 이동, 자동 정지).

**Stop** 버튼(`manualStop`)은 항상 동작한다. 모터가 OFF면 이동이 무시될 수 있어 **모터 ON**
버튼(`enableMotor`)과 상태 표시를 둔다. **오더/work 진행 중에는 조그/거리이동이 거부**되므로,
**오더 취소**(`cancelOrder`) 버튼으로 먼저 중단한 뒤 수동으로 전환한다.

> 안전: WebUi는 소프트웨어 게이트일 뿐 E-stop/안전 PLC를 대체하지 않는다.

### Actions ( `/adapter/<key>/actions` ) — extension·recipe 전용 화면

**extension과 recipe는 전부 이 화면에서만 실행한다.** 어댑터 상세( `/adapter/<key>` )와
어댑터 목록( `/` )에는 extension·recipe가 나오지 않는다 — 상세 화면은 로봇 자체 제어
(서비스, 긴급정지, 주행, 사운드, Goto)만 담는다. 전역 nav의 **Actions**로 들어가고,
어댑터가 여럿이면 로봇을 먼저 고른다.

- **모듈 그룹**: `extensions.hcl`이 활성화한 모듈마다 그룹 하나(Clamp / PIO / EZIO /
  Facility workflows …). 모듈을 추가해도 WebUi 코드를 고칠 필요가 없다. 화면은 두
  경로 중 하나로 만들어진다:
  - 모듈 패키지에 **`panel.html`이 있으면 그 HTML을 그대로** 쓴다(현재 clamp·PIO).
    UI를 모듈이 소유하는 통로다. WebUi가 채워 주는 값은 `$adapter_key`·
    `$csrf_token`·`$return_to`, 그리고 **`$field_<actionType>_<파라미터>`** 다.
    마지막 것은 그 파라미터의 입력 칸을 액션 스키마에서 만들어 준다 — 선택지가
    설정에서 오는 파라미터(`pioWriteOut`의 `signal`은 `extensions.hcl`의
    `output_signals`, `pioPing`의 `stationId`는 `extension "airshower"`와
    elevator motion rule)를 패널에 박지 않기 위한 것이다. 박아 두면 설정을
    고쳐도 드롭다운이 따라오지 않는다.
  - **없으면 액션 파라미터 스키마로 폼을 생성**한다(현재 EZIO·Facility workflows).
    값이 정해진 파라미터(`pioWriteOut`의 `state` 등)는 select로 뜬다.
  - `panel.html`이 모듈보다 뒤처져 **다루지 않는 액션**은 `<모듈> — 그 외` 그룹에
    생성 폼으로 나온다. 액션이 조용히 사라지지 않게 하기 위한 것이므로, 이 그룹이
    보이면 해당 `panel.html`을 갱신하는 게 맞다.
- **Recipes**: `recipes.hcl`이 정의한 recipe와, 정의에서 뽑은 변수 칸
  (`enterDistanceMm`, `targetMapId` …). 값을 비우면 그 파라미터는 전송되지 않으므로,
  필수 변수를 비우면 해당 step에서 `missing recipe parameter: <이름>`으로 실패한다.
- **EZIO/PIO 라이브 상태**: 해당 모듈 그룹 위에 in8/out8 상태가 붙는다. **어떤 상태
  패널이 뜨는지는 extension 설정이 정한다** — `[[action_modules]]`로 모듈을 끄면 그
  패널도 사라진다. out 배지는 클릭하면 해당 핀을 토글하는데, 그 모듈이 쓰기 액션
  (`pioWriteOut` / `ezioWriteOut`)을 노출할 때만 눌린다. 꺼져 있으면 표시 전용이다.
  라이브 값은 자동 갱신되지 않는다(입력 중인 파라미터가 지워지지 않도록) — 브라우저
  새로고침으로 갱신한다.
- **확인 체크박스**: 설비 출력을 실제로 움직이는 `pioPing`·`pioWriteOut`·`clampMoveTo`는
  체크해야 실행된다. 폼뿐 아니라 서버에서도 막으므로 우회되지 않는다.

extension·recipe **설정 편집**은 nav의 **Extensions**( `/source/extensions.hcl` ) ·
**Recipes**( `/source/recipes.hcl` ) 원문 편집기와 **Config**( `/config` )의 값 단위
폼에서 한다. 블록 추가·삭제와 순서 변경은 원문 편집기에서만 가능하다.

### 카메라 ( `/camera` )

- `amr-camera.service` 상태와 start/stop/restart/enable/disable 제어를 제공한다.
- `web_video_server`가 노출하는 ROS image topic의 stream/snapshot 링크를 보여준다.
- `[video].web_video_server_public_url`이 비어 있으면 Web UI 접속 Host 기준으로 `http://<web-ui-host>:9001`을 자동 사용한다.

## 6. 서비스 제어 권한 (polkit) — 제어를 쓰려면 필요

WebUi는 root 전체 권한 없이 **polkit 최소권한**으로 systemctl을 실행한다. 설치를 실행한 배포 사용자로 동작하며,
polkit 규칙으로 어댑터 유닛에 한해 제어를 허용한다.

설치(현장 1회):

```bash
# scripts/setup-adaptor-service.sh가 adapter/WebUi/camera unit + polkit 인가를 설치한다.
# 유닛명과 polkit 형식은 자동으로 맞춰진다(아래 "polkit 버전 자동 감지" 참고) — 손으로 고칠 필요 없다.
sudo ./scripts/setup-adaptor-service.sh
# WebUi 서비스 기동
sudo systemctl enable --now amr-webui
sudo systemctl start amr-camera.service
```

> **polkit 버전 자동 감지.** JS 규칙(`rules.d/*.rules`)은 polkit **0.106부터** 지원된다. 구형
> jibot 온보드(Ubuntu 16.04 = polkit **0.105**)는 JS 규칙을 **조용히 무시**하므로, setup은
> `pkaction --version`을 보고 형식을 자동 분기한다:
> - **0.106+** → `/etc/polkit-1/rules.d/10-amr-webui.rules` (JS, 유닛별 최소권한)
> - **0.105 이하** → `/etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla` (pklocalauthority)
>
> `.pkla`는 유닛별 스코프가 불가해 systemctl 제어 액션을 WebUi 사용자에게 유닛 무관 허용한다(단일 계정 온보드라 허용). 반대 형식 파일은 충돌 방지를 위해 제거된다.

권한 범위:
- `start` / `stop` / `restart` → **대상 어댑터 유닛 + (JIBOT 호스트) `urobot.service`에 한해** 허용(per-unit 최소권한).
  → 이로써 WebUi 실행 사용자는 `urobot.service`의 start/stop/restart 권한을 갖는다(UI는 restart만 노출).
- `enable` / `disable` → systemd가 polkit에 유닛명을 주지 않아 per-unit 스코프가 불가하여 **WebUi 실행 사용자에게 허용**
  (유닛 무관). root 전체 sudo는 아니며, WebUi는 인증·CSRF·기본 localhost로 보호된다.
- `reboot OS` → `org.freedesktop.login1.reboot*` host-level 권한. WebUi 인증 + CSRF + 확인 체크박스를 거친 경우에만 호출된다.

> polkit 인가를 설치하지 않으면(또는 호스트 polkit 버전과 형식이 안 맞으면) start/stop/restart/enable/disable/reboot이 `Interactive authentication required`로 실패한다(상태 조회·instant action은 영향 없음).

## 7. 보안 경계

- HTTP Basic + 평문 HTTP이므로 **격리된 설비 LAN/localhost에서만** 사용한다.
- 자격증명 1개가 모든 제어 권한을 가진다. 외부 노출이 필요하면 TLS 역프록시 + 방화벽을 별도로 둔다.
- `[web_ui].host` 기본값은 `127.0.0.1`. LAN 노출은 명시적으로 설정해야 한다.
- 모든 POST는 프로세스 단일 CSRF 토큰으로 검증된다.
- credential 파일은 `chmod 600`으로 제한하고 VCS에 커밋하지 않는다.
- WebUi는 소프트웨어 게이트일 뿐, 비상정지·안전 PLC·컨트롤러 안전 기능을 대체하지 않는다.

## 8. 문제 해결

WebUi가 뜨지 않는다:
- `config.toml`에 `[web_ui].enabled = true`가 있는지 확인한다.
- `credentials_path`가 비어 있으면 기동이 거부된다(메시지 출력). 파일 경로가 `adaptor/` 기준인지 확인한다.
- 비밀번호가 12자 미만이거나 플레이스홀더 문자열을 포함하면 로드가 거부된다.
- 포트 충돌 시 `[web_ui].port`를 바꾼다.
- Ubuntu 20.04 / Python 3.8에서 `journalctl -u amr-webui`에
  `ModuleNotFoundError: No module named 'tomllib'`가 나오면 Python 3.8에는 stdlib `tomllib`가 없어
  `tomli` wheel을 오프라인 설치해야 한다. 일반 SSH 업데이트 중에도 의존성 설치가 필요하면
  `--install-py-deps`를 지정해 업로드 후 `offline_packages/`에서 설치한다.

  ```bash
  scripts/update-jibot-adapter-over-ssh.sh --install-py-deps ucore@192.168.3.10
  ```

  이미 온보드 PC에 `offline_packages/`가 있다면 전용 스크립트를 실행한다.

  ```bash
  cd ~/adapter
  scripts/install-offline-python-deps.sh .
  sudo systemctl daemon-reload
  sudo systemctl restart amr-webui
  sudo journalctl -u amr-webui -n 50 --no-pager
  ```

로그인이 계속 실패한다:
- 브라우저가 이전 Basic 인증을 캐시할 수 있다. 새 창/시크릿 창에서 재시도한다.
- `web-credentials.toml`의 `username`/`password`를 확인한다.

라이브 상태가 안 보인다(JIBOT):
- 어댑터가 떠 있고 `state.json`을 쓰는지 확인한다: `cat /run/amr-adaptor/<serial>/state.json`
  (없거나 `updated_at`이 오래됐으면 "adapter offline/stale").
- `/run/amr-adaptor`가 있는지(= tmpfiles 설치, `systemd-tmpfiles --create`), 어댑터/WebUi가
  같은 배포 사용자로 실행 중인지 확인한다.
- "ACS broker disconnected"는 어댑터↔ACS 연결 문제(브로커 `[mqtt_broker]`)이지 WebUi 표시
  자체와는 별개다 — 이 경우에도 로컬 상태/제어는 계속 동작한다.
- (비-JIBOT는 여전히 MQTT 모니터 대상이라 브로커 연결을 확인한다.)

start/stop/restart/enable/disable이 `Interactive authentication required`로 실패한다:
- 호스트 polkit 버전(`pkaction --version`)에 맞는 인가 파일이 설치됐는지 확인한다 — 0.106+는
  `/etc/polkit-1/rules.d/10-amr-webui.rules`, 0.105 이하는
  `/etc/polkit-1/localauthority/50-local.d/10-amr-webui.pkla`. **0.105에 `.rules`만 있으면 무시되어 이 에러가 난다** → `sudo ./scripts/setup-adaptor-service.sh` 재실행(자동 분기)으로 교정.
- 인가가 매인 사용자(`Identity`/`subject.user`)가 WebUi 실행 사용자(`systemctl show amr-webui.service -p User`)와 일치하는지 확인한다.
- 설치 직후 적용이 안 되면 `sudo systemctl restart polkit`(없으면 `polkitd`)로 reload 한다.
- 파괴적 동작(stop/restart/disable)·motion 액션은 확인 체크박스를 체크해야 실행된다.

instant action이 전달되지 않는다:
- JIBOT: 플래시에 "adapter offline — not delivered"면 어댑터가 죽어 있거나 control.sock이
  없는 것이다(`ls /run/amr-adaptor/<serial>/control.sock`). 어댑터를 재시작한다.
- 비-JIBOT: MQTT 브로커 연결을 확인한다(플래시에 "publish failed").

## 9. 향후 (참고)

현재 Dashboard · Control · Tests · Config · Logs · Actions · Progress까지 올라와 있다. 남은 항목이 정리되면 curses TUI를 폐기한다. 설계 문서: `docs/superpowers/specs/2026-06-15-amr-webui-design.md`.
