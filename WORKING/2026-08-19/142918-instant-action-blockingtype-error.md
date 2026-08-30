# instant-action-blockingtype-error

### 목표
- gotoNearestNode instant action이 "require blockingType NONE" 로 FAILED 되는 원인 설명

### 지금
- adaptor/adapter_jibot.py instant_actions_accept_procedure 검증 로직 확인 완료

### 완료
- 원인 확인: instant action blockingType != NONE 이면 무조건 FAILED + invalidInstantAction 에러 기록

### 다음
- 없음 (질의 응답만, 코드 변경 없음)

### 검증
- adaptor/adapter_jibot.py:5789-5807, docs/todo/vda5050-compliance-gaps.md:34-42 근거 확인
