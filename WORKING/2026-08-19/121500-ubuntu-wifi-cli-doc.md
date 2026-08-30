# ubuntu-wifi-cli-doc

### 목표
- Ubuntu 명령줄 Wi-Fi 연결 절차(nmcli / netplan + wpa_supplicant)를 문서화하고 README에 링크 추가

### 지금
- 완료. 커밋은 하지 않음(현재 브랜치 feat/move-segment-goto-fallback와 무관한 변경)

### 완료
- docs/manual/ubuntu-wifi-cli.md 신규 작성 (방식 판별 → nmcli → netplan/wpa_supplicant → 문제해결 → AMR keeper 참고)
- docs/manual/README.md Manuals 목록에 링크 추가
- README.md 190행, jibot-onboard-access 안내 아래에 링크 추가

### 다음
- 필요 시 커밋. 링크 대상 문서/스크립트 경로는 검증 완료

### 검증
- 문서 내 앵커 링크 2개 GitHub slug 규칙으로 검증 통과
- 참조 스크립트 경로(scripts/amr-wifi-keeper.sh, scripts/setup-wifi-keeper-over-ssh.sh) 존재 확인
- nmcli radio/device status/device wifi list/rfkill list 실제 실행하여 동작 확인 (iw는 이 장비에 미설치 → 설치 안내를 3.1에 추가)
