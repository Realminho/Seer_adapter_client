# AMR 상태 연동 Action 설정 매뉴얼

이 문서는 AMR의 `workingState`가 시작되거나 끝날 때 extension 또는 recipe를
자동 실행하도록 설정하는 방법을 설명한다. 설정 파일은 `config/extensions.hcl`이다.

## 1. 지원 상태

`state_action`의 이름에는 다음 `workingState` 값을 대문자로 그대로 사용한다.

| 상태 | 의미 |
| --- | --- |
| `IDLE` | 대기 |
| `DRIVING` | 이동 중 |
| `ACTING` | 상하차·도킹 등 작업 중 |
| `CHARGING` | 충전 중 |
| `PAUSED` | 일시 정지 |
| `BLOCKED` | 장애물 등으로 진행이 막힘 |
| `ERROR` | 비상 정지·위치 상실·치명 오류 |

소문자나 표에 없는 이름은 설정 오류로 거부된다. 현재 기능은
`workingStateDetail`의 `LOADING`, `DOCKING`, `BRAKE` 등에는 연결하지 않는다.

## 2. 기본 설정

이동 시작과 종료에 각각 action을 실행하는 예시는 다음과 같다.

```hcl
state_action "DRIVING" {
  start = {
    action = "drivingWarningOn"
    parameters = {
      signal = "warningLamp"
    }
  }

  end = {
    action = "drivingWarningOff"
    parameters = {
      signal = "warningLamp"
    }
  }
}
```

- `start`: 해당 상태에 진입할 때 한 번 실행한다.
- `end`: 해당 상태에서 다른 상태로 이탈할 때 한 번 실행한다.
- `action`: 등록된 extension 또는 `recipes.hcl`의 recipe 이름이다.
- `parameters`: action에 전달할 파라미터다. 필요 없으면 생략할 수 있다.
- `start`와 `end` 중 하나만 필요한 경우 다른 하나를 생략할 수 있다.

같은 상태가 계속 보고되는 동안에는 action을 반복하지 않는다. 다른 상태를 거쳐
다시 진입하면 `start`를 다시 실행한다. 어댑터가 처음 관측한 상태에 설정이 있으면
그 상태의 `start`도 실행한다.

## 3. 실행 및 실패 처리

`start`가 끝나기 전에 상태가 바뀌면 실행 중인 `start`를 취소한다. recipe인 경우
recipe에 선언된 `cleanup`을 마친 뒤 이전 상태의 `end`를 실행한다. 이후 새 상태의
`start`를 실행한다.

state action의 실패는 AMR 이동이나 상태 전환을 중단하지 않는다. 실패 내용은 adapter
로그의 `[STATE ACTION FAILED]` 메시지로 확인한다. 상태 이탈에 필요한 안전 출력은
`start` recipe의 `cleanup`과 `end` 양쪽에 적절히 배치한다.

프로세스 강제 종료나 전원 차단은 정상적인 상태 이탈이 아니므로 `end` 실행이 보장되지
않는다. 전원이 끊겨도 안전 상태가 되어야 하는 출력은 설비 또는 IO 하드웨어 차원에서도
fail-safe로 구성한다.

## 4. 설정 적용

새 `state_action` 블록을 추가한 뒤 adapter 서비스를 재시작한다. 로봇별
`extensions` 경로가 `robots.hcl`에 지정되어 있다면 반드시 그 파일을 수정한다.

```bash
sudo systemctl restart amr-adaptor.service
sudo systemctl status amr-adaptor.service
```

인스턴스 서비스는 해당 로봇 이름을 사용한다.

```bash
sudo systemctl restart amr-adaptor@ROBOT-ID.service
sudo journalctl -u amr-adaptor@ROBOT-ID.service -n 100 --no-pager
```

등록되지 않은 action, 잘못된 상태 이름, 중복 상태 블록, 비어 있는 `start`/`end`는
부팅 또는 파라미터 적용 검증에서 거부된다.

## 5. Equipment Parameter Registry

`state_action`은 Equipment Parameter Registry(EPR)의 `extensions.hcl` source를
사용한다. 기존 블록의 값은 다음 opaque key 형식으로 노출된다.

| 설정 | EPR key 예시 |
| --- | --- |
| 시작 action | `extensions.hcl:DRIVING.start.action` |
| 시작 파라미터 | `extensions.hcl:DRIVING.start.parameters.signal` |
| 종료 action | `extensions.hcl:DRIVING.end.action` |
| 종료 파라미터 | `extensions.hcl:DRIVING.end.parameters.signal` |

기존 leaf 값은 EPR `set` 연산으로 수정할 수 있다. EPR에서 받은 key는 opaque
문자열이므로 외부 시스템에서 분해하거나 이름을 변환하지 않고 그대로 돌려보낸다.

새 `state_action` 블록 추가, 블록 삭제, `start` 또는 `end` 구조 추가처럼 값 하나의
수정이 아닌 작업은 `extensions.hcl` 원문 `replaceText` 방식으로 적용한다. 원문 적용
후에는 실제 부팅 로더와 action registry로 전체 설정을 재검증하며, 실패하면 파일을
원래 내용으로 복원한다.

EPR로 action 이름을 바꿀 때 대상 extension 또는 recipe가 같은 로봇 설정에서 활성화되어
있어야 한다. 없는 action을 참조하면 `unregistered action` 오류로 적용이 거부된다.

## 6. 현장 확인 절차

1. 대상 extension 또는 recipe를 WebUI나 개별 action 명령으로 먼저 실행한다.
2. `state_action "DRIVING"`을 추가하고 서비스를 재시작한다.
3. 정지 상태에서 이동을 시작해 `start`가 한 번만 실행되는지 확인한다.
4. 이동 중 heartbeat마다 action이 반복되지 않는지 확인한다.
5. 이동을 끝내 `end`가 한 번 실행되는지 확인한다.
6. 이동 중 장애물 정지처럼 `DRIVING`에서 다른 상태로 바뀌는 경우에도 종료 출력이
   안전하게 적용되는지 확인한다.
7. adapter 로그에 `[STATE ACTION FAILED]` 또는 설정 검증 오류가 없는지 확인한다.
