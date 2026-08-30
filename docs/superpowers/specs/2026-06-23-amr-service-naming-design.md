# systemd 서비스 명칭 통일 설계

- 날짜: 2026-06-23
- 브랜치: `jibot-client-refactor`
- 상태: 승인됨 (구현 계획 대기)

## 1. 배경 / 문제

systemctl로 제어하는 서비스 이름에 통일성이 없다.

| 현재 이름 | 역할 | 명명 패턴 |
|---|---|---|
| `jibot-adapter.service` (+ `jibot-adapter@<robot-id>.service`) | JIBOT VDA5050 어댑터 | `<벤더>-adapter` |
| `hexplorer-adapter.service` | Dobot Hexplorer VDA5050 어댑터 | `<벤더>-adapter` |
| `amr-webui.service` | 웹 대시보드 | `amr-` 접두 |
| `web_video_server.service` | 카메라 스트림 (ROS web_video_server) | 업스트림 패키지명(언더스코어) |

비일관성:
1. 어댑터는 `<벤더>-adapter`, 웹UI는 `amr-webui`로 접두 규칙이 다름.
2. `web_video_server`만 언더스코어.
3. 문서/스크립트에서 `.service` 접미사 표기가 들쭉날쭉(`systemctl enable --now amr-webui` 등).

범위 밖(이름 변경 안 함):
- `edge-agent.service` / `edge-agent@<id>.service` — 별도 Dobot 제품(`dobot/edge-agent`), VDA5050 어댑터가 아님.
- `urobot.service` — JIBOT 온보드 벤더 서비스.
- `polkit` / `polkitd` — 시스템 서비스.

## 2. 목표

1. systemd 유닛 이름을 `amr-*` 네임스페이스로 통일한다.
2. **기본 서비스명에서 벤더 이름을 뺀다** → `amr-adaptor.service`.
3. 한 호스트에 어댑터가 여러 개일 때만 `config.toml`에 적힌 이름으로 인스턴스 서비스(`amr-adaptor@<name>.service`)를 만든다.
4. 기존 파이썬 엔트리포인트(`main.py`, `main_hexplorer.py`)는 건드리지 않는다(리스크 최소화).
5. 이미 배포된 호스트에서 구 유닛이 신 유닛과 공존하지 않도록 마이그레이션한다.

비목표(YAGNI):
- 파이썬 엔트리포인트 병합 안 함.
- `edge-agent` 리네임 안 함.
- `web_video_server` ROS 패키지/바이너리 이름 변경 안 함(유닛명만 변경).

## 3. 최종 유닛 이름

| 현재 | 변경 후 |
|---|---|
| `jibot-adapter.service` / `hexplorer-adapter.service` | `amr-adaptor.service` (기본, 벤더 없음) |
| `jibot-adapter@<robot-id>.service` | `amr-adaptor@<name>.service` (멀티 인스턴스) |
| `web_video_server.service` | `amr-camera.service` (유닛명만) |
| `amr-webui.service` | 변경 없음 |
| `edge-agent.service` / `@<id>.service` | 변경 없음 |
| `urobot.service` | 변경 없음 |

## 4. 아키텍처

### 4.1 벤더 디스패치 (쉘 디스패처)

신규 `adaptor/run-adapter.sh`가 모든 어댑터 유닛의 `ExecStart`가 된다. 책임:

1. 인자에서 인스턴스 이름을 받는다(`--instance <name>`; 템플릿 유닛에서 `%i`).
2. 인스턴스 → config 해석:
   - 인스턴스 없음(기본 `amr-adaptor.service`): `config/config.toml`을 읽고 `[adapter].vendor`(기본 `"jibot"`).
   - 인스턴스 `<name>`: `config.toml`의 `[[adapter]]` 중 `name == <name>` 항목을 찾아 그 `vendor`/`config`를 사용.
   - `[[adapter]]`에 없으면 `<name>`을 robots.toml의 robot-id로 간주(=jibot fleet 폴백).
3. `vendor` 값에 따라 분기:
   - `"jibot"` → 기존 `run-main.sh`를 exec(필요 시 `--robot <name>` 전달).
   - `"hexplorer"` → 기존 `run-hexplorer.sh`를 exec.
