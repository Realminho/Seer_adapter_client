# Offline xboxdrv Install Design

## Goal

Let a robot that has no internet access get the `xboxdrv` binary installed as
part of the normal adapter deployment, so the 8BitDo joystick bridge
(`amr-xboxdrv.service`) can actually be enabled there.

Today `scripts/setup-adaptor-service.sh` only *checks* for the binary: when
`command -v xboxdrv` fails it prints a warning and skips the unit. Nothing in
the repository installs it, and `--install-py-deps` on
`scripts/update-jibot-adapter-over-ssh.sh` installs Python wheels only. The
operator is left with "install it yourself" on a host that cannot reach an apt
mirror.

The camera service already solves the same problem for
`ros-noetic-web-video-server` by committing `.deb` files under
`scripts/offline-debs/web-video-server/` and installing them with `dpkg -i`
during setup. This design reuses that pattern for `xboxdrv` and adds a fetch
helper so the bundle can be refreshed without hand-copying files.

## Target

arm64 / Ubuntu 20.04 focal — the same combination the existing
`web-video-server` bundle targets.

The three packages, verified against the focal arm64 indices on
`ports.ubuntu.com`:

| Package | Component | Version | Size |
|---|---|---|---|
| `xboxdrv` | universe | 0.8.8-2 | 423,372 B |
| `libdbus-glib-1-2` | main | 0.110-5fakssync1 | 55,392 B |
| `libusb-1.0-0` | main | 2:1.0.23-2build1 | 44,480 B |

`xboxdrv` declares `libc6`, `libdbus-1-3`, `libdbus-glib-1-2`, `libgcc-s1`,
`libglib2.0-0`, `libstdc++6`, `libudev1`, `libusb-1.0-0`, `libx11-6`. Only
`libdbus-glib-1-2` (a legacy binding absent from minimal images) and
`libusb-1.0-0` are plausibly missing from a ROS Noetic robot image; the rest
are present in any Ubuntu base. The staged set is therefore ~523 KB total.

The dependency closure is deliberately **not** resolved recursively. Staging
`libc6` and `libstdc++6` would add tens of megabytes to the repository and let
`dpkg -i` downgrade core packages on an offline robot, which is the least
recoverable failure available. When `dpkg -i` reports a missing dependency, the
fix is to add one line to the package list and re-run the fetch helper — the
same operating procedure `scripts/offline-debs/web-video-server/README.md`
already documents.

## Components

### `scripts/offline-debs/xboxdrv/` (new)

Holds the committed `.deb` files and a `README.md`. No deployment change is
required: `scripts/update-jibot-adapter-over-ssh.sh:546` tars the whole
`scripts/` directory over SSH and excludes only `__pycache__` and `*.pyc`, so
the bundle rides along with every update.

### `scripts/fetch-offline-debs.sh` (new)

Runs on the build machine, which has internet. It follows the UX of
`scripts/fetch-offline-wheels.sh`: Korean comments explaining why the directory
exists, a `--check` flag that reports missing files and exits non-zero without
downloading, and a summary of what was added.

Behavior:

1. Download `dists/focal/{main,universe}/binary-arm64/Packages.xz` from
   `http://ports.ubuntu.com/ubuntu-ports/`.
2. For each package in a fixed list declared at the top of the script, read its
   `Filename`, `Size`, and `SHA256` from the index.
3. Download each `Filename` into `scripts/offline-debs/xboxdrv/` and verify the
   SHA256 before keeping it.
4. Skip files already present with a matching name.

The package list is a literal array so a reader can see what is staged and why:

```bash
PACKAGES=(
  xboxdrv           # the bridge binary itself (universe)
  libdbus-glib-1-2  # legacy dbus binding; absent from minimal ROS images
  libusb-1.0-0      # absent from minimal images
)
```

Suite (`focal`) and architecture (`arm64`) are variables at the top of the
script so a different robot image can be targeted without rewriting logic.

### `scripts/setup-adaptor-service.sh` (modified)

**Extract a shared helper.** The bundled-deb install currently sits inline in
`install_amr_camera_service` (lines 768-795). Move it to
`install_bundled_offline_debs <label> <dir>`, preserving the existing behavior
exactly: honor `--dry-run`, install every `*.deb` in the directory with
`run_root dpkg -i`, never fall back to `apt-get`, and print guidance on
failure. `install_amr_camera_service` then calls the helper.

**Use it from the joystick path.** `install_amr_xboxdrv_service` (line 867)
currently gates on the binary first and the `xpad` driver second. Swap that
order and add the install:

