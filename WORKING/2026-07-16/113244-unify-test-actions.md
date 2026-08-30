# implement-action-module-panels

### 목표
- 승인된 배포 호환성 계획 5개 task를 구현하고 검증한다.

### 지금
- 전체 구현·최종 리뷰·독립 검증을 완료하고 branch 인계 방식을 정한다.

### 완료
- 설계 문서가 개정되고 14개 task 구현 계획이 작성된 상태를 인계받았다.
- 현재 checkout은 일반 branch이며 `.worktrees/`가 이미 ignore됨을 확인했다.
- Task 10 전 clamp 예약 충돌과 PIO panel parameter 불일치를 확인했다.
- 보정 계획과 설계 개정을 `d9e934c`로 커밋했다.
- 권한 확장 환경 baseline 65개 테스트가 통과했다.
- 14개 task 구현과 task별 명세·품질 리뷰를 완료했다.
- 관련 회귀 107개와 return_to HTTP 통합 테스트를 권한 확장 환경에서 통과했다.
- 전체 WebUI 검증에서 legacy spec 호환 회귀를 발견해 `d36448b`로 최소 수정하고 별도 품질 리뷰를 통과했다.
- 최종 전체 구현 리뷰에서 Critical/Important/Minor 이슈가 없음을 확인했다.
- 사용자가 branch 유지(옵션 3)를 선택했다.
- `jibot-client-refactor`를 `origin`에 푸시하고 PR #2를 생성했다.
- JIBOT/Hexplorer tar에 action module 코드와 panel HTML이 포함됨을 확인했다.
- 기존 config 유지와 런처 import 경로는 호환되지만 JIBOT `--restart`가 WebUI를 재시작하지 않고 clean 범위가 core/extensions/web을 제거하지 않는 공백을 확인했다.
- 주 설치 스크립트와 일괄 서비스 관리 스크립트는 adapter/WebUI를 함께 처리함을 확인했다.
- 레거시 installer의 run-main 직접 실행, 잘못된 SSH 원격 경로 문서, stale dispatcher shell test와 제거된 TUI 안내를 추가 확인했다.
- Hexplorer helper 미업로드, WebUI optional 처리, JIBOT legacy discovery·sudoers 조건을 설계에 명시했다.
- Task 1에서 WebUI·camera optional 처리, sudoers camera 범위, stale TUI 안내 정리를 구현하고 두 단계 리뷰를 통과했다.
- Task 2에서 JIBOT clean 범위에 core/extensions/web을 추가하고 설치된 WebUI를 legacy adapter 유닛과 함께 재시작하도록 구현·리뷰했다.
- Task 3에서 Hexplorer restart CLI, 단일 service helper 업로드, clean 범위를 구현하고 빈 restart command 검증까지 리뷰 반영했다.
- Task 4에서 compatibility installer를 dispatcher로 전환하고 선행 `--instance` 계약과 결정적 smoke test를 구현·리뷰했다.
- Task 5에서 SSH/service 문서를 실제 parser·dispatcher·fleet 동작과 맞추고 전체 회귀와 두 단계 리뷰를 통과했다.
- 최종 리뷰에서 sudoers wildcard·escaping과 Hexplorer helper preflight를 보완하고 재리뷰 이슈 0건을 확인했다.

### 다음
- 사용자가 선택한 방식으로 branch를 push/PR 또는 유지한다.

### 검증
- 최종 독립 검증: root tests 88 passed, deploy tests 54 passed, adapter action/config/fleet 94 passed(소켓 권한 환경), installer·dispatcher·tmpfiles smoke 및 helper/setup dry-run 통과, 관련 shell `bash -n`과 branch/working-tree `git diff --check` 통과.
