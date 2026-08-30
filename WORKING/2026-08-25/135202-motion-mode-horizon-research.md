# motion-mode-horizon-research

### 목표
- point-to-point 외 이미 구현/설계된 주행 모드 확인
- FMS 가 8노드를 보내는데 한 스텝씩 소비하는 문제 정리
- 직접 구현 대신 표준/오픈소스 채택안 조사
- 모드로 나눌 때의 축 정리

### 완료
**A. 이 저장소에 대체 주행 모드는 없음**
- 세그먼트 오버라이드뿐: `motion_rules[].mode` = goto(기본)/dock/move (config/config.py:523).
  전역 전략이 아니라 `to`(+`from`)로 특정 도착 구간에만 걸림
- 전역 `*_mode` 3종(last_node_capture_mode / nearest_node_mode / rotate_to_mode)은 주행 전략 아님
- stop-and-go 는 하드코딩: `_process_v3_node_step`(adapter_jibot.py:6184)
  -> UmGoto -> `_wait_until_node_position_reached`(:5473) -> `_settle_goto_arrival`(:4602 완전정지 대기)
- 엣지는 장부일 뿐. `_process_v3_edge_step`(:4938) 주행 없음. edge.maximumSpeed/trajectory 미사용
- `mode` 네임스페이스 포화(6개): motion_rules.mode / elevator motion_rules.mode(enter·inside·passed)
  / vehicle._mode 텔레메트리 / run_mode / operatingMode / settings 의 *_mode -> 새 옵션에 mode 금지

**B. 문제 정의 정정 — horizon 아니라 base**
- 실측(jibot/site/2605-b205-demo/TestingOrderJson): merger1_2 = 8노드 8 released,
  merger2_1 = 8노드 7 released, F1_60_F1_40 = 5노드 3 released. 전부 version 3.0.0
- horizon(released=false)은 스펙상 주행 금지(§6.1.1) — 앞당겨 소비하면 관제 위반
- VDA5050 3.0 §6.1.2: 정지 의무는 base 마지막 노드(decision point) 하나뿐,
  "In order to ensure a fluent movement..." / §6.6.3 newBaseRequest = "prevent unnecessary braking"
- §6.6.2: 통과 판정은 로봇 재량, allowedDeviationXY 는 도착 허용 오차(코리도어 아님).
  노드 정지는 SOFT/HARD 블로킹 액션이 있을 때만의 "예외"
- 판정: SHALL 위반 아님. 허용 범위 안의 비효율 + 설계 의도 무력화. 논거는 성능으로 잡을 것

**C. 모드 축 5개(직교)**
1. 주행 방식 — factsheet `navigationTypes`: PHYSICAL_LINE_GUIDED / VIRTUAL_LINE_GUIDED / FREELY_NAVIGATING
2. 제어 권한 — operatingMode 7종(3.0). SEMIAUTOMATIC = 속도만 사람. 주행 기하와 무관
3. 경로 자유도 — edge.corridor(2.1 도입) / zone(LINE_GUIDED·COORDINATED_REPLANNING·SPEED_LIMIT)
4. 모션 연속성 — VDA5050 에 모드로 없음. allowedDeviationXY(코너 컷)로 표현.
   성숙 용어는 매니퓰레이터 쪽: ABB zonedata fine(stop point) vs fly-by, FANUC FINE vs CNT
5. 선행 명령 깊이 — openTCS 가 정수 파라미터로 표준화(`vda5050:maxStepsBase`/`maxStepsHorizon`,
   `VehicleCommAdapter.getCommandsCapacity`). 지금 우리 값이 1. enum 아니라 정수로 모델링이 관행
- 용어 함정: VDA5050 horizon(관제 릴리스) != pure pursuit look-ahead(m) != MPC prediction horizon.
  VDA5050 `trajectory` 는 NURBS 경로지 시간 궤적 아님

**D. 갖다 쓸 것 — 선반이 거의 비어 있음**
- PyPI 에 차량측 VDA5050 파이썬 패키지 없음(후보 6개 전부 404). pip 경로 없음
- VDA5050 공식 조직에 차량측 참조 구현/시뮬레이터 없음. 쓸 수 있는 건 json_schemas(MIT)뿐이고
  이미 저장소에 있음(공식 3.0.0 order.schema 는 trailing comma 로 파싱 실패, 우리 사본이 수정본)
- 코드 이식 가능 딱 하나: inorbit `_get_drivable_segment()` — BSD-3,
  inorbit-ai/ros_amr_interop, vda5050_connector_py/vda5050_controller.py:1601.
  연속 released 구간을 미릴리즈/HARD·SOFT 액션에서 끊는 순수 파이썬, ROS 타입 의존 없음
- 설계 참고:
  - libVDA5050++ (Open Logistics Foundation GitLab, OLFL-1.3, v3.2.0 2026-08 활발.
    GitHub cmraaron 미러는 2023 에서 멈춤) — `navigateToNextNode` + `upcomingSegment` 병행,
    `evalPosition()`(pose+반경 통과 판정, 네비 완료와 분리), `baseIncreased`/`horizonUpdated`
  - inorbit: segment 모드는 lastNodeId 를 액션 feedback 에서, 기본 모드는 result 에서 갱신.
    정지/무정지 차이가 정확히 갱신 트리거뿐
  - Nav2 `ComputeAndTrackRoute` feedback(last_node_id/next_node_id/current_edge_id) — state 와 1:1
  - openTCS comm adapter 의 maxStepsBase/maxStepsHorizon 이름
- 부적합: Nav2 자체(스택 소유 전제 = urobot 대체), free_fleet/RMF(rmf_traffic 런타임 통째),
  ROS2 메시지 패키지, tum-fml/vda5050_connector(내용 없음).
  ipa320/vda5050_connector 와 idealworks/VDA5050_Connector 는 실재하지 않음(404)
