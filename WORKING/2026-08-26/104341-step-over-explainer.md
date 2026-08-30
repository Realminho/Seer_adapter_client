# step-over-explainer

### 목표
- "step over"(노드 무정차 통과) 기능 설명 자료를 채팅으로 작성

### 지금
- 저장소 조사 완료. 해당 기능 구현 코드는 develop/워킹트리/타 브랜치에 없음.
  근거 문서만 존재: WORKING/2026-08-22/224900-faster-shortcut-driving.md(설계 브레인스토밍),
  WORKING/2026-08-25/135202-motion-mode-horizon-research.md(조사, 기능명 = 연속 경로 주행 CP)

### 완료
- 용어 확인(사용자): point-to-point 이동 중 노드에서 안 멈추고 통과. 액션은 그대로 수행
- 채팅 설명 작성(정의/원인/메커니즘/병합 금지 목록/VDA5050 근거/남은 구멍)

### 다음
- 채팅 설명 작성 후 종료. 구현 코드가 다른 저장소/기기에 있으면 경로 받아 갱신

### 검증
- grep "step[ _-]?over" 전 워크스페이스: AMR 기능 해당 히트 0건
- UmSchedulerList 호출처 여전히 0건(jibot-client client.py:1019 래퍼만), `_settle_goto_arrival` 호출 2곳 유지(:6352,:6420) → 구현 미반영 확인

---

## CP 개발 일정 (2026-09-01 ~ 09-11, 9영업일)

- 9/1 화: 홉 타이밍 로그 추가(발사→도착존→cmd 종료→완전정지), routes 파라미터 수령 / 목표: 기준선 숫자 확보
- 9/2 수: 실기 스파이크 — UmSchedulerThis 2스텝 goto 라우트 / 목표: 하달 방식 확정
- 9/3 목: 검증 하네스 — FakeVehicle 주행 상태 전이, 2홉 테스트, 시뮬레이터 스케줄러 스텁
- 9/4 금: 통과 판정 분리(pose+반경, evalPosition 형태) TDD
- 9/7 월: lastNodeId/nodeStates/edgeStates 배선, edgeStates 제거 시점 교정
- 9/8 화: `_get_drivable_segment()` 이식 + 병합 금지 목록
- 9/9 수: CP 실행 경로 배선, motion_seq/rotateTo 정합, 설정 게이트
- 9/10 목: new_base_request 구현 + 실기 1차 시운전
- 9/11 금: 실기 A/B 계측, 예외 시나리오, 롤백 기준·문서
