# fix-missing-hcl2-on-robot

### 목표
- 로봇(amr2)에서 `amr-adaptor.service`가 기동하지 못하는 원인을 순서대로 제거한다.

### 지금
- venv 문제 해결됨. robots.hcl 작성값 확보(vehicle_ip/ezi_io/ezi_motor). robot id만 확인 대기.

### 완료
- 1차 원인: `.venv`/`venvJIBOT` 둘 다 없어 시스템 python3.8로 폴백 → hcl2 없음.
  로봇 확인 결과 venv 자체가 부재였고, 유닛은 설치·enable된 상태 → setup이
  `repair_venv`의 `die_no_python`에서 죽은 흔적.
- `adaptor/run-adapter.sh`: dispatch 이전 최소 버전 가드 + deps import 가드 추가.
- `scripts/setup-adaptor-service.sh`: `mise_existing_python()` 추가(설치된 mise는
  `--with-mise` 없이 사용), `find_uv()`가 mise 관리 uv도 탐색, `die_no_python()`이
  enable된 유닛의 crash-loop를 경고. → 로봇에서 venv 생성 성공(3.11).
- 2차 원인: `config/robots.hcl` 부재. 배포 스크립트가 빌드 머신 robots.hcl을
  일부러 설치하지 않기 때문(그 파일은 시뮬레이터 2대짜리라 실차에 부적합).
- `adaptor/config/adapter_dispatch.py`: 복구 안내가 `cp robots.hcl.example`만 알려
  주는데 예시는 robot 블록이 2개라 그대로 복사하면 run_multi로 2대가 뜬다.
  "하나만 남길 것 / IP는 자리표시자" 명시. 테스트 2개 추가(총 13개 통과).
- amr2 실측값: vehicle_ip 192.168.101.62, ezi_io 10.8.8.87, ezi_motor 10.8.8.2.
  (amr1은 192.168.101.61 = NH-SH6-TR-001)

### 다음
- robot id 확인. `NH-SH6-TR-002` 가정 — 블록 라벨이 곧 VDA5050 serialNumber라
  틀리면 FMS가 인식하지 못함. 토픽 `amr/v3/<id>`를 broker에서 확인해 확정.
- robots.hcl 작성 후 `sudo systemctl restart amr-adaptor.service`.
- config.toml `[mqtt_broker]` 값 확인(이력상 192.168.2.61 / 192.168.101.50 / 192.168.3.108 혼재).

### 검증
- `bash -n` 통과, 루트 테스트 117개 통과, adaptor 테스트 1372개 통과.
- `mise_existing_python` 가짜 mise 트리로 4경로 확인, `setup --dry-run` 정상.
- 로봇에서 venv 생성 및 hcl2 로드 성공(다음 단계 오류로 진행한 것으로 확인).