- 세 구현이 독립적으로 같은 결론: 통과 판정을 주행 명령 종료에서 떼어내 pose 스트림 + 반경으로

**E. 벤더 — 모드는 JIBOT 전용**
- SEER 3051 은 이미 중간 스테이션 무정지 통과가 네이티브(netprotocol l-1.2.1 「固定路径导航」).
  단일 타겟(id:string), TASK 포트는 3001/3002/3003/3050/3051/3052/3055/3056 뿐, task-list 없음.
  3052(patrol)도 사전등록 route 이름만 받음
- SEER 는 한 시점 태스크 1개, 새 태스크가 이전 것 중단 -> 스텝 큐 공통화하면 N+1이 N을 취소
- adapter_seer.py 없음, Hexplorer 는 order 미구독. 공통 오더 큐 자체가 없음
  (order_queue 는 adapter_jibot.py:202 단독, core/ 는 WebUI 백엔드)
- 결론: 코얼레싱은 JIBOT 어댑터 경계 안. 표준 어휘는 대외 표면(팩트시트)에만

**F. urobot 이 이미 주는 것**
- multi-step route 문서상 가능: PDF p11 UmGetRoutes 예시가 한 content 에 "a","a1" 두 스텝,
  UmRoutes 의 key 가 "entry block step key". `cmd:"goto"` 실재 —
  실측 logs/jibot/f1_60-arrival-signals.jsonl:28 value={"cmd":"goto","goal":"F1_60",...}
- bare UmGoto 는 1스텝 임시 라우트의 설탕문법(routes="ActiveMode [Temp]", key="a")
- 진짜 큐잉 프리미티브는 UmSchedulerList("routes:key" 배열 순차 실행).
  래퍼는 이미 있음(jibot-client client.py:1019), 호출 실적 0건
- 스텝 사이 무정지 여부는 미확인. PDF 15p 에 blending/smoothing 기술 0.
  오히려 goal 전용 감속 세트 존재(nav.limit_goal_linear_deceleration=200 vs run 600,
  goal_distance_tolerance=50, align_angle=45) -> 라우트화만으로 연속 주행 보장 안 됨
- 연속 UmGoto 오버랩은 큐잉 아니라 덮어쓰기 가능성 높음(동일 임시 슬롯 재사용). 근거 약함
- 확인 수단: scripts/fetch-jibot-params-over-ssh.sh 로 /usr/local/urobot/params/routes/ 통째 수령
  (로봇 모션 안 건드림). 아직 한 번도 안 받음

**G. 8/22 설계 대비 정정**
- `config.toml:77/90` 참조 무효 — 저장소 루트 config.toml/robots.hcl/extensions.hcl 은
  어떤 코드도 읽지 않음. 실제 로드는 adaptor/config/ 아래 동명 파일
- allowedDeviationXY 2.0 우려 해소: 이 사이트 좌표계 전체가 mm(노드 x=7290),
  어댑터가 factsheet 에 coordinate_unit_position="mm" 선언. 단 그 설정 소비처 0건이고
  coordinateUnits 는 3.0 팩트시트 스키마에 없는 필드
- "액션 있는 노드 병합 금지"는 과함 — 코드상 SOFT/HARD 만 정지 강제(adapter_jibot.py:4400),
  NONE 은 아님. 단 현장 액션이 {"key":"pio","value":1} 형태라 파서 KeyError -> 실제 blockingType 미확인
- 병합 금지 실제 목록: 미릴리즈 / SOFT·HARD 액션(노드+엣지) / dock 세그먼트 / move 룰 세그먼트 /
  dock work 노드 / 좌표 없는 노드 / actions_only 스텝
- `_order_node_motion_seq`(:6186)가 rotateTo 배경 루프의 supersede 신호라 병합 시 조용히 어긋남

**H. 새로 드러난 구멍 2개**
- `new_base_request` 가 None 하드코딩(adapter_jibot.py:732, publish 는 :3386 복사만).
  스펙이 정한 "불필요한 제동 방지" 유일 신호를 한 번도 안 보냄 -> 연속 주행 만들어도
  base 끝에선 그대로 정지. 연속 주행의 나머지 절반
- edgeStates 를 엣지 진입 시점에 제거(`_process_v3_edge_step` 이 주행 없이 즉시 completed ->
  `_clear_v3_order_step`). 스펙은 "엣지 이탈 = 다음 노드 통과 시" 제거.
  지금은 노드마다 정지해서 창이 짧을 뿐 — 연속 주행이 정확히 이 창을 넓힘. 점유 관제 위험

**I. 게이트 표면·배포 제약**
- `[settings]`(adaptor/config/config.toml:46) + `Settings` dataclass(config/config.py:193-363).
  dataclass 필드 없이 toml 키만 넣으면 부팅 실패
- 배포 기본이 `--config-toml-mode keep` -> 새 키가 현장 로봇에 절대 도달 안 함.
  dataclass 기본값이 곧 현장 동작. "기본 off + toml 한 줄"로 설계하면 영영 안 켜짐
- 설정은 부팅 1회 로드, 원격 reload 미구현 -> 토글에 재시작 필요

**J. 검증 하네스 부재**
- FakeVehicle 이 항상 `_mode="auto"`/`_status="Stopped"` -> `_settle_goto_arrival` 이 전 테스트 no-op
- jibot-simulator 는 UmGoto pose 설정만, 라우트/스케줄러 미구현
- 기존 테스트가 stop-and-go 를 계약으로 고정하진 않음(한 order 에서 2홉 주행 테스트 0건)
  -> 깨질 건 없지만 고쳤는지 확인할 방법도 없음. 하네스 확장이 선행 작업

