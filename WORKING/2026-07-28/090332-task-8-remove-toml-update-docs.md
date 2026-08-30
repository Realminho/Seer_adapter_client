# task-8-remove-toml-update-docs

### 목표
- robots.toml/robots.toml.example 삭제, 문서 7개 robots.hcl로 갱신, 관련 테스트/스크립트 동기화, 전체 검증

### 지금
- 완료. task-8-report.md 작성 후 종료

### 완료
- Step 1: robots.toml/robots.toml.example git rm 완료
- Step 2: 문서 7개 sed 치환 (robots-hcl-mode, robots.hcl) 완료
- Step 3: simulator.md TOML 예제 블록 HCL로 교체, 나머지 prose [[robot]] 참조도 robot 블록/label 용어로 정정 (jibot-onboard-quick-guide.md의 `grep '^id ='` 명령도 `grep '^robot '`로 수정 — 실제로 깨진 명령이었음)
- Step 2b: tests/test_update_jibot_adapter_over_ssh.py:68-69 갱신, 해당 테스트 48 passed
- Step 3b: adaptor/run_multi.py help 문자열 TOML->HCL 수정 (main.py/fleet.py의 TOML 언급은 per-instance config.toml 지칭이라 정상, main.py는 동시작업 보호 대상이라 미수정)
- Step 4: jibot-adapter-ssh-update.md 상단에 마이그레이션 절 추가
- Step 5: grep 검증(WORKING/ 필터 누락은 브리프 정규식 한계, 스코프 밖 파일은 render.py/registry.py/configio.py 등 — Task 5/6에 없던 범위라 미수정), adaptor 스위트 41 failed(허용된 2개 파일 안), root tests 97 passed, git diff --check 무출력
- Step 6: main.py --robot HN-SH6-TR-001 기동 확인, robots.hcl에서 fleet 조회 성공(FleetError 없음), timeout 20 안에 정상 종료
- Step 7: 커밋 7000a96

### 다음
- (없음, 태스크 종료)

### 검증
- adaptor 스위트: 41 failed (test_adapter_jibot_v3_order.py 40, test_dock_approach_config.py 1) — 모두 허용된 파일 안
- root tests: 97 passed
- git diff --check: 무출력
- 시뮬레이터 기동: FleetError 없음, source=fleet:HN-SH6-TR-001 simulator=True
