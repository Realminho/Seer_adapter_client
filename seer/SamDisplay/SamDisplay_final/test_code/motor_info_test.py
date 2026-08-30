# ----------------------- 비동기 처리를 위한 asyncio import -----------------------
import asyncio

# ----------------------- Ezi Motor 제어 클래스 import -----------------------
# 본인 프로젝트 구조에 맞게 import 경로를 수정해야 합니다.
# 예: from utils.ezi_motor import EziMotorClient
from custom_package.motor_commu import EziMotorClient


# ----------------------- Ezi Motor 드라이버 접속 정보 설정 -----------------------
# Ezi Motor 드라이버 IP 주소를 입력합니다.
MOTOR_IP = "192.168.0.14"

# Ezi-SERVOⅡ Plus-E ALL 사용자 Library용 UDP 포트입니다.
MOTOR_PORT = 3002

# 응답 대기 시간입니다.
TIMEOUT_SEC = 2


# ----------------------- 메인 비동기 함수 선언 -----------------------
async def main():
    # Ezi Motor 클라이언트 객체를 생성합니다.
    client = EziMotorClient(
        ip=MOTOR_IP,
        port=MOTOR_PORT,
        timeout=TIMEOUT_SEC
    )

    # 예외 발생 여부와 상관없이 마지막에 socket을 닫기 위해 try/finally를 사용합니다.
    try:
        # ----------------------- 1. Board 정보 확인 -----------------------
        print("\n========== 1. Board Info ==========")

        # 드라이버 보드 정보를 요청합니다.
        board_info = await client.get_board_info()

        # 응답 결과를 출력합니다.
        print(board_info)

        # ----------------------- 2. Motor 정보 확인 -----------------------
        print("\n========== 2. Motor Info ==========")

        # 연결된 모터 정보를 요청합니다.
        motor_info = await client.get_motor_info()

        # 응답 결과를 출력합니다.
        print(motor_info)

        # ----------------------- 3. Axis 상태 확인 -----------------------
        print("\n========== 3. Axis Status ==========")

        # 모터 축 상태 플래그를 요청합니다.
        axis_status = await client.get_axis_status()

        # 응답 결과를 출력합니다.
        print(axis_status)

        # axis_status가 정상적으로 들어온 경우 보기 좋게 주요 상태만 따로 출력합니다.
        if axis_status is not None:
            active_flags = axis_status.get("active_flags", {})

            print("\n----- 주요 상태 -----")
            print("ERRORALL      :", active_flags.get("FFLAG_ERRORALL"))
            print("SERVOON       :", active_flags.get("FFLAG_SERVOON"))
            print("INPOSITION    :", active_flags.get("FFLAG_INPOSITION"))
            print("ORIGINSENSOR  :", active_flags.get("FFLAG_ORIGINSENSOR"))
            print("ORIGINRETOK   :", active_flags.get("FFLAG_ORIGINRETOK"))
            print("MOTIONING     :", active_flags.get("FFLAG_MOTIONING"))
            print("HW +LIMIT     :", active_flags.get("FFLAG_HWPOSILMT"))
            print("HW -LIMIT     :", active_flags.get("FFLAG_HWNEGALMT"))

        # ----------------------- 4. 현재 엔코더 위치 확인 -----------------------
        print("\n========== 4. Actual Position ==========")

        # 현재 엔코더 위치값을 요청합니다.
        actual_position = await client.get_actual_position()

        # 응답 결과를 출력합니다.
        print(actual_position)

    # 테스트가 끝나거나 에러가 발생해도 반드시 소켓을 닫습니다.
    finally:
        # UDP socket을 닫습니다.
        await client.close()

        # 종료 메시지를 출력합니다.
        print("\n========== Test Finished ==========")


# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    asyncio.run(main())