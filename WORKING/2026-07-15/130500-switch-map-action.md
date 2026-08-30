# switch-map-action

### 목표
- switchMap core action을 JIBOT map 전환·확인 경로에 연결

### 지금
- WCS snapshot에서 전달된 switchMap Node action 통합 검증 대기

### 완료
- switchMap Instant·Node scope factsheet capability 추가
- simulator mapId 전환과 JIBOT SetMap→UmGetMapName 확인 구현
- 30초 기본 timeout과 FAILED 상태 구현

### 다음
- 실장비 안전 정지 상태에서 map 전환 소요 시간·현재 맵 확인

### 검증
- switchMap 5건 PASS, 관련 adaptor 회귀 suite exit 0, compileall PASS
