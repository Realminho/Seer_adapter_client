# diagnose-dock-node-unreached

### 목표
- 1_01CH JIBOT_NODE_UNREACHED 원인을 특정하고 dock 이동 의미가 UmDock으로 연결되는지 검증한다.

### 지금
- 192.168.101.61 연결과 포트를 확인했으나 설치된 키로 SSH 인증할 수 없어 원격 파일 확인은 인증 정보 대기 상태다.

### 완료
- hana.json에서 1_01CH가 Goal이 아닌 Dock 객체(10186,-2533)임을 확인했다.
- 기존 motion_rules에 1_01CH가 없어 UmGoto가 발행되고 path=0/Stopped로 즉시 종료된 원인을 특정했다.
- 1_01CH를 from 없는 mode=dock 규칙으로 등록해 모든 접근에서 UmDock을 선택하게 했다.
- JIBOT_NODE_UNREACHED에 command=UmGoto를 추가하고 station 불일치 중심의 부정확한 설명을 pose/semantic 오류로 수정했다.
- 도착 신호 문서에 Dock 대상의 motion rule/UmDock 정책을 추가했다.
- 후속 오류의 command=UmGoto로 보아 새 진단 코드는 실행 중임을 확인했다.
- 배포 스크립트 기본값 CONFIG_TOML_MODE=keep과 원격 config.toml 보존 가능성을 확인했으나, 사용자가 rule을 이미 설정했다고 알려와 런타임 값을 추가 점검한다.
- 현재 작업 트리 config.toml 23행에 `{ to = "1_01CH", mode = "dock" }`가 이미 있으며 TOML 파싱 결과도 정상이다.
- 저장된 실제 송신 기록의 UmGoto는 모두 2026-07-27 기록이고, 이 rule 추가분은 아직 미커밋 변경이다.
- elevator rule이 dock motion_rules를 덮던 버그 수정은 현재 HEAD(3784add)에 이미 포함돼 로컬 최신 코드에서는 재현되지 않는다.
- 192.168.101.61의 TCP 22와 JIBOT 제어 포트 7273이 모두 열려 있음을 확인했다.
- ucore/ubuntu/root/robot/jibot 계정에 현재 설치된 키로 인증을 시도했으나 모두 Permission denied였다.

### 다음
- 192.168.101.61의 SSH 계정/키 또는 어댑터가 실제 실행되는 별도 호스트를 확인한 뒤 서비스 실행 경로와 유효 rule을 대조한다.

### 검증
- config 로드 결과 None->1_01CH dock 확인.
- 1_01CH가 UmGoto 없이 UmDock 1회로 완료되는 회귀 테스트 통과.
- dock/charge/unreached 관련 72건 통과, git diff --check 통과.
- stdlib tomllib로 motion_rules 파싱 및 from 없는 1_01CH dock rule 단언 통과.
- bot A/B 읽기 전용 SSH 확인 시도: 두 호스트 모두 connection timeout.
- 192.168.101.61 포트 확인: 22 open, 7273 open. SSH는 모든 후보 계정에서 인증 거부.
