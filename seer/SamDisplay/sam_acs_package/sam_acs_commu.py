# ----------------------- 범용 라이브러리 import -----------------------
# 소켓 통신을 위한 socket 라이브러리 import
import socket
# 시간 핸들링을 위한 time 라이브러리 import
import time

# ----------------------------------------------------------------------------
# ACS C 명령 응답 클래스 선언
class ACS_C_Command_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # 수신한 프레임을 Body와 checksum으로 분리하는 함수 선언
    def split_frame(self,frame_data):
        # 인자로 받은 프레임 길이가 너무 짧으면
        if len(frame_data) < 4:
            # 디버그 문구 print
            print("[C Command] 프레임 길이가 너무 짧음")
        # 첫 바이트가 STX가 아니면
        if frame_data[:1] != self.stx:
            # 디버그 문구 print
            print("[C Command] STX 불일치")
        # 마지막 바이트가 ETX가 아니면
        if frame_data[-1:] != self.etx:
            # 디버그 문구 print
            print("[C Command] ETX 불일치")
        # STX와 ETX를 제외한 데이터 추출
        inner_data = frame_data[1:-1]
        # 추출한 데이터가 너무 짧으면
        if len(inner_data) < 2:
            # 디버그 문구 print
            print("[C Command] ETX 불일치")
        # 마지막 1byte를 checksum으로 분리
        recv_checksum = inner_data[-1:]
        # 그 앞부분을 Body로 분리
        recv_body = inner_data[:-1]
        # 분리한 body와 checksum return
        return recv_body, recv_checksum
    # checksum 검증 함수 선언
    def check_checksum(self,frame_data):
        # 인자로 받은 frame에서 Body와 Checksum으로 분리
        recv_body, recv_checksum = self.split_frame(frame_data)
        # 수신한 body 기준으로 checksum 계산
        cal_checksum = self.cal_checksum(recv_body)
        # 수신한 checksum과 계산한 checksum을 비교
        checksum_ok = (recv_checksum == cal_checksum)
        # 검증 결과 및 수신한 데이터, 계산한 checksum return
        return checksum_ok, recv_body, recv_checksum, cal_checksum
    # C명령 파싱 함수 선언
    def parse_c_command(self,frame_data):
        # 인자로 받은 frame checksum 검증 수행
        checksum_ok, recv_body, recv_checksum, cal_checksum = self.check_checksum(frame_data)
        # checksum 검증 실패 시 
        if not checksum_ok:
            # 디버그 문구 print
            print("[C Command] checksum 불일치")
            # 수신 checksum print
            print(f"[C Command] 수신한 checksum : {recv_checksum.decode('ascii', errors='ignore')}")
            # 계산한 checksum print
            print(f"[C Command] 계산한 checksum : {cal_checksum.decode('ascii', errors='ignore')}")
            # None return
            return None
        # 에러가 없으면
        try:
            # 수신한 body를 ascii 문자열로 변환
            body_frame = recv_body.decode("ascii")
        # 에러 발생 시 
        except Exception:
            # 디버그 문구 Print
            print("[C Command] Body frame ASCII 변환 중 에러 발생")
            # None return
            return None
        # body 프레임 4번째 글자가 A가 아니면
        if body_frame[3] != "A":
            # 디버그 문구 print
            print(f"[C Command] A 위치 오류 : {body_frame}")
            # None return
            return None
        # 5번째 문자가 C가 아니면
        if body_frame[4] != "C":
            # 디버그 문구 print
            print(f"[C Command] C 명령 위치 오류 : {body_frame}")
            # None return
            return None
        # AMR_ID 저장
        amr_id = body_frame[0:3]
        # 명령 코드 저장
        command_code = body_frame[4]
        # 목적지 번호 저장
        target_node = body_frame[5:9]
        # worktype 저장
        worktype = body_frame[9:11]
        # 파싱 결과 dict type으로 정리
        parse_data = {
            "amr_id": amr_id,
            "command_code": command_code,
            "target_node": target_node,
            "work_type":worktype}
        # 파싱 데이터 return
        return parse_data
    # C명령 응답 프레임 구성 함수 선언
    def build_c_command_response_frame(self,target_node,work_type):
        # 응답 Body frame 생성
        response_body_frame = f"A{self.amr_id}C{target_node}{work_type}"
        # 응답 Body frame ASCII로 변환
        response_body_frame = response_body_frame.encode("ascii")
        # 전체 응답 프레임 구성
        response_frame = self.make_frame(response_body_frame)
        # 전체 응답 프레임 return
        return response_frame
