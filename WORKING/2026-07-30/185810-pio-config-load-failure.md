# pio-config-load-failure

### 목표
- PIO output_pin_map의 0 값으로 인한 서비스 시작 실패 원인을 확인한다.

### 지금
- 원인 확인과 로봇 현장 설정 수정 방법 정리를 완료했다.

### 완료
- PIO out 키는 1~8, EZI IO 핀 값은 0~15이며 로컬 기본 설정은 정상 로드됨을 확인했다.
- 배포 시 보존된 extensions.hcl의 output_pin_map에 0 키가 남은 경우임을 확인했다.

### 다음
- 로봇의 실제 extensions.hcl에서 키 0을 올바른 PIO out 번호로 수정하고 서비스를 재시작한다.

### 검증
- adaptor/config/extensions.hcl 및 루트 extensions.hcl을 get_config()로 각각 정상 로드했다.