### 지금
- P1(settle 대기 계측)은 **기존 캡처 로그로 해소** — 새 실기 테스트 불필요.
  logs/jibot/f1_60-arrival-complete.jsonl (2026-06-22, 192.168.3.222) 에 UmGoto 1회 전체 주행 존재.
  rx UmGetRobotInfo 페이로드 최상위에 x/y/th/vel_f/vel_r/station/mode/status/obs 전부 있음.
  - 타임라인: UmGoto(F1_60) t=3.54 -> 제자리 회전 t=6.4~19.0 (vf=0, vr~14, th 5->143)
    -> 병진 t=21.1~24.7 (vf 84->30, 201mm) -> 정지 t=26.4 (station='F1_60' 복귀)
  - 도착존(+-40mm) 진입 t~24.5, cmd='goto' 종료 t~25.7 -> **settle 대기 상한 ~2초, 실제 ~0.5~1초**
    (샘플링 주기 1.78초라 정밀 측정 불가. 정밀값은 어댑터 타이밍 로그 필요)
  - 더 큰 발견: 이 홉 비용은 회전 13초 + 병진 5.5초. 로봇이 노드에서 속도를 0까지 떨구는 것은
    settle 대기가 아니라 UmGoto 가 매번 별개 목표라서 생김
  - 다른 로그(20260727-170928): UmGoto 후 status '#brake' 로 9초 무동작 -> 출발 지연 관측(1건).
    이후 '#slowdown' -> '#watch' -> 목표 heading 제자리 회전
- **결론: `_settle_goto_arrival` 만 제거하면 대기만 없애고 감속은 그대로 남음.**
  CP 의 실이득은 "로봇이 감속하기 전에 다음 목표를 아는가" 에 달림 -> P2/P3 가 진짜 게이트
- **P2 재설계**: 다중 스텝 수단이 UmGoto 오버랩 말고 4개 더 있음.
  - `UmSchedulerThis(name,key,content)` — ad-hoc 라우트를 디스크 저장 없이 즉시 실행(PDF p14).
    **이미 실기에서 돌고 있음**: move_distance(run_mode="scheduler") 기본 경로이고
    `_run_move_segment`(:6302) / manualMove(:6790) / 장애물 복구(:7654) 가 사용 중
  - `UmSetRoutes` + `UmRoutes(routes,key,id)` — move_distance(run_mode="set_routes") 로 배선.
    디스크 영속 여부 미확인(벤더 라우트 덮어쓸 위험)
  - `UmSchedulerList(size,list)` — "routes:key" 배열 순차 실행. 래퍼만 있고 호출 실적 0
  - `AdvSetRoutes` 계열 — 미사용
  - **goto 스텝 실기 스키마 확보** (logs/jibot 의 UmGetCurTask.value):
    goal 형: {"cmd":"goto","target":"goal","goal":"F1_60","x":2147483647,"y":2147483647,
              "th":2147483647,"heading":false,"strict":true,"comment":"..."}  (INT_MAX=미지정)
    pose 형: {"cmd":"goto","target":"pose","goal":"none","x":14000,"y":-2600,"th":180,...}
  - -> P2 를 "주행 중 UmGoto 재발사"에서 **"2스텝 goto 라우트를 UmSchedulerThis 로 실행"** 으로 교체.
    오버랩 UmGoto 는 3순위 폴백으로 강등
  - 남은 미지수: 스텝 전이 규칙(a -> a1 인지, 알파벳 순인지, next 필드인지). P3 가 푸는 것
- **P2 를 P2-1/P2-2 로 분할, 오버랩은 P2-3 으로 강등** (사용자 지시 2026-08-25)
  - P2-1: goto 스텝 1개짜리 라우트를 UmSchedulerThis 로 실행. 주행 위험 = 평소 UmGoto 1회와 동일.
    확인: goto 스텝이 라우트로 도는가 / UmGetCurTask.routes,key 가 우리 이름으로 바뀌는가
  - P2-2: goto 스텝 2개. P2-1 성공 전제. 확인: 스텝 경계 vel_f≈0 여부 / key a->a1 전이
  - P2-3(폴백): 주행 중 UmGoto 오버랩. 프로토콜 근거 0, 덮어쓰기 위험
  - **프로브 작성 완료**: scripts/probe-jibot-route-goto.py (dry-run 기본, --apply 로만 발사).
    샘플 주기 기본 0.3초(기존 캡처 1.78초는 감속 구간을 못 잡음). Ctrl-C 시 UmStop.
    dry-run 2건 통과 확인(1스텝/2스텝 프레임 정상 생성). 실기 미실행
- 조사 종료. 기능 설계(brainstorming, architectural 경로) 진입.
- 게이트 2 해소(사용자 확답 2026-08-25): pio HARD 액션은 엘리베이터/dock/airshower 등
  실제로 정지가 필요한 노드에만 붙음. 일반 통과 노드는 액션 없음 -> 병합 가능 집합 유효
- 확정 용어: 기능명 = 연속 경로 주행(Continuous Path, CP), 메커니즘 = 경유지 블렌딩(Waypoint Blending)

### 검증(스크립트 수정 — P3 실행 중 막혀서 고침)
- ControlPath 길이: macOS TMPDIR(/var/folders/.../T, 62자) + "%C"(40자 SHA1) = 103바이트로
  sockaddr_un 한계 104 초과. `mktemp -d /tmp/amr-ssh.XXXXXX` 로 고정해 60바이트.
  선례 그대로: scripts/update-jibot-adapter-over-ssh.sh:257
  적용: fetch-jibot-params / fetch-hexplorer-maps / change-jibot-network /
        update-hexplorer-adapter / update-jibot-adapter-config (5건)
  repair-adaptor-ipc-over-ssh.sh 는 %C 미사용(70바이트)이라 제외
