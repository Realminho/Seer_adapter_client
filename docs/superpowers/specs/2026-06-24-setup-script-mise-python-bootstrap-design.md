# setup-adaptor-service.sh: mise 기반 Python 부트스트랩 (opt-in)

**날짜:** 2026-06-24
**대상 파일:** `scripts/setup-adaptor-service.sh`
**관련 메모:** [[robot-python-310-vs-dev-311]] — 어댑터 venv는 Python ≥3.11 필요, 구형 온보드는 3.10.

## 배경 / 문제

`scripts/setup-adaptor-service.sh`의 `repair_venv`는 어댑터 venv를 만들 때
`uv` 우선 → 없으면 PATH의 `python3.11/3.12/3.13` → stdlib `venv` 순으로 인터프리터를
찾는다. 어댑터는 런타임에 PEP 604 `X | None` union을 평가하므로 Python **≥3.11**이
강제다(`adaptor/pyproject.toml: requires-python = ">=3.11"`).

문제는 fresh 온보드(예: Ubuntu 16.04 / Python 3.10)에는 `python3.11` apt 패키지가
없고 uv도 (오프라인이거나 managed-python 비활성 시) ≥3.11을 공급하지 못한다는 점이다.
이 경우 현재 스크립트는 곧장 에러로 죽고, 운영자는 직접 Python 3.11을 깔아야 한다.

dev 환경은 이미 mise로 인터프리터를 공급한다(`adaptor/mise.toml`:
`python = "3.12"`, `uv = "latest"`, `UV_PYTHON_PREFERENCE = "system"` — uv가 mise/시스템
python을 쓰도록). 같은 메커니즘(mise)을 setup 스크립트에서도 **명시적 요청 시에만**
활용해, 부족한 인터프리터를 자동 공급한다.

## 목표 / 비목표

**목표**
- `--with-mise` 플래그를 추가한다(기본 off).
- 플래그가 켜졌을 때: mise가 없으면 설치하고, mise로 Python(floor 버전)을 설치한 뒤,
  그 인터프리터로 venv를 만든다.
- 플래그가 꺼진 기본 상태에서 Python ≥floor를 못 찾으면, **죽기 전에 명확한 에러와
  `--with-mise` 안내 메시지**를 출력한다.

**비목표 (YAGNI)**
- 정상 호스트(이미 ≥3.11 venv 보유) 동작 변경 — 무변경.
- uv-우선 흐름을 mise-우선으로 바꾸기 — 안 함. mise는 **마지막 폴백 공급자**일 뿐.
- mise를 오프라인/사내미러로 설치 — 안 함. 공식 설치 스크립트(네트워크 필요)만 지원.
- `repair_venv` 외 다른 설치 단계(systemd/polkit/sudoers/camera/tmpfiles) 수정 — 무변경.
- venv 경로/이름 변경 — 그대로 `ADAPTER_DIR/.venv`. mise는 인터프리터만 공급한다.

## 사용자 인터페이스

신규 플래그 한 개. 기존 플래그 파싱 루프(`while ... case "$1"`)와 usage 헤더에 추가한다.

```
--with-mise   Python >= floor 인터프리터를 못 찾을 때 mise로 부트스트랩한다
              (mise가 없으면 공식 스크립트로 설치 후 python@<floor> 설치).
              네트워크 필요. 기본은 비활성 — 미설정 시 인터프리터가 없으면
              에러와 함께 이 옵션을 안내한다.
```

내부 플래그 변수: `WITH_MISE=0`, `--with-mise` 시 `1`.

## 설계

### 신규 헬퍼 (모두 `--with-mise`가 켜졌을 때만 호출)

1. **`run_as_deploy_user <cmd...>`**
   root로 실행 중이면(`id -u == 0`) `sudo -u "$DEPLOY_USER" -H <cmd...>`, 아니면 그대로
   실행한다. mise는 DEPLOY_USER 홈(`~/.local/bin`, `~/.local/share/mise`)에 설치·저장되므로
   항상 DEPLOY_USER 컨텍스트로 돌려야 한다(기존 `find_uv`가 deploy-user 홈을 뒤지는 것과
   같은 이유). `-H`로 HOME을 DEPLOY_USER 홈으로 고정한다.

2. **`find_mise`**
   `find_uv`와 동형. PATH, `~$DEPLOY_USER/.local/bin/mise`, `$HOME/.local/bin/mise`,
   `/usr/local/bin/mise`에서 mise 바이너리를 찾아 경로를 echo, 없으면 nonzero.

3. **`mise_provide_python <min>`** — Python ≥`<min>` 인터프리터 경로를 표준출력으로 반환
   (실패 시 nonzero, 안내성 메시지는 stderr).
   - `DRY_RUN`이면 "would install mise (if missing) and python@<min> via mise"만 출력하고
     성공 반환(경로 echo 없음 — 호출부가 dry-run 분기 처리).
   - `find_mise` 실패 → 공식 설치 스크립트로 mise 설치:
     `run_as_deploy_user sh -c 'curl -fsSL https://mise.run | sh'`.
     `curl` 부재/네트워크 실패 시 명확한 에러 후 nonzero.
   - `run_as_deploy_user env MISE_PYTHON_COMPILE=0 "$mise" install "python@<min>"`
     — `MISE_PYTHON_COMPILE=0`로 **precompiled standalone 빌드**를 강제(gcc/build-essential
     불필요 → 구형 Ubuntu 안전). idempotent.
   - 경로 해석: `run_as_deploy_user "$mise" where "python@<min>"` → `<dir>/bin/python`
     (없으면 `<dir>/bin/python3` 폴백). `python_meets_min`으로 ≥min 재검증 후 echo.

