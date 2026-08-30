# WebUI Config Source Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show file path, TOML key path, line number, and source/category badges on the WebUI `/config` page without changing save semantics.

**Architecture:** `core/configio.py` owns source scanning and badge classification. `web/server.py` passes the active config path/text into enumeration. `web/render.py` remains a pure HTML renderer and displays metadata when present while staying backward-compatible with older tuple tests.

**Tech Stack:** Python stdlib `tomllib`, dataclasses, existing stdlib HTTP WebUI renderer, pytest/unittest tests.

---

## File Structure

- Modify `adaptor/core/configio.py`: add metadata dataclasses, line-location scanner, badge classifier, and metadata-aware `iter_config_sections()`.
- Modify `adaptor/web/server.py`: load raw config text once for `/config`, parse it, build metadata using the active config path.
- Modify `adaptor/web/render.py`: render key path, source file/line, badges, and updated page guidance.
- Modify `adaptor/tests/test_configio.py`: add red tests for location and badge metadata; update tuple assertions to handle row objects.
- Modify `adaptor/tests/test_web_render.py`: add red tests for rendered path/key/badges and update fixture rows.
- Modify `adaptor/tests/test_web_server.py`: assert `/config` includes the active config path from a temp file.

## Task 1: Config Metadata Model And Location Scanner

**Files:**
- Modify: `adaptor/core/configio.py`
- Test: `adaptor/tests/test_configio.py`

- [ ] **Step 1: Write failing tests for location scanning**

Append to `adaptor/tests/test_configio.py`:

```python
class ConfigLocationTest(unittest.TestCase):
    def test_scalar_locations_include_path_line_and_key_path(self):
        text = (
            "[mqtt_broker]\n"
            'host = "127.0.0.1"\n'
            "port = 11883\n"
            "\n"
            "[dock.approach_params]\n"
            "detect_charging_signal = true\n"
        )
        locs = configio.scan_scalar_locations(text, "adaptor/config/config.toml")

        host = locs[("mqtt_broker", "host")]
        self.assertEqual(host.path, "adaptor/config/config.toml")
        self.assertEqual(host.line, 2)
        self.assertEqual(host.key_path, "[mqtt_broker].host")

        nested = locs[("dock.approach_params", "detect_charging_signal")]
        self.assertEqual(nested.line, 6)
        self.assertEqual(nested.key_path, "[dock.approach_params].detect_charging_signal")

    def test_location_scanner_ignores_comments_and_array_of_tables(self):
        text = (
            "[settings]\n"
            "# port = 1\n"
            "debug_log = false # keep\n"
            "\n"
            "[[adapter.instances]]\n"
            'name = "line1"\n'
        )
        locs = configio.scan_scalar_locations(text, "/tmp/config.toml")

        self.assertIn(("settings", "debug_log"), locs)
        self.assertNotIn(("settings", "port"), locs)
        self.assertNotIn(("adapter.instances", "name"), locs)
```

- [ ] **Step 2: Run red test**

Run: `cd adaptor && python -m pytest tests/test_configio.py::ConfigLocationTest -q`

Expected: FAIL with `AttributeError: module 'core.configio' has no attribute 'scan_scalar_locations'`.

- [ ] **Step 3: Implement metadata model and scanner**

In `adaptor/core/configio.py`, add imports and dataclasses near the imports:

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfigLocation:
    path: str
    line: int
    key_path: str


@dataclass(frozen=True)
class ConfigScalar:
    section: str
    key: str
    kind: str
    value: Any
    location: Optional[ConfigLocation] = None
    badges: Tuple[str, ...] = field(default_factory=tuple)

    def __iter__(self):
        yield self.key
        yield self.kind
        yield self.value


@dataclass(frozen=True)
class ConfigReadonly:
    section: str
    key: str
    value: Any
    location: Optional[ConfigLocation] = None
    badges: Tuple[str, ...] = ("read-only",)

    def __iter__(self):
        yield self.key
        yield self.value
