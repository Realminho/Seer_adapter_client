# joystick-input-mapping

### 목표
- 전체 변경사항을 보존하고 Bluetooth/USB 조이스틱 연결, 입력 확인, 키 매핑 기반을 추가해 커밋한다.

### 지금
- 작업 완료.

### 완료
- Bluetooth/USB 장치 탐지, Linux joystick 이벤트 출력, JSON 논리 매핑 도구와 매뉴얼 및 단위 테스트 추가.
- 기존 미커밋 상태 action 및 proximity 변경을 포함한 관련 테스트 검증.
- 전체 변경사항 커밋.

### 다음
- 없음.

### 검증
- 관련 테스트: 146 passed, 2 subtests passed. 조이스틱 단위 테스트: 4 passed. 전체 스위트는 기존 PIO 실패 및 장시간 대기로 중단.
