# Dock Timeout Config WebUI Design

## Goal

Make the JIBOT UmDock charging-start timeout operator-configurable from the main config file and WebUI, default it to 90 seconds, and make dock timeout errors tell operators which setting to adjust.

## Design

- Add `[dock].fail_timeout_sec = 90.0` to `config.toml` and `DockConfig`.
- Keep per-motion-rule `fail_timeout_sec` as an optional override. If a dock rule omits it, runtime falls back to `[dock].fail_timeout_sec`.
- Remove the deployed `F2_90_S2CH` inline override from `jibot-config.toml` so AMR #2 uses the new 90 second default.
- Show per-field help text on the WebUI `/config` page. Known fields get explicit Korean/English operational descriptions; unknown scalar fields get a generic but non-empty setting description.
- Expand `JIBOT_DOCK_FAILED` to mention `[dock].fail_timeout_sec` and per-rule `fail_timeout_sec` as the timeout knobs to adjust when charging starts after the failure window.

## Tests

- Config parsing exposes `dock.fail_timeout_sec` and falls back from dock rules.
- WebUI config rendering includes help text for fields.
- Dock failure error includes the timeout setting hint.