### `repair_venv` 통합 (폴백 티어)

기존 흐름에서 바뀌는 지점은 두 곳뿐. 나머지(early-return, deps 설치, floor 재검증,
레거시 venv 정리)는 그대로 둔다.

공통: 인터프리터를 끝내 못 구했을 때의 에러를 헬퍼로 추출한다.

- **`die_no_python <context-msg>`** — 아래 "에러/안내 메시지"를 stderr로 출력하고 `exit 1`.

1. **uv 분기** (uv 존재)
   `uv venv --python "$min"` → 실패 시 `uv venv`(pyproject 존중) → **그래도 실패 시**:
   - `WITH_MISE == 1`: `py="$(mise_provide_python "$min")"` 후
     `"$uv" venv --python "$py" "$venv"`. 이마저 실패면 `die_no_python`.
   - `WITH_MISE == 0`: `die_no_python "uv could not provide Python >= $min"`.

2. **no-uv 분기** (uv 부재)
   PATH에서 `python$min`/`python3.13`/`python3.12`/`python3.11`을 `python_meets_min`으로
   탐색. 못 찾으면:
   - `WITH_MISE == 1`: `found="$(mise_provide_python "$min")"`(절대경로). 실패면 `die_no_python`.
   - `WITH_MISE == 0`: `die_no_python "no Python >= $min on PATH and no uv"`.
   이후 `"$found" -m venv "$venv"`는 기존과 동일(절대경로도 동작).

3. **DRY_RUN early-return**
   `--with-mise --dry-run`이면 기존 "would (re)create venv …" 메시지에
   "(may bootstrap Python via mise)" 한 줄을 덧붙인다. 실제 설치는 하지 않는다.

## 에러 / 안내 메시지 (기본 경로)

`die_no_python`가 출력하는 안내(예시):

```
ERROR: No Python >= 3.11 found, and <context>.
  • Re-run with --with-mise to bootstrap Python 3.11 via mise
    (installs mise if missing; requires network access).
  • Or install it yourself:  sudo apt install python3.11 python3.11-venv
  • Or stage an interpreter / wheels offline and re-run.
```

`<context>`는 호출부에서 전달("uv could not provide Python >= 3.11" /
"no Python >= 3.11 on PATH and no uv"). floor 버전(`$min`)은 변수로 채운다.

## 제약 / 주의

- **네트워크 의존**: mise 설치와 `python@<floor>` 다운로드 모두 네트워크가 필요하다. 진짜
  air-gapped 박스에서는 `--with-mise`도 실패한다 → 그 경우 `mise_provide_python`가
  nonzero를 반환하고 `die_no_python`의 오프라인 안내로 떨어진다.
- **sudo 컨텍스트**: 스크립트가 sudo로 돌든 아니든 mise는 DEPLOY_USER 홈에 설치/저장된다
  (`run_as_deploy_user`로 보장). 생성되는 venv는 여전히 `ADAPTER_DIR/.venv`이고
  run-main.sh의 인터프리터 선택 경로는 무변경.
- **bash 4.3 / 구형 Ubuntu 호환**: 빈 배열 전개 가드 등 기존 패턴 유지. mise는 단일
  정적 바이너리라 bash 버전과 무관. `MISE_PYTHON_COMPILE=0`로 빌드툴 의존 제거.
- **기본 무해성**: `--with-mise` 미지정 시 mise/curl을 절대 호출하지 않는다. 정상 호스트와
  CI는 영향 없음.
- **보안(remote script 실행)**: mise 설치는 `curl -fsSL https://mise.run | sh`로, 버전/체크섬
  핀 없는 원격 스크립트 실행이다. 단, (a) `--with-mise` opt-in일 때만, (b) root가 아닌
  DEPLOY_USER 권한으로, (c) 공식 설치 엔드포인트(`mise.run`)에 한해 실행한다. 오프라인/사내
  미러 설치는 비목표이므로 핀은 두지 않는다 — 운영자가 의식적으로 옵션을 줄 때의 신뢰 가정이다.

## 테스트 / 검증 계획

bash 스크립트라 단위테스트는 두지 않는다. 검증은:

1. `shellcheck scripts/setup-adaptor-service.sh` 통과.
2. `--dry-run` (mise 미지정) → 기존 출력과 동일, mise 흔적 없음.
3. `--dry-run --with-mise` → venv 분기에서 "would … bootstrap Python via mise" 출력,
   실제 설치 없음.
4. 기본 경로 에러 메시지: Python ≥floor가 없고 uv도 없는 상황을 흉내 내(예: PATH 축소)
   `die_no_python`가 `--with-mise` 안내를 내며 `exit 1` 하는지 수동 확인.
5. 실제 mise 설치/`python@floor` 다운로드 → 네트워크 의존이라 자동 검증 제외, 온보드에서
   수동 확인.

## 결정 사항 (확정)

- 통합 방식: **폴백 티어**(uv-우선 유지, mise는 마지막 공급자).
- 설치 버전: **pyproject `requires-python` floor**(현재 3.11). `adapter_min_python` 재사용.
- 활성화: **opt-in `--with-mise`**. 기본은 설치 안 함 + 에러 시 안내.
- mise 설치 방식: **공식 `curl https://mise.run | sh`** (사내미러/오프라인은 비목표).
