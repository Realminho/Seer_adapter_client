# clear-order-action-error

### 목표
- `Order action failed: Unsupported order action type: localization2f` 가 clearErrors 후에도 안 사라지는 원인 규명

### 지금
- 사용자 관측: FMS/ACS 화면에서 계속 보임, clearErrors 눌러도 아예 변화 없음
- 어댑터 실제 state.errors 확인이 다음 판별 지점

### 완료
- 에러 생성: `_dispatch_order_action` :4400 fallthrough → ACTION_NOT_FOUND(WARNING)
  + `_set_order_action_failed_error` :4322 ORDER_ACTION_FAILED(FATAL)
- FATAL 있으면 :1327 → detail "FAULT" 보고. 단 order worker 는 계속 진행해 ORDER COMPLETE 도달
- errors 비우는 경로 2개뿐: 신규 order 수락 :3515, clearErrors :7016
- **orderUpdate(`_update_v3_order_for_queue` :3533)는 errors 를 비우지 않음**
- `_merge_v3_action_states`: 기존 actionId 는 재투입 안 함 → 주기적 재삽입 없음
  → 재발은 "새 orderId 로 다시 들어올 때"만 발생
- 모니터 경로 무혐의: state 는 항상 `errors` 직렬화(`_strip_none` 은 None 만 제거),
  monitor.py:198 이 빈 리스트도 반영
- clearErrors 게이트 없음(instant_actions_accept_procedure :5907). 단 blockingType != NONE 이면
  :5830 에서 FAILED + INVALID_INSTANT_ACTION 에러 추가됨. WebUI 는 NONE 고정(core/control.py)
- 근본 원인: `localization2f` 는 오늘 작성한 설계 문서상의 **미구현 recipe**
  (docs/superpowers/specs/2026-08-20-localization-recipe-and-vendor-portability-design.md,
   WORKING/2026-08-20/220257-localization-recipe-design.md)

### 다음
- 사용자 확인 대기: WebUI :9000 errors 카운트
  - 0 → 어댑터는 깨끗함, FMS 자체 알람이라 FMS 에서 ack/clear 필요
  - >0 → clearErrors 미전달. journalctl 에서 `INSTANT ACTIONS RECEIVED ... type=clearErrors` /
    `MQTT RESUBSCRIBE` 확인 (오늘 커밋 2b413ad 배포 여부 포함)
- 실질 해결: localization1f/2f recipe 구현 또는 FMS 가 해당 actionType 미전송

### 검증
- ORDER_ACTION_FAILED 재삽입 경로 없음(호출처 :4315 단일) grep 확인
- orderUpdate 경로에 `state.errors` 대입 없음 grep 확인

---

## 추가 작업: update-jibot-adapter-over-ssh.sh macOS 호환

### 증상
- `line 426: ${CONFIG_TOML_MODE,,}: bad substitution`

### 원인
- `${var,,}` 는 bash 4.0+ 기능. 이 맥의 bash 는 3.2.57 (`/bin/bash`, homebrew bash 없음)
  → `#!/usr/bin/env bash` 가 3.2 를 집음

### 완료
- `lower()` 헬퍼(tr 기반) 추가 :289, `${var,,}` 7곳 전부 치환 (:314 :340 :368 :432 :448 :464 :480)
- 후속 차단 요인 선제 수정: `tar --mtime` 은 GNU tar 전용이라 macOS bsdtar 3.5.3 이 거부
  → `tar --version` 이 "GNU tar" 일 때만 플래그 부착 :1030, 사용부 :1056
  (빈 아카이브 trial-run probe 는 GNU tar 에서도 실패해 Linux 에서 플래그가 조용히 빠짐 → 채택 안 함)
  (bash 3.2 는 `set -u` 에서 빈 배열 전개가 unbound 라 `${arr[@]+"${arr[@]}"}` 사용)

