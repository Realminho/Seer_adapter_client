# joystick-speed-buttons-review-fixes

### 목표
- 2026-08-12 joystick 속도버튼 작업 code review findings 전부 수정 (Important 1, Minor 4/5/6/7 + doc 5개 지점)

### 지금
- 완료. 커밋 cb30f11.

### 완료
- extensions.py: speed_max_percent > 100 검증 추가, DEFAULT_SPEED_* 상수화, 숫자 캐스팅 실패시 ExtensionsError로 래핑
- config.py: extensions.py의 DEFAULT_SPEED_* import해서 JoystickConfig 필드 기본값으로 사용 (순환 없음)
- plan 문서에서 circular import 관련 obsolete 문장 삭제
- test_extensions_config.py: speed_max_percent 상한 테스트, speed_start>speed_max 테스트, 비숫자 값 테스트, deprecation print 검증 추가
- test_joystick_runtime.py: evdev 경로에서 주행 중 속도버튼 즉시 재전송 테스트 추가
- docs/manual/joystick-input-test.md: :9, :33-34, :47-48(삭제), :61-62, :147 수정 (음량/volume/아직/연결되지 grep 전부 해결)
- tests/test_extensions_config.py, tests/test_joystick.py, tests/test_joystick_runtime.py 개별 실행 통과 확인

### 다음
- (없음, 작업 종료)

### 검증
- tests/test_extensions_config.py: 25 passed (신규 4개 포함)
- tests/test_joystick.py + test_joystick_runtime.py: 38 passed
- 전체 스위트: 9 failed(기존 known failure와 동일), 1924 passed — 신규 실패 없음
- report: .superpowers/sdd/2026-08-12-joystick-speed-buttons/final-fix-report.md
