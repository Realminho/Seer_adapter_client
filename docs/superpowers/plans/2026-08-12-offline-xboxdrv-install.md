# Offline xboxdrv Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `scripts/setup-adaptor-service.sh` install `xboxdrv` from `.deb` files committed to the repository, so an offline robot can enable `amr-xboxdrv.service` without reaching an apt mirror.

**Architecture:** Reuse the pattern the camera service already uses for `ros-noetic-web-video-server`. Extract its inline bundled-deb install into a shared shell function, add a `scripts/fetch-offline-debs.sh` helper that refreshes the bundle from `ports.ubuntu.com`, and call the shared function from the joystick path. `scripts/update-jibot-adapter-over-ssh.sh` is untouched — it already tars the whole `scripts/` directory over SSH (`:546`), so the bundle ships with every update.

**Tech Stack:** Bash 5 (`set -euo pipefail`), `dpkg`, `curl`/`xzcat`/`sha256sum`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-offline-xboxdrv-install-design.md`

## Global Constraints

- Target image is **arm64 / Ubuntu 20.04 focal** — the same combination the existing `web-video-server` bundle targets.
- **No apt fallback anywhere.** The robot is offline; what is not staged in the bundle directory cannot be installed.
- **No recursive dependency closure.** Stage only `xboxdrv`, `libdbus-glib-1-2`, `libusb-1.0-0`. Pulling `libc6`/`libstdc++6` would add tens of megabytes and let `dpkg -i` downgrade core packages on an offline robot.
- `scripts/setup-adaptor-service.sh` runs under **`set -euo pipefail` (line 34)**. `install_bundled_offline_debs` returns non-zero on failure, so every call site must end in `|| true` or the whole setup aborts. This is the single easiest way to break this change.
- Setup must **degrade, never fail**: a missing or broken bundle falls back to the existing "xboxdrv is not installed" warning and skips the unit.
- Existing camera behavior must not regress. `install_amr_camera_service` prints its own `==> amr-camera.service` header, so the shared helper must emit **indented body lines only, never a `==>` header**.
- Test command (run from the repo root):

  ```bash
  ./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests
  ```

  Each part is load-bearing. `run-tests.sh` looks for a venv at the **repo root**, but this checkout's venv is at `adaptor/.venv`, so without `--python` it falls back to a `python3` that has no `hcl2` and the run dies during collection. The path must be **absolute** because `run-tests.sh` `cd`s into `adaptor/` before exec. That same `cd` is why repo-root tests are addressed as `../tests`. `-o addopts=""` drops the `pytest_timeout` plugin that `adaptor/pyproject.toml` requires but `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` does not load.

- Test baseline over the full `../tests` suite: **139 passed, 2 failed**. Both failures pre-date this work, fail on HEAD, and are unrelated — they must remain the only two:
  - `test_adaptor_service_scripts.py::test_run_adapter_dispatches_multi_robot_fleet_to_run_multi`
  - `test_update_jibot_adapter_over_ssh.py::test_update_script_restarts_after_upload_with_tty`

- **Another session is working in this same checkout concurrently.** Touch only the files each task names. Never `git add -A`, never `git commit -a`, and never stage anything under `adaptor/` — those changes belong to the other session.

---

### Task 1: Extract the shared bundled-deb installer

Behavior-preserving refactor. The camera service keeps working exactly as before; this only creates the function the joystick path will call in Task 3.

**Files:**
- Modify: `scripts/setup-adaptor-service.sh` — insert helper after `dpkg_package_installed()` (ends line 738); replace the inline block at lines 767-793
- Modify: `tests/test_update_jibot_adapter_over_ssh.py:92-98`

**Interfaces:**
- Consumes: `run_root()` (`:112`), `DRY_RUN` (`:47`), `REPO_ROOT` (`:37`)
- Produces: `install_bundled_offline_debs <label> <dir> [extra_hint]` — installs every `*.deb` in `<dir>` with `dpkg -i`. Returns 0 on success or in dry-run; returns 1 when nothing is staged or `dpkg` did not complete. Prints indented body lines only.

- [ ] **Step 1: Capture the current camera dry-run output as a baseline**

```bash
cd /ssd2/workspaces/unified-amr-adaptor
scripts/setup-adaptor-service.sh --jibot --dry-run > /tmp/claude-1000/-ssd2-workspaces-unified-amr-adaptor/dac94494-44e4-463f-a314-0d18af51d174/scratchpad/setup-dryrun-before.txt 2>&1 || true
```

This file is the reference for Step 6. Do not skip it — the refactor's whole claim is "camera output does not change".

- [ ] **Step 2: Update the camera test to the post-refactor call site**

The `mapfile` and `run_root dpkg -i` lines survive verbatim inside the helper, so only the `offline_deb_dir=` assignment assertion and the `$pkg` message assertion change. In `tests/test_update_jibot_adapter_over_ssh.py`, replace lines 92-98 with:

```python
def test_setup_script_installs_web_video_server_from_uploaded_offline_debs_first():
    text = SETUP_SCRIPT.read_text()

    assert 'install_bundled_offline_debs "$pkg"' in text
    assert '"$REPO_ROOT/scripts/offline-debs/web-video-server"' in text
    assert 'mapfile -t offline_debs < <(find "$offline_deb_dir" -maxdepth 1 -type f -name \'*.deb\' | sort)' in text
    assert 'run_root dpkg -i "${offline_debs[@]}"' in text
    assert "Stage $label and its .deb dependencies there, then re-run setup." in text
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests/test_update_jibot_adapter_over_ssh.py::test_setup_script_installs_web_video_server_from_uploaded_offline_debs_first -v
```

Expected: FAIL — `assert 'install_bundled_offline_debs "$pkg"' in text`.

- [ ] **Step 4: Add the helper**

Insert immediately after `dpkg_package_installed()` (which ends at line 738 with `}`):

```bash
# Install every .deb staged under a bundled offline directory.
#
# Robots have no internet, so there is no apt fallback: what is not staged in
# the directory cannot be installed. Callers print their own "==> ..." header,
# so this helper emits indented body lines only.
#
#   $1  label named in messages (a package name, or the tool being installed)
#   $2  directory holding the .deb files
#   $3  optional extra hint printed after a dpkg failure
#
# Returns non-zero when nothing was staged or dpkg did not complete, so the
# caller decides whether that is fatal. Under `set -e` every call site that
# wants to continue must append `|| true`.
install_bundled_offline_debs() {
  local label="$1" offline_deb_dir="$2" extra_hint="${3:-}"
  local offline_debs=()
  if [[ -d "$offline_deb_dir" ]]; then
    mapfile -t offline_debs < <(find "$offline_deb_dir" -maxdepth 1 -type f -name '*.deb' | sort)
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    if [[ ${#offline_debs[@]} -gt 0 ]]; then
      echo "  [dry-run] would install bundled offline debs from $offline_deb_dir"
    else
      echo "  [dry-run] no bundled offline debs found; $label install would be skipped"
    fi
    return 0
  fi
  if [[ ${#offline_debs[@]} -eq 0 ]]; then
    echo "  WARNING: $label is not installed and no bundled offline debs were found in $offline_deb_dir." >&2
    echo "  Stage $label and its .deb dependencies there, then re-run setup." >&2
    return 1
  fi
  echo "  installing bundled offline debs from $offline_deb_dir ..."
  if ! run_root dpkg -i "${offline_debs[@]}"; then
    echo "  ERROR: bundled offline deb install did not complete; not using apt because this target is offline." >&2
    echo "  Stage missing .deb dependencies in $offline_deb_dir and re-run." >&2
    if [[ -n "$extra_hint" ]]; then
      echo "  $extra_hint" >&2
    fi
    return 1
  fi
  return 0
}
```

- [ ] **Step 5: Replace the inline camera block**

Replace lines 767-793 (from `local pkg="ros-${distro}-web-video-server"` through the `fi` that closes the `command -v dpkg` test) with:

```bash
  local pkg="ros-${distro}-web-video-server"
  if command -v dpkg >/dev/null 2>&1 && ! dpkg_package_installed "$pkg"; then
    install_bundled_offline_debs "$pkg" \
      "$REPO_ROOT/scripts/offline-debs/web-video-server" \
      "The JIBOT image may require ffmpeg and related libav packages in that directory." || true
  fi
```

The `|| true` is load-bearing: without it a robot missing one `.deb` would abort setup before the adapter unit is installed.

- [ ] **Step 6: Verify the tests pass and the dry-run output is unchanged**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests/test_update_jibot_adapter_over_ssh.py ../tests/test_adaptor_service_scripts.py -q
scripts/setup-adaptor-service.sh --jibot --dry-run > /tmp/claude-1000/-ssd2-workspaces-unified-amr-adaptor/dac94494-44e4-463f-a314-0d18af51d174/scratchpad/setup-dryrun-after.txt 2>&1 || true
diff /tmp/claude-1000/-ssd2-workspaces-unified-amr-adaptor/dac94494-44e4-463f-a314-0d18af51d174/scratchpad/setup-dryrun-{before,after}.txt && echo "IDENTICAL"
```

Expected: `IDENTICAL`, and the only failures are the two pre-existing ones named in Global Constraints.

One camera message does change wording, on a branch this diff will not reach: the empty-bundle dry-run line becomes `no bundled offline debs found; ros-noetic-web-video-server install would be skipped` instead of `... camera package install would be skipped`. That branch needs `dpkg` present, the ROS package absent, and `scripts/offline-debs/web-video-server/` empty — the directory has debs committed, so neither this machine nor a deployed robot hits it. Naming the package is strictly more useful than "camera", and no test asserts the old string. Everything else is byte-identical.

- [ ] **Step 7: Commit**

```bash
git add scripts/setup-adaptor-service.sh tests/test_update_jibot_adapter_over_ssh.py
git commit -m "refactor: extract the bundled offline deb installer

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add the fetch helper and stage the xboxdrv bundle

**Files:**
- Create: `scripts/fetch-offline-debs.sh` (mode 755)
- Create: `scripts/offline-debs/xboxdrv/README.md`
- Create: `tests/test_offline_deb_fetch.py`
- Data: `scripts/offline-debs/xboxdrv/*.deb` (downloaded in Step 4, committed)

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `scripts/offline-debs/xboxdrv/` containing `xboxdrv_0.8.8-2_arm64.deb`, `libdbus-glib-1-2_0.110-5fakssync1_arm64.deb`, `libusb-1.0-0_1.0.23-2build1_arm64.deb` — Task 3 installs from this directory.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_offline_deb_fetch.py`:

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FETCH_SCRIPT = REPO_ROOT / "scripts" / "fetch-offline-debs.sh"
XBOXDRV_DEB_DIR = REPO_ROOT / "scripts" / "offline-debs" / "xboxdrv"


def test_fetch_script_targets_the_robot_image():
    # The JIBOT onboard PC is arm64/focal, and arm64 lives on ports.ubuntu.com
    # rather than archive.ubuntu.com.
    text = FETCH_SCRIPT.read_text()

    assert 'SUITE="${SUITE:-focal}"' in text
    assert 'ARCH="${ARCH:-arm64}"' in text
    assert "ports.ubuntu.com" in text


def test_fetch_script_stages_only_the_deps_a_minimal_image_may_lack():
    # Resolving the dependency closure recursively would drag in libc6 and
    # libstdc++6, which is tens of megabytes in git and lets dpkg downgrade core
    # packages on a robot that cannot apt its way back out. The list stays
    # explicit; a missing dependency is fixed by adding one line here.
    text = FETCH_SCRIPT.read_text()

    block = text.split("PACKAGES=(", 1)[1].split("\n)", 1)[0]
    listed = [line.split("#")[0].strip() for line in block.splitlines()]
    listed = [name for name in listed if name]

    assert listed == ["xboxdrv", "libdbus-glib-1-2", "libusb-1.0-0"]


def test_fetch_script_verifies_downloads_and_never_uses_apt():
    text = FETCH_SCRIPT.read_text()

    assert "sha256sum" in text
    assert "SHA256" in text
    assert "apt-get" not in text


def test_fetch_script_supports_check_only():
    text = FETCH_SCRIPT.read_text()

    assert "--check" in text
    assert "CHECK_ONLY=1" in text


def test_xboxdrv_bundle_is_committed_for_offline_robots():
    # scripts/ is tarred wholesale to the robot, so committing the debs here is
    # what actually puts them on an offline machine.
    names = sorted(path.name for path in XBOXDRV_DEB_DIR.glob("*.deb"))

    assert any(n.startswith("xboxdrv_") and n.endswith("_arm64.deb") for n in names)
    assert any(n.startswith("libdbus-glib-1-2_") for n in names)
    assert any(n.startswith("libusb-1.0-0_") for n in names)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests/test_offline_deb_fetch.py -v
```

Expected: all five FAIL — `FileNotFoundError` for `scripts/fetch-offline-debs.sh`.

- [ ] **Step 3: Write the fetch script**

Create `scripts/fetch-offline-debs.sh`:

```bash
#!/usr/bin/env bash
# scripts/offline-debs/xboxdrv/ 를 Ubuntu 아카이브에서 다시 채운다.
#
# 로봇은 인터넷이 없다. setup-adaptor-service.sh 가 이 디렉터리의 .deb 를
# `dpkg -i` 로 설치하고 apt 로 폴백하지 않으므로, **로봇의 배포판·아키텍처에
# 맞는 .deb** 가 미리 들어 있어야 한다.
#
# 의존성 폐포를 재귀로 받지 않는다. libc6 같은 핵심 패키지까지 끌어오면 저장소가
# 수십 MB 커지고, 오프라인 로봇에서 dpkg 가 코어 패키지를 다운그레이드하는 사고가
# 난다. 아래 PACKAGES 에는 "베이스 이미지에 없을 만한 것"만 명시한다. dpkg 가
# 의존성 오류를 내면 그 패키지 이름을 한 줄 추가하고 다시 받는다.
#
# 사용:
#   scripts/fetch-offline-debs.sh            # 빠진 deb 를 받는다
#   scripts/fetch-offline-debs.sh --check    # 빠진 것만 보고(다운로드 안 함)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_DIR/scripts/offline-debs/xboxdrv"

# JIBOT 온보드 PC = arm64 / Ubuntu 20.04 focal.
SUITE="${SUITE:-focal}"
ARCH="${ARCH:-arm64}"
# arm64 는 ports.ubuntu.com 이, amd64 는 archive.ubuntu.com 이 호스팅한다.
MIRROR="${MIRROR:-http://ports.ubuntu.com/ubuntu-ports}"
COMPONENTS=(main universe)

# xboxdrv 의 Depends 중 최소 이미지에 없을 수 있는 것만 받는다.
# libc6 libdbus-1-3 libgcc-s1 libglib2.0-0 libstdc++6 libudev1 libx11-6 은
# 어떤 Ubuntu 베이스에도 있으므로 받지 않는다.
#
# 주석에 괄호를 쓰지 말 것. tests/test_offline_deb_fetch.py 가 이 배열을
# `PACKAGES=(` ~ 줄머리 `)` 로 잘라 목록을 검증한다.
PACKAGES=(
  xboxdrv           # bridge 바이너리 본체. universe 컴포넌트
  libdbus-glib-1-2  # 레거시 dbus 바인딩. 최소 이미지에 없다
  libusb-1.0-0      # 최소 이미지에 없다
)

CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

for tool in curl xzcat sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: '$tool' 이 필요합니다." >&2
    exit 1
  }
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "미러   : $MIRROR"
echo "대상   : $SUITE / $ARCH"
echo "저장소 : $OUT_DIR"
echo

# 인덱스는 한 번만 받아 합쳐 둔다. focal universe 인덱스는 수십 MB 라
# 패키지마다 다시 받으면 안 된다.
index="$tmp_dir/Packages"
: > "$index"
for comp in "${COMPONENTS[@]}"; do
  url="$MIRROR/dists/$SUITE/$comp/binary-$ARCH/Packages.xz"
  echo "  인덱스 받는 중  $comp"
  if ! curl -fsSL --max-time 900 -o "$tmp_dir/$comp.xz" "$url"; then
    echo "ERROR: 인덱스를 받지 못했습니다: $url" >&2
    exit 1
  fi
  xzcat "$tmp_dir/$comp.xz" >> "$index"
  # 컴포넌트 경계에서 stanza 가 붙지 않도록 빈 줄을 넣는다.
  echo >> "$index"
done
echo

mkdir -p "$OUT_DIR"
missing=0
added=0
for pkg in "${PACKAGES[@]}"; do
  stanza="$(awk -v want="Package: $pkg" '
    $0 == want { found = 1 }
    found && /^$/ { exit }
    found { print }
  ' "$index")"
  if [[ -z "$stanza" ]]; then
    echo "ERROR: $SUITE/$ARCH 인덱스에서 '$pkg' 를 찾지 못했습니다." >&2
    echo "       패키지 이름과 SUITE 를 확인하세요." >&2
    exit 1
  fi
  filename="$(awk '/^Filename: /{print $2; exit}' <<<"$stanza")"
  sha256="$(awk '/^SHA256: /{print $2; exit}' <<<"$stanza")"
  version="$(awk '/^Version: /{print $2; exit}' <<<"$stanza")"
  base="$(basename "$filename")"

  if [[ -f "$OUT_DIR/$base" ]]; then
    echo "  있음  $base"
    continue
  fi

  missing=$((missing + 1))
  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo "  빠짐  $base  ($pkg $version)"
    continue
  fi

  echo "  받는 중  $base  ($pkg $version)"
  if ! curl -fsSL --max-time 900 -o "$tmp_dir/$base" "$MIRROR/$filename"; then
    echo "ERROR: 다운로드 실패: $MIRROR/$filename" >&2
    exit 1
  fi
  actual="$(sha256sum "$tmp_dir/$base" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    echo "ERROR: SHA256 불일치 ($base)" >&2
    echo "       기대: $sha256" >&2
    echo "       실제: $actual" >&2
    exit 1
  fi
  mv "$tmp_dir/$base" "$OUT_DIR/$base"
  added=$((added + 1))
done

echo
if [[ $missing -eq 0 ]]; then
  echo "$OUT_DIR 가 최신입니다 ($(find "$OUT_DIR" -maxdepth 1 -name '*.deb' | wc -l)개 deb)."
elif [[ $CHECK_ONLY -eq 1 ]]; then
  echo "빠진 deb $missing 개. --check 없이 다시 실행하면 받습니다."
  exit 1
else
  echo "deb $added 개 추가. 총 $(find "$OUT_DIR" -maxdepth 1 -name '*.deb' | wc -l)개."
  echo "이 디렉터리는 저장소에 커밋해야 로봇 배포에 실립니다."
fi
```

Then: `chmod 755 scripts/fetch-offline-debs.sh`

- [ ] **Step 4: Run the fetch script for real**

```bash
scripts/fetch-offline-debs.sh
```

Expected output names and sizes (verified against the focal arm64 indices):

| File | Size |
|---|---|
| `xboxdrv_0.8.8-2_arm64.deb` | 423,372 B |
| `libdbus-glib-1-2_0.110-5fakssync1_arm64.deb` | 55,392 B |
| `libusb-1.0-0_1.0.23-2build1_arm64.deb` | 44,480 B |

The universe index download takes a few minutes; `--max-time 900` covers it. Then confirm idempotence and the check mode:

```bash
scripts/fetch-offline-debs.sh          # expect "있음" for all three, "최신입니다"
scripts/fetch-offline-debs.sh --check  # expect exit 0, all three present
ls -l scripts/offline-debs/xboxdrv/
```

- [ ] **Step 5: Write the bundle README**

Create `scripts/offline-debs/xboxdrv/README.md`:

```markdown
# xboxdrv offline .deb (arm64 / Ubuntu 20.04 focal)

The robot has **no internet access**, so `apt install xboxdrv` fails there.
These prebuilt **arm64** packages let `scripts/setup-adaptor-service.sh`
install the joystick bridge binary offline.

Contents:
- `xboxdrv_*_arm64.deb` — the userspace Xbox360 driver the bridge runs
- `libdbus-glib-1-2_*_arm64.deb` — legacy dbus binding; absent from minimal images
- `libusb-1.0-0_*_arm64.deb` — absent from minimal images

`xboxdrv` also depends on `libc6`, `libdbus-1-3`, `libgcc-s1`, `libglib2.0-0`,
`libstdc++6`, `libudev1`, and `libx11-6`. Those are present in any Ubuntu base
image and are deliberately **not** staged: `dpkg -i` on a bundled `libc6` can
downgrade a core package on a machine that cannot apt its way back out.

## Automatic install

`scripts/update-jibot-adapter-over-ssh.sh` tars the whole `scripts/` directory
to the robot, so these files ship with every update. Running
`scripts/setup-adaptor-service.sh --jibot` on the robot then installs them with
`dpkg -i` when `xboxdrv` is missing. It never falls back to `apt-get`.

If `dpkg -i` reports a missing dependency, add that package name to
`PACKAGES` in `scripts/fetch-offline-debs.sh`, re-run the fetch on the build
machine, commit, and redeploy.

## Refreshing these files

```bash
scripts/fetch-offline-debs.sh --check   # what is missing
scripts/fetch-offline-debs.sh           # download and verify (SHA256 from the index)
```

Targets `focal`/`arm64` on `http://ports.ubuntu.com/ubuntu-ports` by default.
Override with `SUITE=`, `ARCH=`, `MIRROR=` for a different robot image — note
that amd64 lives on `archive.ubuntu.com`, not the ports mirror.

## Manual install on the robot

```bash
ssh ucore@<host> 'mkdir -p /tmp/xboxdrv_debs'
scp scripts/offline-debs/xboxdrv/*.deb ucore@<host>:/tmp/xboxdrv_debs/
ssh -t ucore@<host> 'sudo dpkg -i /tmp/xboxdrv_debs/*.deb'
```

Success looks like `Setting up xboxdrv ...` with no dependency errors, and
`command -v xboxdrv` returning `/usr/bin/xboxdrv`.
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests/test_offline_deb_fetch.py -v
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch-offline-debs.sh scripts/offline-debs/xboxdrv tests/test_offline_deb_fetch.py
git commit -m "feat: stage xboxdrv debs for offline robots

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Install xboxdrv from the bundle during setup

**Files:**
- Modify: `scripts/setup-adaptor-service.sh:867-897` (`install_amr_xboxdrv_service`)
- Modify: `tests/test_adaptor_service_scripts.py` — add one test after `test_setup_skips_the_bridge_when_the_kernel_driver_can_bind_the_receiver`
- Modify: `docs/manual/joystick-runtime-setup.md:328-337`

**Interfaces:**
- Consumes: `install_bundled_offline_debs <label> <dir> [extra_hint]` from Task 1; `scripts/offline-debs/xboxdrv/` from Task 2
- Produces: nothing downstream

- [ ] **Step 1: Write the failing test**

Add to `tests/test_adaptor_service_scripts.py`, after `test_setup_skips_the_bridge_when_the_kernel_driver_can_bind_the_receiver`:

```python
def test_setup_installs_xboxdrv_from_the_bundled_offline_debs():
    # The robot has no internet, so apt cannot supply xboxdrv. The .deb bundle
    # rides along with the scripts/ directory the update script uploads.
    text = SETUP_SCRIPT.read_text()

    assert 'install_bundled_offline_debs "xboxdrv"' in text
    assert '"$REPO_ROOT/scripts/offline-debs/xboxdrv"' in text
    # The helper returns non-zero on failure and setup runs under `set -e`, so a
    # missing deb must not abort the rest of the install.
    assert 'scripts/fetch-offline-debs.sh and re-run it on the build machine." || true' in text
    # The binary is re-checked after the install attempt, so a failed dpkg run
    # still degrades to the old skip rather than enabling a crash-looping unit.
    assert text.count("command -v xboxdrv") >= 2
    # Installing xboxdrv on a host that will skip the bridge anyway is waste, so
    # the xpad gate runs first.
    assert text.index("xpad_driver_available; then") < text.index('install_bundled_offline_debs "xboxdrv"')
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests/test_adaptor_service_scripts.py::test_setup_installs_xboxdrv_from_the_bundled_offline_debs -v
```

Expected: FAIL — `assert 'install_bundled_offline_debs "xboxdrv"' in text`.

- [ ] **Step 3: Rewrite `install_amr_xboxdrv_service`**

Replace lines 867-881 (the function opening through the `fi` that closes the xpad gate) with:

```bash
install_amr_xboxdrv_service() {
  # The xpad gate runs first. On a host where the kernel already drives the
  # receiver the bridge is skipped anyway, so there is no reason to push an
  # unused package onto an offline robot. --with-xboxdrv clears this gate and
  # still installs.
  if [[ $DRY_RUN -eq 0 && $FORCE_XBOXDRV -eq 0 ]] && xpad_driver_available; then
    echo "==> amr-xboxdrv.service"
    echo "  WARNING: the xpad driver is present; skipping amr-xboxdrv.service." >&2
    echo "           The kernel already exposes this receiver, and xboxdrv would" >&2
    echo "           fight it for the USB interface. Pass --with-xboxdrv to" >&2
    echo "           install the bridge anyway." >&2
    return 0
  fi
  # No apt here: the robot is offline. Install the .deb bundle that shipped with
  # the deployed scripts/ directory instead. install_unit prints its own header
  # later, so this branch prints one of its own.
  if ! command -v xboxdrv >/dev/null 2>&1; then
    echo "==> amr-xboxdrv.service (offline deb bundle)"
    install_bundled_offline_debs "xboxdrv" \
      "$REPO_ROOT/scripts/offline-debs/xboxdrv" \
      "Add the missing package to PACKAGES in scripts/fetch-offline-debs.sh and re-run it on the build machine." || true
  fi
  if [[ $DRY_RUN -eq 0 ]] && ! command -v xboxdrv >/dev/null 2>&1; then
    echo "==> amr-xboxdrv.service"
    echo "  WARNING: xboxdrv is not installed; skipping amr-xboxdrv.service." >&2
    echo "           Install it and re-run setup to enable the joystick bridge." >&2
    return 0
  fi
```

Everything from `install_unit "amr-xboxdrv.service"` (line 882) to the closing `}` (line 897) stays exactly as it is.

Two details that are easy to get wrong:

1. The deb-install branch is **not** guarded by `$DRY_RUN -eq 0`. In dry-run the helper prints `[dry-run] would install bundled offline debs from ...` and returns 0, and the following `$DRY_RUN -eq 0` guard still lets the unit render — matching how dry-run already renders units on hosts that lack their prerequisites.
2. The `|| true` is required. `install_bundled_offline_debs` returns 1 when the bundle is empty or `dpkg` fails, and the script runs under `set -euo pipefail` (line 34).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./scripts/run-tests.sh --python /ssd2/workspaces/unified-amr-adaptor/adaptor/.venv/bin/python -o addopts="" ../tests -q
```

Expected: 144 passed, 2 failed — the two pre-existing failures named in Global Constraints and nothing else.

- [ ] **Step 5: Verify the dry-run output**

```bash
scripts/setup-adaptor-service.sh --jibot --dry-run 2>&1 | grep -A 4 'amr-xboxdrv'
```

This build machine has no `xboxdrv`, so expect:

```
==> amr-xboxdrv.service (offline deb bundle)
  [dry-run] would install bundled offline debs from .../scripts/offline-debs/xboxdrv
==> amr-xboxdrv.service (User=..., WorkingDirectory=...)
```

The unit must still render — dry-run on a host without the prerequisite is expected to show what a real robot would get.

- [ ] **Step 6: Update the manual**

In `docs/manual/joystick-runtime-setup.md`, replace lines 328-337 (from `다음 두 경우에는` through the `--no-xboxdrv` sentence) with:

```markdown
`xboxdrv` 바이너리가 없으면 저장소에 번들된 오프라인 `.deb`를 먼저 설치한다. 로봇은
인터넷이 없어 apt로는 받을 수 없고, 업데이트마다 `scripts/`가 통째로 업로드되므로
`scripts/offline-debs/xboxdrv/`의 `.deb`가 함께 실려 온다. `apt-get` 폴백은 없다.

`dpkg -i`가 의존성 오류를 내면 빌드 머신에서 `scripts/fetch-offline-debs.sh`의
`PACKAGES` 목록에 그 패키지를 추가하고 다시 실행한 뒤 커밋·재배포한다. 자세한 내용은
[scripts/offline-debs/xboxdrv/README.md](../../scripts/offline-debs/xboxdrv/README.md)를
참고한다.

다음 두 경우에는 경고만 남기고 bridge를 건너뛴다.

- 커널에 `xpad`가 있다. bridge는 커널이 receiver를 직접 다루지 못할 때 쓰는 우회로인데,
  `xpad`가 있으면 같은 USB interface를 커널이 먼저 잡는다. 이 상태로 xboxdrv를 띄우면
  interface를 못 잡아 crash-loop 하거나, 반대로 뺏어 와서 runtime이 우선하는
  `/dev/input/jsN` 경로를 없앤다. 둘 다 원하는 결과가 아니다. `xpad`가 있는데도 이
  receiver에는 bind되지 않는 것이 확인된 호스트라면 `--with-xboxdrv`로 강제 설치한다.
  이 경우 오프라인 `.deb` 설치도 함께 건너뛴다 — 쓰지 않을 패키지를 로봇에 넣지 않는다.
- 번들 설치 후에도 `xboxdrv` 바이너리가 없다. 바이너리 없이 unit만 깔면 crash-loop만
  하기 때문이다.

joystick을 쓰지 않는 로봇에서 명시적으로 제외하려면 `--no-xboxdrv`를 준다. 이때는 오프라인
`.deb` 설치도 하지 않는다.
```

- [ ] **Step 7: Commit**

```bash
git add scripts/setup-adaptor-service.sh tests/test_adaptor_service_scripts.py docs/manual/joystick-runtime-setup.md
git commit -m "feat: install xboxdrv from the offline deb bundle during setup

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## On-Robot Verification

Not part of any task's commit gate — run after all three tasks land.

1. `scripts/update-jibot-adapter-over-ssh.sh ucore@<host>` — then on the robot, confirm `ls ~/adaptor/scripts/offline-debs/xboxdrv/` shows all three `.deb` files.
2. `cd ~/adaptor && scripts/setup-adaptor-service.sh --jibot --dry-run` — confirm the gate order and the `[dry-run]` deb line.
3. `scripts/setup-adaptor-service.sh --jibot` — then `command -v xboxdrv` returns `/usr/bin/xboxdrv` and `systemctl status amr-xboxdrv.service` is active.
4. Sleep and wake the controller once to confirm `amr-xboxdrv-run.sh` still cycles the bridge (receiver `310b` ↔ `3109`), and confirm manual drive works.