# ----------------------------------------------------------------------------
# AMS S 명령 구성 클래스 선언
class ACS_S_Command_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # S 코드 프레임 구성 함수 선언
    def build_s_command_frame(self,target_node,AMR_Status,carry_flag,move_flag):
        # S코드 Body frame 생성
        s_command_body_frame = f"A{self.amr_id}S{target_node}{AMR_Status}{carry_flag}{move_flag}"
        # S코드 Body frame ASCII로 변환
        s_command_body_frame = s_command_body_frame.encode("ascii")
        # 전체 S 명령 구성
        s_command_frame = self.make_frame(s_command_body_frame)
        # 전체 응답 프레임 return
        return s_command_frame
# ----------------------------------------------------------------------------
# AMS T 명령 구성 클래스 선언
class ACS_T_Command_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # T 코드 프레임 구성 함수 선언
    def build_t_command_frame(self,location_node):
        # T코드 Body frame 생성
        t_command_body_frame = f"A{self.amr_id}T00{location_node}"
        # T코드 Body frame ASCII로 변환
        t_command_body_frame = t_command_body_frame.encode("ascii")
        # 전체 T 명령 구성
        t_command_frame = self.make_frame(t_command_body_frame)
        # 전체 응답 프레임 return
        return t_command_frame
# ----------------------------------------------------------------------------
# AMS L 명령 구성 클래스 선언
class ACS_L_Command_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # L 코드 프레임 구성 함수 선언
    def build_l_command_frame(self,loading_node):
        # L코드 Body frame 생성
        l_command_body_frame = f"A{self.amr_id}L{loading_node}00"
        # l코드 Body frame ASCII로 변환
        l_command_body_frame = l_command_body_frame.encode("ascii")
        # 전체 L 명령 구성
        l_command_frame = self.make_frame(l_command_body_frame)
        # 전체 응답 프레임 return
        return l_command_frame
# ----------------------------------------------------------------------------
# AMS U 명령 구성 클래스 선언
class ACS_U_Command_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # U 코드 프레임 구성 함수 선언
    def build_u_command_frame(self,unloading_node):
        # U코드 Body frame 생성
        u_command_body_frame = f"A{self.amr_id}U{unloading_node}00"
        # U코드 Body frame ASCII로 변환
        u_command_body_frame = u_command_body_frame.encode("ascii")
        # 전체 U 명령 구성
        u_command_frame = self.make_frame(u_command_body_frame)
        # 전체 응답 프레임 return
        return u_command_frame
