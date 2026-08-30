# Action Module Deploy Compatibility Design

## 1. Goal

Bring the existing deploy, service-install, documentation, and smoke-test paths
in line with the action-module/WebUI architecture merged in PR #2. Both deploy
scripts already copy the complete `adaptor/` tree, including module packages and
`panel.html`; the remaining work is to clean resources removed by package
migrations and reload both processes that discover modules. The canonical
service names remain `amr-adaptor.service`/`amr-adaptor@*.service` and
`amr-webui.service`.

## 2. Considered approaches

1. **Surgical updates to the current scripts (selected).** Extend existing
   restart and clean lists, point the legacy installer at `run-adapter.sh`, and
   correct tests and operator commands. This preserves current CLI compatibility
   and has the smallest deployment risk.
2. Replace both SSH deploy scripts with one shared deploy framework. This would
   reduce duplication but changes mature SSH, legacy-crypto, and offline-host
   behavior outside the action-module scope.
3. Remove legacy scripts and require `setup-adaptor-service.sh` everywhere. This
   is simpler long-term but breaks documented field procedures immediately.

Approach 1 is selected because every required fix fits existing hooks and no new
abstraction or dependency is needed.

## 3. Behavior changes

### JIBOT deploy

- The existing inline unit discovery remains in place so legacy
  `jibot-adapter*` and misspelled `amr-adapter*` units are still covered.
- The discovery also adds `amr-webui.service` when that unit is installed. If
  WebUI is absent, it is skipped and adapter restart still succeeds.
- `--restart-cmd` remains an override with unchanged semantics.
- `--clean-remote` also removes managed `core`, `extensions`, and `web`
  directories before extracting the new payload. Remote `config.toml`,
  `robots.toml`, virtual environments, runtime data, and logs remain preserved.
- Cleanup remains before payload extraction. `extensions` removal is required
  for file-to-package migrations such as `clamp.py` to `clamp/__init__.py`;
  `core` and `web` removal defensively prevent renamed/deleted source and HTML
  resources from surviving overlay deployment.
- Hosts installed by `scripts/setup-adaptor-service.sh` already grant the deploy
  user passwordless restart access to `amr-webui.service`. The existing restart
  loop keeps its root, passwordless sudo, interactive sudo, and direct
  `systemctl` fallbacks for other installations. Non-interactive hosts without
  that sudoers grant must use `--restart-cmd` appropriate to the host.

### Hexplorer deploy

- Add `--restart`/`--no-restart` and `--restart-cmd` CLI options while retaining
  the existing positional host and remote-directory arguments and `RESTART_CMD`
  environment compatibility.
- Upload `scripts/adaptor-services.sh` into the remote adapter's `scripts/`
  directory in addition to the existing `adaptor/` and `hexplorer_client`
  payloads. No other repository-level script is required by this helper.
- `--restart` invokes the uploaded helper, which restarts the configured adapter
  unit and only those optional WebUI/camera units that are installed. A host
  without `amr-webui.service` therefore still completes the adapter restart.
- The script first resolves `--restart-cmd` precedence or the built-in helper
  command into one remote restart command. When local `/dev/tty` can actually be
  opened, it runs that command through `ssh -tt` using the opened descriptor as
  stdin so password-based remote `sudo` can prompt. A permissions-only test is
  insufficient because `/dev/tty` can be readable without a controlling terminal.
  Headless runs use non-TTY SSH; root and passwordless-sudo hosts therefore remain
  non-interactive, while unavailable sudo still fails visibly.
- The helper remains uploaded even without `--restart`, so the deployed tree has
  the documented manual service-control command and later restarts need no
  additional payload.
- `--clean-remote` receives the same `core`, `extensions`, and `web` cleanup.

### Shared service helper

- `scripts/adaptor-services.sh` treats both `amr-webui.service` and
  `amr-camera.service` as optional installed units. Dry-run output continues to
  show them so operators can preview the full managed stack.
- Adapter unit selection and multi-instance behavior remain unchanged.

### Service installation

- `adaptor/install-systemd-service.sh` remains available for compatibility but
  launches `run-adapter.sh`, not vendor-specific `run-main.sh`.
- Its help text, runner validation, and systemd Description use vendor-neutral
  wording. `--args` remains supported: `run-adapter.sh` consumes only a leading
  `--instance NAME` and forwards remaining arguments to the selected launcher.
- The installer is explicitly documented as adapter-only. The full-stack
  recommendation remains `scripts/setup-adaptor-service.sh`, which installs and
  restarts adapter and WebUI units.

### Documentation and messages

- All JIBOT SSH examples use `--remote-dir DIR`; a second positional argument is
  never documented as a remote path.
- Examples that deploy WebUI/action-module changes restart adapter and WebUI,
  preferably through `--restart` or `scripts/adaptor-services.sh restart`.
- Active setup output says WebUI/control panel, not the removed curses TUI.
  The installed `amr-adaptor.service` template comment receives the same update.
  Historical TUI documentation may remain clearly historical.
- `/etc/sudoers.d/adaptor-tui` and its command alias keep their existing names
  for compatibility; only their comments describe the current WebUI/service
  management role.

## 4. Tests

- Extend `tests/test_update_jibot_adapter_over_ssh.py` to assert module resource
  cleanup, optional WebUI inclusion in the built-in restart path, and the
  existing sudoers coverage for non-interactive WebUI restart.
- Add focused text/CLI assertions for Hexplorer restart options, helper upload,
  restart-command precedence, TTY openability/non-TTY SSH selection, and
  WebUI-absent behavior in `adaptor-services.sh`.
- Update `scripts/test-install-systemd-service.sh` to require
  `run-adapter.sh`.
- Make `scripts/test-run-adapter-dispatch.sh` independent of the tracked fleet
  size by passing an explicit synthetic JIBOT instance and asserting
  `run-main.sh` plus the instance argument.
- Run shell syntax checks, deployment/setup tests, dry-run service management,
  action-module tests, and `git diff --check`.

## 5. Non-goals

- No unified deploy framework, new package manager, or new service.
- No change to hardware action behavior, module discovery, panel rendering, or
  remote configuration preservation.
- No automatic remote deployment during tests.
