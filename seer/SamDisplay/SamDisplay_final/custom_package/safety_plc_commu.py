# ----------------------- 범용 라이브러리 import -----------------------
# 알람 리셋 펄스 출력을 위한 time 라이브러리 import
import time
# ----------------------- Modbus TCP 라이브러리 import -----------------------
# Pilz Safety PLC와 Modbus TCP 통신하기 위한 ModbusTcpClient import
from pymodbus.client import ModbusTcpClient


# =============================================================
# Pilz PNOZmulti2 Safety PLC Modbus TCP 통신 클래스 선언
# =============================================================
class Safety_PLC_Commu:
    # =============================================================
    # Safety PLC Virtual Input 번호 선언
    #
    # 통신방향 : AMR PC → Safety PLC
    # Modbus Coil 주소 0 ~ 127에 대응
    # =============================================================
    # Alarm Reset 요청(I5)
    INPUT_ALARM_RESET = 5
    # 장비 Running 상태(I10~13, 현재는 사용 X) 
    INPUT_AMR_RUNNING = 10
    INPUT_ROBOT_RUNNING = 11
    INPUT_ROLLER_RUNNING = 12
    INPUT_TRAY_RUNNING = 13
    # LIDAR Skip 요청(I20~23)
    INPUT_MOMA_LIDAR_01_SKIP = 20
    INPUT_MOMA_LIDAR_02_SKIP = 21
    INPUT_BASE_LIDAR_01_SKIP = 22
    INPUT_BASE_LIDAR_02_SKIP = 23
    # MOMA LIDAR 01 Warning Mode(AMR 라이다1 맵 바꾸는 용도, I30~33)
    INPUT_MOMA_LIDAR_01_MODE_0 = 30
    INPUT_MOMA_LIDAR_01_MODE_1 = 31
    INPUT_MOMA_LIDAR_01_MODE_2 = 32
    INPUT_MOMA_LIDAR_01_MODE_3 = 33
    # MOMA LIDAR 02 Warning Mode(AMR 라이다2 맵 바꾸는 용도, I35~38)
    INPUT_MOMA_LIDAR_02_MODE_0 = 35
    INPUT_MOMA_LIDAR_02_MODE_1 = 36
    INPUT_MOMA_LIDAR_02_MODE_2 = 37
    INPUT_MOMA_LIDAR_02_MODE_3 = 38
    # =============================================================
    # Safety PLC Virtual Output 번호 선언
    #
    # 통신 방향 : Safety PLC → AMR PC 방향
    # Modbus Discrete Input 주소 8192 ~ 8319에 대응
    # =============================================================
    # 운전 모드 상태(O0~O3)
    OUTPUT_AUTO_MODE = 0
    OUTPUT_MANUAL_MODE = 1
    OUTPUT_TECH_MODE = 2
    OUTPUT_KEY_SWITCH_LOCK = 3
    # Alarm Reset 관련 상태(O5, 리셋 스위치 눌렸는지 여부)
    OUTPUT_ALARM_RESET = 5
    # Emergency 정상 상태 (O6~08)
    OUTPUT_EMS_OK = 6
    OUTPUT_AMR_ROBOT_EMERGENCY_OK = 7
    OUTPUT_UPPER_UNIT_EMERGENCY_OK = 8
    # AMR 주행 허가 상태(O10)
    OUTPUT_RUNNING_ENABLE = 10
    # 테이블 정위치 센서 상태(O11)
    OUTPUT_POSITION_SENSOR_01 = 11
    # 트레이 정위치 센서 상태(O12)
    OUTPUT_POSITION_SENSOR_02 = 12
    # LIDAR 상태
    # 라이다 1 정상 상태(O14)
    OUTPUT_MOMA_LIDAR_01_OK = 14
    # 라이다 1 Warning 상태(O15)
    OUTPUT_MOMA_LIDAR_01_WARNING_OK = 15
    # 라이다 2 정상 상태(O16)
    OUTPUT_MOMA_LIDAR_02_OK = 16
    # 라이다 2 Warning 상태(O17)
    OUTPUT_MOMA_LIDAR_02_WARNING_OK = 17
    # AMR에 달린 라이다1 정상 상태(O18)
    OUTPUT_BASE_LIDAR_01 = 18
    #  AMR에 달린 라이다2 정상 상태(O19)
    OUTPUT_BASE_LIDAR_02 = 19
    # 연기 센서 상태(O20)
    OUTPUT_SMOKE_SENSOR = 20
    # 온도 센서 상태(O21)
    OUTPUT_TEMPERATURE_SENSOR = 21
    # MOMA LIDAR 01 Warning Mode 적용 상태
    OUTPUT_MOMA_LIDAR_01_MODE_0 = 30
    OUTPUT_MOMA_LIDAR_01_MODE_1 = 31
    OUTPUT_MOMA_LIDAR_01_MODE_2 = 32
    OUTPUT_MOMA_LIDAR_01_MODE_3 = 33
    # MOMA LIDAR 02 Warning Mode 적용 상태
    OUTPUT_MOMA_LIDAR_02_MODE_0 = 35
    OUTPUT_MOMA_LIDAR_02_MODE_1 = 36
    OUTPUT_MOMA_LIDAR_02_MODE_2 = 37
    OUTPUT_MOMA_LIDAR_02_MODE_3 = 38
    # Safety Alarm 시작 및 종료 번호
    OUTPUT_SAFETY_ALARM_START = 60
    OUTPUT_SAFETY_ALARM_END = 79
    # ----------------------- 클래스 초기화 함수 선언 -----------------------
    def __init__(self,ip="192.168.0.40",port=502,timeout=60.0,device_id=1):
        # Safety PLC IP 저장
        self.ip = ip
        # Modbus TCP Port 저장
        self.port = port
        # 통신 Timeout 저장
        self.timeout = timeout
        # Modbus Device ID 저장
        self.device_id = device_id
        # Modbus TCP Client 객체 초기화
        self.client = None
        # Virtual Input 시작 Coil 주소 저장
        # Safety PLC i0 → Modbus Coil 주소 0
        self.virtual_input_start_address = 0
        # Virtual Output 시작 Discrete Input 주소 저장
        # Safety PLC o0 → Modbus Discrete Input 주소 8192
        self.virtual_output_start_address = 8192
    # =============================================================
    # 기본 연결 함수
    # =============================================================
    # ----------------------- Safety PLC 연결 함수 선언 -----------------------
    def plc_connect(self):
        # Client 객체가 이미 존재하면
        if self.client is not None:
            # 현재 연결된 상태이면
            if self.client.connected:
                # 연결 성공 반환
                return True
        # Modbus TCP Client 객체 생성
        self.client = ModbusTcpClient(
            # Safety PLC IP 입력
            host=self.ip,
            # Modbus TCP Port 입력
            port=self.port,
            # 통신 Timeout 입력
            timeout=self.timeout)
        # Safety PLC 연결 시도
        connect_result = self.client.connect()
        # 연결 성공이면
        if connect_result:
            # 연결 성공 로그 출력
            print(
                f"[Safety PLC] Modbus TCP 연결 성공 : "
                f"{self.ip}:{self.port}"
            )
            # 연결 성공 반환
            return True
        # 연결 실패이면
        else:
            # 연결 실패 로그 출력
            print(
                f"[Safety PLC] Modbus TCP 연결 실패 : "
                f"{self.ip}:{self.port}")
            # Client 객체 초기화
            self.client = None
            # 연결 실패 반환
            return False

    # ----------------------- Safety PLC 연결 상태 확인 함수 선언 -----------------------
    def is_connected(self):
        # Client 객체가 없으면
        if self.client is None:
            # 연결되지 않은 상태 반환
            return False
        # Client 연결 상태 반환
        return self.client.connected

    # ----------------------- Safety PLC 연결 종료 함수 선언 -----------------------
    def plc_close(self):
        # Client 객체가 없으면
        if self.client is None:
            # 함수 종료
            return
        # 에러가 없으면
        try:
            # Modbus TCP 연결 종료
            self.client.close()
        # 연결 종료 중 에러가 발생하면
        except Exception as error:
            # 에러 로그 출력
            print(
                f"[Safety PLC] 연결 종료 오류 : "
                f"{error}")
        # 최종적으론
        finally:
            # Client 객체 초기화
            self.client = None
            # 연결 종료 로그 출력
            print("[Safety PLC] Modbus TCP 연결 종료")

    # =============================================================
    # 내부 Modbus 통신 함수
    # =============================================================
    # ----------------------- Coil 읽기 함수 선언 -----------------------
    def _read_coils(self, address, count):
        # Modbus Coil 읽기 요청
        return self.client.read_coils(
            # 시작 Coil 주소
            address=address,
            # 읽을 Coil 개수
            count=count,
            # Modbus Device ID
            device_id=self.device_id)

    # ----------------------- Discrete Input 읽기 함수 선언 -----------------------
    def _read_discrete_inputs(self, address, count):
        # Modbus Discrete Input 읽기 요청
        return self.client.read_discrete_inputs(
            # 시작 Discrete Input 주소
            address=address,
            # 읽을 Discrete Input 개수
            count=count,
            # Modbus Device ID
            device_id=self.device_id)

    # ----------------------- Coil 쓰기 함수 선언 -----------------------
    def _write_coil(self, address, value):
        # Modbus Coil 쓰기 요청
        return self.client.write_coil(
            # 제어할 Coil 주소
            address=address,
            # ON 또는 OFF 상태
            value=value,
            # Modbus Device ID
            device_id=self.device_id)

    # =============================================================
    # 공통 Virtual Input / Output 함수
    # =============================================================

    # ----------------------- Virtual Input 번호 검사 함수 선언 -----------------------
    def _check_virtual_input_no(self, input_no):
        # 입력 번호가 0~127 범위를 벗어나면
        if input_no < 0 or input_no > 127:
            # 에러 로그 출력
            print(
                f"[Safety PLC] 잘못된 Virtual Input 번호 : "
                f"i{input_no}")
            # 검사 실패 반환
            return False
        # 검사 성공 반환
        return True
    # ----------------------- Virtual Output 번호 검사 함수 선언 -----------------------
    def _check_virtual_output_no(self, output_no):
        # 출력 번호가 0~127 범위를 벗어나면
        if output_no < 0 or output_no > 127:
            # 에러 로그 출력
            print(
                f"[Safety PLC] 잘못된 Virtual Output 번호 : "
                f"o{output_no}")
            # 검사 실패 반환
            return False
        # 검사 성공 반환
        return True
    # ----------------------- Virtual Input 1개 제어 함수 선언 -----------------------
    def set_virtual_input(self, input_no, state):
        """
        AMR PC에서 Safety PLC로 보내는 Virtual Input을 제어합니다.
        input_no:
            0 ~ 127
        state:
            True  → ON
            False → OFF
        """
        # Virtual Input 번호 검사
        if not self._check_virtual_input_no(input_no):
            # 제어 실패 반환
            return False
        # state 값을 bool 형태로 변환
        state = bool(state)
        # Safety PLC와 연결되어 있지 않으면
        if not self.plc_connect():
            # 제어 실패 반환
            return False
        # 실제 Modbus Coil 주소 계산
        coil_address = (
            self.virtual_input_start_address
            + input_no)
        # 에러가 없으면
        try:
            # Coil 쓰기 요청
            response = self._write_coil(
                # 실제 Coil 주소
                address=coil_address,
                # ON 또는 OFF 상태
                value=state)
            # Modbus 에러 응답이면
            if response.isError():
                # 제어 실패 로그 출력
                print(
                    f"[Safety PLC] i{input_no} 제어 실패 : "
                    f"{response}")
                # 제어 실패 반환
                return False
            # 제어 성공 로그 출력
            print(
                f"[Safety PLC] i{input_no} "
                f"{'ON' if state else 'OFF'}")

            # 제어 성공 반환
            return True
        # 통신 에러가 발생하면
        except Exception as error:
            # 통신 에러 로그 출력
            print(
                f"[Safety PLC] i{input_no} 제어 오류 : "
                f"{error}")
            # 문제가 발생한 연결 종료
            self.plc_close()
            # 제어 실패 반환
            return False

    # ----------------------- Virtual Input 1개 읽기 함수 선언 -----------------------
    def get_virtual_input(self, input_no):
        """
        Safety PLC Virtual Input 상태를 읽습니다.

        반환값:
            True  → ON
            False → OFF
            None  → 통신 실패
        """
        # Virtual Input 번호 검사
        if not self._check_virtual_input_no(input_no):
            # 읽기 실패 반환
            return None
        # Safety PLC와 연결되어 있지 않으면
        if not self.plc_connect():
            # 읽기 실패 반환
            return None
        # 실제 Modbus Coil 주소 계산
        coil_address = (
            self.virtual_input_start_address
            + input_no)
        # 에러가 없으면
        try:
            # Coil 1개 읽기 요청
            response = self._read_coils(
                # 실제 Coil 주소
                address=coil_address,
                # Coil 1개 읽기
                count=1)
            # Modbus 에러 응답이면
            if response.isError():
                # 읽기 실패 로그 출력
                print(
                    f"[Safety PLC] i{input_no} 읽기 실패 : "
                    f"{response}")
                # 읽기 실패 반환
                return None
            # 읽은 상태 반환
            return response.bits[0]
        # 통신 에러가 발생하면
        except Exception as error:
            # 통신 에러 로그 출력
            print(
                f"[Safety PLC] i{input_no} 읽기 오류 : "
                f"{error}")
            # 문제가 발생한 연결 종료
            self.plc_close()
            # 읽기 실패 반환
            return None

    # ----------------------- Virtual Output 1개 읽기 함수 선언 -----------------------
    def get_virtual_output(self, output_no):
        """
        Safety PLC에서 AMR PC로 보내는 Virtual Output을 읽습니다.
        반환값:
            True  → ON
            False → OFF
            None  → 통신 실패
        """
        # Virtual Output 번호 검사
        if not self._check_virtual_output_no(output_no):
            # 읽기 실패 반환
            return None
        # Safety PLC와 연결되어 있지 않으면
        if not self.plc_connect():
            # 읽기 실패 반환
            return None
        # 실제 Modbus Discrete Input 주소 계산
        discrete_input_address = (
            self.virtual_output_start_address
            + output_no)
        # 에러가 없으면
        try:
            # Discrete Input 1개 읽기 요청
            response = self._read_discrete_inputs(
                # 실제 Discrete Input 주소
                address=discrete_input_address,
                # Discrete Input 1개 읽기
                count=1)
            # Modbus 에러 응답이면
            if response.isError():
                # 읽기 실패 로그 출력
                print(
                    f"[Safety PLC] o{output_no} 읽기 실패 : "
                    f"{response}")
                # 읽기 실패 반환
                return None
            # 읽은 상태 반환
            return response.bits[0]
        # 통신 에러가 발생하면
        except Exception as error:
            # 통신 에러 로그 출력
            print(
                f"[Safety PLC] o{output_no} 읽기 오류 : "
                f"{error}")
            # 문제가 발생한 연결 종료
            self.plc_close()
            # 읽기 실패 반환
            return None

    # ----------------------- Virtual Input 전체 읽기 함수 선언 -----------------------
    def read_all_virtual_inputs(self):
        """
        Safety PLC Virtual Input i0~i127을 한 번에 읽습니다.
        반환값:
            성공 → 128개의 bool List
            실패 → None
        """
        # Safety PLC와 연결되어 있지 않으면
        if not self.plc_connect():
            # 읽기 실패 반환
            return None
        # 에러가 없으면
        try:
            # Coil 주소 0부터 128개 읽기
            response = self._read_coils(
                # Virtual Input 시작 주소
                address=self.virtual_input_start_address,
                # i0~i127 총 128개 읽기
                count=128)
            # Modbus 에러 응답이면
            if response.isError():
                # 읽기 실패 로그 출력
                print(
                    "[Safety PLC] "
                    f"Virtual Input 전체 읽기 실패 : "
                    f"{response}")
                # 읽기 실패 반환
                return None
            # 필요한 128개 Bit만 List로 반환
            return list(response.bits[:128])
        # 통신 에러가 발생하면
        except Exception as error:
            # 통신 에러 로그 출력
            print(
                "[Safety PLC] "
                f"Virtual Input 전체 읽기 오류 : "
                f"{error}")
            # 문제가 발생한 연결 종료
            self.plc_close()
            # 읽기 실패 반환
            return None

    # ----------------------- Virtual Output 전체 읽기 함수 선언 -----------------------
    def read_all_virtual_outputs(self):
        """
        Safety PLC Virtual Output o0~o127을 한 번에 읽습니다.

        반환값:
            성공 → 128개의 bool List
            실패 → None
        """
        # Safety PLC와 연결되어 있지 않으면
        if not self.plc_connect():
            # 읽기 실패 반환
            return None
        # 에러가 없으면
        try:
            # Discrete Input 주소 8192부터 128개 읽기
            response = self._read_discrete_inputs(
                # Virtual Output 시작 주소
                address=self.virtual_output_start_address,
                # o0~o127 총 128개 읽기
                count=128)
            # Modbus 에러 응답이면
            if response.isError():
                # 읽기 실패 로그 출력
                print(
                    "[Safety PLC] "
                    f"Virtual Output 전체 읽기 실패 : "
                    f"{response}")
                # 읽기 실패 반환
                return None
            # 필요한 128개 Bit만 List로 반환
            return list(response.bits[:128])
        # 통신 에러가 발생하면
        except Exception as error:
            # 통신 에러 로그 출력
            print(
                "[Safety PLC] "
                f"Virtual Output 전체 읽기 오류 : "
                f"{error}"
            )
            # 문제가 발생한 연결 종료
            self.plc_close()
            # 읽기 실패 반환
            return None

    # =============================================================
    # 장비 상태 전달 함수
    # =============================================================

    # ----------------------- Alarm Reset 함수 선언 -----------------------
    def alarm_reset(self, pulse_sec=0.5):
        """
        i5 ALARM RESET 신호를 일정 시간 ON 후 OFF합니다.
        """

        # i5 Alarm Reset ON
        if not self.set_virtual_input(
            input_no=self.INPUT_ALARM_RESET,
            state=True
        ):
            # ON 실패 시 실패 반환
            return False

        # 지정한 시간만큼 대기
        time.sleep(pulse_sec)

        # i5 Alarm Reset OFF
        if not self.set_virtual_input(
            input_no=self.INPUT_ALARM_RESET,
            state=False
        ):
            # OFF 실패 시 실패 반환
            return False

        # 성공 반환
        return True

    # ----------------------- AMR Running 상태 설정 함수 선언 -----------------------
    def set_amr_running(self, running):
        # i10 AMR RUNNING 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_AMR_RUNNING,
            state=running
        )

    # ----------------------- Robot Running 상태 설정 함수 선언 -----------------------
    def set_robot_running(self, running):
        # i11 ROBOT RUNNING 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_ROBOT_RUNNING,
            state=running
        )

    # ----------------------- Roller Running 상태 설정 함수 선언 -----------------------
    def set_roller_running(self, running):
        # i12 ROLLER RUNNING 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_ROLLER_RUNNING,
            state=running
        )

    # ----------------------- Tray Running 상태 설정 함수 선언 -----------------------
    def set_tray_running(self, running):
        # i13 TRAY RUNNING 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_TRAY_RUNNING,
            state=running
        )

    # =============================================================
    # LIDAR Skip 요청 함수
    # =============================================================

    # ----------------------- MOMA LIDAR 01 Skip 설정 함수 선언 -----------------------
    def set_moma_lidar_01_skip(self, skip):
        # i20 MOMA LIDAR 01 SKIP 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_MOMA_LIDAR_01_SKIP,
            state=skip
        )

    # ----------------------- MOMA LIDAR 02 Skip 설정 함수 선언 -----------------------
    def set_moma_lidar_02_skip(self, skip):
        # i21 MOMA LIDAR 02 SKIP 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_MOMA_LIDAR_02_SKIP,
            state=skip
        )

    # ----------------------- BASE LIDAR 01 Skip 설정 함수 선언 -----------------------
    def set_base_lidar_01_skip(self, skip):
        # i22 BASE LIDAR 01 SKIP 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_BASE_LIDAR_01_SKIP,
            state=skip
        )

    # ----------------------- BASE LIDAR 02 Skip 설정 함수 선언 -----------------------
    def set_base_lidar_02_skip(self, skip):
        # i23 BASE LIDAR 02 SKIP 상태 설정
        return self.set_virtual_input(
            input_no=self.INPUT_BASE_LIDAR_02_SKIP,
            state=skip
        )

    # =============================================================
    # LIDAR Warning Mode 요청 함수
    # =============================================================

    # ----------------------- MOMA LIDAR 01 Warning Mode 설정 함수 선언 -----------------------
    def set_moma_lidar_01_warning_mode(self, mode):
        """
        MOMA LIDAR 01 Warning Mode를 설정합니다.

        mode:
            0 ~ 4

        i30~i34 중 선택된 Mode 하나만 ON합니다.
        """

        # Mode 번호 범위 검사
        if mode < 0 or mode > 4:
            # 잘못된 Mode 로그 출력
            print(
                "[Safety PLC] "
                "MOMA LIDAR 01 Warning Mode는 "
                "0~4만 가능합니다."
            )

            # 설정 실패 반환
            return False

        # i30~i34 모든 Mode를 OFF
        for input_no in range(
            self.INPUT_MOMA_LIDAR_01_MODE_0,
            self.INPUT_MOMA_LIDAR_01_MODE_4 + 1
        ):
            # 해당 Mode OFF
            if not self.set_virtual_input(
                input_no=input_no,
                state=False
            ):
                # OFF 실패 시 실패 반환
                return False

        # 선택한 Mode의 Virtual Input 번호 계산
        selected_input_no = (
            self.INPUT_MOMA_LIDAR_01_MODE_0
            + mode
        )

        # 선택한 Mode만 ON
        return self.set_virtual_input(
            input_no=selected_input_no,
            state=True
        )

    # ----------------------- MOMA LIDAR 02 Warning Mode 설정 함수 선언 -----------------------
    def set_moma_lidar_02_warning_mode(self, mode):
        """
        MOMA LIDAR 02 Warning Mode를 설정합니다.

        mode:
            0 ~ 4

        i35~i39 중 선택된 Mode 하나만 ON합니다.
        """

        # Mode 번호 범위 검사
        if mode < 0 or mode > 4:
            # 잘못된 Mode 로그 출력
            print(
                "[Safety PLC] "
                "MOMA LIDAR 02 Warning Mode는 "
                "0~4만 가능합니다."
            )

            # 설정 실패 반환
            return False

        # i35~i39 모든 Mode를 OFF
        for input_no in range(
            self.INPUT_MOMA_LIDAR_02_MODE_0,
            self.INPUT_MOMA_LIDAR_02_MODE_4 + 1
        ):
            # 해당 Mode OFF
            if not self.set_virtual_input(
                input_no=input_no,
                state=False
            ):
                # OFF 실패 시 실패 반환
                return False

        # 선택한 Mode의 Virtual Input 번호 계산
        selected_input_no = (
            self.INPUT_MOMA_LIDAR_02_MODE_0
            + mode
        )

        # 선택한 Mode만 ON
        return self.set_virtual_input(
            input_no=selected_input_no,
            state=True
        )

    # =============================================================
    # Safety 상태 확인 함수
    # =============================================================

    # ----------------------- 주요 Safety 상태 읽기 함수 선언 -----------------------
    def get_safety_status(self):
        """
        Device Map 기준 주요 Safety 상태를 Dictionary로 반환합니다.

        통신 실패 시 None을 반환합니다.
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 실패 반환
            return None

        # 주요 Safety 상태 Dictionary 생성
        safety_status = {
            # 운전 모드
            "auto_mode": output_bits[self.OUTPUT_AUTO_MODE],
            "manual_mode": output_bits[self.OUTPUT_MANUAL_MODE],
            "tech_mode": output_bits[self.OUTPUT_TECH_MODE],
            "key_switch_lock": output_bits[self.OUTPUT_KEY_SWITCH_LOCK],

            # Alarm Reset 관련 상태
            "alarm_reset": output_bits[self.OUTPUT_ALARM_RESET],

            # Emergency 상태
            "ems_ok": output_bits[self.OUTPUT_EMS_OK],
            "amr_robot_emergency_ok": output_bits[
                self.OUTPUT_AMR_ROBOT_EMERGENCY_OK
            ],
            "upper_unit_emergency_ok": output_bits[
                self.OUTPUT_UPPER_UNIT_EMERGENCY_OK
            ],

            # AMR 주행 허가
            "running_enable": output_bits[
                self.OUTPUT_RUNNING_ENABLE
            ],

            # 정위치 센서
            "position_sensor_01": output_bits[
                self.OUTPUT_POSITION_SENSOR_01
            ],
            "position_sensor_02": output_bits[
                self.OUTPUT_POSITION_SENSOR_02
            ],

            # MOMA LIDAR 상태
            "moma_lidar_01_ok": output_bits[
                self.OUTPUT_MOMA_LIDAR_01_OK
            ],
            "moma_lidar_01_warning_ok": output_bits[
                self.OUTPUT_MOMA_LIDAR_01_WARNING_OK
            ],
            "moma_lidar_02_ok": output_bits[
                self.OUTPUT_MOMA_LIDAR_02_OK
            ],
            "moma_lidar_02_warning_ok": output_bits[
                self.OUTPUT_MOMA_LIDAR_02_WARNING_OK
            ],

            # BASE LIDAR 원본 상태
            "base_lidar_01_raw": output_bits[
                self.OUTPUT_BASE_LIDAR_01
            ],
            "base_lidar_02_raw": output_bits[
                self.OUTPUT_BASE_LIDAR_02
            ],

            # 연기 및 온도 센서 원본 상태
            "smoke_sensor_raw": output_bits[
                self.OUTPUT_SMOKE_SENSOR
            ],
            "temperature_sensor_raw": output_bits[
                self.OUTPUT_TEMPERATURE_SENSOR
            ]
        }

        # Safety 상태 반환
        return safety_status

    # ----------------------- 활성 Safety Alarm 확인 함수 선언 -----------------------
    def get_active_safety_alarms(self):
        """
        o60~o79 중 ON 상태인 Safety Alarm 번호를 반환합니다.

        반환 예:
            []        → 활성 알람 없음
            [1, 3, 5] → Safety Alarm 1, 3, 5 활성
            None      → 통신 실패
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 실패 반환
            return None

        # 활성 Safety Alarm 목록 선언
        active_alarms = []

        # o60부터 o79까지 반복
        for output_no in range(
            self.OUTPUT_SAFETY_ALARM_START,
            self.OUTPUT_SAFETY_ALARM_END + 1
        ):
            # 해당 Safety Alarm이 ON이면
            if output_bits[output_no]:
                # 알람 번호를 1~20으로 변환하여 추가
                active_alarms.append(
                    output_no
                    - self.OUTPUT_SAFETY_ALARM_START
                    + 1
                )

        # 활성 알람 목록 반환
        return active_alarms

    # ----------------------- AMR 주행 허가 확인 함수 선언 -----------------------
    def is_amr_running_allowed(self):
        """
        현재 Device Map 기준으로 다음 신호가 모두 ON이면
        AMR 주행 허가 상태로 판단합니다.

        o6  : EMS 정상
        o7  : AMR/ROBOT Emergency 정상
        o8  : Upper Unit Emergency 정상
        o10 : Running Enable

        통신 실패 시 False를 반환합니다.
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 통신 실패는 주행 불가로 판단
            return False

        # 모든 주행 허가 조건 확인
        return (
            output_bits[self.OUTPUT_EMS_OK]
            and output_bits[self.OUTPUT_AMR_ROBOT_EMERGENCY_OK]
            and output_bits[self.OUTPUT_UPPER_UNIT_EMERGENCY_OK]
            and output_bits[self.OUTPUT_RUNNING_ENABLE]
        )

    # ----------------------- 정위치 상태 확인 함수 선언 -----------------------
    def is_correct_position(self):
        """
        o11과 o12가 모두 ON이면 정위치로 판단합니다.

        통신 실패 시 False를 반환합니다.
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 정위치 아님으로 판단
            return False

        # 정위치 센서 2개 모두 확인
        return (
            output_bits[self.OUTPUT_POSITION_SENSOR_01]
            and output_bits[self.OUTPUT_POSITION_SENSOR_02]
        )

    # ----------------------- MOMA LIDAR 01 Warning Mode 상태 확인 함수 선언 -----------------------
    def get_moma_lidar_01_warning_mode(self):
        """
        o30~o34를 확인하여 현재 적용된 Warning Mode를 반환합니다.

        반환값:
            0~4  → 해당 Mode ON
            None → 적용된 Mode가 없거나 통신 실패
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 실패 반환
            return None

        # o30~o34 반복
        for mode in range(5):
            # 해당 Mode 출력 번호 계산
            output_no = (
                self.OUTPUT_MOMA_LIDAR_01_MODE_0
                + mode
            )

            # 해당 Mode가 ON이면
            if output_bits[output_no]:
                # 현재 Mode 반환
                return mode

        # 활성화된 Mode가 없으면 None 반환
        return None

    # ----------------------- MOMA LIDAR 02 Warning Mode 상태 확인 함수 선언 -----------------------
    def get_moma_lidar_02_warning_mode(self):
        """
        o35~o39를 확인하여 현재 적용된 Warning Mode를 반환합니다.

        반환값:
            0~4  → 해당 Mode ON
            None → 적용된 Mode가 없거나 통신 실패
        """

        # Virtual Output 전체 읽기
        output_bits = self.read_all_virtual_outputs()

        # 읽기 실패이면
        if output_bits is None:
            # 실패 반환
            return None

        # o35~o39 반복
        for mode in range(5):
            # 해당 Mode 출력 번호 계산
            output_no = (
                self.OUTPUT_MOMA_LIDAR_02_MODE_0
                + mode
            )

            # 해당 Mode가 ON이면
            if output_bits[output_no]:
                # 현재 Mode 반환
                return mode

        # 활성화된 Mode가 없으면 None 반환
        return None