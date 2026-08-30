# install-statusline

### 목표
- 이 머신에 Claude Code statusline 설치 (~/.claude/statusline-command.sh + settings.json 등록)

### 지금
- 완료

### 완료
- jq 1.7.1-apple 확인 (>=1.6)
- 기존 파일 백업: ~/.claude/statusline-command.sh.bak.20260821, ~/.claude/settings.json.bak.20260821
- statusline-command.sh 신규 작성 + chmod +x (bash -n 통과)
- settings.json .statusLine = {type:command, command:"bash $HOME/.claude/statusline-command.sh"} (다른 키 15개 보존)

### 다음
- 없음. 롤백 필요 시 *.bak.20260821 복원

### 검증
- [4] 명령 실행: exit=0, 3행(awk NR=3), 7d=0;31(빨강) / ctx·5h=0;32(초록), 시각은 transcript 없어 생략
