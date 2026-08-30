# jibot-7273-down-again

### 목표
- 10.8.8.8 의 7273 미개방 원인 확인 (8/20 건과 동일 여부 판별)

### 지금
- **완료.** 신 워치독 설치·검증 통과 (08-26)

### 완료
- 7273 리스너 없음. 어댑터는 5초마다 `[Errno 111]` 재접속 실패 반복
- jarvis-g: 06:47:14 기동 → 06:48:05 `exit[-1] reason:"Bot start failed!" run time 51s`
  → 이후 재시도 없음. 프로세스 0개, `rosnode list` 에도 jarvis_g 없음
- **USB 정상** — `/dev/ttyUSB0~4` 5개, 다운스트림 8개
  → 8/20 의 USB 허브 미열거 고장과 **다른 모드**. 브링업 실패(CPU 경쟁) 쪽
- **워치독이 통째로 제거돼 있음**
  - `/usr/local/sbin/urobot-watchdog` 없음
  - `urobot-watchdog.service` / `.timer` 없음, 로그 0건(8/11 이후 전무)
  - `/etc/systemd/system/urobot.service.d/` 없음 → `ExecStartPre=sleep 30` 부트 딜레이(A-2)도 소실
  - 결과: 06:46:22 부팅 후 urobot.service 는 `Started` 1회뿐, 자동 재시작 0회
- 부트 딜레이가 사라져 부팅 버스트와 31노드 launch 가 겹친 것이 실패 확률을 올렸을 수 있음
- 8/22 작성된 신 워치독(`scripts/urobot-watchdog.sh`)은 레포에만 있고 로봇 미배포

### 다음
1. `sudo systemctl restart urobot` — 이 모드는 재시작으로 복구되는 부류
2. 신 워치독 배포: `scripts/robot-host/install-urobot-watchdog.sh --min-serial 4`
   (부트 딜레이 드롭인 A-2 도 함께 복구됨)
3. 복구 확인: `ss -ltnp | grep 7273` → 어댑터 `[JIBOT RX]` 재개

### 검증
- ss/ps/rosnode, `/usr/local/urobot/log/log-20260825-064714.log` 종료 라인,
  `journalctl -u urobot -b`(systemd 이벤트 1건), `journalctl -t urobot-watchdog`(0건),
  `ls /dev/ttyUSB*`, `/sys/bus/usb/devices` 카운트

### 메모
- 별건: `/jrobot_status` 에서 md5sum 불일치 관측 —
  클라이언트 `/rostopic_2829_*` 는 `store_motor/RobotStatus`, 서버는 `jarvis_msgs/RobotStatus`.
  `Unable to find message class ImuAll/Battery/Io in module jarvis_msgs` 도 동반.
  이번 다운의 원인은 아니나 추적 필요


### 07:05 워치독 설치 진행
- 설치 스크립트는 `scripts/setup-urobot-watchdog-over-ssh.sh`(8/23 신규)
  ※ 구 `scripts/robot-host/install-urobot-watchdog.sh` 아님
- 완료한 것(비번 불필요 구간):
  - `~/urobot-watchdog/`에 자산 업로드 — urobot-watchdog.sh / .service / .timer / install-remote.sh
  - 체크섬 일치 확인 `b0da45143e3e3c47b2317364fb08d82f`
- 사전 점검 전부 통과:
  - xHCI 플랫폼 디바이스 `xhci-hcd.5.auto` 존재 → USB 복구(재바인드) 경로 사용 가능
  - ttyUSB 5개(FT4232H 4 + PL2303 1), 임계값 4 → 여유 있게 갈림
  - 구 래치 파일 `/run/urobot-watchdog.*` 없음
  - 7273 listening, urobot active → 정상 로봇에 설치(스크립트상 안전)
- **막힌 지점**: `sudo -n -l` 결과 NOPASSWD 는 `amr-adaptor/webui/camera` 의
  systemctl start|stop|restart|enable|disable 뿐. 설치에 필요한
  `install`/`tee`/`mkdir`/`daemon-reload`/`enable urobot-watchdog.timer` 는 미포함
  → 대화형 sudo 필요, 비대화형 세션에서 완료 불가

