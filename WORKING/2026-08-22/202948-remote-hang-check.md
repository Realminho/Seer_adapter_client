# remote-hang-check

### 목표
- ssh lab2m-llm1@192.168.1.100 "다운된 것처럼 안 움직임" 원인 확인

### 지금
- 원인 규명 완료 + ① jest maxWorkers 제한 적용/검증 완료 (커밋 전)

### 완료
- 다운 아님. ping OK, ssh OK. uptime 2일 21시간 (재부팅 흔적 없음)
- 증상 시점(20:30): load 375, MEM 107/123Gi, SWAP 57Gi 100% 소진(free 4.0Ki)
  vmstat sy=99%, bi≈4.5GB/s, r=350 b=75 → 스왑 스래싱. 32코어 대비 load 10배 초과
- 주범: PID 3962985 jest @ /ssd2/workspaces/wcs-nodejs
  `pnpm --dir packages/api run test -- --silent src/nestjs/wms src/nestjs/order src/nestjs/notifications`
  jest-worker 31개, 각 RSS ~1.7GB (스왑분 제외한 수치)
  조상: claude PID 4085682 (--dangerously-skip-permissions 세션이 Bash로 실행)
- 부주범: PID 3959096 vitest (자식 32개) + esbuild
- 배경 상시부하: claude 프로세스 97개, npm 88개, MCP 서버(playwright/chrome-devtools/context7) 다수
- 커널 OOM killer(dmesg)는 미발동. 대신 유저스페이스 systemd-oomd 가 발동 (아래 정정)
- [정정] 자체 종료 아님. 20:31:37 systemd-oomd가 app-orca-13789.scope 를 kill
  "memory pressure for user@1000.service being 90.81% > 90.00% for > 20s with reclaim activity"
  systemd: "systemd-oomd killed 692 process(es) in this unit"
  → Orca IDE 세션 통째로 사망: claude 96개 + jest + vitest 전부 함께 종료됨 (현재 claude 1개)
  → 이후 MEM 107→23Gi, SWAP 57→11Gi, load 375→84
- earlyoom은 발동 안 함: `-m 8 -s 8` = mem<8% AND swap<8% 동시 조건.
  로그상 swap free 0% 였으나 mem avail은 계속 12.9~13.0% 유지 → 조건 미충족으로 4분+ 방치
- 이 호스트에 AMR 어댑터 프로세스 없음 (python adaptor / amr systemd 서비스 전무)
- 디스크 여유: / 25%, /ssd2 8%

### 시스템 구성 (조사 결과)
- SWAP 57.4G 실체: zram0 **49.4G (prio 100, RAM 백업)** + /swap.img **8G (prio -1, 디스크)**
  zram comp=zstd, 압축비 4.2:1, mem_used_max=11.4GB → 피크에 RAM 11.4GB를 zram이 점유
  vmstat sy=99% / bi=4.5GB/s 의 정체가 zram 압축·해제 CPU
- user-1000.slice: MemoryHigh=90G (systemctl set-property 드롭인), MemoryMax=infinity
  → High는 throttle+reclaim만 하고 kill 안 함 = 스래싱의 직접 원인
- systemd-oomd: ManagedOOMMemoryPressure=kill, Limit=90% → user@1000.service 전체를 보고 scope 단위로 죽임
- wcs-nodejs jest.config.js / packages/api/jest.config.js 둘 다 maxWorkers 없음
  → jest 기본값 = nproc-1 = **31** (관측된 워커 수와 일치)
- 디스크 여유: / 1.3T, /ssd2 1.6T (NVMe)

### 질문 3건 답변 근거
- Q1 machine-wise? → 아님. maxWorkers/maxForks는 repo별 config. 머신 단위는 cgroup.
  user@1000.service Delegate=yes, DelegateControllers=cpu memory pids → `systemd-run --user --scope -p MemoryMax=` 강제 가능 확인
