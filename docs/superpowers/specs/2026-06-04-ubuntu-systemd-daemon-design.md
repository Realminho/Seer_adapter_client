# Ubuntu Systemd Daemon Design

## Goal

Add a Ubuntu setup command that registers the JIBOT adapter as a boot-time systemd service.

## Approach

Create `adaptor/install-systemd-service.sh` as the local Ubuntu installer. It writes a systemd unit for `adaptor/run-main.sh`, reloads systemd, enables the service, and starts or restarts it.

The installer defaults to:

- service name: `jibot-adapter`
- adapter directory: the directory containing the installer
- run user/group: the current Linux user/group
- unit path: `/etc/systemd/system/jibot-adapter.service`
- startup behavior: `systemctl enable` and `systemctl restart`

## Boundaries

The script does not install Python packages, create the virtual environment, or edit `config/config.toml`. Those are already covered by existing install and SSH update flows.

## Validation

Add a shell test script that runs the installer against a temporary unit directory with fake `sudo` and `systemctl` commands. The test verifies the generated unit and the expected daemon-reload, enable, restart, and status command sequence.
