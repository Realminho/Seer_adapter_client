# move-extension-recipe-plan

### 목표
- state_action 운영 매뉴얼 작성 및 Equipment Parameter Registry 호환성 확인

### 지금
- 매뉴얼 및 EPR 호환성 보강 완료

### 완료
- 기존 state_action 기능 구현 및 관련 테스트 완료
- 고객용 상태 연동 action 매뉴얼과 manual 목차 추가
- EPR opaque key 스캔·값 단위 set 호환성 테스트 추가
- EPR 적용 검증에 실제 action/recipe registry 및 state_action 참조 검사 추가

### 다음
- 현장 actionType과 파라미터로 DRIVING start/end 설정 후 실장비 확인

### 검증
- EPR/설정/state action 관련 97 passed, 2 subtests passed; py_compile 및 diff check 통과