- SSH 포트: 하드코딩된 `-p 22` 가 ~/.ssh/config 의 Port 를 덮어써서 amr2(Port 10020)에
  22번으로 붙어 인증 거부. `SSH_PORT` 미지정 시 -p 를 안 넘기도록 수정.
  `ssh -G amr2` = user ucore / hostname 192.168.101.50 / port 10020 확인
  적용: fetch-jibot-params / fetch-hexplorer-maps (읽기 전용 fetch 2건만)
  **미적용(같은 잠재 버그 남음)**: update-jibot-adapter-over-ssh / update-jibot-adapter-config /
  update-hexplorer-adapter / change-jibot-network — 배포·변경 스크립트라 포트 해석을
  요청 없이 바꾸지 않음. 필요하면 별도 판단
- 별건(기존 문제, 내 수정과 무관): change-jibot-network-over-ssh.sh 와
  update-jibot-adapter-config.sh 는 macOS 기본 bash 3.2 로 `bash -n` 파싱 실패.
  HEAD 에서도 동일(289/305 -> 294/310, 주석 5줄만큼만 밀림). 이 맥에 bash5 없음

### P3 완료 (2026-08-26, ucore@amr2 = 192.168.101.50:10020) — 읽기 전용 수령
- `/usr/local/urobot/params/{map,routes}` 수령. routes 25개 파일 전부 파싱 성공
  (routes.json 은 `//` 주석이 섞인 JSONC 라 주석·trailing comma 제거 후 파싱)
- **multi-step 라우트 342건 실재.** 최대 51스텝(routes.template.json 의 JTLK-M)
- **스텝 전이 규칙 확정: 키 뒤에 "1" 을 덧붙이면 다음 스텝** — a -> a1 -> a11 -> a111.
  "0" 으로 끝나는 키(a110, a1110)는 조건 분기의 다른 가지로 보임. 알파벳 순도, next 필드도 아님
- **goto 스텝의 벤더 작성 형식** (routes.json 의 AB_1000 / EF / AB_Loop):
    {"cmd":"goto","target":"goal","goal":"A","x":0,"y":0,"th":0,"time":-1,"note":1,"max_vel":1000}
  런타임 에코(UmGetCurTask.value)의 x/y/th=INT_MAX 와 다름. **작성 형식이 x/y/th=0**
- **goto 스텝에 `max_vel` 존재.** 실측값 200/300/500/550/600/700/800/1000/1200/1400/1500.
  AB_600 / AB_1000 / AB_1400 / AB_1500 은 같은 구간의 **속도 변종 라우트**임
  -> 속도 상향(C안)이 UmSetConfig 없이 스텝 필드로 가능. task-goto.max_vel=350 은 기본값일 뿐
- **goto 스텝에 `start_reach_max_dis` 존재** (routes.json 의 A-CAM2 에서 300).
  도착 판정 반경으로 보이며 **블렌딩 반경 후보**. 용례 1건뿐이라 의미 미확정
- goto 전체 필드: cmd, goal, target, x, y, th, time, times, note, log, max_vel, start_reach_max_dis
- cmd 어휘 103종. 상위: jump 645 / return 304 / goto 290 / wait 251 / set 231 /
  turntable_rotate 206 / move 135 / socket2 109 / rms_goto 96
- **전이·제어흐름 프리미티브는 `jump`**: {"cmd":"jump","routes":<라우트명>,"key":<스텝키>,"back":bool}
  다른 라우트의 특정 스텝으로 점프하고 back=true 면 복귀. `return` 은 호출자 복귀
- **종료 스텝 필요**: AB_1000/EF 는 마지막이 jump back to "a" 인 **무한 루프**다.
  em_route 처럼 {"cmd":"idle"} 로 닫아야 한 번만 돌고 끝난다
- **PDF 반증**: PDF p14 는 UmSchedulerThis 가 "디스크에 저장하지 않는다"고 하지만,
  routes.temp.json 에 어댑터가 만든 `manual_move`(distance=-2000, speed=150) 와
  `ActiveMode`/`TEMP_DEFAULT` 슬롯이 그대로 있음. 영속 파일(routes.json)엔 안 쓰고
  routes.temp.json 에는 남는다. UmGetCurTask 의 "ActiveMode [Temp]" 의 [Temp] 가 이 파일임
- 프로브를 벤더 실측 형식으로 교체 완료(scripts/probe-jibot-route-goto.py):
  x/y/th=0, time=-1, note=1, 선택 max_vel/start_reach_max_dis, 키 a/a1/a11..., 종료 idle 스텝.
  dry-run 1·2·3스텝 통과. 실기 미실행
- jibot/params/, jibot/backups/ 를 .gitignore 에 추가(map 만 154M, 언제든 재수령 가능)

### 맵 확인 (hana.json = amr2 현재 맵, map.json 이 지목)
- 노드는 두 네임스페이스로 갈린다: `Objs.Goal`(51개, 6_05/7_01CH 형식) / `Objs.PathPoint`(53개, p2~p57)
- **어댑터는 PathPoint 를 이름으로 안 부른다** — `_send_node_goto` 가
  `get_path_point_pose(id)` 로 좌표를 얻으면 `goto_xyz`(target=pose), 아니면
  `goto_point`(target=goal). PathPoint 는 urobot 의 goal 네임스페이스에 없음