4. venv 활성화는 기존 `run-main.sh`/`run-hexplorer.sh`가 이미 처리하므로 그대로 재사용한다.

> 디스패처는 벤더 선택과 인스턴스→config 해석만 담당한다. 어댑터 실행 로직은 기존 스크립트/파이썬에 그대로 남는다. 입력은 인스턴스 이름, 출력은 올바른 `run-*.sh`의 exec.

### 4.2 systemd 유닛

- `scripts/systemd/amr-adaptor.service` — 기본 단일 어댑터.
  `ExecStart=__WORKDIR__/run-adapter.sh`
- `scripts/systemd/amr-adaptor@.service` — 인스턴스 템플릿.
  `ExecStart=__WORKDIR__/run-adapter.sh --instance %i`
  Description은 `AMR adapter (instance %i)`.
- 기존 `jibot-adapter.service` / `jibot-adapter@.service` / `hexplorer-adapter.service` 파일은 삭제한다.
- 두 유닛 모두 기존 jibot 유닛의 graceful-shutdown 설정(`KillSignal=SIGINT`, `TimeoutStopSec=15`)과 `CPUAccounting=yes`(웹UI CPU% 표시용)를 유지한다.

### 4.3 config 스키마

`config/config.toml`:

```toml
[adapter]
vendor = "jibot"        # 기본 단일 어댑터 벤더: "jibot" | "hexplorer", 기본값 "jibot"

# 한 호스트에 어댑터가 여러 개일 때만 사용. 각 항목이 amr-adaptor@<name>.service 가 된다.
# TOML상 [adapter] 테이블과 [[adapter]] 배열은 같은 이름이라 충돌하므로,
# 인스턴스는 [adapter] 하위의 [[adapter.instances]] 배열로 둔다.
[[adapter.instances]]
name   = "line1"
vendor = "hexplorer"
config = "config/line1.toml"   # (선택) 인스턴스 전용 config 경로
```

- `[adapter].vendor` 누락 시 `"jibot"`으로 간주(하위 호환).
- `[[adapter]]`가 없으면 기존 `robots.toml`(JIBOT fleet) 동작을 유지한다.

### 4.4 인스턴스 결정 우선순위 (setup + 디스패처 공통)

1. `config.toml`에 `[[adapter]]`가 있으면 그 목록이 인스턴스를 정의 → 각 `amr-adaptor@<name>.service`.
2. 없고 `robots.toml`이 있으면 기존 JIBOT fleet 규칙:
   - 1대 → `amr-adaptor.service`
   - 2대 이상 → robot-id마다 `amr-adaptor@<robot-id>.service`
3. 둘 다 없으면 → `amr-adaptor.service` 하나(벤더는 `[adapter].vendor`).

## 5. 컴포넌트별 변경

### 5.1 `scripts/setup-adaptor-service.sh`
- 플래그 재정의: `--jibot`/`--hexplorer`는 기본 단일 어댑터의 벤더를 정해 `config/config.toml`의 `[adapter].vendor`에 기록한다. `--all`은 제거한다 — 다벤더 공존은 이제 `config.toml`의 `[[adapter]]` 목록으로 표현한다.
- `install_unit "amr-adaptor.service"` + `install_unit "amr-adaptor@.service"` 설치.
- enable 로직(섹션 4.4 우선순위)에 맞춰 단일/인스턴스 enable.
- 카메라: `install_web_video_server`가 `/etc/systemd/system/amr-camera.service`를 생성(내부 `rosrun web_video_server ...`는 유지). `enable amr-camera.service`.
- sudoers(`/etc/sudoers.d/adaptor-tui`) 관리 유닛 목록: `amr-adaptor.service`, `amr-adaptor@*.service`.
- polkit(`render_webui_polkit`) 관리 유닛 목록: `amr-adaptor.service`, `urobot.service`, `amr-camera.service`. 템플릿 규칙의 `jibot-adapter@` 접두 검사 → `amr-adaptor@`.
- **마이그레이션 단계 추가**: 구 유닛(`jibot-adapter.service`, `jibot-adapter@*.service`, `hexplorer-adapter.service`, `web_video_server.service`)을 stop → disable → `/etc/systemd/system`에서 rm → `daemon-reload`.

