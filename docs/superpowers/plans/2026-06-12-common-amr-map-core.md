# Common AMR Map Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Common AMR Map Core payload converter for JIBOT and SEER map samples.

**Architecture:** Keep the core in `adaptor/common_amr_map.py` as a small pure-Python module. The module emits vendor-neutral `uamap.core.v1` dictionaries; machine-wise/WCS conversion stays outside this repo and consumes the emitted payload later.

**Tech Stack:** Python 3 standard library, `unittest`, existing `adaptor/tests` style.

---

## Files

- Create: `adaptor/common_amr_map.py` — schema constants, helper parsers, JIBOT importer, SEER importer.
- Create: `adaptor/tests/test_common_amr_map.py` — focused tests for JIBOT and SEER import behavior.
- Create: `docs/reference/common-amr-map-core.md` — map-only standard document.

## Task 1: JIBOT Importer

- [ ] **Step 1: Write failing JIBOT importer tests**

Add tests in `adaptor/tests/test_common_amr_map.py` that import `common_amr_map` and assert:

- `from_jibot_snapshot()` returns `schemaVersion == "uamap.core.v1"`.
- JIBOT `Goal` and `Dock` objects become graph nodes with canonical meter poses.
- JIBOT `PathPoint.vertex` becomes graph edges.
- JIBOT `AvoidArea` becomes an avoid zone.
- JIBOT `ObsPoints` becomes an occupancy layer with `encoding == "nativeRef"`.

- [ ] **Step 2: Run JIBOT importer tests and verify RED**

Run:

```bash
python -m unittest adaptor.tests.test_common_amr_map -v
```

Expected: import or attribute failure because `common_amr_map.py` does not exist yet.

- [ ] **Step 3: Implement minimal JIBOT importer**

Create `adaptor/common_amr_map.py` with helper functions and `from_jibot_snapshot(snapshot, raw_ref=None)`.

- [ ] **Step 4: Run JIBOT importer tests and verify GREEN**

Run:

```bash
python -m unittest adaptor.tests.test_common_amr_map -v
```

Expected: JIBOT tests pass.

## Task 2: SEER Importer

- [ ] **Step 1: Write failing SEER importer tests**

Extend `adaptor/tests/test_common_amr_map.py` to assert:

- `from_seer_smap()` uses `header.mapName` as the map id.
- `advancedPointList` `LocationMark` entries become graph nodes.
- `advancedCurveList` entries become graph edges with Bezier control points.
- `advancedLineList` entries become feature lines.
- `normalPosList` becomes an occupancy layer with `encoding == "nativeRef"`.

- [ ] **Step 2: Run SEER importer tests and verify RED**

Run:

```bash
python -m unittest adaptor.tests.test_common_amr_map -v
```

Expected: SEER test fails because `from_seer_smap()` is missing or incomplete.

- [ ] **Step 3: Implement minimal SEER importer**

Add `from_seer_smap(payload, raw_ref=None)` to `adaptor/common_amr_map.py`.

- [ ] **Step 4: Run importer tests and verify GREEN**

Run:

```bash
python -m unittest adaptor.tests.test_common_amr_map -v
```

Expected: all common map tests pass.

## Task 3: Regression Scope

- [ ] **Step 1: Run existing JIBOT map store tests**

Run:

```bash
python -m unittest adaptor.tests.test_jibot_map_store -v
```

Expected: existing map snapshot behavior still passes.

- [ ] **Step 2: Run combined targeted tests**

Run:

```bash
python -m unittest adaptor.tests.test_common_amr_map adaptor.tests.test_jibot_map_store -v
```

Expected: both test modules pass.

## Self-Review

- The first version intentionally does not write WCS DB records.
- The first version intentionally stores large occupancy arrays by native reference instead of duplicating points.
- Vendor-native export is documented but not implemented in this slice.