```

Also change the typing import to include `Mapping` and `Tuple`:

```python
from typing import Any, List, Mapping, Optional, Tuple
```

Add this scanner after `_parse_section_header()`:

```python
def scan_scalar_locations(text: str, path: str | Path = CONFIG_PATH) -> Mapping[Tuple[str, str], ConfigLocation]:
    """Return source locations for scalar key assignments in plain TOML tables."""
    locations: dict[Tuple[str, str], ConfigLocation] = {}
    current: Optional[str] = None
    display_path = str(path)
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        code, _comment = _split_comment(raw_line)
        header = _parse_section_header(code)
        if header is not None:
            stripped = code.strip()
            current = None if stripped.startswith("[[") else header
            continue
        if current is None or "=" not in code:
            continue
        left = code.split("=", 1)[0].strip()
        if not left or any(ch.isspace() for ch in left):
            continue
        locations[(current, left)] = ConfigLocation(
            path=display_path,
            line=line_no,
            key_path=f"[{current}].{left}",
        )
    return locations
```

- [ ] **Step 4: Run scanner tests**

Run: `cd adaptor && python -m pytest tests/test_configio.py::ConfigLocationTest -q`

Expected: PASS.

## Task 2: Badge Classification And Metadata-Aware Enumeration

**Files:**
- Modify: `adaptor/core/configio.py`
- Test: `adaptor/tests/test_configio.py`

- [ ] **Step 1: Write failing tests for badges and rows**

Append to `IterConfigSectionsTest` in `adaptor/tests/test_configio.py`:

```python
    def test_iter_sections_attaches_locations_and_badges(self):
        raw = {
            "mqtt_broker": {"host": "127.0.0.1", "port": 11883},
            "settings": {"jibot_rx_timeout": 10.0},
            "dock": {"nodes": [], "approach_params": {"detect_charging_signal": True}},
            "jibot_client": {"user": "test"},
        }
        text = (
            "[mqtt_broker]\n"
            'host = "127.0.0.1"\n'
            "port = 11883\n"
            "[settings]\n"
            "jibot_rx_timeout = 10.0\n"
            "[dock]\n"
            "nodes = []\n"
            "[dock.approach_params]\n"
            "detect_charging_signal = true\n"
            "[jibot_client]\n"
            'user = "test"\n'
        )
        locs = configio.scan_scalar_locations(text, "config/config.toml")
        secs = dict((s, (sc, ro)) for s, sc, ro in configio.iter_config_sections(raw, locations=locs))

        host = secs["mqtt_broker"][0][0]
        self.assertEqual(host.location.line, 2)
        self.assertIn("robot override", host.badges)

        timeout = secs["settings"][0][0]
        self.assertIn("advanced", timeout.badges)

        overlay = secs["dock.approach_params"][0][0]
        self.assertIn("jibot overlay", overlay.badges)

        user = secs["jibot_client"][0][0]
        self.assertIn("jibot overlay", user.badges)

        readonly = secs["dock"][1][0]
        self.assertEqual(readonly.key, "nodes")
        self.assertIn("read-only", readonly.badges)

    def test_legacy_tuple_unpacking_still_works_for_scalar_rows(self):
        raw = {"mqtt_broker": {"host": "127.0.0.1"}}
        section, scalars, readonly = configio.iter_config_sections(raw)[0]
        key, kind, value = scalars[0]
        self.assertEqual((section, key, kind, value, readonly), ("mqtt_broker", "host", "str", "127.0.0.1", []))
```

- [ ] **Step 2: Run red test**

Run: `cd adaptor && python -m pytest tests/test_configio.py::IterConfigSectionsTest -q`

Expected: FAIL because `iter_config_sections()` does not accept `locations` and scalar rows do not have `badges`.

- [ ] **Step 3: Implement badge helpers**

Add below `_kind_of()` in `adaptor/core/configio.py`:

```python
_ROBOT_OVERRIDE_FIELDS = {
    ("vehicle", "serial_number"),
    ("vehicle", "vehicle_ip"),
    ("vehicle", "vehicle_port"),
    ("ezi", "ezi_io"),
    ("ezi", "ezi_motor"),
    ("mqtt_broker", "host"),
    ("mqtt_broker", "port"),
}

