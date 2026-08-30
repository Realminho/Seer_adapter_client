# joystick-followup-review

### 목표
- 조이스틱 후속 수정 A~E를 반영한다.
  A 무음 중 계속 주행, B product_id 기본값, C bridge 설치 게이트, D slot/음량 action 배선, E 테스트.

### 지금
- A~E 구현·테스트·문서 완료. 실기 검증만 남았다.

### 완료
- A: 무음 타임아웃이 아니라 **receiver 존재 확인**으로 해결했다. 트리거를 일정하게 당기면
  이벤트가 없으므로 무음을 신호로 쓰면 정상 주행이 끊긴다. evdev 경로 heartbeat마다
  `receiver_present()`로 `vendor:product`를 재확인하고, 없으면 정지+latch.
  `xboxdrv_receiver_guard`(기본 true), sysfs 없으면 시작 시 경고 후 가드 해제.
  supervisor poll 기본값 1s -> 0.25s.
- B: `config.py` `product_id` 기본값 3109 -> 310b(3109는 잠든 상태 id).
- C: `xpad`가 있으면 bridge 설치를 건너뛴다(같은 USB interface 경합). `--with-xboxdrv`로 강제.
- D: Ultimate 2 D-pad+ABXY 16 slot과 −/+ 음량을 배선했다. `actions_enabled`(기본 false)로 묶었다.
  실행은 `Adapter.submit_local_instant_action()`으로 기존 instant-action 경로를 그대로 탄다 —
  order/작업 차단과 상태 보고를 재사용하기 위해서다. 주행 입력이 중립이 아니면 slot 무시.
  대각선 D-pad 무시. Micro는 D-pad가 주행이라 chord 불가(문서에 명시).
- E: chord 순수 로직, receiver 가드(가드 유/무 대조), slot/음량/모드별 코드,
  `submit_local_instant_action` 5건 추가.

### 다음
- AMR 실기 검증: (1) 트리거 당긴 채 controller 전원 off -> 즉시 정지 확인,
  (2) `actions_enabled = true`로 slot 1~2개만 켜서 확인, (3) xpad 있는 로봇에서 setup 게이트 확인.
- 검증 후 커밋(이번 변경 포함 전부 아직 미커밋).

### 검증
- `run-tests.sh -o addopts="" ../tests` 139 passed / 2 failed.
  실패 2건은 HEAD(9a0e4c0) worktree에서도 동일. 이번 변경과 무관.
- adaptor `tests` 1897 passed / 8 failed. 8건 모두 HEAD baseline worktree에서 동일하게 실패
  (pio_init/pio_ping/goto_nearest_node timeout/elevator recipe). 이번 변경과 무관.
- 가드 대조 실행: guard=False면 receiver가 사라져도 100ms 동안 11회 계속 주행(정지 0),
  guard=True면 즉시 정지+latch.
- `extensions.hcl` / `.example` 둘 다 새 키로 로드 확인.
- 주의: `setSoundVolume`은 누를 때마다 `config.toml`의 `startup_volume`을 기록한다.
