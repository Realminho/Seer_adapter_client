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
