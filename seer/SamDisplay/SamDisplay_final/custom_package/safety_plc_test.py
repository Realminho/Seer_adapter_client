# ----------------------- Modbus TCP 라이브러리 import -----------------------
# Modbus TCP 통신을 위한 ModbusTcpClient import
from pymodbus.client import ModbusTcpClient


# ----------------------- 메인 함수 선언 -----------------------
def main():
    # Safety PLC Modbus TCP Client 객체 생성
    client = ModbusTcpClient(
        # Safety PLC IP
        host="192.168.0.40",

        # Modbus TCP Port
        port=502,

        # 통신 Timeout
        timeout=2.0
    )

    try:
        # Safety PLC 연결
        if not client.connect():
            # 연결 실패 출력
            print("[Safety PLC] 연결 실패")

            # 함수 종료
            return

        # 연결 성공 출력
        print("[Safety PLC] 연결 성공")

        # -----------------------------------------------------
        # PNOZmulti2 Virtual Output 주소
        #
        # o0 시작 주소 = Discrete Input 8192
        #
        # o6 = 8192 + 6 = 8198
        # o7 = 8192 + 7 = 8199
        # o8 = 8192 + 8 = 8200
        # -----------------------------------------------------

        # o6, o7, o8을 한 번에 읽기
        response = client.read_discrete_inputs(
            # o6의 시작 주소
            address=8198,

            # o6, o7, o8 총 3개 읽기
            count=3,

            # 현재 PyModbus 버전의 장치 ID 인자
            device_id=1
        )

        # Modbus 에러 응답이면
        if response.isError():
            # 에러 출력
            print(
                f"[Safety PLC] Virtual Output 읽기 실패 : "
                f"{response}"
            )

            # 함수 종료
            return

        # 응답 Bit 목록에서 o6 상태 저장
        o6_state = response.bits[0]

        # 응답 Bit 목록에서 o7 상태 저장
        o7_state = response.bits[1]

        # 응답 Bit 목록에서 o8 상태 저장
        o8_state = response.bits[2]

        # 결과 출력
        print(
            f"[Safety PLC] o6 EMS OK                  : "
            f"{o6_state}"
        )

        print(
            f"[Safety PLC] o7 AMR/ROBOT EMERGENCY OK : "
            f"{o7_state}"
        )

        print(
            f"[Safety PLC] o8 UPPER EMERGENCY OK     : "
            f"{o8_state}"
        )

    # 실행 중 에러 발생 시
    except Exception as error:
        # 에러 출력
        print(
            f"[Safety PLC] 통신 오류 : "
            f"{error}"
        )

    # 성공 또는 실패와 관계없이 마지막에 실행
    finally:
        # Modbus TCP 연결 종료
        client.close()

        # 종료 출력
        print("[Safety PLC] 연결 종료")


# ----------------------- 이 파일을 직접 실행한 경우 -----------------------
if __name__ == "__main__":
    # 메인 함수 실행
    main()