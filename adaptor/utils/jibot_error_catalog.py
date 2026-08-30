"""urobot system_error_code 디코드 카탈로그.

robot의 /usr/local/urobot/params/service/fault.json `error_code` 섹션 스냅샷.
런타임 파일 의존 없이(sim/dev/테스트 결정적) 코드→설명을 제공한다. 이 모듈은
의미만 디코드할 뿐 심각도/errorType은 부여하지 않는다(관측성 전용).
"""

# code(int) -> human-readable description. name은 f"ERROR{code:04d}"로 파생.
ERROR_CODE_CATALOG = {
    0: "",  # no error
    1: "tag mode robot no localization result when init",
    2: "tag mode robot error too large when init",
    3: "tag mode Lateral error too large after approving",
    4: "tag mode error too large after checkpose",
    5: "tag mode id error after checkpose",
    6: "tag mode do not get localization result when approving",
    7: "tag mode do not get localization result when checkpose, camera offline",
    8: "tag mode do not get localization result when checkpose, no code",
    9: "tag mode do not get localization result when checkpose, recognition program error",
    100: "ref mode no localization result when approving",
    101: "ref mode robot no localization result when init",
    102: "ref mode laser data error when approving",
    103: "ref mode robot error too large when init",
    104: "ref mode Longitudinal error too large when final check",
    105: "ref mode robot docking timeout",
    106: "ref mode Lateral error too large after approving",
    107: "ref mode robot dock failed after 3 times",
    200: "robot do not find goal name in map",
    201: "charge mode robot charge failed",
    400: "robot status error after back from machine",
    500: "robot odometry data outtime",
    501: "robot inner imu data outtime",
    502: "robot outer imu data outtime",
    503: "robot front laser data outtime",
    504: "robot back laser data outtime",
    505: "robot top laser data outtime",
    506: "robot left laser data outtime",
    507: "robot right laser data outtime",
    508: "robot cam1 data outtime",
    509: "robot cam2 data outtime",
    510: "robot deep cam data outtime",
    600: "lost by map file error",
    603: "lost by localization failed",
    700: "robot not receive message long time",
    701: "robot not send vel long time",
    702: "robot main loop stuck",
    1000: "received a disable signal of less than 2 seconds",
}
# 주의: code 0(=ERROR0000 "tag mode robot docking timeout")은 운영상 "에러 없음"으로
# 쓰이므로 의도적으로 빈 설명으로 둔다(decode_system_error_code가 0을 no-error 처리).


def decode_system_error_code(raw):
    """system_error_code → (name, description).

    0/빈값/None/비정수 → ("",""). 알려진 비0 코드 → ("ERROR%04d", description).
    미지 비0 코드 → ("ERROR%04d", "").
    """
    try:
        code = int(str(raw).strip())
    except (TypeError, ValueError):
        return "", ""
    if code == 0:
        return "", ""
    return "ERROR%04d" % code, ERROR_CODE_CATALOG.get(code, "")
