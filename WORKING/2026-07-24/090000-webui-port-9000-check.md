# webui-port-9000-check

### 목표
- 192.168.3.221 업데이트 후에도 WebUI 포트가 9000으로 안 바뀐 원인 확인

### 지금
- 카메라 8080→9001 이행 방법 안내 (systemd unit 재생성 필요)

### 정정
- 원격 config.toml 은 이미 web_ui.port = 9000 (WebUI /config 로 확인) → config 문제 아님
- 8090 서비스 중 = WebUI 프로세스 미재시작 (stale)
- 카메라: amr-camera.service active(enabled), uptime ~1106s, 8080 listen
  → /etc/systemd/system/amr-camera.service 가 구버전 _port:=8080 로 렌더된 상태
  → 이 unit 은 update 스크립트가 아니라 setup-adaptor-service.sh 가 생성(WEB_VIDEO_PORT 기본 9001)
- 원격 config: video.web_video_server_url=http://127.0.0.1:8080, web_ui.camera_default_port=8080

### 완료
- 레포 기본값은 전부 9000 (config.toml:309, config.py:394, run-web.sh:25 fallback)
- update-jibot-adapter-over-ssh.sh: CONFIG_TOML_MODE 기본 keep(L70) + tar --exclude config/config.toml(L414-415)
  → 업데이트가 원격 config.toml 을 절대 덮지 않음
- .221 포트 스캔: 22 OPEN, 8090 OPEN, 9000 closed, 8080 OPEN, 9001 closed
- SSH 직접 확인 실패 (publickey,password — BatchMode 불가)

### 다음
- 사용자: 원격 ~/adapter/config/config.toml [web_ui].port=9000 수정 후 webui 재시작
- 카메라도 동일 이슈(8080 → 9001) 같이 처리할지 확인

### 검증
- 수정 후 9000 OPEN / 8090 closed 재스캔