_JIBOT_OVERLAY_FIELDS = {
    ("jibot_client", "user"),
    ("jibot_client", "password"),
    ("jibot_client", "device_type"),
}

_ADVANCED_SECTIONS = {
    "pio_advanced",
    "internal_actions",
    "web_ui",
    "bms_ros",
    "charge_circuit",
}

_ADVANCED_SUFFIXES = (
    "_sec",
    "_timeout_sec",
    "_poll_sec",
    "_interval_sec",
    "_bytes",
)


def field_badges(section: str, key: str, *, readonly: bool = False) -> Tuple[str, ...]:
    badges: list[str] = []
    if readonly:
        badges.append("read-only")
    if (section, key) in _ROBOT_OVERRIDE_FIELDS:
        badges.append("robot override")
    if section == "dock.approach_params" or (section, key) in _JIBOT_OVERLAY_FIELDS:
        badges.append("jibot overlay")
    if section in _ADVANCED_SECTIONS or key.endswith(_ADVANCED_SUFFIXES):
        badges.append("advanced")
    if not badges and not readonly:
        badges.append("base config")
    return tuple(badges)
```

- [ ] **Step 4: Update `iter_config_sections()`**

Replace the function signature and internals with metadata-aware row creation:

```python
def iter_config_sections(
    raw: Any,
    *,
    locations: Optional[Mapping[Tuple[str, str], ConfigLocation]] = None,
    source_path: str | Path = CONFIG_PATH,
) -> List[Tuple[str, List[ConfigScalar], List[ConfigReadonly]]]:
    """Enumerate every ``config.toml`` ``[section]`` for per-field editing."""
    locs = locations or {}
    source = str(source_path)

    def scalar(section: str, key: str, kind: str, val: Any) -> ConfigScalar:
        return ConfigScalar(
            section=section,
            key=key,
            kind=kind,
            value=val,
            location=locs.get((section, key)),
            badges=field_badges(section, key),
        )

    def readonly(section: str, key: str, val: Any) -> ConfigReadonly:
        location = locs.get((section, key))
        if location is None:
            key_path = f"[{section}]" if key in ("(array)", "(value)") else f"[{section}].{key}"
            location = ConfigLocation(path=source, line=0, key_path=key_path)
        return ConfigReadonly(
            section=section,
            key=key,
            value=val,
            location=location,
            badges=field_badges(section, key, readonly=True),
        )

    out: List[Tuple[str, List[ConfigScalar], List[ConfigReadonly]]] = []
    for section, body in raw.items():
        if isinstance(body, dict):
            scalars: List[ConfigScalar] = []
            readonly_rows: List[ConfigReadonly] = []
            nested: List[Tuple[str, dict]] = []
            for key, val in body.items():
                if isinstance(val, dict):
                    nested.append((f"{section}.{key}", val))
                    continue
                kind = _kind_of(val)
                if kind is not None:
                    scalars.append(scalar(section, key, kind, val))
                else:
                    readonly_rows.append(readonly(section, key, val))
            out.append((section, scalars, readonly_rows))
            for nsec, nbody in nested:
                nsc: List[ConfigScalar] = []
                nro: List[ConfigReadonly] = []
                for key, val in nbody.items():
                    kind = _kind_of(val)
                    if kind is not None:
                        nsc.append(scalar(nsec, key, kind, val))
                    else:
                        nro.append(readonly(nsec, key, val))
                out.append((nsec, nsc, nro))
        elif isinstance(body, list):
            out.append((section, [], [readonly(section, "(array)", body)]))
        else:
            out.append((section, [], [readonly(section, "(value)", body)]))
    return out
```

- [ ] **Step 5: Run configio tests**

Run: `cd adaptor && python -m pytest tests/test_configio.py -q`

Expected: PASS.

## Task 3: Server Passes Active Source Metadata

**Files:**
- Modify: `adaptor/web/server.py`
- Test: `adaptor/tests/test_web_server.py`

- [ ] **Step 1: Write failing server test**

In `adaptor/tests/test_web_server.py`, update `test_config_page_get`:

```python
def test_config_page_get(tmp_path):
    web, cfg = _ui_with_config(tmp_path)
    try:
        body = _get(web, "/config").read().decode()
        assert "[mqtt_broker]" in body
        assert "127.0.0.1" in body
        assert 'name="section"' in body
        assert str(cfg) in body
        assert "[mqtt_broker].host" in body
    finally:
        web.stop()
