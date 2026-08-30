# WebUI Config Source Metadata Design

Date: 2026-06-29
Status: Approved design
Scope: `unified-amr-adaptor` WebUI `/config`

## Problem

The WebUI `/config` page already edits scalar values from the active
`config.toml`, preserving comments and rolling back invalid edits. Operators can
change values, but the page does not make it obvious where each setting lives or
which settings are normal operational knobs versus older, advanced, overlay, or
robot-specific configuration.

Operators need two things:

- A clear answer to "can this `config.toml` setting be edited from WebUI?"
- A clear answer to "where is this setting defined, and what other config source
  might override or own it?"

## Goals

- Show the source file path for each rendered config field.
- Show the exact TOML key path, such as `[settings].last_node_capture_mode`.
- Preserve the existing scalar-only edit model and rollback behavior.
- Distinguish common config categories with visible badges.
- Explain at the top of `/config` how `config.toml`, `jibot-config.toml`, and
  `robots.toml` relate.

## Non-Goals

- Do not add editing support for arrays, inline tables, or `motion_rules`.
- Do not edit `jibot-config.toml` or `robots.toml` from this change.
- Do not change runtime config precedence.
- Do not reload adapter services automatically after saving.
- Do not reorganize `/config` into tabs yet.

## Current Behavior

`core/configio.iter_config_sections(raw)` returns `(section, scalars, readonly)`
tuples from parsed TOML. `web.render.config_page()` renders every scalar as its
own POST form. `web.server._post_config()` rewrites a single scalar line in the
active config file and validates the file by reloading it through `get_config`.

This design keeps that flow. It only adds metadata to enumeration and rendering.

## Design

### Source Location

`core/configio` will expose a location lookup for the active config text:

- Input: TOML text and the file path passed to WebUI.
- Output: a mapping from `(section, key)` to `ConfigLocation`.
- `ConfigLocation` contains:
  - `path`: display path for the active config file.
  - `line`: 1-based line number of the actual `key = value` line.
  - `key_path`: `[section].key`.

The scan should reuse the same section parsing rules as `rewrite_scalar` so the
displayed line number matches the line that saving would edit.

Nested sections such as `[sound_settings.state_replay_gap_overrides]` are
reported by their dotted section name. Top-level arrays such as `motion_rules`
remain read-only and do not need per-key scalar locations.

### Field Metadata

`iter_config_sections()` will return row objects or tuples that include:

- `section`
- `key`
- `kind`
- `value`
- `location`
- `badges`

To limit churn, the public shape may remain tuple-based if tests are clearer, but
rendering must receive the location and badges for every scalar row.

Read-only entries will also carry a section-level source label:

- `path`
- `[section].key` when the read-only item is under a table
- `[section]` for a top-level array
- `read-only` badge

### Badges

Badges are informational. They do not change edit permissions.

- `base config`: scalar value saved directly to the active `config.toml`.
- `read-only`: lists, arrays, tables, and other values not edited by WebUI.
- `robot override`: values normally owned by deployment inventory
  `robots.toml` for real robots. Initial keys:
  - `[vehicle].serial_number`
  - `[vehicle].vehicle_ip`
  - `[vehicle].vehicle_port`
  - `[ezi].ezi_io`
  - `[ezi].ezi_motor`
  - `[mqtt_broker].host`
  - `[mqtt_broker].port`
- `jibot overlay`: JIBOT hardware-characteristic values that may belong in the
  optional `jibot-config.toml` overlay. Initial keys:
  - `[dock.approach_params].*`
  - `[jibot_client].user`
  - `[jibot_client].password`
  - `[jibot_client].device_type`
- `advanced`: timing, timeout, polling, internal contract, or low-level hardware
  knobs. Initial rule: keys ending in `_sec`, `_timeout_sec`, `_poll_sec`,
  `_interval_sec`, `_bytes`, plus all fields in `[pio_advanced]`,
  `[internal_actions]`, `[web_ui]`, `[bms_ros]`, and `[charge_circuit]`.

If a field matches multiple categories, render all matching badges except that
`base config` may be omitted when a more specific badge is present. This keeps
the UI from becoming noisy while preserving the source path.

### UI Rendering

Each config row will show:

- Field name and type.
- Key path, e.g. `[dock].fail_timeout_sec`.
- Source path and line, e.g. `adaptor/config/config.toml:187`.
- Badges.
- Existing description text.

The top of `/config` will include a short note:

> This page edits scalar values in the active `config.toml`. Robot identity and
> connectivity may be overridden by `robots.toml`; JIBOT hardware characteristics
> may be merged from `jibot-config.toml`. Lists and arrays are shown read-only.

Use Korean UI text consistent with the current page.

### Saving

Saving remains unchanged:

1. POST one scalar value.
2. Coerce according to `kind`.
3. Rewrite only that scalar line.
4. Validate on disk.
5. Roll back on invalid config.

The new metadata must not be trusted for writes. `_post_config()` should continue
to rely on `rewrite_scalar()` and validation as the safety boundary.

## Testing

Add focused tests:

- `configio` returns line numbers for simple and nested scalar keys.
- `iter_config_sections()` includes source metadata for scalar fields.
- `iter_config_sections()` marks list/array values read-only with source info.
- Known robot override fields get the `robot override` badge.
- Known JIBOT overlay fields get the `jibot overlay` badge.
- Advanced timing fields get the `advanced` badge.
- `render.config_page()` shows file path, line number, key path, and badges.
- Existing config save tests continue to pass unchanged.

## Risks

Line-number scanning can drift from write behavior if it uses different parsing
rules. Reuse `_split_comment()` and `_parse_section_header()` to keep read and
write interpretation aligned.

Badge classification can be imperfect. The initial set should be conservative
and display-only; incorrect badges must not block edits or alter config
precedence.

## Out of Scope Follow-Up

Once metadata exists, `/config` can later add tabs or filters such as
"운영 설정", "고급 설정", and "읽기전용" without changing the underlying config
edit logic.