### 검증
- `/bin/bash -n` 문법 통과, `--help` exit 0
- bash 3.2 에서 lower() ASK→ask / Keep→keep / 빈문자열 OK
- 버전 판별: "tar (GNU tar) 1.34"→opts=1, "bsdtar 3.5.3"→opts=0, ""→opts=0
- 로컬 구간에 다른 GNU 전용 도구 없음 확인(md5sum/timeout/stat --/sed -i/readlink -f 등 미사용)

### 남은 것
- 같은 macOS 문제 있는 다른 스크립트(미수정): update-hexplorer-adapter-over-ssh.sh(`,,}`),
  change-jibot-network-over-ssh.sh(mapfile), fetch-hexplorer-maps-over-ssh.sh(mapfile)
- 실행 시 `ucore@10.8.8.8` 형태로 user 지정 권장(현재 경고만 뜨고 진행)

### 배포 시 주의 (사용자 goal 직결)
- `recipes.hcl` / `extensions.hcl` 은 업로드 번들에서 제외되고 별도 모드로 처리됨(:557, :109 기본 keep)
  → 그냥 배포하면 로봇의 옛 recipes.hcl 이 유지되어 localization2f 가 여전히 미등록
- 올바른 실행: `./scripts/update-jibot-adapter-over-ssh.sh ucore@10.8.8.8 --configure-device --restart`
  (`--configure-device` 가 extensions/recipes 둘 다 overwrite, .bak-* 백업 생성)

### 추가 수정 2: ControlPath too long (macOS)
- 증상: `ControlPath too long ('/var/folders/.../tmp.XXXX/<40자 hash>' >= 104 bytes)`
- 원인: macOS TMPDIR 이 `/var/folders/<...>/T/` 라 `mktemp -d` 결과가 이미 길고,
  `%C` 가 40자 → sockaddr_un 104바이트 한계 초과
- 수정: `CONTROL_DIR="$(mktemp -d /tmp/amr-ssh.XXXXXX)"` 로 고정 :261
- 검증: 실제 생성 경로 + `%C` 40자 = 60바이트, 디렉터리 0700

### 배포 옵션 재확인
- `--recipes-hcl-mode overwrite` 만으로는 부족:
  새 recipes.hcl 이 `airShower3lOpen`/`airShower4lOpen` 참조(:454 등) → 이 신호는
  새 extensions.hcl 에만 존재(output_pin_map). 로봇의 옛 extensions.hcl 유지 시
  에어샤워 recipe 가 런타임에 undeclared 로 실패
- 단 부팅은 안 죽음: config/recipes.py 는 구조만 검증하고 signal 존재는 검사 안 함
- localization1f/2f 자체는 `localize` step 만 써서 extensions 불필요
- 권장: `./scripts/update-jibot-adapter-over-ssh.sh ucore@10.8.8.8 --configure-device --restart`
- 검증: `scripts/run-tests.sh tests/test_recipes_config.py` → 22 passed

### 배포 후 실측 검증 (2026-08-20 23:2x)
- 배포 성공. 단 로그상 `[remote] kept existing config/extensions.hcl` → 로봇은 옛 extensions.hcl 유지
- 로봇 상태 재현 테스트: 새 recipes.hcl + `git show HEAD:adaptor/config/extensions.hcl`
  (scratchpad/probe_deploy_state.py)
  - localization1f/2f: steps=['localize'], params 정상, `localize` action spec 등록됨 → **정상 동작 예상**
  - config 로드 자체는 예외 없음 → 어댑터 부팅 영향 없음
  - airShower3lOpen/4lOpen: `ValueError: PIO signal "..." is not declared`
    (선언된 이름은 elevator*만) → **에어샤워 recipe 실행 시 실패**
- tar xattr 경고(`LIBARCHIVE.xattr.com.apple.provenance`)는 무해.
  없애려면 bsdtar 에 `--no-mac-metadata --no-xattrs` (둘 다 지원 확인함) — 미적용