```

- [ ] **Step 2: Run red test**

Run: `cd adaptor && python -m pytest tests/test_web_server.py::test_config_page_get -q`

Expected: FAIL because the rendered config page does not show `str(cfg)`.

- [ ] **Step 3: Update `/config` GET**

In `adaptor/web/server.py`, replace:

```python
raw = configio.load_raw(self._config_path)
sections = configio.iter_config_sections(raw)
```

with:

```python
text = configio.read_text(self._config_path)
raw = configio.load_raw(self._config_path)
locations = configio.scan_scalar_locations(text, self._config_path)
sections = configio.iter_config_sections(
    raw,
    locations=locations,
    source_path=self._config_path,
)
```

- [ ] **Step 4: Run server test**

Run: `cd adaptor && python -m pytest tests/test_web_server.py::test_config_page_get -q`

Expected: still FAIL until render displays metadata; leave this red for Task 4.

## Task 4: Render Metadata On `/config`

**Files:**
- Modify: `adaptor/web/render.py`
- Test: `adaptor/tests/test_web_render.py`

- [ ] **Step 1: Write failing render tests**

Add to `adaptor/tests/test_web_render.py` near `test_config_page_renders_fields`:

```python
def test_config_page_renders_source_metadata_and_badges():
    loc = configio.ConfigLocation(
        path="adaptor/config/config.toml",
        line=42,
        key_path="[mqtt_broker].host",
    )
    row = configio.ConfigScalar(
        section="mqtt_broker",
        key="host",
        kind="str",
        value="127.0.0.1",
        location=loc,
        badges=("robot override", "advanced"),
    )
    sections = [("mqtt_broker", [row], [])]

    out = render.config_page(sections, "tok", {})

    assert "adaptor/config/config.toml:42" in out
    assert "[mqtt_broker].host" in out
    assert "robot override" in out
    assert "advanced" in out
    assert "robots.toml" in out
    assert "jibot-config.toml" in out
```

- [ ] **Step 2: Run red render test**

Run: `cd adaptor && python -m pytest tests/test_web_render.py::test_config_page_renders_source_metadata_and_badges -q`

Expected: FAIL because source metadata and badges are not rendered.

- [ ] **Step 3: Add CSS for metadata and badges**

In `adaptor/web/render.py` CSS, near `/config per-field editor`, add:

```css
.cfg-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:var(--muted);font-size:11.5px;line-height:1.35}
.cfg-source{font-family:var(--font-mono);word-break:break-all}
.cfg-badge{display:inline-flex;align-items:center;min-height:20px;border:1px solid var(--border);border-radius:var(--radius-pill);padding:0 7px;background:var(--neutral-soft);color:var(--secondary);font-size:11px;font-weight:600;white-space:nowrap}
.cfg-badge.read-only{background:var(--warning-soft);color:var(--warning)}
.cfg-badge.robot-override{background:var(--accent-soft);color:var(--accent)}
.cfg-badge.jibot-overlay{background:var(--success-soft);color:var(--success)}
.cfg-badge.advanced{background:var(--danger-soft);color:var(--danger)}
```

- [ ] **Step 4: Add renderer helpers**

Add near `_config_field_form()`:

```python
def _row_attr(row, name: str, default=None):
    return getattr(row, name, default)


def _row_value(row, attr: str, index: int):
    if hasattr(row, attr):
        return getattr(row, attr)
    return row[index]


def _badge_class(label: str) -> str:
    return label.replace(" ", "-")


