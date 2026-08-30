# missing-robots-hcl

### 목표
- 원격 AMR adapter의 robots.hcl 누락으로 인한 재시작 실패 복구 방법 확인

### 지금
- 원인 및 복구 경로 확인 완료

### 완료
- 업데이트 기본 keep 모드는 원격 robots.hcl이 없으면 파일을 생성하지 않음을 확인
- 원격 robots.toml을 일회성 변환하는 스크립트와 절차 확인
- 로컬 robots.hcl은 simulator 2대 설정이므로 실차에 overwrite하면 안 됨을 확인

### 다음
-

### 검증
- 배포 스크립트 robots_hcl keep/overwrite 분기와 로컬 fleet 내용 정적 확인