- p47 = PathPoint (109413, 9882), 이웃 p42/p43. **p49 는 현재 맵에 없음**(p48/p49 결번)
- p36 (16350, 91) / p37 (16376, -1077) / p38 (16376, -2462) / p39 (13032, -2511) / p40 (12994, -1077)
  - p36-p39 = 4217mm, 직접 연결 아님(p37 경유). 단 target=pose 는 로봇이 자유공간 재계획이라 무관
  - **p36 -> p37 -> p38 이 거의 일직선**(x 편차 26mm / 2553mm) — 회전 없는 CP 테스트로 가장 깨끗
- `_node_goto_theta_deg` 주석의 실측 사례 poseX=16350/poseY=91 이 정확히 p36 —
  이 맵이 그 8/22 실측 로봇의 맵과 같음
- **PathPoint theta 가 전부 0.00** — 맵 값을 그대로 poseTh 로 쓰면 도착마다 제자리 회전.
  프로브는 어댑터와 같은 기준(직전 지점 -> 이 지점 방위)으로 계산. 첫 스텝은 실기
  접속 후 실제 pose 를 읽어 재계산
- Goal 51개 전부 allowPassingThrough=false (통과 허용 플래그가 다 꺼져 있음. CP 관련 확인 필요)
- 프로브 갱신: `--node`(PathPoint/Goal 자동 판별), `--map`, 없는 노드는 후보 목록과 함께 종료

### 설계 문서 작성 완료 (2026-08-27)
- `docs/superpowers/specs/2026-08-26-continuous-path-design.md` (320줄). **미커밋**
- 자체 검토: adapter_jibot.py 인용 15건 + config 5건을 현재 파일과 전수 대조해 정정함.
  최초 초안의 줄번호가 대부분 어긋나 있었다(파일이 그동안 드리프트). 심볼명을 함께 적어
  다시 어긋나도 찾을 수 있게 했다. TBD/TODO 0건
- spec 승인됨 (2026-08-27)

### 구현 계획 작성 완료 (2026-08-27)
- `docs/superpowers/plans/2026-08-27-continuous-path.md` (865줄, 6태스크, 40스텝). **미커밋**
- 태스크: 1 테스트 하네스 / 2 CP-4 edgeStates / 3 설정 표면 / 4 선분 보간 /
  5 CP-1+CP-2 코얼레싱 / 6 CP-3 newBaseRequest
- 자체 검토에서 고친 것:
  - `_is_run_end` / `_step_has_blocking_action` 를 정의 없이 참조하고 있었음 -> 실제 코드로 정의
  - "기존 헬퍼를 그대로 쓴다 + grep 해보라" 식 placeholder 2건 -> 실제 형태로 교체
    (`_make_adapter` 는 모듈 함수가 아니라 테스트 클래스 메서드, OrderStep 은 위치인자 3개 +
     SimpleNamespace, edge_states 는 List[Any])
  - Task 6 의 "큐에서 세어 부른다" 서술 -> 실제 코드
  - spec 6 의 `_order_node_motion_seq` 위험에 대응 태스크가 없었음 -> Task 5 Step 8 로 추가
    (goto 는 구간당 1회지만 seq 증가와 `_finalize_v3_node_step` 은 노드마다 유지)
- 최종: placeholder 0건, 정의 없이 참조하는 심볼 0건

### (구) 다음
- 0단계 계측: `_settle_goto_arrival` 실제 대기 시간. 이 작업 가치를 정하는 유일한 숫자.
  이론 램프 손실은 8노드 2.5~4.1초뿐이라 병목이 램프가 아닐 가능성 높음. 지금 로그에 남기는 코드 없음
- 1단계: 통과 판정을 goto 종료에서 분리해 pose 스트림+반경으로(libVDA5050++ evalPosition 형태).
  UmGetRobotInfo 가 #GAP# 주기로 x/y/th 서버 푸시 — 재료는 이미 있음
- 2단계: inorbit `_get_drivable_segment()` 이식
- 3단계 하달 방식은 실기 스파이크 후 결정(multi-step route / UmSchedulerList / settle 게이트만)
- spec 문서는 만들지 않음(사용자 "참고만", 8/22 설계 승인 미완)

### 검증
- order 픽스처 released 집계 재실측 완료(위 B): merger1_2 8/8, merger2_1 7/8, F1_60_F1_40 3/5, 전부 3.0.0
- navigation_types=["AUTONOMOUS"] 실재 확인 (config/config.toml:350, config/config.py:818,
  core/factsheet.py:139) — 3.0 팩트시트 enum(PHYSICAL/VIRTUAL_LINE_GUIDED, FREELY_NAVIGATING) 밖의 값
- new_base_request 하드코딩 None 확인 (adapter_jibot.py:732, :3386)

### P2-1 1차 실행 (2026-08-26 21:56~22:00, 192.168.101.50) — 근본 원인: 모터 OFF
- 증상: UmSchedulerThis 3회 발사, 로봇 안 움직임, 에러 프레임 0건
- **라우트 하달 자체는 성공했다**: UmGetCurTask.routes 가 'TEMP_DEFAULT [Temp]'/'ActiveMode [Temp]'
  -> **'cp_probe'**, key='a', cmd='goto' 로 바뀜. 스텝 4개짜리(a/a1/a11/a111)도 동일하게 수락됨
- 그런데 mode='Stop', status='Stopped', vel_f=0, pose 불변(111498, 7278, th=88)으로 고정
- **UmGetMotorState.flag = false** (2026-08-26 22:33 --status-only 로 실측). UmGetLocState.score=350
- 이 증상은 adapter_jibot.py `_ensure_motor_enabled_before_order_motion` docstring 이 서술한 것과
  동일함: "The firmware accepts the command and the robot buzzes without moving"
  (2026-08-17 192.168.101.61 UmDock 실측). 어댑터를 내리라고 한 것이 그 자동 인가를 없앤 것
