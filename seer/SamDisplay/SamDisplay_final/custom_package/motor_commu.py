# ----------------------- 범용 라이브러리 import -----------------------
# UDP 통신을 위한 socket 라이브러리 import
import socket
# 시간 처리를 위한 time 라이브러리 import
import time
# -------------------- Ezi-SERVOⅡ Plus-E ALL 모터 제어 클래스 선언 --------------------
class Ezi_Motor_Commu:
    # ----------------------- 클래스 초기화 함수 선언 -----------------------
    def __init__(self, ip="192.168.0.14", port=3002, timeout=2.0):
        # 모터 드라이버 IP 주소 저장
        self.ip = ip
        # 모터 드라이버 UDP 포트 저장
        self.port = port
        # UDP 통신 timeout 저장
        self.timeout = timeout
        # UDP socket 객체 저장 변수 선언
        self.socket = None
        # Sync No. 저장 변수 선언
        self.sync_no = 0
        # Ezi-SERVO 통신 Header 값 저장
        self.header = 0xAA
        # Ezi-SERVO 통신 Reserved 값 저장
        self.reserved = 0x00
        # 정상 통신 상태값 저장
        self.result_ok = 0x00
        # ----------------------- Frame Type 값 선언 -----------------------
        # Board 정보 요청 명령 Frame Type
        self.get_board_info_cmd = 0x01
        # Motor 정보 요청 명령 Frame Type
        self.get_motor_info_cmd = 0x05
        # 모든 Parameter ROM 저장 명령 Frame Type
        self.save_all_parameters_cmd = 0x10
        # ROM Parameter 읽기 명령 Frame Type
        self.get_rom_parameter_cmd = 0x11
        # RAM Parameter 쓰기 명령 Frame Type
        self.set_parameter_cmd = 0x12
        # RAM Parameter 읽기 명령 Frame Type
        self.get_parameter_cmd = 0x13
        # IO Output 설정 명령 Frame Type
        self.set_io_output_cmd = 0x20
        # IO Input 상태 읽기 명령 Frame Type
        self.get_io_input_cmd = 0x22
        # IO Output 상태 읽기 명령 Frame Type
        self.get_io_output_cmd = 0x23
        # Servo ON/OFF 명령 Frame Type
        self.servo_enable_cmd = 0x2A
        # Servo Alarm Reset 명령 Frame Type
        self.alarm_reset_cmd = 0x2B
        # Alarm Type 읽기 명령 Frame Type
        self.get_alarm_type_cmd = 0x2E
        # 일반 정지 명령 Frame Type
        self.move_stop_cmd = 0x31
        # 비상 정지 명령 Frame Type
        self.emergency_stop_cmd = 0x32
        # 원점 복귀 명령 Frame Type
        self.move_origin_cmd = 0x33
        # 절대 위치 이동 명령 Frame Type
        self.move_abs_position_cmd = 0x34
        # 상대 위치 이동 명령 Frame Type
        self.move_inc_position_cmd = 0x35
        # Limit 이동 명령 Frame Type
        self.move_to_limit_cmd = 0x36
        # Jog 속도 이동 명령 Frame Type
        self.move_velocity_cmd = 0x37
        # Axis Status 읽기 명령 Frame Type
        self.get_axis_status_cmd = 0x40
        # Command Position 읽기 명령 Frame Type
        self.get_command_position_cmd = 0x51
        # Actual Position 설정 명령 Frame Type
        self.set_actual_position_cmd = 0x52
        # Actual Position 읽기 명령 Frame Type
        self.get_actual_position_cmd = 0x53
        # Position Error 읽기 명령 Frame Type
        self.get_position_error_cmd = 0x54
        # Actual Velocity 읽기 명령 Frame Type
        self.get_actual_velocity_cmd = 0x55
        # Position Clear
        self.clear_position_cmd = 0x56
        # ----------------------- Axis Status Flag bit 선언 -----------------------
        self.axis_flags = {
            # 에러가 1개라도 있을 떄 Status Flag bit
            "FFLAG_ERRORALL":         0x00000001,
            # 하드웨어 Limit + 센서 감지되었을 때 Status Flag bit
            "FFLAG_HWPOSILMT":       0x00000002,
            # 하드웨어 Limit - 센서 감지되었을 때 Status Flag bit
            "FFLAG_HWNEGALMT":       0x00000004,
            # 소프트웨어 Limit +에 도달하였을 때 Status Flag bit
            "FFLAG_SWPOGILMT":       0x00000008,
            # 소프트웨어 Limit -에 도달하였을 때 Status Flag bit
            "FFLAG_SWNEGALMT":       0x00000010,
            # 위치값이 허용 범위를 초과한 경우 Status Flag bit
            "FFLAG_ERRPOSOVERFLOW":  0x00000080,
            # 과전류 에러 발생 시 Status Flag bit
            "FFLAG_ERROVERCURRENT":  0x00000100,
            # 과속도 에러 발생 시 Status Flag bit
            "FFLAG_ERROVERSPEED":    0x00000200,
            # 명령 위치와 실제 위치 차이가 허용 범위 초과 시 Status Flag bit
            "FFLAG_ERRPOSTRACKING":  0x00000400,
            # 과부하 에러 발생 시 Status Flag bit
            "FFLAG_ERROVERLOAD":     0x00000800,
            # 과열 에러 발생 시 Status Flag bit
            "FFLAG_ERROVERHEAT":     0x00001000,
            # 역기전력 발생 시 Status Flag bit
            "FFLAG_ERRBACKEMF":      0x00002000,
            # 모터 전원 관련 에러 발생 시 Status Flag bit
            "FFLAG_ERRMOTORPOWER":   0x00004000,
            # 목표 위치에 정상적으로 도달하지 못했을 경우 Status Flag bit
            "FFLAG_ERRINPOSITION":   0x00008000,
            # 비상 정지 상태 시 Status Flag bit
            "FFLAG_EMGSTOP":         0x00010000,
            # 정지 상태 시 Status Flag bit
            "FFLAG_SLOWSTOP":        0x00020000,
            # 원점 복귀 운전 상태 시 Status Flag bit
            "FFLAG_ORIGINRETURNING": 0x00040000,
            # 목표 위치에 도달 후 Satatus Flag bit
            "FFLAG_INPOSITION":      0x00080000,
            # Servo On 상태일 때 Status Flag bit
            "FFLAG_SERVOON":         0x00100000,
            # Alarm Reset 상태 시 Status Flag bit
            "FFLAG_ALARMRESET":      0x00200000,
            # Position Table 운전 정지 시 Status Flag bit
            "FFLAG_PTSTOPED":        0x00400000,
            # Origin Sensor 감지 시 Sataus Flag bit
            "FFLAG_ORIGINSENSOR":    0x00800000,
            # Z Pluse 감지 시 Status Flag bit
            "FFLAG_ZPULSE":          0x01000000,
            # 원점 복귀 완료 후 Status Flag bit
            "FFLAG_ORIGINRETOK":     0x02000000,
            # 현재 이동 모터 이동 방향 상태일 때 Status Flag bit
            "FFLAG_MOTIONDIR":       0x04000000,
            # 현재 모터 이동 중인 상태일 때 Status Flag bit
            "FFLAG_MOTIONING":       0x08000000,
            # 현재 모터 이동이 일시정지 상태일 때 Status Flag bit
            "FFLAG_MOTIONPAUSE":     0x10000000,
            # 모터가 가속중인 상태일 때 Status Flag bit
            "FFLAG_MOTIONACCEL":     0x20000000,
            # 모터가 감속중인 상태일 때 Status Flag bit
            "FFLAG_MOTIONDECEL":     0x40000000,
            # 모터가 등속 운전 상태일 때 Status Flag bit
            "FFLAG_MOTIONCONST":     0x80000000}
        # ----------------------- 통신 상태 설명 Dictionary 선언 -----------------------
        self.comm_status_text = {
            0x00: "통신 정상",
            0x80: "Frame type 에러",
            0x81: "Data 에러 / ROM Data 읽기 쓰기 에러",
            0x82: "수신 Frame 에러",
            0x85: "운전 명령 실패",
            0x86: "Reset 실패",
            0x87: "Servo ON 실패 1 : 알람 발생 중",
            0x88: "Servo ON 실패 2 : 비상 정지 중",
            0x89: "Servo ON 실패 3 : 외부 입력 Servo ON 할당 상태"}
        # --------------------- 삼성디스플레이 프로젝트용 변수 선언 ----------------------------
        # 테이블 닫기 거리 변수 선언
        self.table_close_distance = 260000
        # 테이블 닫기 제어 속도
        self.table_close_speed = 7000 * 4
        # 테이블 닫기 최대 시간
        self.table_close_timeout = 300.0
        # 테이블 오픈 제어 속도
        self.table_open_speed = 7000 * 4
        # 테이블 오픈 최대 시간
        self.table_open_timeout = 300.0
        # 테이블 오픈 상태 확인 주기
        self.table_open_check_time = 0.05
    # --------------------------------- 통신 관련 함수 선언 ----------------------------------
    # 모터 드라이버 연결 함수 선언
    def motor_connect(self):
        # 이미 socket 객체가 있으면 함수 종료
        if self.socket is not None:
            return
        # UDP socket 객체 생성
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # UDP socket timeout 설정
        self.socket.settimeout(self.timeout)
        # 연결 준비 완료 문구 출력
        print(f"[EZI-MOTOR] 모터드라이버 연결 완료 : {self.ip}:{self.port}")

    # 모터 드라이버 연결 종료 함수 선언
    def motor_close(self):
        # socket 객체가 존재하면
        if self.socket is not None:
            # socket 종료
            self.socket.close()
            # socket 객체 초기화
            self.socket = None
            # 연결 종료 문구 출력
            print("[EZI-MOTOR] 모터드라이버 연결 종료")
    # --------------------------------- 프로토콜 관련 범용 함수 선언 ----------------------------------
    # Sync No. 순차 생성 함수 선언
    def create_sync_no(self):
        # 현재 sync no 번호 저장
        current_sync_no = self.sync_no
        # 다음 sync no로 증가
        self.sync_no = (self.sync_no + 1) & 0xFF
        # 현재 sync no return
        return current_sync_no

    # uint8 값을 1byte로 변환하는 함수 선언
    def uint8_to_bytes(self, value):
        # 1byte little-endian 변환
        return int(value).to_bytes(1, byteorder="little", signed=False)

    # int32 값을 4byte로 변환하는 함수 선언
    def int32_to_bytes(self, value):
        # 4byte little-endian signed 변환
        return int(value).to_bytes(4, byteorder="little", signed=True)

    # uint32 값을 4byte로 변환하는 함수 선언
    def uint32_to_bytes(self, value):
        # 4byte little-endian unsigned 변환
        return int(value).to_bytes(4, byteorder="little", signed=False)

    # 4byte를 int32 값으로 변환하는 함수 선언
    def bytes_to_int32(self, value):
        # 4byte little-endian signed 변환
        return int.from_bytes(value, byteorder="little", signed=True)

    # 4byte를 uint32 값으로 변환하는 함수 선언
    def bytes_to_uint32(self, value):
        # 4byte little-endian unsigned 변환
        return int.from_bytes(value, byteorder="little", signed=False)

    # 통신 상태 설명 반환 함수 선언
    def get_comm_status_text(self, result):
        # 통신 상태 설명 반환
        return self.comm_status_text.get(result, f"알 수 없는 통신 상태 : 0x{result:02X}")

    # 모터 통신 Frame 생성 함수 선언
    def build_motor_frame(self, frame_type, data=b""):
        # Sync No. 생성
        sync_no = self.create_sync_no()
        # Data 길이 계산
        data_length = len(data)
        # Length 계산
        # Length = Sync No + Reserved + Frame type + Data
        length = 3 + data_length
        # 빈 Frame 생성
        frame = bytearray()
        # Header 추가
        frame.append(self.header)
        # Length 추가
        frame.append(length)
        # Sync No. 추가
        frame.append(sync_no)
        # Reserved 추가
        frame.append(self.reserved)
        # Frame type 추가
        frame.append(frame_type)
        # Data 추가
        frame.extend(data)
        # 생성된 Frame과 Sync No. return
        return bytes(frame), sync_no

    # 명령 송신 후 응답 수신 및 파싱 함수 선언
    def send_motor_frame(self, frame_type, data=b""):
        # socket 연결이 안되어 있으면
        if self.socket is None:
            # 에러 발생
            raise ConnectionError("[EZI-MOTOR] 모터 드라이버와 먼저 연결 필요")
        # 송신 Frame 생성
        send_frame, sync_no = self.build_motor_frame(frame_type, data)
        # UDP Frame 송신
        self.socket.sendto(send_frame, (self.ip, self.port))
        # UDP 응답 수신
        recv_frame, _ = self.socket.recvfrom(1024)
        # 응답 Frame 길이 확인
        if len(recv_frame) < 6:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] 응답 길이 부족 : {recv_frame.hex(' ')}")
        # Header 확인
        if recv_frame[0] != self.header:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Header 오류 : {recv_frame.hex(' ')}")
        # Length 추출
        length = recv_frame[1]
        # 실제 수신 길이와 Length 비교
        if len(recv_frame) != length + 2:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Length 오류 : length={length}, raw={recv_frame.hex(' ')}")
        # Sync No. 확인
        if recv_frame[2] != sync_no:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Sync No 오류 : recv={recv_frame[2]}, expected={sync_no}, raw={recv_frame.hex(' ')}")
        # Reserved 확인
        if recv_frame[3] != self.reserved:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Reserved 오류 : {recv_frame.hex(' ')}")
        # Frame type 확인
        if recv_frame[4] != frame_type:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Frame type 오류 : recv=0x{recv_frame[4]:02X}, expected=0x{frame_type:02X}")
        # 통신 상태 추출
        result = recv_frame[5]
        # 통신 상태가 정상이 아니면
        if result != self.result_ok:
            # 에러 발생
            raise RuntimeError(f"[EZI-MOTOR] 통신 에러 : result=0x{result:02X}({self.get_comm_status_text(result)}), raw={recv_frame.hex(' ')}")
        # 응답 Data 길이 계산
        # 응답 Length = Sync No + Reserved + Frame type + 통신상태 + 응답 Data
        response_data_length = length - 4
        # 응답 Data 추출
        response_data = recv_frame[6:6 + response_data_length]
        # 응답 Data return
        return response_data

    # 단순 실행 명령 함수 선언
    def execute_simple_command(self, frame_type, data=b""):
        # 명령 송신
        self.send_motor_frame(frame_type, data)
        # 정상 완료 return
        return True
    # --------------------------------- 모터 드라이버 정보 관련 함수 선언 ----------------------------------
    # Board 정보 읽기 함수 선언
    def get_board_info(self):
        # Board 정보 요청 후 응답 수신
        response_data = self.send_motor_frame(self.get_board_info_cmd)
        # 응답 길이 확인
        if len(response_data) < 1:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Board Info 응답 길이 오류 : {response_data.hex(' ')}")
        # Board 종류 추출
        board_type = response_data[0]
        # Board 설명 추출
        board_description = response_data[1:].split(b"\x00")[0].decode(errors="ignore")
        # Board 정보 return
        return board_type, board_description

    # Motor 정보 읽기 함수 선언
    def get_motor_info(self):
        # Motor 정보 요청 후 응답 수신
        response_data = self.send_motor_frame(self.get_motor_info_cmd)
        # 응답 길이 확인
        if len(response_data) < 1:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Motor Info 응답 길이 오류 : {response_data.hex(' ')}")
        # Motor 번호 추출
        motor_no = response_data[0]
        # Motor 설명 추출
        motor_description = response_data[1:].split(b"\x00")[0].decode(errors="ignore")
        # Motor 정보 return
        return motor_no, motor_description

    # --------------------------------- Parameter 관련 함수 선언 ----------------------------------
    # 모든 Parameter ROM 저장 함수 선언
    def save_all_parameters(self):
        # Save All Parameters 명령 실행
        return self.execute_simple_command(self.save_all_parameters_cmd)

    # ROM Parameter 읽기 함수 선언
    def get_rom_parameter(self, parameter_no):
        # Parameter 번호 Data 생성
        data = self.uint8_to_bytes(parameter_no)
        # ROM Parameter 읽기 요청
        response_data = self.send_motor_frame(self.get_rom_parameter_cmd, data)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] ROM Parameter 응답 길이 오류 : {response_data.hex(' ')}")
        # Parameter 값 추출
        parameter_value = self.bytes_to_int32(response_data[0:4])
        # Parameter 값 return
        return parameter_value

    # RAM Parameter 읽기 함수 선언
    def get_parameter(self, parameter_no):
        # Parameter 번호 Data 생성
        data = self.uint8_to_bytes(parameter_no)
        # Parameter 읽기 요청
        response_data = self.send_motor_frame(self.get_parameter_cmd, data)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Parameter 응답 길이 오류 : {response_data.hex(' ')}")
        # Parameter 값 추출
        parameter_value = self.bytes_to_int32(response_data[0:4])
        # Parameter 값 return
        return parameter_value

    # RAM Parameter 설정 함수 선언
    def set_parameter(self, parameter_no, parameter_value):
        # Data 생성
        # Data = Parameter No. 1byte + Parameter Value 4byte
        data = self.uint8_to_bytes(parameter_no) + self.int32_to_bytes(parameter_value)
        # Parameter 설정 명령 실행
        return self.execute_simple_command(self.set_parameter_cmd, data)

    # --------------------------------- IO 관련 함수 선언 ----------------------------------
    # IO Input 상태 읽기 함수 선언
    def get_io_input(self):
        # IO Input 상태 요청
        response_data = self.send_motor_frame(self.get_io_input_cmd)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] IO Input 응답 길이 오류 : {response_data.hex(' ')}")
        # 입력 상태 값 추출
        input_bits = self.bytes_to_uint32(response_data[0:4])
        # 입력 상태 dictionary 생성
        input_status = {}
        # 0~31 bit 반복
        for input_no in range(32):
            # 각 bit 상태 저장
            input_status[input_no] = bool(input_bits & (1 << input_no))
        # 입력 상태 return
        return input_status, input_bits

    # IO Output 상태 읽기 함수 선언
    def get_io_output(self):
        # IO Output 상태 요청
        response_data = self.send_motor_frame(self.get_io_output_cmd)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] IO Output 응답 길이 오류 : {response_data.hex(' ')}")
        # 출력 상태 값 추출
        output_bits = self.bytes_to_uint32(response_data[0:4])
        # 출력 상태 dictionary 생성
        output_status = {}
        # 0~31 bit 반복
        for output_no in range(32):
            # 각 bit 상태 저장
            output_status[output_no] = bool(output_bits & (1 << output_no))
        # 출력 상태 return
        return output_status, output_bits

    # IO Output 설정 함수 선언
    def set_io_output(self, set_mask, clear_mask):
        # Data 생성
        # Data = Set Mask 4byte + Clear Mask 4byte
        data = self.uint32_to_bytes(set_mask) + self.uint32_to_bytes(clear_mask)
        # IO Output 설정 명령 실행
        return self.execute_simple_command(self.set_io_output_cmd, data)
    # --------------------------------- Servo / Alarm 관련 함수 선언 ----------------------------------
    # Servo ON/OFF 함수 선언
    def servo_enable(self, flag):
        # flag가 True이면
        if flag == True:
            # Servo ON 값
            servo_value = 1
        # flag가 False이면
        else:
            # Servo OFF 값
            servo_value = 0
        # Data 생성
        data = self.uint8_to_bytes(servo_value)
        # Servo Enable 명령 실행
        return self.execute_simple_command(self.servo_enable_cmd, data)

    # Servo ON 함수 선언
    def servo_on(self):
        # Servo ON 명령 실행
        return self.servo_enable(True)

    # Servo OFF 함수 선언
    def servo_off(self):
        # Servo OFF 명령 실행
        return self.servo_enable(False)

    # Alarm Reset 함수 선언
    def alarm_reset(self):
        # Alarm Reset 명령 실행
        return self.execute_simple_command(self.alarm_reset_cmd)

    # Alarm Type 읽기 함수 선언
    def get_alarm_type(self):
        # Alarm Type 요청
        response_data = self.send_motor_frame(self.get_alarm_type_cmd)
        # 응답 길이 확인
        if len(response_data) < 1:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Alarm Type 응답 길이 오류 : {response_data.hex(' ')}")
        # Alarm Type 추출
        alarm_type = response_data[0]
        # Alarm Type return
        return alarm_type, self.get_alarm_text(alarm_type)

    # Alarm Type 설명 반환 함수 선언
    def get_alarm_text(self, alarm_type):
        # Alarm Type 설명 Dictionary 선언
        alarm_text = {
            0: "No alarm",
            1: "OverCurrent",
            2: "OverSpeed",
            3: "Position Tracking",
            4: "OverLoad",
            5: "OverTemperature",
            6: "Back EMF",
            7: "Motor Connect",
            8: "Encoder Connect",
            10: "Inposition",
            12: "ROM Device",
            15: "Position Overflow"}
        # Alarm 설명 return
        return alarm_text.get(alarm_type, f"Unknown Alarm Type : {alarm_type}")
    # --------------------------------- 운전 제어 관련 함수 선언 ----------------------------------
    # 일반 정지 함수 선언
    def move_stop(self):
        # 일반 정지 명령 실행
        return self.execute_simple_command(self.move_stop_cmd)

    # 비상 정지 함수 선언
    def emergency_stop(self):
        # 비상 정지 명령 실행
        return self.execute_simple_command(self.emergency_stop_cmd)

    # 원점 복귀 함수 선언
    def goto_origin(self):
        # 원점 복귀 명령 실행
        return self.execute_simple_command(self.move_origin_cmd)

    # 절대 위치 이동 함수 선언
    def move_abs_position(self, position, speed):
        # Data 생성
        # Data = Position 4byte + Speed 4byte
        data = self.int32_to_bytes(position) + self.int32_to_bytes(speed)
        # 절대 위치 이동 명령 실행
        return self.execute_simple_command(self.move_abs_position_cmd, data)

    # 상대 위치 이동 함수 선언
    def move_inc_position(self, pulse, speed):
        # Data 생성
        # Data = Distance 4byte + Speed 4byte
        data = self.int32_to_bytes(pulse) + self.int32_to_bytes(speed)
        # 상대 위치 이동 명령 실행
        return self.execute_simple_command(self.move_inc_position_cmd, data)

    # Limit 이동 함수 선언
    def move_to_limit(self, speed, direction):
        # 방향 값이 프로토콜 범위와 다르면
        if direction not in (0, 1):
            # 에러 발생
            raise ValueError("direction은 0(-Limit) 또는 1(+Limit)만 가능")
        # Data 생성
        # Data = Speed 4byte unsigned + Direction 1byte
        data = self.uint32_to_bytes(speed) + self.uint8_to_bytes(direction)
        # Limit 이동 명령 실행
        return self.execute_simple_command(self.move_to_limit_cmd, data)

    # +Limit 이동 함수 선언
    def goto_limit_plus(self, speed):
        # +Limit 방향 이동
        return self.move_to_limit(speed, 1)

    # -Limit 이동 함수 선언
    def goto_limit_minus(self, speed):
        # -Limit 방향 이동
        return self.move_to_limit(speed, 0)

    # Jog 속도 이동 함수 선언
    # Jog 이동 : 특정 방향으로 계속 모터를 회전시키는 이동 방식
    def move_velocity(self, speed, direction):
        # 방향 값이 프로토콜 범위와 다르면
        if direction not in (0, 1):
            # 에러 발생
            raise ValueError("direction은 0(-Jog) 또는 1(+Jog)만 가능합니다.")
        # Data 생성
        # Data = Speed 4byte unsigned + Direction 1byte
        data = self.uint32_to_bytes(speed) + self.uint8_to_bytes(direction)
        # Jog 이동 명령 실행
        return self.execute_simple_command(self.move_velocity_cmd, data)
    # --------------------------------- 상태 읽기 관련 함수 선언 ----------------------------------
    # Axis Status bit 읽기 함수 선언
    def get_axis_status(self):
        # Axis Status 요청
        response_data = self.send_motor_frame(self.get_axis_status_cmd)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] Axis Status 응답 길이 오류 : {response_data.hex(' ')}")
        # Axis Status bit 값 추출
        axis_status_bits = self.bytes_to_uint32(response_data[0:4])
        # Axis Status dictionary 생성
        axis_status = {}
        # Flag 반복
        for flag_name, flag_bit in self.axis_flags.items():
            # 각 Flag 상태 저장
            axis_status[flag_name] = bool(axis_status_bits & flag_bit)
        # Axis Status return
        return axis_status, axis_status_bits

    # Command Position 읽기 함수 선언
    def get_command_position(self):
        # Command Position return
        return self.read_int32_value(self.get_command_position_cmd)

    # Actual Position 읽기 함수 선언
    def get_actual_position(self):
        # Actual Position return
        return self.read_int32_value(self.get_actual_position_cmd)

    # Position Error 읽기 함수 선언
    def get_position_error(self):
        # Position Error return
        return self.read_int32_value(self.get_position_error_cmd)

    # Actual Velocity 읽기 함수 선언
    def get_actual_velocity(self):
        # Actual Velocity return
        return self.read_int32_value(self.get_actual_velocity_cmd)

    # int32 값 읽기 공통 함수 선언
    def read_int32_value(self, frame_type):
        # int32 값 요청
        response_data = self.send_motor_frame(frame_type)
        # 응답 길이 확인
        if len(response_data) < 4:
            # 에러 발생
            raise ValueError(f"[EZI-MOTOR] int32 응답 길이 오류 : {response_data.hex(' ')}")
        # int32 값 추출
        value = self.bytes_to_int32(response_data[0:4])
        # 값 return
        return value

    # Actual Position 설정 함수 선언
    def set_actual_position(self, position):
        # Data 생성
        data = self.int32_to_bytes(position)
        # Actual Position 설정 명령 실행
        return self.execute_simple_command(self.set_actual_position_cmd, data)

    # 현재 위치 0점 초기화 함수 선언
    def clear_position(self):
        # Position Clear 명령 실행
        return self.execute_simple_command(self.clear_position_cmd)
    # --------------------------------- 상태 확인 Helper 함수 선언 ----------------------------------
    # Servo ON 상태 확인 함수 선언
    def is_servo_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # Servo ON 상태 return
        return axis_status["FFLAG_SERVOON"]

    # Error 상태 확인 함수 선언
    def is_error_all(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # Error 상태 return
        return axis_status["FFLAG_ERRORALL"]

    # 모터 이동 중 상태 확인 함수 선언
    def is_motioning(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # Motioning 상태 return
        return axis_status["FFLAG_MOTIONING"]

    # In Position 상태 확인 함수 선언
    def is_in_position(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # In Position 상태 return
        return axis_status["FFLAG_INPOSITION"]

    # +Limit 감지 상태 확인 함수 선언
    def is_limit_plus_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # +Limit 상태 return
        return axis_status["FFLAG_HWPOSILMT"]

    # -Limit 감지 상태 확인 함수 선언
    def is_limit_minus_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # -Limit 상태 return
        return axis_status["FFLAG_HWNEGALMT"]

    # Origin Sensor 감지 상태 확인 함수 선언
    def is_origin_sensor_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # Origin Sensor 상태 return
        return axis_status["FFLAG_ORIGINSENSOR"]

    # Origin 완료 상태 확인 함수 선언
    def is_origin_done(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # Origin 완료 상태 return
        return axis_status["FFLAG_ORIGINRETOK"]
    # --------------------------------- 대기 함수 선언 ----------------------------------
    # Servo ON이 될 때까지 대기하는 함수 선언
    def wait_servo_on(self, timeout_sec=3.0, check_interval=0.1):
        # 시작 시간 저장
        start_time = time.time()
        # timeout까지 반복
        while time.time() - start_time < timeout_sec:
            # Servo ON 상태이면
            if self.is_servo_on():
                # True return
                return True
            # 지정 시간 대기
            time.sleep(check_interval)
        # timeout 시 False return
        return False

    # 이동 시작까지 대기하는 함수 선언
    def wait_motion_start(self, timeout_sec=2.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()
        # timeout까지 반복
        while time.time() - start_time < timeout_sec:
            # 이동 중이면
            if self.is_motioning():
                # True return
                return True
            # 지정 시간 대기
            time.sleep(check_interval)
        # timeout 시 False return
        return False

    # 이동 완료까지 대기하는 함수 선언
    def wait_motion_done(self, timeout_sec=10.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()
        # timeout까지 반복
        while time.time() - start_time < timeout_sec:
            # 이동 중 여부 확인
            motioning = self.is_motioning()
            # In Position 여부 확인
            in_position = self.is_in_position()
            # 이동 중이 아니고 In Position이면
            if motioning == False and in_position == True:
                # True return
                return True
            # 지정 시간 대기
            time.sleep(check_interval)
        # timeout 시 False return
        return False

    # --------------------------------- 출력용 Helper 함수 선언 ----------------------------------
    # Axis Status 출력 함수 선언
    def print_axis_status(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # 상태 출력
        print(f"Axis Status Hex : 0x{axis_status_bits:08X}")
        # True인 Flag만 출력
        for flag_name, flag_value in axis_status.items():
            # Flag가 True이면
            if flag_value:
                # Flag 출력
                print(f"{flag_name} : {flag_value}")

    # 구현 안 된 기능 안내 함수 선언
    def print_not_implemented_functions(self):
        # 안내 출력
        print("[EZI-MOTOR] 현재 클래스에 아직 구현하지 않은 주요 기능")
        print("0x24 : FAS_SetIOAssignMap")
        print("0x25 : FAS_GetIOAssignMap")
        print("0x26 : FAS_IOAssignMapReadROM")
        print("0x27 : FAS_TriggerOutput_RunA")
        print("0x28 : FAS_TriggerOutput_Status")
        print("0x38 : FAS_PositionAbsOverride")
        print("0x39 : FAS_PositionIncOverride")
        print("0x3A : FAS_VelocityOverride")
        print("0x3B : FAS_MovePause")
        print("0x3C : FAS_MovePauseStatus")
        print("0x7E : FAS_SetTriggerOutputEx")
        print("0x7F : FAS_GetTriggerOutputEx")

    # --------------------------------- 테스트 / 진단 Helper 함수 선언 ----------------------------------
    # Axis Status 요약 출력 함수 선언
    def print_axis_summary(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()

        # 주요 상태 출력
        print("Status Hex :", f"0x{axis_status_bits:08X}")
        print("ERRORALL   :", axis_status["FFLAG_ERRORALL"])
        print("SERVOON    :", axis_status["FFLAG_SERVOON"])
        print("INPOSITION :", axis_status["FFLAG_INPOSITION"])
        print("MOTIONING  :", axis_status["FFLAG_MOTIONING"])
        print("HW +LIMIT  :", axis_status["FFLAG_HWPOSILMT"])
        print("HW -LIMIT  :", axis_status["FFLAG_HWNEGALMT"])

    # True인 Axis Flag만 출력하는 함수 선언
    def print_true_axis_flags(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # 상태 Hex 출력
        print("Status Hex :", f"0x{axis_status_bits:08X}")
        # True인 Flag만 출력
        print("----- True인 Axis Flag -----")
        # True Flag 존재 여부 변수 선언
        has_true_flag = False
        # Axis Flag 반복
        for flag_name, flag_value in axis_status.items():
            # Flag가 True이면
            if flag_value:
                # True Flag 존재 표시
                has_true_flag = True
                # Flag 출력
                print(flag_name, ":", flag_value)
        # True인 Flag가 없으면
        if not has_true_flag:
            # 없음 출력
            print("True인 Axis Flag 없음")

    # 위치 상태 전체 출력 함수 선언
    def print_position_all(self, title="현재 위치 상태"):
        # 제목 출력
        print(f"\n========== {title} ==========")
        # Actual Position 읽기
        actual_position = self.get_actual_position()
        # Command Position 읽기
        command_position = self.get_command_position()
        # Position Error 읽기
        position_error = self.get_position_error()
        # Actual Velocity 읽기
        actual_velocity = self.get_actual_velocity()
        # 위치 정보 출력
        print("Actual Position :", actual_position)
        print("Command Position:", command_position)
        print("Position Error  :", position_error)
        print("Actual Velocity :", actual_velocity)
        # 위치 정보 return
        return actual_position, command_position, position_error, actual_velocity

    # Servo ON 대기 상태를 출력하면서 확인하는 함수 선언
    def wait_servo_on_with_log(self, timeout_sec=3.0, check_interval=0.2):
        # 시작 시간 저장
        start_time = time.time()
        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()
            # 현재 상태 출력
            print(
                "Servo ON 대기:",
                f"0x{axis_status_bits:08X}",
                "SERVOON:",
                axis_status["FFLAG_SERVOON"])
            # Servo ON 상태이면
            if axis_status["FFLAG_SERVOON"]:
                # True return
                return True
            # Timeout 시간이 지났으면
            if time.time() - start_time >= timeout_sec:
                # False return
                return False
            # 지정 시간 대기
            time.sleep(check_interval)

    # 이동 시작 상태를 출력하면서 확인하는 함수 선언
    def wait_motion_start_with_log(self, timeout_sec=2.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()
        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()
            # 이동 중 여부 확인
            motioning = axis_status["FFLAG_MOTIONING"]
            # 현재 상태 출력
            print(
                "이동 시작 대기:",
                f"0x{axis_status_bits:08X}",
                "MOTIONING:",
                motioning)
            # 이동 중이면
            if motioning:
                # True return
                return True
            # Timeout 시간이 지났으면
            if time.time() - start_time >= timeout_sec:
                # 안내 출력
                print("[EZI-MOTOR] MOTIONING이 True로 바뀌지 않았습니다.")
                # False return
                return False
            # 지정 시간 대기
            time.sleep(check_interval)

    # 이동 완료 상태를 출력하면서 확인하는 함수 선언
    def wait_motion_done_with_log(self, timeout_sec=20.0, check_interval=0.1):
        # 시작 시간 저장
        start_time = time.time()
        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()
            # 이동 중 여부 확인
            motioning = axis_status["FFLAG_MOTIONING"]
            # In Position 여부 확인
            inposition = axis_status["FFLAG_INPOSITION"]
            # 현재 상태 출력
            print(
                "이동 완료 대기:",
                f"0x{axis_status_bits:08X}",
                "MOTIONING:",
                motioning,
                "INPOSITION:",
                inposition)
            # 이동 중이 아니고 In Position이면 이동 완료로 판단
            if motioning == False and inposition == True:
                # True return
                return True
            # Timeout 시간이 지났으면
            if time.time() - start_time >= timeout_sec:
                # 안내 출력
                print("[EZI-MOTOR] 이동 완료 대기 Timeout")
                # False return
                return False
            # 지정 시간 대기
            time.sleep(check_interval)

    # Servo ON 준비 함수 선언
    def ready_servo_on(self, timeout_sec=3.0):
        # Alarm Type 읽기
        alarm_type, alarm_text = self.get_alarm_type()
        # Alarm 상태 출력
        print("Alarm Type :", alarm_type)
        print("Alarm Text :", alarm_text)
        # 알람이 있으면
        if alarm_type != 0:
            # 중단 출력
            print("[EZI-MOTOR] 알람 상태라서 Servo ON을 중단합니다.")
            # False return
            return False
        # Servo ON 명령 실행
        print("Servo ON :", self.servo_on())
        # Servo ON 대기
        servo_on_ok = self.wait_servo_on_with_log(timeout_sec=timeout_sec)
        # Servo ON 실패 시
        if not servo_on_ok:
            # 실패 출력
            print("[EZI-MOTOR] Servo ON 실패")
            # False return
            return False
        # Servo ON 성공 출력
        print("[EZI-MOTOR] Servo ON 완료")
        # True return
        return True

    # 상대 이동 1회 실행 함수 선언
    def move_relative_once_with_check(self, pulse, speed, motion_timeout_sec=20.0):
        # 이동 명령 정보 출력
        print(f"\n========== 상대 이동 명령: pulse={pulse}, speed={speed} ==========")
        # 이동 명령 전 위치 출력
        before_actual, before_command, _, _ = self.print_position_all("이동 명령 전 위치")
        # 상대 이동 명령 실행
        move_result = self.move_inc_position(
            pulse=pulse,
            speed=speed)
        # 이동 명령 결과 출력
        print("\n이동 명령 결과:")
        print(move_result)
        # 이동 명령 실패 시
        if move_result != True:
            # 실패 출력
            print("[EZI-MOTOR] 이동 명령 실패")
            # False return
            return False
        # 명령 반영 대기
        time.sleep(0.1)
        # 이동 명령 직후 위치 출력
        after_actual, after_command, _, _ = self.print_position_all("이동 명령 직후 위치")
        # Command Position 변화 출력
        print("\n----- Command Position 변화 확인 -----")
        print("이동 전 Command Position :", before_command)
        print("이동 후 Command Position :", after_command)
        print("Command Position 변화량  :", after_command - before_command)
        # 이동 시작 대기
        motion_started = self.wait_motion_start_with_log(
            timeout_sec=2.0,
            check_interval=0.05)
        # 이동 시작이 확인되지 않으면
        if not motion_started:
            # 안내 출력
            print("[EZI-MOTOR] 모터가 실제 이동 상태로 진입 실패")
            # 위치 재확인
            final_actual, final_command, _, _ = self.print_position_all("MOTIONING 미감지 후 위치 재확인")
            # 위치 변화가 있으면
            if final_actual != before_actual or final_command != before_command:
                # True return
                return True
            # 위치 변화도 없으면 False return
            return False
        # 이동 완료 대기
        motion_done = self.wait_motion_done_with_log(
            timeout_sec=motion_timeout_sec,
            check_interval=0.1)
        # 이동 완료 여부 출력
        print("이동 완료 여부:", motion_done)
        # 이동 완료 후 위치 출력
        self.print_position_all("이동 완료 후 위치")
        # 이동 완료 여부 return
        return motion_done

    # 상대 왕복 이동 테스트 함수 선언
    def move_relative_round_trip_test(self, pulse, speed, motion_timeout_sec=20.0, delay_sec=0.5):
        # + 방향 이동 테스트 출력
        print("\n========== + 방향 이동 테스트 ==========")
        # + 방향 상대 이동 실행
        plus_ok = self.move_relative_once_with_check(
            pulse=pulse,
            speed=speed,
            motion_timeout_sec=motion_timeout_sec)
        # + 방향 이동 실패 시
        if not plus_ok:
            # 실패 출력
            print("[EZI-MOTOR] + 방향 이동 실패 또는 이동 없음")
            # False return
            return False
        # 잠시 대기
        time.sleep(delay_sec)
        # - 방향 복귀 테스트 출력
        print("\n========== - 방향 복귀 테스트 ==========")
        # - 방향 상대 이동 실행
        minus_ok = self.move_relative_once_with_check(
            pulse=-pulse,
            speed=speed,
            motion_timeout_sec=motion_timeout_sec)
        # - 방향 이동 실패 시
        if not minus_ok:
            # 실패 출력
            print("[EZI-MOTOR] - 방향 이동 실패 또는 이동 없음")
            # False return
            return False
        # True return
        return True

    # --------------------------------- S/W Limit 이동 관련 함수 선언 ----------------------------------
    # +S/W Limit 감지 상태 확인 함수 선언
    def is_sw_limit_plus_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()

        # +S/W Limit 상태 return
        return axis_status["FFLAG_SWPOGILMT"]

    # -S/W Limit 감지 상태 확인 함수 선언
    def is_sw_limit_minus_on(self):
        # Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()

        # -S/W Limit 상태 return
        return axis_status["FFLAG_SWNEGALMT"]

    # +S/W Limit 감지까지 대기하는 함수 선언
    def wait_sw_limit_plus_on(self, timeout_sec=60.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()

        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()

            # +S/W Limit 감지 여부 확인
            sw_plus_limit_on = axis_status["FFLAG_SWPOGILMT"]

            # -S/W Limit 감지 여부 확인
            sw_minus_limit_on = axis_status["FFLAG_SWNEGALMT"]

            # 이동 중 여부 확인
            motioning = axis_status["FFLAG_MOTIONING"]

            # 현재 상태 출력
            print(
                "+S/W Limit 대기:",
                f"0x{axis_status_bits:08X}",
                "SW +LIMIT:", sw_plus_limit_on,
                "SW -LIMIT:", sw_minus_limit_on,
                "MOTIONING:", motioning
            )

            # +S/W Limit이 감지되면
            if sw_plus_limit_on:
                # True return
                return True

            # Timeout 시간이 지나면
            if time.time() - start_time >= timeout_sec:
                # Timeout 출력
                print("[EZI-MOTOR] +S/W Limit 감지 Timeout")

                # False return
                return False

            # 지정 시간 대기
            time.sleep(check_interval)

    # -S/W Limit 감지까지 대기하는 함수 선언
    def wait_sw_limit_minus_on(self, timeout_sec=60.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()

        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()

            # +S/W Limit 감지 여부 확인
            sw_plus_limit_on = axis_status["FFLAG_SWPOGILMT"]

            # -S/W Limit 감지 여부 확인
            sw_minus_limit_on = axis_status["FFLAG_SWNEGALMT"]

            # 이동 중 여부 확인
            motioning = axis_status["FFLAG_MOTIONING"]

            # 현재 상태 출력
            print(
                "-S/W Limit 대기:",
                f"0x{axis_status_bits:08X}",
                "SW +LIMIT:", sw_plus_limit_on,
                "SW -LIMIT:", sw_minus_limit_on,
                "MOTIONING:", motioning
            )

            # -S/W Limit이 감지되면
            if sw_minus_limit_on:
                # True return
                return True

            # Timeout 시간이 지나면
            if time.time() - start_time >= timeout_sec:
                # Timeout 출력
                print("[EZI-MOTOR] -S/W Limit 감지 Timeout")

                # False return
                return False

            # 지정 시간 대기
            time.sleep(check_interval)

    # +S/W Limit까지 이동하는 함수 선언
    def goto_sw_limit_plus(self, speed, timeout_sec=60.0, check_interval=0.05):
        # 시작 문구 출력
        print(f"[EZI-MOTOR] +S/W Limit 이동 시작 : speed={speed}")
        # 이미 +S/W Limit 상태이면
        if self.is_sw_limit_plus_on():
            # 이미 감지 상태 출력
            print("[EZI-MOTOR] 이미 +S/W Limit 상태입니다.")
            # True return
            return True
        # + 방향 Jog 이동 시작
        self.move_velocity(
            speed=speed,
            direction=1
        )
        # 예외 발생 여부와 상관없이 정지하기 위해 try/finally 사용
        try:
            # +S/W Limit 감지까지 대기
            sw_limit_ok = self.wait_sw_limit_plus_on(
                timeout_sec=timeout_sec,
                check_interval=check_interval
            )
            # 결과 return
            return sw_limit_ok
        # 마지막에 반드시 실행
        finally:
            # S/W Limit 감지 또는 Timeout 후 정지
            print("[EZI-MOTOR] +S/W Limit 이동 정지")
            self.move_stop()

    # -S/W Limit까지 이동하는 함수 선언
    def goto_sw_limit_minus(self, speed, timeout_sec=60.0, check_interval=0.05):
        # 시작 문구 출력
        print(f"[EZI-MOTOR] -S/W Limit 이동 시작 : speed={speed}")
        # 이미 -S/W Limit 상태이면
        if self.is_sw_limit_minus_on():
            # 이미 감지 상태 출력
            print("[EZI-MOTOR] 이미 -S/W Limit 상태입니다.")

            # True return
            return True
        # - 방향 Jog 이동 시작
        self.move_velocity(
            speed=speed,
            direction=0
        )
        # 예외 발생 여부와 상관없이 정지하기 위해 try/finally 사용
        try:
            # -S/W Limit 감지까지 대기
            sw_limit_ok = self.wait_sw_limit_minus_on(
                timeout_sec=timeout_sec,
                check_interval=check_interval
            )
            # 결과 return
            return sw_limit_ok
        # 마지막에 반드시 실행
        finally:
            # S/W Limit 감지 또는 Timeout 후 정지
            print("[EZI-MOTOR] -S/W Limit 이동 정지")
            self.move_stop()

    # ---------------------------------------------- 삼성디스플레이용 사용 함수 선언 --------------------------------------------------
    # Servo ON 전 알람 상태를 확인하고 필요하면 Reset하는 함수 선언
    def reset_alarm_if_error(self, reset_wait_sec=0.5):
        # Alarm Type 읽기
        alarm_type, alarm_text = self.get_alarm_type()
        # 현재 Alarm 상태 출력
        print("Alarm Type :", alarm_type)
        print("Alarm Text :", alarm_text)
        # 알람이 없으면
        if alarm_type == 0:
            # 정상 출력
            print("[Servo] 알람 없음")
            # True return
            return True
        # 알람이 있으면 Reset 시도
        print("[Servo] 알람 감지됨. Alarm Reset 실행")
        # Alarm Reset 실행
        reset_result = self.alarm_reset()
        # Reset 결과 출력
        print("Alarm Reset :", reset_result)
        # Reset 후 잠깐 대기
        time.sleep(reset_wait_sec)
        # Reset 후 Alarm Type 다시 읽기
        alarm_type_after, alarm_text_after = self.get_alarm_type()
        # Reset 후 Alarm 상태 출력
        print("Reset 후 Alarm Type :", alarm_type_after)
        print("Reset 후 Alarm Text :", alarm_text_after)
        # Reset 후 알람이 없어졌으면
        if alarm_type_after == 0:
            # 성공 출력
            print("[Servo] Alarm Reset 완료")
            # True return
            return True
        # Reset 후에도 알람이 남아있으면
        print("[Servo] Alarm Reset 후에도 알람이 남아있습니다.")
        # False return
        return False


    # -Limit 감지까지 대기하는 함수 선언
    def wait_minus_limit_on(self, timeout_sec=60.0, check_interval=0.05):
        # 시작 시간 저장
        start_time = time.time()
        # Timeout까지 반복
        while True:
            # Axis Status 읽기
            axis_status, axis_status_bits = self.get_axis_status()
            # +Limit 감지 여부 확인
            plus_limit_on = axis_status["FFLAG_HWPOSILMT"]
            # -Limit 감지 여부 확인
            minus_limit_on = axis_status["FFLAG_HWNEGALMT"]
            # 이동 중 여부 확인
            motioning = axis_status["FFLAG_MOTIONING"]
            # 현재 상태 출력
            print(
                "-Limit 대기:",
                f"0x{axis_status_bits:08X}",
                "PLUS_LIMIT:", plus_limit_on,
                "MINUS_LIMIT:", minus_limit_on,
                "MOTIONING:", motioning)
            # -Limit이 감지되면
            if minus_limit_on:
                # True return
                return True
            # Timeout 시간이 지나면
            if time.time() - start_time >= timeout_sec:
                # Timeout 출력
                print("[EZI-MOTOR] -Limit 감지 Timeout")
                # False return
                return False
            # 지정 시간 대기
            time.sleep(check_interval)

    # 테이블 닫기 함수 선언
    def table_close(self):
        # 예외 발생 여부와 관계없이 정리하기 위해 try/finally 사용
        try:
            # 모터 연결
            self.motor_connect()
            # 혹시 알람이 있으면 Reset 시도
            if not self.reset_alarm_if_error(reset_wait_sec=0.5):
                # 실패 출력
                print("[Servo] 알람 Reset 실패로 테이블 Close 중단")
                # False return
                return False
            # Servo On 실행 후 에러 발생 시
            if not self.ready_servo_on(timeout_sec=30.0):
                # 디버그 문구 출력
                print("[Servo] 서보모터 ON 중 이상 발생")
                # False return
                return False
            # 테이블 Close 시작 출력
            print("[Servo] 테이블 Close 실행")
            # + 방향 상대 이동 명령
            close_result = self.move_inc_position(
                self.table_close_distance,
                self.table_close_speed)
            # 이동 명령 결과 출력
            print("Table Close 이동 명령 결과 :", close_result)
            # 이동 명령 실패 시
            if close_result != True:
                # 실패 출력
                print("[Servo] 테이블 Close 이동 명령 실패")
                # False return
                return False
            # +방향 이동 완료 대기
            close_ok = self.wait_motion_done_with_log(
                timeout_sec=self.table_close_timeout,
                check_interval=0.1)
            # 이동 완료 실패 시
            if not close_ok:
                # 실패 출력
                print("[Servo] 테이블 Close 이동 완료 Timeout")
                # False return
                return False
            # 완료 출력
            print("[Servo] 테이블 Close 완료")
            # True return
            return True
        except Exception as error:
            # 예외 출력
            print(f"[Servo] 테이블 Close 중 예외 발생 : {error}")
            # False return
            return False
        finally:
            # 안전 정지
            try:
                print("Move Stop :", self.move_stop())
            except Exception as error:
                print("Move Stop 실패 :", error)
            # Servo OFF
            try:
                print("Servo OFF :", self.servo_off())
            except Exception as error:
                print("Servo OFF 실패 :", error)
            # 모터 드라이버 연결 종료
            self.motor_close()

# 테이블 Open 함수 선언
def table_open(self):
    # Table Open 시작 구분선 출력
    print("\n==================================================")
    print("[EZI-MOTOR] Table Open 시작")
    print("==================================================")
    # 모터 연결 성공 여부 저장 변수 선언
    motor_connected = False
    try:
        # 모터 드라이버와 통신 연결
        self.motor_connect()
        # 모터 연결 성공 상태 저장
        motor_connected = True
        # Table Open 시작 전 현재 위치 출력
        self.print_position_all("Table Open 시작 전 위치")
        # 현재 알람이 있다면 Alarm Reset 실행
        if not self.reset_alarm_if_error(reset_wait_sec=0.5):
            # 알람 Reset 실패 로그 출력
            print("[EZI-MOTOR] Alarm Reset 실패로 Table Open을 중단합니다.")
            # 실패 결과 반환
            return False
        # Servo ON 준비 실행
        if not self.ready_servo_on(timeout_sec=30.0):
            # Servo ON 실패 로그 출력
            print("[EZI-MOTOR] Servo ON 실패로 Table Open을 중단합니다.")
            # 실패 결과 반환
            return False
        # Servo ON 후 Axis Status 출력
        print("\n========== Servo ON 후 Axis Status ==========")
        # 주요 Axis Status 출력
        self.print_axis_summary()
        # True 상태인 Axis Flag 출력
        self.print_true_axis_flags()
        # 현재 Axis Status 읽기
        axis_status, axis_status_bits = self.get_axis_status()
        # 현재 -Limit 상태 저장
        minus_limit_on = axis_status["FFLAG_HWNEGALMT"]
        # 이미 -Limit이 감지된 상태라면
        if minus_limit_on:
            # 이미 열린 상태임을 출력
            print("[EZI-MOTOR] 이미 -Limit이 감지되어 있습니다.")
            # 현재 상태를 Table Open 완료로 처리
            print("[EZI-MOTOR] Table Open 완료로 처리합니다.")
            # 현재 위치 출력
            self.print_position_all("현재 Table Open 위치")
            # 성공 결과 반환
            return True
        # -Limit 방향 이동 시작 로그 출력
        print("\n========== -Limit 방향 Table Open 이동 시작 ==========")
        # -Limit 방향으로 모터 이동 명령 전송
        open_result = self.goto_limit_minus(
            self.table_open_speed)
        # 이동 명령 결과 출력
        print("[EZI-MOTOR] Goto -Limit 명령 결과 :", open_result)
        # 이동 명령이 실패한 경우
        if open_result is not True:
            # 이동 명령 실패 로그 출력
            print("[EZI-MOTOR] Table Open 이동 명령 실패")
            # 실패 결과 반환
            return False
        # -Limit이 감지될 때까지 대기
        open_ok = self.wait_minus_limit_on(
            timeout_sec=self.table_open_timeout,
            check_interval=self.table_open_check_time)
        # -Limit이 감지되지 않은 경우
        if not open_ok:
            # 감지 실패 로그 출력
            print("[EZI-MOTOR] Table Open 실패 : -Limit을 감지하지 못했습니다.")
            # 실패 결과 반환
            return False
        # -Limit 감지 직후 이동 정지
        stop_result = self.move_stop()
        # 정지 명령 결과 출력
        print("[EZI-MOTOR] Move Stop :", stop_result)
        # Table Open 완료 후 현재 위치 출력
        self.print_position_all("Table Open 완료 후 위치")
        # Table Open 완료 로그 출력
        print("\n==================================================")
        print("[EZI-MOTOR] Table Open 완료")
        print("==================================================")
        # 성공 결과 반환
        return True
    # Table Open 동작 중 예외가 발생한 경우
    except Exception as error:
        # 예외 내용 출력
        print(f"[EZI-MOTOR] Table Open 중 예외 발생 : {error}")
        # 실패 결과 반환
        return False
    # 성공 또는 실패와 관계없이 항상 실행
    finally:
        # 모터가 연결된 경우
        if motor_connected:
            # 안전 정지 시도
            try:
                # 모터 일반 정지 명령 실행
                stop_result = self.move_stop()
                # 정지 결과 출력
                print("[EZI-MOTOR] 최종 Move Stop :", stop_result)
            # 정지 명령 중 예외 발생 시
            except Exception as error:
                # 정지 실패 로그 출력
                print(f"[EZI-MOTOR] 최종 Move Stop 실패 : {error}")
            # Servo OFF 시도
            try:
                # Servo OFF 명령 실행
                servo_off_result = self.servo_off()
                # Servo OFF 결과 출력
                print("[EZI-MOTOR] Servo OFF :", servo_off_result)
            # Servo OFF 중 예외 발생 시
            except Exception as error:
                # Servo OFF 실패 로그 출력
                print(f"[EZI-MOTOR] Servo OFF 실패 : {error}")
            # 모터 드라이버 연결 종료 시도
            try:
                # 모터 통신 연결 종료
                self.motor_close()
            # 모터 연결 종료 중 예외 발생 시
            except Exception as error:
                # 연결 종료 실패 로그 출력
                print(f"[EZI-MOTOR] 모터 연결 종료 실패 : {error}")
        

    
        

    