# ----------------------------------------------------------------------------
# 삼성 ACS TCP 통신용 AMR 클래스 선언
class SAM_ACS_AMR_Client:
    # 클래스 초기화 함수 선언
    def __init__(self,acs_ip,acs_port,amr_id='444',timeout_sec=5.0,recv_buffer_size=1024):
        # 삼성 ACS IP 변수 선언
        self.acs_ip = acs_ip
        # 삼성 ACS 포트 변수 선언
        self.acs_port = acs_port
        # AMR ID 변수 선언
        self.amr_id = str(amr_id).zfill(3)
        # TCP timeout 변수 선언
        self.timeout_sec = timeout_sec
        # TCP 수신 버퍼 크기 변수 선언
        self.recv_buffer_size = recv_buffer_size
        # TCP 수신 버퍼 저장 변수 선언
        self.recv_queue = b""
        # TCP 클라이언트 소켓 저장 변수 선언
        self.client_socket = None
        # STX 저장 변수 선언
        self.stx = b"\x02"
        # ETX 저장 변수 서언
        self.etx = b"\x03"
        # C_command 응답 클래스 객체 선언
        self.c_command = ACS_C_Command_commu(amr_id=self.amr_id)
        # S_command 명령 구성 클래스 객체 선언
        self.s_command = ACS_S_Command_commu(amr_id=self.amr_id)
        # T_command 명령 구성 클래스 객체 선언
        self.t_command = ACS_T_Command_commu(amr_id=self.amr_id)
        # L_command 명령 구성 클래스 객체 선언
        self.l_command = ACS_L_Command_commu(amr_id=self.amr_id)
        # U_command 명령 구성 클래스 객체 선언
        self.u_command = ACS_U_Command_commu(amr_id=self.amr_id)
    # TCP 서버와 연결 함수 선언
    def connect_to_server(self):
        # 이미 연결된 소켓이 있으면
        if self.client_socket is not None:
            # True return
            return True
        # 에러가 없으면
        try:
            # TCP 소켓 생성
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # TCP timeout 설정
            self.client_socket.settimeout(self.timeout_sec)
            # ACS 서버에 접속 시도
            self.client_socket.connect((self.acs_ip,self.acs_port))
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] ACS 연결 성공 : {self.acs_ip}:{self.acs_port}")
            print("---------------------------------------------------------")
            # Ture return
            return True
        # 에러가 발생하면
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] ACS 연결 실패 : {error}")
            print("---------------------------------------------------------")
            # 소켓 닫기
            self.close_socket()
            # False return
            return False
    # 소켓 닫기 함수 선언
    def close_socket(self):
        # 만약 소켓이 있다면
        if self.client_socket is not None:
            # 소켓 종료
            self.client_socket.close()
        # 소켓 변수 초기화
        self.client_socket = None
        # 수신 버퍼 초기화
        self.recv_queue = b""
    # 데이터 송신 함수 선언
    def send_frame(self,data):
        # 소켓이 열려있지 않다면
        if self.client_socket is None:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print("[ACS CLIENT] 데이터 송신 실패 : 소켓 연결 안됨")
            print("---------------------------------------------------------")
            # False return
            return False
        # 에러가 없으면
        try:
            # 전체 frame 송신
            self.client_socket.sendall(data)
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 송신 완료 : {data}")
            print(f"[ACS CLIENT] HEX : {self.c_command.byte_to_hex_string(data)}")
            print("---------------------------------------------------------")
            # True return
            return True
        # 에러가 있으면
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 데이터 송신 중 error 발생 : {error}")
            print("---------------------------------------------------------")
            # 데이터 전송 실패 시 소켓 닫기
            self.close_socket()
            # False return
            return False
    # 데이터 수신 함수 선언
    def recv_data(self):
        # 소켓이 열려있지 않다면
        if self.client_socket is None:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print("[ACS CLIENT] 데이터 수신 실패 : 소켓 연결 안됨")
            print("---------------------------------------------------------")
            # 소켓 닫기
            self.close_socket()
            # 빈 바이트 return
            return b""
        # 에러가 없으면
        try:
            # 소켓 데이터 수신
            recv_data = self.client_socket.recv(self.recv_buffer_size)
            # 수신 데이터가 없으면
            if not recv_data:
                # 디버그 문구 print
                print("[ACS CLIENT] 수신 데이터 X")
                # 소켓 종료
                self.close_socket()
                # 빈 바이트 return
                return b""
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 데이터 수신 완료 : {recv_data}")
            print(f"[ACS CLIENT] HEX : {self.c_command.byte_to_hex_string(recv_data)}")
            print("---------------------------------------------------------")
            # 수신 데이터 return
            return recv_data
        # 에러 발생 시 
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 데이터 수신 중 error 발생 : {error}")
            print("---------------------------------------------------------")
            # 소켓 종료
            self.close_socket()
            # 빈 바이트 return
            return b""
    # STX,ETX 기준 1프레임 추출 함수 선언
    def extract_one_frame(self):
        # STX가 수신 버퍼에 없으면
        if self.stx not in self.recv_queue:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 수신 버퍼에 STX X")
            print("---------------------------------------------------------")
            # None return
            return None
        # 수신 버퍼에서 첫 STX 위치 찾기
        stx_index = self.recv_queue.find(self.stx)
        # 첫 STX 다음에 오는 ETX 위치 찾기
        etx_index = self.recv_queue.find(self.etx, stx_index + 1)
        # ETX가 아직 없으면
        if etx_index ==-1:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 수신 버퍼에 ETX X")
            print("---------------------------------------------------------")
            # None return
            return None
        # STX ~ ETX까지 1프레임 추출
        one_frame = self.recv_queue[stx_index:etx_index + 1]
        # 남은 버퍼 업데이트 
        self.recv_queue = self.recv_queue[etx_index+1:]
        # 추출한 1frame return
        return one_frame
    # 1개 프레임 수신 대기 함수 선언
    def recv_one_frame(self,wait_timeout_sec=None):
        # timeout이 없으면 
        if wait_timeout_sec is None:
            # tcp timeout 사용
            wait_timeout_sec = self.timeout_sec
        # 시작 시간 저장
        start_time = time.time()
        # timeout까지 반복
        while True:
            # 기존 버퍼에서 1 frame 추출
            one_frame = self.extract_one_frame()
            # 추출 성공 시 
            if one_frame is not None:
                # 추출 frame return
                return one_frame
            # timeout 초과 시 
            if time.time() - start_time > wait_timeout_sec:
                print("---------------------------------------------------------")
                print(f"[ACS CLIENT] 수신 버퍼에 ETX X")
                print("---------------------------------------------------------")       
                # None return
                return None
            # 소켓에서 데이터 수신
            recv_data = self.recv_data()
            # 데이터가 수신되면
            if recv_data:
                # 버퍼에 누적
                self.recv_queue += recv_data
            # 0.1초 대기
            time.sleep(0.1)
    # C 명령 1개 수신 후 파싱 함수 선언
    def wait_c_command(self,wait_timeout_sec=None):
        # 프레임 1개 수신
        recv_frame = self.recv_one_frame(wait_timeout_sec=wait_timeout_sec)
        # 수신 실패 했다면
        if recv_frame is None:
            # 디버그 문구 print
            print("[ACS CLIENT] C Command 수신 실패")
            # None return
            return None
        # C명령 파싱 진행
        parse_data = self.c_command.parse_c_command(recv_frame)
        # 파싱 결과 return
        return parse_data
    # C 명령 응답 송신 함수 선언
    def send_c_command_response(self,target_node):
        # C 응답 프레임 구성
        c_command_response_frame = self.c_command.build_c_command_response_frame(target_node=target_node,work_type="00")
        # 송신 결과 return
        return self.send_frame(c_command_response_frame)
    # c명령 수신 후 응답 함수 선언
    def recv_and_reply_c_command(self,wait_timeout_sec=None):
        # c 명령 수신 대기
        recv_c_command = self.wait_c_command(wait_timeout_sec=wait_timeout_sec)
        # c 명령이 수신되지 않앗다면
        if recv_c_command is None:
            # None return
            return None
        # 디버그 문구 print
        print("--------------------------------------------------")
        print(f"[ACS CLIENT] C Command 수신 성공 : {recv_c_command}")
        print("--------------------------------------------------")
        # c 명령 응답 송신
        c_command_response = self.send_c_command_response(
            target_node=recv_c_command["target_node"])
        # 응답 송신 실패 시
        if not c_command_response:
            # None return
            return None
        # 수신한 c 명령 return
        return recv_c_command
    # S 명령 송신 함수 선언
    def send_s_command(self,target_node,amr_status,carry_flag,move_flag):
        # S 명령 프레임 구성
        s_command_frame = self.s_command.build_s_command_frame(target_node=target_node, AMR_Status=amr_status,carry_flag=carry_flag,move_flag=move_flag)
        # 송신 결과 return
        return self.send_frame(s_command_frame)
    # T 명령 송신 함수 선언
    def send_t_command(self,location_node):
        # T 명령 프레임 구성
        t_command_frame = self.t_command.build_t_command_frame(location_node=location_node)
        # 송신 결과 return
        return self.send_frame(t_command_frame)
    # L 명령 송신 함수 선언
    def send_l_command(self,loading_node):
        # L 명령 프레임 구성
        l_command_frame = self.l_command.build_l_command_frame(loading_node=loading_node)
        # 송신 결과 return
        return self.send_frame(l_command_frame)
    # U 명령 송신 함수 선언
    def send_u_command(self,unloading_node):
        # U 명령 프레임 구성
        u_command_frame = self.u_command.build_u_command_frame(unloading_node=unloading_node)
        # 송신 결과 return
        return self.send_frame(u_command_frame)
