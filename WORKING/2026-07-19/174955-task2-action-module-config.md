# task2-action-module-config

### 목표
- `ActionModuleConfig`와 `Config.action_modules`를 TDD로 추가한다.

### 지금
- 구현과 검증을 마치고 task 파일만 커밋한다.

### 완료
- action_modules 테스트가 누락 필드로 실패하는 RED를 확인했다.
- `ActionModuleConfig`, 기본 빈 목록, TOML 테이블 파싱을 추가했다.

### 다음
- 커밋 SHA와 검증 결과를 상위 에이전트에 전달한다.

### 검증
- RED: 2 failed, `AttributeError: Config has no attribute action_modules`; GREEN: focused 2 passed, full 27 passed; `git diff --check` 통과.
