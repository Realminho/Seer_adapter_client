# AMR 오류 어휘 — 벤더 중립 공통 코드

> 소스: `adaptor/protocol/amr_fault_taxonomy.py` · 번역 지점: `adaptor/protocol/vda_2_0_0/vda5050_2_0_0_state.py` → `Error.to_dict()`
> FMS 측 대응 문서: `wcs-nodejs/docs/reference/equipment/amr-error-taxonomy.md`

## 한 줄 요약

어댑터는 계속 벤더 고유 `ErrorType`(`JIBOT_*`)을 raise 하고, **wire 로 나갈 때 한 번만** 벤더 중립 공통 코드로 번역한다. 원문은 `errorReferences.vendorErrorType` 에 보존한다.

## 왜

VDA5050 은 `error.errorType` 을 자유 문자열로 둔다. 어댑터가 JIBOT 이름을 그대로 실으면 FMS 가 그 문자열로 분기하게 되고, 교통 제어가 한 기종에 묶인다.

**실패가 조용한 게 문제다.** SEER/Hexplorer 가 같은 물리 상황을 다른 이름으로 보고하면, JIBOT 철자를 학습한 FMS 게이트는 예외 없이 그냥 매칭에 실패한다. "오류"가 아니라 "아무 일도 안 일어남"으로 나타난다.

## 동작

```
JIBOT 신호
  └─ adapter_jibot.py 가 ErrorType.JIBOT_MOTOR_FAULT 로 raise   ← 진단 정밀도 유지
       └─ Error.to_dict()                                       ← 번역은 여기 한 곳
            errorType            = "DRIVE_MOTOR_FAULT"
            vendorErrorType ref  = "JIBOT_MOTOR_FAULT"
            vendorName ref       = "JIBOT"
```

`Error.to_dict()` 는 v2·v3 발행 경로가 공유하는 유일한 직렬화 지점이다(`adapter_jibot.py:3120` 이 v3 조립 시 호출). 여기서 번역하면 20개가 넘는 raise 지점을 건드리지 않고 전부 정규화된다.

## 새 벤더/새 오류를 추가할 때

1. 벤더 어댑터가 자기 `ErrorType` 멤버를 선언한다.
2. `VENDOR_ERROR_ALIASES` 에 한 줄 추가한다.
3. 끝.

기존 공통 코드로 표현 안 되는 **새로운 물리 상황**일 때만 `CommonErrorType` 에 추가하고, 그때 **FMS 쪽 `AmrErrorType` 에도 같은 값을 추가한다**(두 목록은 1:1).

이름은 상황을 기술하고 기종을 언급하지 않는다. `JIBOT_MOTOR_FAULT` → `DRIVE_MOTOR_FAULT`.

## 매핑을 빠뜨리면

원문이 그대로 발행된다. 조용히 버리지 않는다 — 낯선 코드가 로그에 보여야 누락을 알 수 있다. 다만 FMS 의 order-blocking 게이트는 매핑되지 않은 코드를 승격하지 않으므로, 그 오류는 order 를 종결시키지 못한다.

## errorLevel

`protocol/vda5050_common.py` 의 4단계를 그대로 쓴다. 2단계(`WARNING`/`FATAL`)로 되돌리면 `CRITICAL` 을 표현할 수 없다.

| 레벨 | 의미 |
|---|---|
| `WARNING` | 즉시 조치 불필요. order 계속, 신규 order 수령 가능 |
| `URGENT` | 즉시 확인 필요. 주행·신규 order 계속 가능 |
| `CRITICAL` | 주행 중단. 신규 order 는 수령 가능 |
| `FATAL` | 사람 개입 필요. 현 order·신규 order 모두 불가 |

FMS 의 배차 차단 게이트는 `FATAL`/`CRITICAL` 만 본다. **시간이 지나며 심각해지는 상황은 errorType 을 바꾸지 말고 errorLevel 을 승격시키는 쪽이 맞다** — FMS 코드 변경 없이 게이트가 발동한다.

## 자체 점검

```bash
cd adaptor && python3 protocol/amr_fault_taxonomy.py
```

매핑 테이블 정합성(모든 alias 가 실제 멤버를 가리키는지, 공통 코드와 이름이 겹치지 않는지)을 assert 로 확인한다.

## 변경 이력

| 날짜 | 변경 | 사유 |
|------|------|------|
| 2026-08-19 | 공통 taxonomy 도입, `Error.to_dict()` 에서 번역 | FMS 가 `JIBOT_*` 원문으로 분기해 교통 제어가 한 기종에 결합돼 있었음 |
