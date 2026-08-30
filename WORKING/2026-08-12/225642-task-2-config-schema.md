# task-2-config-schema

### 목표
- Task 2 브리프대로 joystick 설정 스키마 변경: volume_* -> speed_* 리네임 + speed range 추가 + legacy alias 지원

### 지금
- 완료. 커밋함 (4b05bab)

### 완료
- config.py: volume_* -> speed_* 리네임, speed range 4개 필드 추가
- extensions.py: legacy alias 처리 + speed range validation 추가
- test_extensions_config.py: TDD로 4개 테스트 추가, RED->GREEN 확인
- 커밋 4b05bab (3개 파일만: config.py, extensions.py, test_extensions_config.py)

### 다음
- (완료, 후속 작업 없음 — Task 3가 joystick_runtime.py 담당)

### 검증
- focused: tests/test_extensions_config.py tests/test_joystick.py tests/test_joystick_runtime.py -q -> 57 passed
- full suite: 9 failed(전부 무관/기존 실패, 확인됨), 1918 passed
