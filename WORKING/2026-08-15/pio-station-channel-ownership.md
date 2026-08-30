# pio-station-channel-ownership

### 목표
- station_id/channel을 extension "pio"에서 설비 블록으로 이전
- 옛 키가 남아 있으면 부팅 거부(무엇을 고칠지 알려주며)

### 지금
- 구현·리뷰 완료. .61 설정 이전과 배포 대기

### 완료
- 1dc559a 설비 블록에 필드 추가 (airshower: pio_station_id+channel, elevator: channel)
- 8128a55 소비자 4곳 전환 + pio_link_params 폴백 제거
- 2b47f77 옛 필드 삭제 + 부팅 거부 가드
- 3abb13c pioPing/pioScenario 필수화 + 패널
- ff0f6a5 $field_<action>_<name> 치환 (3abb13c의 짝. 그전까지 패널이 조용히 자동 폼으로 폴백)
- 0df8bc9 최종 리뷰 지적 10건 수정

### 소유 구조
- extension "airshower": pio_station_id + channel  (한 설비 = 한 주소)
- extension "elevator": channel 1개 (시스템의 무선 채널)
  motion_rules의 pio_station_id는 층마다 다르므로 그대로
- recipe pioInit: stationId + channel 리터럴
- extension "pio": media/port/vehicle_num만 (로봇 고유값)

### 다음 — .61 배포 (코드와 설정이 함께 나가야 함)
1. .61 extensions.hcl에서 extension "pio"의 station_id, channel 두 줄 삭제
2. extension "airshower"에 pio_station_id = "123456", channel = 250 추가
3. extension "elevator"에 channel = 250 추가
4. 코드 배포 후 재시작
- 순서를 어기면 부팅 거부됨 (의도된 동작). .61은 아직 재시작 전이라 지금은 안전
- 에어샤워 "123456"은 기존 동작 그대로 옮긴 값. 현장 실제 station 확인 필요

### 검증
- 전체 1959 passed / 7 failed. 7건은 착수 전과 동일 (pio_init/pio_ping ×6 + goto timeout)
- 착수 시점 1951 passed → +8 (새 테스트)
- 최종 리뷰 Critical: 가드 메시지가 channel을 motion_rules에 넣으라고 안내 → 그대로 따르면
  라벨 없는 KeyError. 계획 결함이 코드로 그대로 옮겨간 것. 수정 후 재리뷰 전건 해소
- park: 설비 키가 빈 문자열이면 짝 검증이 통과함. 가드는 키 존재만 검사
