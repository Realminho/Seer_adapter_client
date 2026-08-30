# clean-old-toml-refs

### 목표
- Task 9: WebUI·registry에 남은 낡은 참조 정리
- 구 `robots.toml` 참조를 `robots.hcl`로 변경 (문구, 테스트 픽스처)
- 5개 파일 수정: render.py, configio.py, registry.py, test_web_render.py, test_fleet_registry.py

### 완료
- Step 1: test_web_render.py 단언 고침 (3곳: line 103, 1004-1008, 1035)
- Step 2: 테스트 실패 확인 (RED 상태)
- Step 3: render.py 3곳 고침 (1877, 1892, 1902) + docstring
- Step 4: configio.py sed 7곳 고침 (git diff로 확인)
- Step 5: registry.py sed 3곳 고침 (git diff로 확인)
- Step 6: test_fleet_registry.py line 112 고침
- Step 7: 테스트 통과 (234 passed, 41 failed but expected)
- Step 8: grep 검증 (intentional refs only: convert, migration doc)
- Step 9: 커밋 (d71190a)

### 검증
- 대상 테스트: 234 passed
- 전체 스위트: 41 failed (test_adapter_jibot_v3_order.py, test_dock_approach_config.py만)
- git diff: 정확히 예상 개수 변경 (render.py 3 + configio.py 7 + registry.py 3 + test_web_render.py 3 + test_fleet_registry.py 1 = 17곳)
- grep: 남은 것은 의도된 참조만 (converter test, migration guide)