- Q2 swap 증설? → oomd는 swap 잔량이 아니라 PSI 압력(90%>90% 20s)으로 kill함.
  swap 늘려도 kill 시점 안 바뀌고 프리즈만 길어짐. zram(49.4G)은 RAM이라 증설 시 역효과.
- Q3 claude 정리 → 안 함. 이미 20:31:37 oomd가 692개 통째로 죽여서 현재 1개.
- earlyoom v1.7 `--prefer chrome` 존재 → -m 임계 올려도 victim 선정이 jest로 갈지는 미검증

### 조치 (①만 진행, 사용자 승인)
- 대상: 원격 /ssd2/workspaces/wcs-nodejs (branch feature/hana-p2-2)
- jest.config.js **25개 전부**에 아래 2줄 삽입 (module.exports 바로 다음, 파일별 들여쓰기 유지)
  `// OOM 방지: 기본값 nproc-1(=31) 대신 워커 수 제한. 필요시 CLI --maxWorkers=N 으로 override`
  `maxWorkers: 4,`
- preset 상속이 섞여 있어(11개 preset / 11개 없음 / ts-jest 등) 명시 삽입 선택
- 커밋 안 함. 워킹트리에만 존재
- .worktrees/, .claude/worktrees/ 내 사본은 제외 (다른 브랜치 체크아웃)

### 검증
- 20:32 `free -h` MEM 23Gi/SWAP 11Gi, `/proc/loadavg` 84.33 하락 확인
- `pgrep -f processChild.js` → jest/vitest 트리 소멸 확인
- `node --check` 25/25 통과
- `jest --showConfig | grep maxWorkers` → packages/* 14개 전부 `"maxWorkers": 4` 확인
  (api, gateway, connector, workflow, utilities, mcp-server, mcp-core, grpc-lib,
   historian, cli, server-monitor, equipment-helper, function-call, node-red-tester)
- libs/redis 등 libs/* 는 showConfig 자체가 실패 → `git show HEAD:libs/redis/jest.config.js`
  원본으로도 동일 실패(jest-preset-angular 누락) = **사전 존재 문제, 내 변경과 무관** 확인
- git diff --name-only 결과 내 변경 25개 전부 jest.config.js.
  packages/wcs-ui/** 5개는 **다른 세션이 동시 편집 중인 파일** — 손대지 않음

- CLI 우회 점검: repo 전체 package.json/yml 에서 maxWorkers 상향 스크립트 **없음**.
  발견된 건 전부 `--runInBand`(1프로세스, 더 제한적)라 무해.
  machine-wise-deploy/, pulse-deploy/ 는 .gitignore 된 배포 산출물(tracked 0) — 대상 아님

### 미조치 (범위 밖)
- **vitest 미조치**: 사고 당시 32-child vitest 런은 그대로임.
  설정 위치 `packages/wcs-ui/vite.config.ts` 의 `test:` 블록 — poolOptions/maxForks/maxThreads 없음
  (별도 vitest.config.* 파일은 없음. `find -name vitest.config*` 빈 결과는 오답이었고 vite.config.ts 안에 있었음)
- ② systemd-run --scope 격리, ③ swap/zram → 진행 안 함

### 다음
- **커밋 결정 필요**: 현재 워킹트리에만 있음. 이 저장소는 다른 세션이 동시 편집 중이라
  `git checkout .` 한 번에 소실 가능. feature/hana-p2-2 에 인프라 변경을 섞을지 판단 필요
- 되돌리기: 25개 파일에서 `maxWorkers: 4,` + 바로 위 주석 2줄 삭제
  (`git checkout -- <files>` 는 타 세션 변경 위험으로 비권장)

### 검증
- 20:32 `free -h` MEM 23Gi/SWAP 11Gi, `/proc/loadavg` 84.33 하락 확인
- `pgrep -f processChild.js` → 1개(자기 자신 grep), jest/vitest 트리 소멸 확인
