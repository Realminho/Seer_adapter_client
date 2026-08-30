# reject-cross-amr-command-and-stale-cancel

### 목표
- 다른 AMR 명령 차단 및 이전 cancelOrder가 신규 주문을 제거하는 race 수정

### 지금
- 교차 AMR 차단 및 stale cancel race 수정 완료

### 완료
- Order/instantActions의 토픽 serial이 현재 AMR과 일치할 때만 실행하도록 차단
- payload serial은 누락·빈 값이면 허용하고 값이 있으면 현재 AMR과 일치하는지 추가 검증
- HN-SH6-TR-002 취소 명령을 HN-SH6-TR-001이 거부하는 테스트 추가
- cancelOrder 시작 시점 주문 ID를 고정하고 정지 대기 중 들어온 신규 주문 보존
- 이전 cancel과 신규 order 경합 회귀 테스트 추가

### 다음
- AMR1/2 배포 후 신규 주문 전체 흐름 재실증

### 검증
- stale cancel·기존 cancel·토픽 격리 집중 테스트 6건 통과
- 전체 단일 파일 테스트는 장시간 정지로 중단했으며 실행 중 기존 fixture 1건을 보완 후 집중 재검증
