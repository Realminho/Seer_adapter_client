# fix-action-blocking-scheduler

### 목표
- HARD action이 주행·다른 action과 겹치지 않게 하고 모든 blockingType 규칙을 보완한다.

### 지금
- 구현과 관련 회귀 검증을 완료했다.

### 완료
- 실로그에서 도착 반경 판정 후 MRosGoto가 지속된 채 HARD가 시작된 원인을 확정했다.
- HARD/SOFT 시작 전 자동 주행 정지, 타입별 병렬·배타 실행, 취소 정리를 구현했다.
- 미등록 action 실패 처리와 instant action의 NONE 전용 검증을 보완했다.

### 다음
- AMR2에 배포·재시작 후 동일 elevator order로 실기 동작을 재검증한다.

### 검증
- py_compile/diff-check 통과; blocking·instant·cancel 집중 36건, registry·recipe·map 30건 통과(실기 2건 제외).
