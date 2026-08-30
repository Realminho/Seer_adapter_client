# amr62-state-frequency

### 목표
- 62번 AMR의 `state_frequency` 설정 로드 실패 원인을 확인하고 수정한다.

### 지금
- 수정 및 가능한 범위의 검증을 마쳤다.

### 완료
- `state_frequency` 등 보존 설정 5개의 로드 호환성을 복구했다.
- 기존 설정 키 회귀 테스트를 추가했다.

### 다음
- 의존성이 설치된 환경에서 전체 설정 테스트를 실행한 뒤 62번에 재배포한다.

### 검증
- `py_compile`, `git diff --check`, 독립 `Settings` 구성을 통과했다. 시스템 Python의 `hcl2` 부재로 pytest 수집은 불가했다.