- 즉 **P2-1 은 실패가 아니라 절반 확인**임. 라우트 수락·현재 태스크 등재까지는 확정,
  실행 여부만 미확인. UmSchedulerThis 는 UmConnect 응답에도 ret:none 이라 ack 이 없다

### 부수 발견 (P2-1 1차에서)
- 두 번째 변수도 섞여 있었음: 로봇은 (111498, 7278) 인데 지시한 p36/p39 는 (16350, 91) 부근으로
  **약 95 m 떨어진 다른 구역**. 맵의 PathPoint 가 12000~17000 구역과 104000~110000 구역으로
  나뉘어 있음. 로봇 근처는 p43(2143mm) / p47(3336mm) / p44(4348mm) / p42(5054mm)
  -> 프로브에 `--max-gap`(기본 20m) 가드 추가
- 벤더의 pose 형 goto 는 `goal` 필드를 안 넣음 (routes.samples.json / samples_goto / a1).
  런타임 에코의 goal:"none" 은 출력용 값이라 입력에서 제거함
- max_vel 은 필수 아님 (벤더 goto 290개 중 10개가 생략). target 분포는 goal 288 / pose 2
- 프로브 추가: `--status-only`(모션 없이 상태만), `--enable-motor`, `--plain-goto`(대조군), `--max-gap`

### 로봇이 두 대였음 (2026-08-26 22:43) — 앞선 진단 정정
- 192.168.101.50 에 포트 두 개가 각각 다른 로봇이다. 둘 다 UmConnect 정상 응답
  - **7273**: motor flag=false(OFF), locScore=350, pose (111498, 7278) — p43/p47 구역
  - **7274**: motor flag=true(ON),  locScore=844, pose (12463, -2474) station='1_06' — p39/p36 구역
- 즉 "모터 OFF" 는 7273 로봇의 사실이고, 사용자가 의도한 대상은 7274 였다.
  p39/p36 지정이 맞았고 내가 엉뚱한 로봇을 보고 있었다
- 저장소 설정에는 7273 만 있다(robots.hcl:14 vehicle_port = 7273). 7274 는 어디에도 없음
- 22:36 실행에서 7274 가 전 명령 무응답이었던 건 일시적 — 22:43 에는 정상. 원인 미확인

### P2-1 확정 (2026-08-26 22:43, 7274, p39, 570mm) — **라우트 goto 는 실행된다**
- UmSchedulerThis 발사 2.3s 후 mode='MRosGoto', status='nrunto pose (13032 -2511 -4)',
  routes='cp_probe' 로 전환되고 **로봇이 실제로 주행**함. vel_f 최대 577.6 mm/s
- 즉 UmSchedulerThis 단독으로 cmd:"goto" 스텝이 집행된다. 별도 트리거(UmRoutes) 불필요
- 라우트 종료 후 routes 가 'ActiveMode [Temp]' / cmd='stop' 으로 복귀 — idle 스텝의 효과로 보임

### P2-1 에서 드러난 진짜 비용 구조 (CP 설계에 직결)
- 전체 38s 중 **제자리 회전이 26s**, 병진은 ~11s 뿐이었다
  - t=3~19  (16s): th 139 -> 1 제자리 회전 (vf=0, 출발 정렬)
  - t=19~26 ( 7s): 병진 570mm -> 목표 60mm 앞
  - t=26~36 (10s): th 1 -> 91 제자리 회전 (vf=0, 도착 정렬)
  - t=37~41 ( 4s): 북쪽으로 1.2m 주행 후 정지 (12982,-1223) station='1_06'
- **목표 p39 최근접 60mm — 도착존 ±40mm 안에 못 들어감**
- 즉 CP 의 병목은 `_settle_goto_arrival` 도 감속도 아니고 **노드마다의 제자리 회전**이다.
  이건 goto 스텝의 `th`(도착 헤딩)가 정하므로, 연속 노드의 th 를 진행 방위로 주면 사라질 값이다
- 미해명: 마지막 1.2m 북진(목표 반대 방향). #watch/#slowdown 이 붙어 있어 장애물 회피
  또는 재접근 시도로 보이나 확정 못 함. 단발 관측이라 일반화 금지
- 참고: max_vel 미지정인데 577mm/s 가 나왔다. task-goto.max_vel=350 이 이 로봇에 안 걸린다는 뜻일 수 있음

### P2-2 + 대조군 (2026-08-26 22:51~23:05, 7274) — 로봇은 PathPoint 그래프를 따라간다
- P2-2 (a=p39, a1=p38, a11=idle): 라우트 수락됨(status 'BaseStart MRosGoto').
  그런데 목표 p39 는 남쪽 1.3m 인데 로봇이 **동쪽으로 3.2m** 를 999mm/s 로 달려
  p37 근처 (16205,-1036) 에 정지. 그동안 status 는 계속 'nrunto pose (13032 -2511 -88)'
- 스텝 전이 미관측: key 가 'a' 에서 안 바뀌고 t=22s 에 cmd='stop' 으로 라우트 종료.
  **스텝 a 가 목표 미도달로 중단되면서 a1 이 실행되지 않은 것으로 보임**(미확정)
- 경쟁 클라이언트 배제: 아무 명령도 안 보내고 25샘플 관찰 — Stop/Stopped,
  'ActiveMode [Temp]' 고정, 로봇 완전 정지. 다른 클라이언트 없음
- **대조군(--plain-goto, 평범한 UmGoto, 같은 목표 p39)이 결정적**:
  라우트 goto 와 **거동이 동일**했다. (16205,-1036)에서 제자리 회전 14s 후
  x≈16320 을 따라 **남쪽으로** 주행 — 즉 p37 -> p38 -> p39 그래프 경로
