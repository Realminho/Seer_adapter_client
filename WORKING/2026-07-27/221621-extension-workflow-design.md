# extension-recipe-hcl-design

### 목표
- extension/recipe 개념 재정립 + HCL 설정 전환 설계, 그리고 1단계(HCL 로더 + robots.hcl) 구현 계획 작성.

### 지금
- 1단계 구현 계획 작성 완료. 실행 방식(subagent/inline) 선택 대기.

### 완료
- 설계 문서: docs/superpowers/specs/2026-07-27-extension-recipe-hcl-design.md (678줄, 14절)
- 리뷰 8건 검증 후 반영 (7건 타당, 3번은 절반 — tombstone 근거를 오완료 방지에서 진단으로 재정의)
- 설계 문서 사실 오류 1건 추가 수정: WebUI는 fleet 파일을 원문 텍스트로 저장함(web/server.py:973-998). "읽기 전용"이 아니었음. 구조 직렬화가 아니라 HCL 전환에는 영향 없음
- 구현 계획: docs/superpowers/plans/2026-07-27-hcl-loader-and-robots-migration.md (8 태스크 45스텝)
- 계획 자체 검토에서 2건 보완: robot의 config 상대경로가 resolve_robot_path를 안 거치던 문제(main.py:270,285 / web/server.py:966), test_adaptor_cli.py 픽스처 본문 미기재

### 다음
- 실행 방식 확정 후 Task 1부터 진행. 이후 2~6단계는 별도 계획으로 작성
- 커밋은 아직 안 함 (설계 문서 + 계획 문서 + 기존 미커밋 extension_groups 구현이 함께 대기 중)

### 검증
- python-hcl2 8.1.2 파싱 검증(블록/순서/중첩/var 미평가), asyncio 취소 실험(finally 완주 확인), 엣지 케이스 3종 사전 검증, 계획 플레이스홀더 스캔 통과
