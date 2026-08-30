# local-adaptor-remote-jibot

### 목표
- 로컬 Ultimate 2 입력으로 원격 JIBOT이 움직이지 않는 원인 진단

### 지금
- receiver-only 상태 진단 및 udev fallback 추가 완료

### 완료
- 작업 복구 로그 생성
- run-main.sh가 인자를 main.py로 전달하며 --vehicle-ip와 --id를 지원함을 확인
- Micro 키 매핑은 준비되어 있으나 joystick.enabled와 micro_enabled가 모두 false임을 확인
- joystick 활성화 시 slot 1~16의 미구현/비활성 액션 매핑까지 검증되어 enableMotor에서 종료됨을 확인
- joystick/ultimate2를 활성화하고 Micro를 비활성 상태로 유지
- 액션 1~16은 보존하고 현재 비활성 대상에 맞춰 각 매핑을 enabled=false 처리
- USB sysfs 전체 순회 대신 3초 제한 lsusb 조회를 사용하도록 입력 테스트 도구 수정
- lsusb timeout 시 udev DB에서 8BitDo를 찾고 3109 receiver-only 상태를 안내하도록 수정

### 다음
- 컨트롤러를 깨우거나 receiver와 재페어링해 3109가 310b로 바뀌는지 확인

### 검증
- `bash adaptor/run-main.sh --help`에서 옵션 확인; 기본 JIBOT TCP 포트 7273 확인
- extensions.hcl 및 jibot-config.toml 실설정 확인
- extensions.hcl의 slot 1 enableMotor 매핑과 비활성 액션 상태 확인
- joystick.enabled=false, ultimate2_enabled=false 및 /dev/input/js*, event* 부재 확인
- 설정 로더: enabled=True, ultimate2=True, micro=False, actions=16, active_actions=0
- joystick 단위 테스트 16개 통과
- joystick_input_test.py --list 정상 종료(exit 0), py_compile 통과, 단위 테스트 16개 재통과
- udev에서 8BitDo UM 2 Receiver 2dc8:3109 확인; 개선된 --list 출력 및 테스트 16개 통과
