# implement-extension-recipe-hcl

### 목표
- extension/recipe 구현과 NH-SH6-TR-001 전용 현장 recipe를 완성한다.

### 지금
- NH-SH6-TR-001 전용 recipe와 적용 문서·검증을 완료했다.

### 완료
- motion_rules 섀도잉 수정과 extensions.hcl 로더의 필수 블록·중복·bool 검증을 확인했다.
- robot별 extensions 경로를 main/WebUI/fleet registry까지 전파했다.
- extension_groups 초안을 recipes.hcl + recipe/step/cleanup으로 교체했다.
- fail-fast, retry, recipe 내부 deadline, 별도 cleanup budget, 항상 cleanup, 결과 병합을 구현했다.
- robot별 recipes 경로와 배포 시 현장 recipes.hcl 보존을 연결했다.
- 내장 5종의 공통 async primitive와 order ownership gate를 연결했다.
- 합성 액션 completion table, tombstone, attempt별 task 추적과 timeout/외부 취소 계약을 구현했다.
- ASWorkflow/EVWorkflow를 adapter 소유 PIO/EZI/config 주입 방식으로 바꾸고 facility 액션 6종으로 등록했다.
- airShowerPassage/elevatorTrip 현장 recipe 예제와 typed parameter acceptance를 추가했다.
- 전체 로봇 설정 조합을 hardware I/O 없이 검사하는 validate-extension-recipes.py를 추가했다.
- simulator/실설비 실행 순서, 중단 조건, 롤백 절차를 문서화했다.
- NH-SH6-TR-001 전용 airShowerPassage/elevatorUp/elevatorDown recipe를 추가했다.
- 확정된 door/floor pin과 PIO station ID만 고정하고 미확정 map/distance/speed는 실행 파라미터로 유지했다.

### 다음
- NH/HN 장비 ID를 현장에서 확인한 뒤 robots.hcl에 recipes 경로를 활성화한다.

### 검증
- extensions/config/fleet/CLI/deploy 대상 테스트 160건 통과; socket 기반 테스트는 sandbox 제약으로 제외.
- recipe/config/registry/action module 대상 최종 117건(4 subtests), config 결합 76건,
  root CLI/deploy 61건 통과. py_compile, bash -n, git diff --check 통과.
- bridge/facility/motion/switchMap/goto 관련 137건 통과, 최종 결합 94건과 root 61건 통과.
- adaptor 전체는 218건 통과 후 sandbox의 UDS 생성 금지 테스트에서 중단됨(코드 회귀 아님).
- shipped example HCL→Config→registry→실행 acceptance 2건과 설정 검증 CLI 2건 통과.
- 기본 HN-SH6-TR-001 설정 CLI 검증 성공(registered actions 29, recipes none).
- 최종 관련 통합 스위트 161건 통과; bash -n, py_compile, git diff --check 통과.
- NH 전용 recipe 포함 관련 테스트 22건 통과, 전용 3 recipe/등록 액션 32개 검증 성공.