### 5.2 `adaptor/run-adapter.sh` (신규)
- 섹션 4.1 동작.

### 5.3 `adaptor/config/config.py`
- `[adapter].vendor` 및 `[[adapter]]` 목록 파싱 추가. (기존 config 구조에 맞춰 dataclass/accessor 확장)

### 5.4 `adaptor/core/registry.py`
- 유닛명 생성부를 새 규칙으로:
  - 단일 JIBOT 또는 hexplorer 기본 → `amr-adaptor.service`
  - JIBOT fleet(robots.toml 2대 이상) → `amr-adaptor@<robot-id>.service`
  - `[[adapter]]` 인스턴스 → `amr-adaptor@<name>.service`
- `build_registry`는 **config의 실제 구성(vendor + 인스턴스)을 반영**해 spec을 만든다. 기존처럼 jibot+hexplorer를 무조건 동시에 노출하지 않는다(통일 유닛명 `amr-adaptor.service`가 중복될 수 있으므로). edge-agent는 기존대로 `discover_edge_agent_units()`로 노출.

### 5.5 `adaptor/web/main.py`
- `SystemdController("web_video_server.service", ...)` → `SystemdController("amr-camera.service", ...)`.

### 5.6 `scripts/setup-web-video-server-on-onboard.sh`
- 생성 유닛 `web_video_server.service` → `amr-camera.service`.

### 5.7 `adaptor/install-systemd-service.sh`
- 기본 `SERVICE_NAME="jibot-adapter"` → `"amr-adaptor"`. 도움말 텍스트 갱신.

### 5.8 `adaptor/main.py`
- 도움말/주석의 `jibot-adapter@<id>.service`, `jibot-adapter.service` 표현 갱신.

## 6. 테스트

영향 테스트(유닛명 assert 갱신 필요):
- `tests/test_update_jibot_adapter_over_ssh.py` — `jibot-adapter.service`, `urobot.service`, `web_video_server.service`, `hexplorer-adapter.service` assert.
- `adaptor/tests/test_web_server.py` — `unit="jibot-adapter.service"`, `web_video_server.service`.
- `adaptor/tests/test_fleet_registry.py` — `jibot-adapter@<id>.service`, `jibot-adapter.service`.
- `scripts/test-install-systemd-service.sh` — 테스트 파라미터(`test-jibot.service`)는 임의값이라 그대로 둬도 됨.

추가 테스트:
- `run-adapter.sh` 디스패치: vendor=jibot/hexplorer, 인스턴스 해석(`[[adapter]]` 우선 → robots.toml 폴백), 기본값(jibot) 동작.
- setup 마이그레이션(dry-run): 구 유닛 stop/disable/rm 단계가 렌더링되는지.
- setup가 `amr-adaptor`/`amr-adaptor@`/`amr-camera` 유닛을 설치/enable 하는지.

## 7. 마이그레이션

setup 재실행 시(섹션 5.1) 구 유닛을 stop/disable/rm 한 뒤 신 유닛을 설치한다. 구 polkit/sudoers 유닛 목록은 재생성으로 덮어쓴다(`amr-webui` 파일명은 동일하므로 별도 처리 불필요).

## 8. 문서

- `README.md`, `adaptor/readme.md`, `docs/guide/web-ui.md`의 systemctl 예시/표를 새 유닛명으로 갱신.
- config.toml의 `[adapter]` / `[[adapter]]` 사용법 문서화.

## 9. 결정 사항 (기본값)

- 플래그: `--jibot`/`--hexplorer`는 `[adapter].vendor`를 설정, `--all`은 제거(다벤더는 `[[adapter]]`로). (5.1)
- registry: config 실제 구성만 노출. (5.4)
- `[[adapter]]`와 `robots.toml`은 별도 소스로 유지하고 디스패처가 `[[adapter]]` 우선 → robots.toml 폴백으로 해석(4.4). 인스턴스에서 robots.toml robot을 참조하는 `robot = "<id>"` 같은 기능은 현 범위에서 만들지 않는다(YAGNI). 필요해지면 별도 작업으로.