### 08-26 16:31 — 설치 실행됨(사용자), 검증 미완
- 사용자가 `setup-urobot-watchdog-over-ssh.sh` 실행 완료했다고 알림
- **검증 불가**: 맥이 로봇 네트워크에서 이탈. `en0` IPv4 없음,
  기본 경로가 `en8` → `172.20.10.1`(아이폰 핫스팟 대역)
  - 10.8.8.8(카메라 LAN 직결) 도달 불가 — ping 100% 손실
  - 192.168.101.61(FMS 와이파이) 도달 불가
  - 우회 경로 없음: 100.103.231.66 불가, lab2m3.iptime.org:10220 닫힘, tailscale 미설치
- 설치 성공 여부는 **미확인 상태**로 남음

### 다음
- 로봇 네트워크 복귀(랜 케이블 직결 또는 FMS 와이파이 접속) 후 아래 검증 실행:
```
ssh ucore@10.8.8.8 '
  md5sum /usr/local/sbin/urobot-watchdog          # b0da45143e3e3c47b2317364fb08d82f 이어야 함
  systemctl is-enabled urobot-watchdog.timer
  systemctl is-active  urobot-watchdog.timer
  grep -c rebind_xhci /usr/local/sbin/urobot-watchdog     # 0 이면 구버전
  grep -c serial=absent /usr/local/sbin/urobot-watchdog   # 0 이면 구버전
  systemctl show urobot-watchdog.service -p Environment --value
  journalctl -t urobot-watchdog --no-pager -n 10
'
```
- 기대값: 타이머 enabled+active, 두 grep 모두 1 이상, Environment 에 `MIN_SERIAL=4`


### 08-26 — 설치 검증 통과
사용자가 `ssh amr2`로 실행한 결과, 기대값 전부 일치:

| 항목 | 결과 | 판정 |
|---|---|---|
| `md5sum /usr/local/sbin/urobot-watchdog` | `b0da45143e3e3c47b2317364fb08d82f` | 레포 `scripts/urobot-watchdog.sh`와 동일 ✅ |
| `is-enabled urobot-watchdog.timer` | enabled | ✅ |
| `is-active urobot-watchdog.timer` | active | ✅ |
| `grep -c rebind_xhci` | 3 | 신 USB 복구 로직 존재 ✅ |
| Environment | `PORT=7273 MIN_UPTIME=150 MAX_RESTARTS=3 MAX_REBINDS=3 MIN_SERIAL=4` | ✅ |

- 검증은 ssh 별칭 `amr2` 대상. 스테이징은 `ucore@10.8.8.8`로 했으므로
  둘이 다른 호스트라면 원래 대상도 같은 검증이 필요함(같은 호스트로 추정)

### 남은 것 (이번 작업 범위 밖)
- FMS 연동 설계(`rebootHost` instantAction + `usbDownstream`/`watchdogGaveUp`/`remedy`
  errorReferences)는 설계 ①② 승인까지만 되고 **스펙·구현 모두 미착수**
- `/jrobot_status` 타입 충돌 — **별건 메모가 아니라 실동작 버그로 보임**
  - `adaptor/bms_ros_listener.py`가 `rostopic echo -p /jrobot_status` 자식 프로세스를 띄움
    → 로그의 `Client [/rostopic_2829_*]`가 바로 이것
  - 에러: 클라이언트는 `store_motor/RobotStatus/5ea91f3f…`를 기대, 퍼블리셔는
    `jarvis_msgs/RobotStatus/297d7d3b…` → `Dropping connection`
  - 동반: `Unable to find message class ImuAll / Battery / Io in module jarvis_msgs`
    → 로봇의 python `jarvis_msgs` 패키지가 부분 설치/구버전으로 의심됨
  - **증상 근거**: 어댑터 JIBOT RX 로그의 BMS 값이 사라짐
    - 08-20 21:50 `battery_voltage=53.03 battery_current=-1.2`
    - 08-20 22:24 이후 `battery_voltage=None battery_current=None`
  - 즉 BMS 전압/전류와 safety/motor 필드가 어댑터에 안 들어오고 있음. 확인 필요
