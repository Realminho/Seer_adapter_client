# Ubuntu Systemd Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested Ubuntu systemd installer for the JIBOT adapter.

**Architecture:** Keep daemon setup in a standalone shell script under `adaptor/`. The script generates a systemd unit that executes the existing `run-main.sh`, then reloads and enables systemd. A shell test runs the script with fake command hooks and a temporary unit directory.

**Tech Stack:** Bash, systemd unit files, shell-based tests.

---

### Task 1: Installer Test

**Files:**
- Create: `scripts/test-install-systemd-service.sh`

- [ ] **Step 1: Write the failing test**

Create a shell test that invokes `adaptor/install-systemd-service.sh` with `UNIT_DIR`, `SYSTEMCTL_BIN`, and `SUDO_BIN` pointed at temporary fake commands.

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/test-install-systemd-service.sh`

Expected: fails because `adaptor/install-systemd-service.sh` does not exist.

### Task 2: Systemd Installer

**Files:**
- Create: `adaptor/install-systemd-service.sh`

- [ ] **Step 1: Write minimal implementation**

Create the installer with argument parsing for `--name`, `--adapter-dir`, `--user`, `--group`, `--args`, `--no-start`, and `--help`.

- [ ] **Step 2: Run test to verify it passes**

Run: `bash scripts/test-install-systemd-service.sh`

Expected: PASS.

### Task 3: Documentation

**Files:**
- Modify: `adaptor/readme.md`
- Modify: `README.md`

- [ ] **Step 1: Document Ubuntu daemon install**

Add the setup command, service management commands, and optional SSH update restart command.

- [ ] **Step 2: Run verification**

Run: `bash scripts/test-install-systemd-service.sh`

Expected: PASS.