- **결론: 이 로봇은 target="pose" 여도 직선 자유주행이 아니라 PathPoint 그래프를 탄다.**
  P2-2 에서 동쪽으로 간 것은 p40 -> p37 -> p38 -> p39 우회이지 오작동이 아니었다.
  8/22 로그의 "로봇은 이미 자유공간 플래너" 전제는 이 로봇에는 성립하지 않는다
- **CP 에 직접 걸리는 관찰**: 로봇은 goto 한 번으로 중간 그래프 노드(p37, p38)를
  정지 없이 통과한다. 즉 무정지 통과 능력은 로봇에 이미 있고,
  stop-and-go 는 어댑터가 VDA5050 노드마다 goto 를 끊어 쏘기 때문에 생긴다
- 노드마다의 제자리 회전이 여전히 큼 (P2-1 16s+10s, 대조군 14s)

### 확정/미확정 정리
- [확정] UmSchedulerThis 단독으로 cmd:"goto" 스텝이 집행된다 (별도 UmRoutes 트리거 불필요)
- [확정] 라우트 goto 와 평범한 UmGoto 의 주행 거동이 동일하다
- [확정] 로봇은 PathPoint 그래프를 따라가며 중간 노드를 정지 없이 통과한다
- [미확정] 스텝 a -> a1 전이. 스텝이 성공적으로 도달한 경우를 아직 못 만들었다
- [미확정] 목표 도달 실패 원인 (P2-1 최근접 60mm, 도착존 ±40mm). th 값 영향 여부 미분리

### 2026-08-26 23:0x — 앞 절의 "대조군은 여러 홉 무정지" 주장 **철회**
- 실제 대조군(logs/jibot/20260826-230255): (16205,-1036)≈p37 에서 남쪽으로 주행해
  (16324,-2387)≈p38 에서 t=21.4 에 정지. **p39 까지 가지 않았다.** 한 홉이다
- 라우트/평범 UmGoto 모두 **한 그래프 홉만 가고 멈춘다**는 쪽으로 관측이 모인다
- 추가 2스텝 실행(23:07, a=p38 / a1=p37): p39 에서 목표 p38(동 3.2m)인데 **북쪽** p40 으로
  한 홉 가고 t=24 에 종료. key 는 'a' 고정, a1 미실행

### 미해결 모순 — 관측 없는 구간에서 로봇이 움직인다
- 23:03:16 (16324,-2387)≈p38  (대조군 t=21)
- 23:03:50~23:04:07 **(16205,-1036)≈p37** (수동 관찰, 내가 보낸 명령 0건)
- 23:07 (13143,-2539)≈p39 (status 조회)
-> 내가 명령을 보내지 않은 구간에 로봇이 p38 -> p37 -> p39 로 이동했다.
   수동 관찰 25샘플 동안에는 완전 정지였으므로 "다른 클라이언트 없음"과도 어긋난다

### 판단: 여기서 실기 실험 중단
- 가설이 3개 이상 연속으로 어긋났다. 계측이 부족한 상태에서 실기 주행을 더 쓰는 건 낭비다
- **빠진 계측**: `UmGetPath` (ret: num, path(array of x,y)) — 로봇이 계획한 전역 경로다.
  이걸 매 샘플 찍었으면 "왜 반대로 가나 / 어디서 끊기나"가 한 번에 보였을 것이다.
  프로브가 UmGetRobotInfo/UmGetCurTask 만 찍고 있어 계획 경로를 못 봤다
- 다음에 실기를 쓸 때는 UmGetPath 를 샘플에 포함하고, 로봇을 건드리지 않는
  장시간 수동 관찰(누가 움직이는지)을 먼저 할 것

### 그럼에도 확정으로 남는 것
- [확정] UmSchedulerThis 단독으로 cmd:"goto" 스텝이 집행되고 로봇이 실제로 주행한다
- [확정] 라우트 goto 와 평범한 UmGoto 의 거동에 차이가 없다 — 라우트 경로는 정상 수단이다
- [확정] 이 로봇은 target="pose" 여도 직선이 아니라 PathPoint 그래프를 탄다.
  vertex 필드는 방향성이다(p43->p44->p42->p47->p43 식의 단방향 순환)
- [확정] 노드마다 제자리 회전이 14~16초로 병진보다 크다 (3회 관측)
- [미확정] 스텝 a -> a1 전이. 스텝이 성공 도달하는 케이스를 끝내 못 만들었다

### 원인 확정 (2026-08-26 23:1x) — 다른 클라이언트가 우리 명령을 끊고 있었다
- 사용자 지적("fms 나 adaptor 가 켜져 있어서 끊는 것 아닌가")이 맞았다
- 7274 실행 3건 전부에서 **우리 라우트가 주행 도중 교체**됐다:
  routes 'cp_probe' -> 'ActiveMode [Temp]', value.cmd 'goto' -> 'stop'
  | 실행 | 교체 직전 vel_f | 그때 status |
  |---|---|---|
  | 22:43 P2-1   |  71.3 mm/s | nrunto pose (13032 -2511 -4)#slowdown |
  | 22:51 P2-2   | 110.4 mm/s | nrunto pose (13032 -2511 -88)#slowdown |
  | 23:08 2스텝  |  32.1 mm/s | nrunto pose (16376 -2462 1)#slowdown |
  -> goto 가 살아 있고 로봇이 아직 굴러가는 중에 stop 이 들어왔다. 자연 종료가 아니다
