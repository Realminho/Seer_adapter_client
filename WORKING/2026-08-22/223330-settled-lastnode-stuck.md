# settled-lastnode-stuck

### 목표
- settled 모드에서 수동 주행 후 lastNodeId 와 거리가 벌어져도 해제되지 않는 문제 수정

### 지금
- 결정: 둘 다. 기본값 0.0 → 500.0 변경 완료. 로봇 62 config 한 줄 추가는 **미완**(로봇 오프라인)

### 완료
- 근본 원인: settled 는 "쓰기 전용". `_capture_idle_settled`(adapter_jibot.py)는 reach 밖이면 early return 하고,
  pose 기준으로 lastNodeId 를 되돌리는 경로가 localize 말고는 없었음. `_refresh_last_node_id_errors` 도
  값이 빌 때만 에러를 내므로 stale 값은 감지 못 함.
- 수정: `_release_stale_last_node(x, y)` 추가 — settled + 오더 비활성 + gap > `last_node_release_xy` 면
  `_set_last_node("", 0)` 후 `[LAST NODE RELEASE]` 로그. `_update_nearest_node_from_position` 에서 seed/capture 앞에 호출.
- 플래핑 방지: `_missing_last_node_seed_gate()` 로 빈 lastNodeId seed 게이트를 release 반경으로 캡.
- 설정: `Settings.last_node_release_xy = 0.0`(기본 off), config.toml 2곳에 500.0 설정.
- 문서: docs/reference/jibot-arrival-and-last-node.md 정책 항목 추가.

### 실기 62 확인 (2026-08-23 01:0x)
- `~/adaptor/adapter_jibot.py` 에 `_release_stale_last_node` 있음 = 코드는 배포됨 (서비스 재시작 00:37:01)
- 로봇 실효 설정: capture_mode=settled, idle_reach=200, **last_node_release_xy=0.0(키 없음→기본값)**,
  missing_gate=0.0, seed=True, nearest_mode=pathPoint
- `journalctl | grep "LAST NODE RELEASE"` = 0건, `[LAST NODE FALLBACK] nearestNodeId=p2 gap=27.1` (00:40:29) 이후 고착
- 배포 스크립트 기본값이 `--config-toml-mode keep` 이라 로봇 config.toml 은 절대 덮이지 않음
  → 0.0 기본값이면 어떤 로봇에도 이 수정이 도달하지 않음
- 좌표(hana.json): p2(16377,1666) p36(16350,91) p37(16376,-897). p2→p36 1575, p36→p37 988,
  p36~p37 중간에서 p2 까지 2069. 중간 지점은 최근접 노드도 494 로 capture reach 200 밖 → 갱신도 해제도 안 됨
- 로봇 설정 그대로 재현/검증: release=0 → 'p2' 고착, release=500 → 해제 후 p36 재시드 (scratchpad/sim62.py)

### 다음 (로봇 62 복귀하면)
- config 한 줄 추가 + 재시작. **코드 재배포 불필요** — 해제 로직은 이미 배포돼 있고 값만 없다:
  `ssh ucore@192.168.101.62 'grep -q last_node_release_xy ~/adaptor/config/config.toml || sed -i "/^last_node_capture_mode/a last_node_release_xy = 500" ~/adaptor/config/config.toml; sudo systemctl restart amr-adaptor'`
- 확인: `journalctl -u amr-adaptor -f | grep "LAST NODE"` 에서 `[LAST NODE RELEASE]` 확인
- 활성 오더 유무도 볼 것: 오더가 active 면 설계상 해제를 건너뛰므로 같은 증상으로 보인다
- 다른 로봇(61 등)은 기본값 500.0 이 들어간 코드 재배포만으로 적용됨

### 검증
- 새 테스트 7개(×2 클래스) + config 2개 추가, TDD 로 RED 확인 후 GREEN.
- `scripts/run-tests.sh -q`: 2190 passed / 13 failed (13건은 HEAD 사본에서도 동일하게 실패 = 사전 실패).
- 재현 스크립트 재실행: `lastNodeId ''` + `LAST_NODE_ID_MISSING` 확인.
