# check-pio-elevator-action-logs

### 목표
- pioElevatorMove1/Move2/Open/Close의 실제 실행 로그와 성공 여부를 확인한다.

### 지금
- AMR2 journal과 원격 recipe 설정 대조를 완료했다.

### 완료
- Move1/Open/Close의 최신 실행에서 init, 출력 ON/OFF, cleanup, disconnect가 ok:true임을 확인했다.
- Move2는 최근 이틀 journal에 수신 또는 실행 기록이 없음을 확인했다.
- 원격 Close cleanup이 close(out3)가 아닌 open(out4)을 OFF하는 배포 설정 오류를 확인했다.

### 다음
- 원격 recipes.hcl을 현재 저장소 버전으로 배포한 뒤 Move2 및 Close 실패-cleanup을 시험한다.

### 검증
- 192.168.101.62 amr-adaptor journal 2026-07-30~31과 /home/ucore/adaptor/config/recipes.hcl 대조.
