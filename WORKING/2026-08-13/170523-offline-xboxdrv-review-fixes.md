# offline-xboxdrv-review-fixes

### 목표
- 최종 브랜치 리뷰 지적 7건 수정 (Important 1~3, Minor 4~7)

### 지금
- 완료. 커밋 3개 (c56a12a, 3544774, 20acd0f)

### 완료
- Important 1: `|| true` -> `|| bundle_failed=1`, skip 게이트에 dpkg 종료코드 반영
- Important 2: fetch 스크립트 이전 버전 deb prune + `dpkg -i --refuse-downgrade`
- Important 3: camera `|| true` 테스트 추가
- Minor 4: 매뉴얼 xpad skip 문장 순서/의미 수정
- Minor 5: deb 설치 분기가 DRY_RUN 가드 없음을 테스트로 고정
- Minor 6: 패키지당 deb 정확히 1개 + arm64 assert
- Minor 7: spec `XBOXDRV_PACKAGES` -> `PACKAGES`
- 추가: 헬퍼 계약 주석의 낡은 `|| true` 안내 수정

### 다음
- 없음. 보고 완료.

### 검증
- 전체 테스트 146 passed / 2 failed (기존 실패 2건 그대로, baseline 145+2)
- 실행 하니스로 Important 1 6개 케이스 전부 확인, 수정 전 코드에서는 case 2 실패 재현