```
xpad present and not --with-xboxdrv?  -> warn, skip (unchanged message)
command -v xboxdrv fails?             -> install_bundled_offline_debs "xboxdrv" \
                                           "$REPO_ROOT/scripts/offline-debs/xboxdrv"
                                         then re-check command -v xboxdrv
bundle install failed, or still
  missing?                            -> warn, skip (unchanged message)
otherwise                             -> install_unit, usermod -aG input,
                                         enable, restart (all unchanged)
```

The reorder matters: with the binary check first, a host that has `xpad` — and
will therefore skip the bridge anyway — would still get `xboxdrv` installed.
Pushing an unused package onto an offline robot is worth avoiding. Passing
`--with-xboxdrv` clears the `xpad` gate, so the forced path still installs.

`--no-xboxdrv` continues to skip the whole function, including the deb install.

**Output framing.** `install_amr_camera_service` prints its own
`==> amr-camera.service` header at the top of the function and writes the unit
file itself, so the inline deb output already sits under a header. The joystick
path instead delegates to `install_unit`, which prints `==> amr-xboxdrv.service`
at line 217 — after the deb install would run. To keep the camera output
byte-identical, `install_bundled_offline_debs` prints only indented body lines
and never a `==>` header; `install_amr_xboxdrv_service` prints
`==> amr-xboxdrv.service (offline deb bundle)` immediately before calling it,
and only on the branch where the binary is actually missing.

### `scripts/update-jibot-adapter-over-ssh.sh` (unchanged)

No preflight validation is added. The web-video bundle has
`validate_web_video_offline_debs`, which aborts the update before opening SSH
when a required deb is missing. That is correct for the camera because every
JIBOT needs it. The joystick bridge is optional, so the same gate would fail
deployments to every robot without a controller. A missing bundle degrades to
the existing "xboxdrv is not installed" warning instead.

## Error Handling

- **`dpkg -i` reports a missing dependency.** No `apt-get` fallback — the
  target is offline. Print the failing output, name the directory, and instruct
  the operator to add the package to `PACKAGES` and re-run
  `scripts/fetch-offline-debs.sh` on the build machine. Setup continues so the
  remaining units still install, but `amr-xboxdrv.service` is skipped: `dpkg -i`
  unpacks a package with an unsatisfied `Depends` and only refuses to configure
  it, so the binary is on disk even though the install failed, and a re-check of
  `command -v xboxdrv` alone would enable a unit that crash-loops in the dynamic
  linker.
- **Bundle directory missing or empty.** Same as today: warn that `xboxdrv` is
  not installed and skip the unit. No regression for robots that never staged
  the debs.
- **`--dry-run`.** Print `[dry-run] would install bundled offline debs from
  <dir>` and run no `dpkg`, matching the camera path.
- **Fetch: SHA256 mismatch.** Fail immediately and leave the existing file
  untouched.
- **Fetch: package not found in the index.** Print the package name and fail,
  so a typo or a suite change surfaces on the build machine rather than on the
  robot.

## Testing

The existing suites assert against script source text, and the new tests follow
that convention.

`tests/test_adaptor_service_scripts.py`:

- `install_amr_xboxdrv_service` references
  `scripts/offline-debs/xboxdrv` and calls `install_bundled_offline_debs`.
- The `xpad` gate appears before the bundled-deb install.
- `command -v xboxdrv` is re-checked after the install attempt.
- `install_amr_camera_service` uses the shared helper rather than an inline
  `dpkg -i`.
- The existing xboxdrv tests (opt-out flag, unit enable, restart order,
  sudoers) still pass unchanged.

`tests/test_offline_deb_fetch.py` (new):

- The script declares `xboxdrv`, `libdbus-glib-1-2`, and `libusb-1.0-0`.
- Suite and architecture are declared as variables.
- `--check` is supported and does not download.
- SHA256 verification is present and there is no `apt-get` call.

Run with `./scripts/run-tests.sh -o addopts="" ../tests`.

On-robot verification:

1. `scripts/update-jibot-adapter-over-ssh.sh ucore@<host>` — confirm the debs
   arrive under `scripts/offline-debs/xboxdrv/`.
2. `scripts/setup-adaptor-service.sh --jibot --dry-run` — confirm the rendered
   order and the `[dry-run]` deb line.
3. `scripts/setup-adaptor-service.sh --jibot` — then `command -v xboxdrv` and
   `systemctl status amr-xboxdrv.service`.
4. Exercise a controller sleep/wake cycle to confirm the bridge still recovers.

## Documentation

- `scripts/offline-debs/xboxdrv/README.md` (new): why the directory exists,
  what is staged, how to refresh it with `fetch-offline-debs.sh`, and the manual
  `scp` + `dpkg -i` fallback — mirroring the web-video-server README.
- `docs/manual/joystick-runtime-setup.md:330`: replace "`xboxdrv` 바이너리가
  없다. 설치해도 계속 crash-loop만 한다." with a description of the automatic
  bundled install and what to do when a dependency is missing.
