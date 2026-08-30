# task-4-ship-config-docs

### 목표
- joystick speed 버튼 관련 config(3벌), --learn 헬퍼, 문서 2개 volume_*→speed_* 치환

### 지금
- 브리핑 확인 완료, 대상 파일 clean 상태 확인

### 완료
- HCL 3벌(adaptor/config/extensions.hcl, .example, root extensions.hcl) volume_*→speed_* 치환
- scripts/joystick_input_test.py --learn 출력 키 치환
- docs/manual/joystick-runtime-setup.md, joystick-input-test.md 갱신
- 커밋 7ad3049 완료 (6개 파일만 staged 확인)

### 다음
- (완료됨)

### 검증
- scripts/run-tests.sh --python .../python tests/test_extensions_config.py tests/test_joystick.py tests/test_joystick_runtime.py -> 60 passed
- full suite: 1921 passed, 9 pre-existing failures (recipes/clamp/web, joystick 무관)
- [EXTENSIONS] deprecated 로그가 shipped 파일에서 더 이상 안 뜸 (alias 테스트만 여전히 출력)
