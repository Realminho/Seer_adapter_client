# move-segment-config-knobs

### 목표
- Task 1: Add four configuration knobs (move_start_timeout_sec, move_started_min_travel_mm, move_complete_travel_ratio, move_stall_timeout_sec) to Settings dataclass and config.toml
- Add test to verify defaults are set correctly
- Commit changes with specified files

### 지금
- Task completed successfully

### 완료
- Step 1: Added failing test to test_adapter_jibot_v3_order.py (lines 9209-9216)
- Step 2: Verified test fails with AttributeError as expected
- Step 3: Added config fields to adaptor/config/config.py (lines 268-281)
- Step 4: Added config keys to config.toml (lines 119-126)
- Step 5: Verified test passes
- Step 6: Committed with git add of specific files (config.py, config.toml, test file)
- Report written to task-1-report.md

### 다음
- Task 1 complete. Task 2 can proceed.

### 검증
- Test passes: 2 passed, 587 deselected in 0.18s
- Commit SHA: eeb34a9
- All files committed as specified in brief
