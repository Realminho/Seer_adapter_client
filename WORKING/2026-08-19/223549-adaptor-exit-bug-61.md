# adaptor-exit-bug-61

### 목표
- 192.168.101.61(HN-SH6-TR-001) adaptor 종료 버그 원인 규명

### 지금
- Phase 1 근본원인 조사. 저널상 비정상 종료 흔적 0건 → 사용자 관측 증상 확인 필요

### 완료 (22:35~22:50)
- 22:35 접속 시도 실패(No route to host). WiFi 끊김 구간(22:37~22:43)과 일치
- 22:44 ARP 복귀 후 무선 192.168.101.61로 접속 성공 (10.6.6.6은 이 PC에 유선 경로 없음)
- 서비스 현재 상태: active, ExecMainStatus=0, Result=success, NRestarts=0,
  기동 21:50:15 (PID 545090)
- **저널 전 구간(8/18 20:31~현재) amr-adaptor 비정상 종료 0건**.
  모든 종료가 `Stopping… → Succeeded → Stopped` 정상 경로
- `[TASK DIED]` / `[TASK EXITED]` / `[ADAPTER EXIT]` 0건 → 감시 가드 미발동
  (가드 코드는 배포됨: `_supervise_task` 5회 등장)
- Traceback 0건, OOM/kill 0건, mem 여유(1.7G free), load ~7.0
- 21:50 종료의 정체: `ucore` pts/0에서 사람이 친 `sudo systemctl restart`
  (xboxdrv→adaptor→webui→camera 순차, 배포 스크립트 패턴)
- 14:18 / 14:24 / 18:47 의 "Stop 없는 Started"는 **호스트 재부팅**
  (`journalctl --list-boots`로 boot -3/-2/-1 경계 확인)
- 22:37:29 MQTT rc=16 끊김 → 22:43:04 재연결. 이 구간에도 프로세스는 생존,
  publish 실패 로그 0건(자동 재연결로 흡수)

### 다음
- 사용자 관측 증상 확인: 어디서 "종료"로 보였는지 / 대략 시각
  (FMS OFFLINE인지, WebUI 서비스 stopped인지, 수동 실행이 죽은 건지)
- 시각을 받으면 해당 구간 저널 정밀 조사

### 검증
- `systemctl show amr-adaptor -p Result -p ExecMainStatus -p NRestarts` → success / 0 / 0
- `journalctl _COMM=systemd | grep amr-adaptor` 전 구간 → Failed/Scheduled restart 0건
