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


def test_fetch_script_prunes_superseded_debs_for_the_same_package():
    # setup-adaptor-service.sh hands the whole directory to one `dpkg -i`, and
    # the file list comes from `sort`, which is lexicographic rather than
    # version-aware: xboxdrv_0.8.8-10_arm64.deb sorts *before*
    # xboxdrv_0.8.8-2_arm64.deb, so leaving the old file behind installs the new
    # version and then the old one on top of it. The staleness check above is on
    # the versioned filename, so a version bump downloads the new deb without
    # ever touching the old one. Drop the sibling once the new file is verified.
    text = FETCH_SCRIPT.read_text()

    prune = 'find "$OUT_DIR" -maxdepth 1 -type f -name "${pkg}_*_${ARCH}.deb" ! -name "$base" -print -delete'
    assert prune in text
    # ...before the new file is moved in, not after.
    assert text.index(prune) < text.index('mv "$tmp_dir/$base" "$OUT_DIR/$base"')
    # The trailing `_` in the glob is load-bearing: without it libusb-1.0-0_*
    # would also match libusb-1.0-0-dev_* and delete an unrelated package.
    assert "${pkg}_*_${ARCH}.deb" in text


def test_xboxdrv_bundle_is_committed_for_offline_robots():
    # scripts/ is tarred wholesale to the robot, so committing the debs here is
    # what actually puts them on an offline machine.
    names = sorted(path.name for path in XBOXDRV_DEB_DIR.glob("*.deb"))

    # Exactly one deb per package. Two versions of the same package in this
    # directory is the downgrade described above, and the count is the cheap
    # regression guard for it. Versions are deliberately not pinned here — a
    # bump should not need a test edit, a leftover sibling should.
    for prefix in ("xboxdrv_", "libdbus-glib-1-2_", "libusb-1.0-0_"):
        staged = [name for name in names if name.startswith(prefix)]
        assert len(staged) == 1, f"expected exactly one {prefix}*.deb, got {staged}"
    # The JIBOT onboard PC is arm64; an amd64 deb here would only make dpkg fail
    # on the robot.
    assert names and all(name.endswith("_arm64.deb") for name in names)
