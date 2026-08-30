# check-jibot-network-settings

### 목표
- NetworkManager 프로필 이름과 실제 SSID가 다르게 남는 혼동을 제거한다.

### 지금
- 구현과 검증을 완료했다.

### 완료
- `TP_Link_AMR_5G`는 실제 SSID가 아니라 변경 전 NetworkManager 프로필 이름이 남은 것임을 확인했다.
- 새 SSID 지정 시 NetworkManager `connection.id`도 같이 변경하고, 이름 변경 후에도 같은 프로필을 UUID로 추적/활성화하도록 보완했다.
- nmcli 활성화가 unavailable로 실패해도 실제 wlan0가 목표 SSID에 연결됐는지 최대 10초 확인하여 성공 여부를 정확히 판정하도록 보완했다.

### 다음
- 현재 장비에서는 재연결 없이 프로필 이름만 변경하거나, 다음 스크립트 실행부터 자동 동기화되는지 확인한다.

### 검증
- 외부/embedded 스크립트 `bash -n`, `git diff --check`, pytest 3개 통과.
