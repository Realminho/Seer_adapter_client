# jibot-connect-62

### 목표
- 192.168.101.62에서 jibot(10.8.8.8:7273) 접속 실패 원인 파악

### 지금
- 원인 규명 완료. 현재는 정상 연결 상태(21:39:29 복구)

### 완료
- Errno 111 = 로컬 7273 리스너 부재. 10.8.8.8은 로봇 자신의 eth0 → 네트워크 문제 아님
- 7273 서버는 `jarvis-g`(urobot.service / roslaunch 노드 `jarvis_g`)
- 타임라인: 21:27:30 재부팅 → 21:27:35 urobot 1차 실행 → 21:28:12 어댑터 첫 시도 실패
  → 21:38:33 사용자가 192.168.101.51에서 `sudo systemctl restart urobot`
  → 21:39:20 jarvis-g 기동, 21:39:28 배너 `jarvis-g:a2bc90b3`, 21:39:29 첫 JIBOT RX
- urobot 1차 실행에서 jarvis-g가 끝까지 안 뜸: 기동 배너 없음, stop 시 SIGKILL 대상
  목록(roslaunch/nodelet만)에도 jarvis-g 없음
- 1차 실행은 스택 전체가 반쯤 죽음: `map` TF 프레임 부재(tf2odom 경고 폭주, 재시작 후 0건),
  Berxel 뎁스 카메라 미검출(`Get No Connected BerxelDevice`, `readDepthFrame() failed with -9`)
- 어댑터 무결: 5초 재접속 루프 정상 동작, 서버 뜨자마자 즉시 연결
- 이번 부팅만 이상. 직전 11개 부팅은 어댑터 첫 시도 후 16~26초 내 연결, 이번만 11분 17초
- 현재 ESTAB 확인: adaptor(pid 2040) ↔ 10.8.8.8:7273, 외부 클라이언트 192.168.101.55도 접속 중

### 다음
- 재발 시 즉시 조치: `sudo systemctl restart urobot`
- 로그 소실 해결 우선: `/usr/local/urobot/logs/jarvis`(ROS_LOG_DIR)가 통째로 사라져
  1차 실행 jarvis_g 노드 로그 확보 불가. 현재 실행 중에도 디렉터리 없음 → 삭제 주체 추적 필요
- 부팅 후 7273 미개방 감시/자동 복구 도입 검토 (어댑터가 상태를 이미 알고 있음)

### 검증
- `ss -ltnp`(7273 → jarvis-g pid 5651), `journalctl -u urobot/-u amr-adaptor -b`,
  부팅별 첫 JIBOT RX 지연 비교(boot -0 ~ -11), `last -x`/sudo 로그로 수동 재시작 확인
