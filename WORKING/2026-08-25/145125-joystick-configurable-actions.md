# joystick-configurable-actions

### 목표
- joystick 키와 D-pad 1~16 extension/recipe action 매핑을 쉽게 변경 가능한 설정으로 추가한다.

### 지금
- 작업 완료.

### 완료
- extensions.hcl joystick 키 설정과 1~16 action 슬롯, dataclass 로딩, registry 검증, 문서·예제를 추가.

### 다음
- 실제 controller 입력 번호를 측정해 enabled=true 및 PL/PR/R4 번호를 확정한다.

### 검증
- 관련 테스트 79개와 subtest 2개 통과, HCL/JSON 로딩 및 git diff --check 통과.
