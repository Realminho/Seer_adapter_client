# ubuntu-desktop-gui-down

### 목표
- GUI 미표시 원인 규명 → (재부팅 불가 상황) 드라이버 업데이트 가능성 검토

### 지금
- 드라이버 업데이트 조사 완료. "재부팅 없이는 무효 + 지금 실행 시 위험" 결론
- 사용자 승인 대기: 로그 폭주로 인한 SSD 쓰기 억제 조치

### 완료
- 근본 원인: NVIDIA GPU(RTX PRO 6000 Blackwell, MSI, 01:00.0) GSP 펌웨어 응답 불능
  - 08:57:35 MMIO 읽기 0xbadf3200 시작 / 08:58:50 Xid 119 GSP RPC 45s 타임아웃
    (스택: IdleTimerProc → nvkms_kthread_q_callback [nvidia_modeset])
  - 09:00:21 Xid 154 "GPU Reset Required" / 09:22~10:58 Xid 16 반복
  - Xorg(18368) D 상태로 정지 → GUI 미표시
- 드라이버 버전 조사
  - 현재: nvidia-driver-580-open 580.173.02 (noble-updates/restricted, DKMS)
  - 가능: 590.48.01 / 595.84 (ubuntu-drivers 권장) / 610.43.02 (최신) — 모두 noble-updates/multiverse
  - 커널 7.0.0-28-generic, DKMS 580.173.02 빌드됨(7.0.0-28, 7.0.0-29)
- 지금 업데이트하면 안 되는 근거
  1. nvidia-dkms-580-open.prerm → `dkms remove --all` → 모듈 언로드 시도
     nvidia refcount 571, /dev/nvidia* 점유 프로세스 31개(다수 D) → 행/실패 → dpkg 트랜잭션 파손 위험
  2. 595 설치 시 580 계열 15개 패키지 제거됨
  3. 기존 모듈 언로드 불가 → 새 모듈 로드 불가 → 재부팅 전까지 효과 0
- 추가 발견: 커널 로그 폭주로 SSD 쓰기 약 140MB/s (시간당 약 500GB), 08:57부터 누적 약 1.5TB
  - journald Storage=auto(디스크), kernel.printk_ratelimit=5 이나 NVRM 메시지가 우회
  - 디스크 여유는 충분(1.3T), journald는 4GB 상한 회전 → 용량 문제 아님, SSD 수명 문제

### 다음
- 승인 시: journald Storage=volatile 로 전환해 SSD 쓰기 차단 (재부팅 불필요)
- 재부팅 가능 시점에: 610-open 또는 595-open 설치 → 재부팅
- 595/610 릴리스 노트에서 이 GSP 행 수정 여부 미확인 (WebSearch 필요)

### 검증
- apt-cache policy / apt-get --simulate / dpkg prerm 원문 / /proc/modules refcount / lsof /dev/nvidia*
- /proc/diskstats 10초 측정으로 쓰기량 확인
- 진단 중 실행한 nvidia-smi(564737)는 SIGTERM으로 종료됨, 잔여 프로세스 없음