- 프로브는 UmStop 을 중단(Ctrl-C) 시에만 보낸다. 우리 소행이 아니다
- 7273(모터 OFF) 실행 3건에는 교체가 없다 — 거기선 아무도 몰지 않았기 때문
- 이것으로 앞선 관측이 전부 설명된다:
  - "한 그래프 홉만 가고 멈춘다" -> 한 홉쯤에서 외부 stop 이 들어온 것
  - "관측 없는 구간에 로봇이 움직였다" -> 외부 클라이언트의 오더 주행
  - "17초 수동 관찰에 아무 일도 없었다" -> 그 순간 외부 클라이언트가 유휴였을 뿐
- **따라서 22:43~23:08 의 주행 관측(그래프 우회, 한 홉 종료, 도달 실패)은 전부 오염됐다.**
  "로봇이 PathPoint 그래프를 탄다"는 관측만은 대조군과 라우트가 일치했으므로 남겨 두되,
  단독 근거로 쓰지 말 것
- 프로브에 교체 감지 추가: 우리 라우트가 도중에 바뀌면 [경고] 로 크게 보고하고
  "어댑터를 내리고 다시 측정하라"고 알린다. 이번에 이걸 놓쳐 두 번 오독했다

### 다음 (실기 재개 전 필수)
1. 이 로봇(192.168.101.50:7274)을 맡은 어댑터/FMS 를 **실제로 정지**시킬 것.
   저장소 robots.hcl 은 다른 사이트(10.8.8.8)라 배포 위치 정보가 여기 없음
2. 정지 확인 후 다시 --status-only 로 장시간(수 분) 수동 관찰해 로봇이 스스로 안 움직이는지 확인
3. 그다음 P2-2 재실행. 프로브에 UmGetPath 샘플 추가(계획 경로를 봐야 그래프 우회를 판정 가능)

### 2026-08-26 23:3x~23:5x — 어댑터 정지 후 깨끗한 측정. **CP 설계 결론 확정**
사전 확인: 45샘플 수동 관찰 전부 동일 상태, 움직임 0 -> 외부 간섭 없음.
맵은 UmGetMap 으로 실시간 수령(jibot/params/map/_live_UmGetMap.json, PathPoint 55개).
사용자가 새로 만든 p58(7618,-965) / p59(10365,-911) / p40(12994,-1077) 은 **직진 코스**(총 4.7° 꺾임).

**A. 정지는 "노드라서"가 아니라 "꺾여서" 생긴다**
- 단일 goto p40 -> p58 (91s): 정지가 p37(20.8s) p38(9.7s) p39(10.3s) p40(9.8s) 에서만 발생.
  이 넷은 전부 **90° 코너**다. 총 91s 중 정지 51s(56%)
- 같은 주행에서 **p59 는 1002 mm/s 로 감속 없이 통과**했다:
    84.66 (11350,-992) vf=1002.7 d(p59)=988
    85.92 (10038,-937) vf=1002.1 d(p59)=328   <- 최근접
    86.51 ( 9470,-937) vf= 998.5 d(p59)=895
- 분기수로는 설명 안 된다(p38/p39 는 out-degree 1 인데 정지, p59 는 2 인데 통과).
  차동구동 로봇이 90° 회전을 제자리에서 해야 하는 기구학 제약이다
- **앞 절의 "로봇은 모든 PathPoint 에서 멈춘다"는 철회한다.** 그 관측은 코너만 있는
  루프 구간(p37/p38/p39/p40)이라 코너와 노드가 구분되지 않았다

**B. 라우트 스텝 경계는 정지를 만든다 — 같은 직선, 같은 지점에서**
- 2스텝 라우트 (a=p59, a1=p40), p58 에서 출발:
  t=26.6 감속 시작(405->232->147->101->65->38->31->29 mm/s 크리프)
  t=33.3~35.1 **vf=0 (1.9s)**, t=35.7 소폭 후진(-26.4), t=36.3~37.6 **vf=0 (1.3s)**
  t=38.7 재출발 341 -> t=39.4 504
  즉 감속 시작부터 복귀까지 약 13초, 완전 정지 3.2초
- 스텝 전이는 정상 관측: key a -> a1 -> a11(idle)

**C. 결론 — CP 의 메커니즘은 "라우트로 묶기"가 아니라 "하나의 goto 로 합치기"다**
- 로봇은 직선 중간 노드를 이미 무정지·무감속으로 통과한다. 새 기능이 필요 없다
- 반대로 multi-step 라우트는 스텝 경계마다 정지를 **새로 만든다**. CP 에 해롭다
- 따라서 UmSchedulerThis / UmRoutes / UmSchedulerList 노선은 **CP 목적으로는 폐기**한다
  (P2-1/P2-2 로 기능 자체는 확정했지만 쓸 이유가 없어졌다)
- 어댑터가 할 일: 연속 released base 노드 구간을 **마지막 노드 하나의 goto** 로 내리고,
  중간 노드는 pose 스트림 + 반경으로 통과 판정만 해서 lastNodeId/nodeStates 를 갱신한다
  -> 이것이 libVDA5050++ `evalPosition()` / inorbit feedback 방식과 정확히 같은 형태다
- 코너 정지(~10s)는 기구학 제약이라 남는다. 이득은 직선/완만 구간에서 나온다

**D. 미해명**
- 단일 goto 실행에 `--max-vel 500` 을 줬는데 1003 mm/s 가 관측됐다.
  2스텝 실행에서는 504.2 로 지켜졌다. max_vel 이 언제 걸리는지 불명
- 코너 정지 10초의 내역(회전 시간 vs #watch 대기)을 분리하지 않았다.
  `start_reach_max_dis` 로 코너 정지를 줄일 수 있는지도 미검증
- 각 관측은 1회씩이다. 반복 없이 수치를 일반화하지 말 것
