# check-action-blocking-type

### 목표
- action의 blockingType이 실행 순서와 상태에 올바르게 반영되는지 확인한다.

### 지금
- 구현·공식 VDA5050 v3 규칙·동시성 probe의 대조를 완료했다.

### 완료
- order action은 NONE/기타의 2분기로만 처리됨을 확인했다.
- SOFT 병렬 실행, SINGLE 주행 허용, HARD 배타 실행이 지켜지지 않음을 확인했다.
- instant action은 blockingType을 실행 제어에 사용하지 않음을 확인했다.

### 다음
- 수정 요청 시 action scheduler와 blockingType 검증 테스트를 구현한다.

### 검증
- 관련 기존 pytest 6개 통과; NONE→HARD, SOFT→SOFT, SINGLE 동시성 probe로 실제 순서 확인.
