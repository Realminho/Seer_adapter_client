# update-vda-loads

### 목표
- loadId는 실제 Lot ID가 있을 때만 보내고 loadType은 항상 TRAY로 전송

### 지금
- 구현 및 검증 완료

### 완료
- 광센서 기반 load에서 가짜 tray-NNN loadId 제거
- 적재 load의 loadType을 TRAY로 고정
- loadId가 None이면 생략되고 실제 값이 있으면 직렬화되는 동작 확인

### 다음
- 실제 Lot ID 입력 경로가 추가되면 Load.load_id에 해당 값을 연결

### 검증
- 관련 EZIO 회귀 테스트 2개 통과
- Load 직렬화: ID 없음은 loadId 생략, LOT-42는 loadId=LOT-42 확인
- git diff --check 통과