def _config_row_meta(section: str, key: str, row=None) -> str:
    location = _row_attr(row, "location")
    badges = _row_attr(row, "badges", ()) or ()
    key_path = getattr(location, "key_path", f"[{section}].{key}")
    source = ""
    if location is not None:
        suffix = f":{location.line}" if getattr(location, "line", 0) else ""
        source = f'<span class="cfg-source">{esc(location.path)}{esc(suffix)}</span>'
    badge_html = "".join(
        f'<span class="cfg-badge {_badge_class(str(badge))}">{esc(badge)}</span>'
        for badge in badges
    )
    return (
        '<span class="cfg-meta">'
        f'<span class="cfg-source">{esc(key_path)}</span>'
        f'{source}'
        f'{badge_html}'
        '</span>'
    )
```

- [ ] **Step 5: Update `_config_field_form()`**

Change signature:

```python
def _config_field_form(section: str, key: str, kind: str, value, csrf: str, row=None) -> str:
```

Replace the help span block with:

```python
f'{_config_row_meta(section, key, row)}'
f'<span class="cfg-help">{esc(desc)}</span>'
```

- [ ] **Step 6: Update scalar loop and read-only rendering**

In `config_page()`, replace scalar row iteration with:

```python
        rows = "".join(
            _config_field_form(
                section,
                _row_value(row, "key", 0),
                _row_value(row, "kind", 1),
                _row_value(row, "value", 2),
                csrf,
                row=row,
            )
            for row in scalars
        )
```

Replace read-only item rendering with:

```python
            items = "".join(
                "<tr>"
                f"<td>{esc(_row_value(row, 'key', 0))}</td>"
                f"<td>{_config_row_meta(section, _row_value(row, 'key', 0), row)}</td>"
                f"<td><code>{esc(str(_row_value(row, 'value', 1)))}</code></td>"
                "</tr>"
                for row in readonly
            )
```

- [ ] **Step 7: Update top guidance**

In `config_page()` body text, replace the existing muted paragraph with:

```python
        '<p class="muted">이 페이지는 현재 로드된 config.toml의 scalar 값을 편집합니다. '
        '각 행에는 실제 파일 경로와 TOML 키 위치가 표시됩니다. robots.toml은 실제 '
        '로봇 identity/connectivity를 덮어쓸 수 있고, jibot-config.toml은 JIBOT '
        '하드웨어 특성 overlay로 병합될 수 있습니다. 저장 후 적용하려면 어댑터 '
        '서비스 재시작이 필요합니다. 리스트/배열은 읽기전용입니다.</p>'
```

- [ ] **Step 8: Run render tests**

Run: `cd adaptor && python -m pytest tests/test_web_render.py::test_config_page_renders_fields tests/test_web_render.py::test_config_page_renders_source_metadata_and_badges -q`

Expected: PASS.

## Task 5: Regression Suite For Config Page

**Files:**
- Modify: `adaptor/core/configio.py`
- Modify: `adaptor/web/server.py`
- Modify: `adaptor/web/render.py`
- Test: `adaptor/tests/test_configio.py`, `adaptor/tests/test_web_render.py`, `adaptor/tests/test_web_server.py`

- [ ] **Step 1: Run focused tests**

Run:

```bash
cd adaptor && python -m pytest tests/test_configio.py tests/test_web_render.py::test_config_page_renders_fields tests/test_web_render.py::test_config_page_renders_source_metadata_and_badges tests/test_web_server.py::test_config_page_get tests/test_web_server.py::test_config_save_rewrites_field tests/test_web_server.py::test_config_save_restores_on_invalid tests/test_web_server.py::test_config_unknown_field_rejected tests/test_web_server.py::test_config_bad_coerce_flashes_not_500 -q
```

Expected: PASS.

- [ ] **Step 2: Run whitespace check**

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 3: Commit implementation**

```bash
git add adaptor/core/configio.py adaptor/web/server.py adaptor/web/render.py adaptor/tests/test_configio.py adaptor/tests/test_web_render.py adaptor/tests/test_web_server.py
git commit -m "feat(web): show config source metadata"
```

Expected: commit includes only the implementation and tests for this feature.

## Self-Review Checklist

- Spec coverage: path, line, key path, badges, top guidance, scalar-only save semantics, and read-only arrays all have tasks.
- Red-flag scan: no unresolved marker text or unspecified implementation steps.
- Type consistency: `ConfigLocation`, `ConfigScalar`, and `ConfigReadonly` names are used consistently across configio and render tasks.
