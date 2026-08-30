# final-review-fixes

### 목표
- robots.toml -> robots.hcl 마이그레이션 최종 리뷰 findings 5건 수정 (Critical 1,2,3 / Important 4,5)

### 지금
- 완료. 커밋 대기/완료.

### 완료
- Critical 1: requirements.txt에 python-hcl2 핀 추가, setup-adaptor-service.sh:340 프로브에 hcl2 추가, offline_packages에 python_hcl2/lark/regex(x86_64+aarch64 cp312) 휠 vendoring, 오프라인 설치 실측 검증
- Critical 2: update-jibot-adapter-over-ssh.sh find 필터에 ! -name robots.toml 추가 (주석 포함), bash -n 통과
- Critical 3: jibot-adapter-ssh-update.md 마이그레이션 절 재작성 (원격 경로 기준, 5단계)
- Important 4: hcl.py를 explicit_blocks=True + __is_block__ 마커 기반 검증으로 전환, TDD RED->GREEN 5개 테스트, 두 반증 케이스 FleetError로 거절 확인
- Important 5: core/registry.py `_jibot_specs_from_fleet`가 resolve_robot_path(robot,"config",DEFAULT_FLEET_PATH) 쓰도록 수정, 회귀 테스트 추가(구코드로 되돌려 실패 확인 후 복원)
- 두 스위트 실행: tests/test_hcl.py+test_fleet_registry.py 31 passed; adaptor/tests 전체 41 failed(베이스라인과 동일 파일에만 국한)/1196 passed, 반복 실행으로 안정성 확인
- 보고서 작성: .superpowers/sdd/2026-07-27-hcl-loader-and-robots-migration/final-fix-report.md

### 다음
- (없음, 작업 완료)

### 검증
- pytest tests/test_hcl.py tests/test_fleet_registry.py -q: 31 passed
- pytest adaptor/tests -q: 41 failed (전부 test_adapter_jibot_v3_order.py/test_dock_approach_config.py), 1196 passed, 2회 반복 확인
- bash -n scripts/update-jibot-adapter-over-ssh.sh, scripts/setup-adaptor-service.sh: 둘 다 OK
- 오프라인 venv에 --no-index --find-links offline_packages -r requirements.txt 설치 후 import hcl2 성공
