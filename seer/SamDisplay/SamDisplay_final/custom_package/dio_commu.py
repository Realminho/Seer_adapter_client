# ----------------------- 범용 라이브러리 import -----------------------
# TCP 통신을 위한 socket 라이브러리 import
import socket
# 시간 처리를 위한 time 라이브러리 import
import time
# Thread 처리를 위한 threading 라이브러리 import
import threading
# -------------------- Ezi-IO Ethernet DIO 제어 클래스 선언 --------------------
class DIO_Commu:
    # 클래스 초기화 함수 선언
    def __init__(self,ip="192.168.192.2",port=2001,timeout=5.0):
        # io 모듈 IP 주소 저장
        self.ip = ip
        # io 모듈 포트 저장
        self.port = port
        # tcp 통신 timeout 설정
        self.timeout = timeout
        # socket 객체 저장 변수 선언
        self.socket = None
        # Sync No. 저장 변수 선언
        self.sync_no = 0
        # IO 모듈 통신 프로토콜 Header값 저장
        self.header = 0xAA
        # IO 모듈 통신 프로토콜 Reserved값 저장
        self.reserved = 0x00
        # IO 모듈 정보 요청 통신 프로토콜 Frame type값 저장
        self.get_module_info = 0x01
        # IO 상태 Read 통신 프로토콜 Frame type값 저장
        self.get_io_info = 0xC0
        # DO 상태 Read 통신 프로토콜 Frame type값 저장
        self.get_do_info = 0xC5
        # DO 상태 제어 통신 프로토콜 Frame tyep값 저장
        self.control_do = 0xC6
        # 정상 통신상태 수신값 저장
        self.result_ok = 0x00
    # --------------------------------- 통신 관련 함수 선언 ----------------------------------
    # DIO 모듈 연결 함수 선언
    def dio_connect(self):
        # TCP socket 객체 선언
        self.socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        # socket timeout 설정
        self.socket.settimeout(self.timeout)
        # DIO 모듈과 연결
        self.socket.connect((self.ip,self.port))
        # 연결 완료 문구 print
        print(f"[EZI-DIO] TCP 연결 완료 :{self.ip}:{self.port}")
    # DIO 모듈 연결 종료 함수 선언
    def dio_close(self):
        # socket 객체가 존재하면
        if self.socket is not None:
            # socket 종료
            self.socket.close()
            # socket 객체 초기화
            self.socket = None
            # 연결 종료 문구 print
            print("[EZI-DIO] TCP 연결 종료")
    # --------------------------------- 프로토콜 관련 범용 함수 선언 ----------------------------------
    # Sync No. 순차 생성 함수 선언
    def create_sync_no(self):
        # 현재 sync no 번호 저장
        current_sync_no = self.sync_no
        # 다음 sync_no로 증가
        self.sync_no = (current_sync_no+1)& 0xFF
        # 현재 sync_no return
        return current_sync_no
    # 32bit 변수를 4byte로 변환하는 함수 선언
    def uint32_to_bytes(self, value):
        # Ezi-IO 실제 통신 데이터는 little-endian 형식으로 변환
        return value.to_bytes(4, byteorder="little", signed=False)
    # 4byte 변수를 32bit로 변환하는 함수 선언
    def bytes_to_uint32(self, value):
        # Ezi-IO 실제 통신 데이터는 little-endian 형식으로 변환
        return int.from_bytes(value, byteorder="little", signed=False)
    # DIO 모듈 통신 Frame 생성 함수 선언
    def build_dio_frame(self,frame_type,data=b""):
        # Sync_no 생성
        sync_no = self.create_sync_no()
        # Data 길이 계산
        data_length = len(data)
        # 최종 length 계산
        length = 3 + data_length
        # 빈 Frame 생성
        frame = bytearray()
        # Frame에 Header 추가
        frame.append(self.header)
        # Frame에 LENGTH 추가
        frame.append(length)
        # Frame에 Sync No 추가
        frame.append(sync_no)
        # Frame에 Reserved 추가
        frame.append(self.reserved)
        # Frame에 Frame_type 추가
        frame.append(frame_type)
        # Frame에 Data 추가
        frame.extend(data)
        # 생성된 Frame, sync_no return
        return bytes(frame), sync_no
    # 지정한 길이만큼 프레임을 수신하는 함수 선언
    def recv_frame(self,size):
        # 수신데이터 저장 변수 선언
        recv_data = bytearray()
        # 인자로 받은 size만큼
        while len(recv_data)<size:
            # 데이터 수신
            packet = self.socket.recv(size - len(recv_data))
            # 수신 데이터가 없으면
            if not packet:
                # 연결 끊김 에러 발생
                raise ConnectionError("[EZI-DIO] 데이터 수신 중 연결이 끊어짐")
            # 수신 데이터 누적
            recv_data.extend(packet)
        # 수신 데이터 return
        return recv_data
    # 응답 Frame 수신 함수 선언
    def recv_response_frame(self):
        # Header+Length 2byte 먼저 수신
        response_frame_header_length = self.recv_frame(2)
        # Header가 프로토콜과 다르다면
        if response_frame_header_length[0] != self.header:
            # 에러 발생
            raise ValueError(f"[EZI-DIO] Header 오류 : {response_frame_header_length.hex(' ')}")
        # Length 추출
        response_frame_length = response_frame_header_length[1]
        # Length만큼 데이터 추출
        response_frame_body = self.recv_frame(response_frame_length)
        # 응답 프레임 return
        return response_frame_header_length + response_frame_body
    # 명령 송신 후 응답 파싱 함수 선언
    def send_dio_frame(self,frame_type,data=b""):
        # socket 연결이 안되었으면
        if self.socket is None:
            # 에러 발생
            raise ConnectionError("[EZI-DIO] DIO와 우선 연결 필요")
        # 송신 frame 생성
        send_frame,sync_no = self.build_dio_frame(frame_type,data)
        # 생성한 frame 송신
        self.socket.sendall(send_frame)
        # 응답 frame 수신
        recv_frame = self.recv_response_frame()
        # 응답 frame이 최소 길이보다 작으면
        if len(recv_frame) < 6:
            # 에러 발생
            raise ValueError(f"[EZI-DIO] 응답 길이 부족 : {recv_frame.hex(' ')}")
        # 응답 frame length 확인
        length = recv_frame[1]
        # 응답 frame Sync No 확인 후 값이 송신 프레임과 일치하지 않다면
        if recv_frame[2] != sync_no:
            # 에러 발생
                raise ValueError(f"[EZI-DIO] Sync No 오류 : recv={recv_frame[2]}, expected={sync_no}, raw={recv_frame.hex(' ')}")
        # 응답 frame reserved 확인 후 값이 송신 프레임과 일치하지 않다면
        if recv_frame[3] != self.reserved:
            # 에러 발생
            raise ValueError(f"[EZI-DIO] Reserved 오류 : {recv_frame.hex(' ')}")
        # 응답 frame frame_type 확인 후 값이 송신 프레임과 일치하지 않다면
        if recv_frame[4] != frame_type:
            # 에러 발생
            raise ValueError(f"[EZI-DIO] Frame type 오류 : recv=0x{recv_frame[4]:02X}, expected=0x{frame_type:02X}")
        # 응답 frame에서 통신 상태 추출
        result = recv_frame[5]
        # 통신 상태가 정상이 아니면
        if result != self.result_ok:
            # 에러 발생
            raise RuntimeError(f"[EZI-DIO] 통신 에러 : result=0x{result:02X}, raw={recv_frame.hex(' ')}")
        # 응답 Data 길이 계산
        # 응답 Length = Sync No + Reserved + Frame type + 통신상태 + 응답 Data
        response_data_length = length - 4
        # 응답 Data 추출
        response_data = recv_frame[6:6 + response_data_length]
        # 응답 Data return
        return response_data
    # --------------------------------- DIO 모듈 관련 함수 선언 ----------------------------------
    # DIO 모듈 정보 읽기 함수 선언
    def get_dio_module_info(self):
        # DIO 모듈 정보 요청 후 응답 수신
        response_data = self.send_dio_frame(self.get_module_info)
        # 응답 데이터 길이 확인 후 프로토콜과 일치하지 않으면
        if len(response_data) < 8:
            # 에러 발생
            raise ValueError(f"[EZI-IO] DI 응답 길이 오류 : {response_data.hex(' ')}")
        # DIO 모듈 종류 추출
        dio_type = response_data[0]
        # DIO 모듈 버전 추출
        version = response_data[1:].split(b"\x00")[0].decode(errors="ignore")
        # DIO 모듈 정보 return
        return dio_type, version
    # --------------------------------- DI 제어 관련 함수 선언 ----------------------------------
    # DI 전체 상태 Read 함수 선언
    def read_all_di(self):
        # DI 상태 요청 후 응답 수신
        response_data = self.send_dio_frame(self.get_io_info)
        # 응답 데이터 길이 확인 후 프로토콜과 일치하지 않다면
        if len(response_data) < 8:
            # 에러 발생
            raise ValueError(f"[EZI-IO] DI 응답 길이 오류 : {response_data.hex(' ')}")
        # DI 상태 4바이트로 추출
        input_bytes = response_data[0:4]
        # DI 상태 정수형으로 변환
        input_bits = self.bytes_to_uint32(input_bytes)
        # DI 0~15 상태 dictionary 생성
        di_status = {}
        # DI 0~15 반복
        for channel in range(16):
            # 해당 bit ON/OFF 확인
            di_status[channel] = bool(input_bits & (1 << channel))
        # DI 상태 return
        return di_status, input_bits
     # 특정 DI read 함수 선언
    def read_di(self,di_no):
        # 인자로 받은 DI 번호 유효성 검사
        if di_no < 0 or di_no > 15:
            # 검사 실패 시 에러 발생
            raise ValueError("DI 번호는 0~15 사이여야 합니다.")
        # 전체 DI 상태 읽기
        di_status, latch_bits = self.read_all_di()
        # 특정 DI 상태만 return
        return di_status[di_no]
    # --------------------------------- DO 제어 관련 함수 선언 ----------------------------------
    # DO 전체 상태 Read 함수 선언
    def read_all_do(self):
        # DO 상태 요청 후 응답 수신
        response_data = self.send_dio_frame(self.get_do_info)
        # 응답 데이터 길이 확인 후 프로토콜과 일치하지 않다면
        if len(response_data) < 8:
            # 에러 발생
            raise ValueError(f"[EZI-IO] DO 응답 길이 오류 : {response_data.hex(' ')}")
        # Output 상태 4바이트 추출
        output_bytes = response_data[0:4]
        # Run/Stop 상태 4바이트 추출
        run_stop_bytes = response_data[4:8]
        # Output 상태 정수 변환
        output_bits = self.bytes_to_uint32(output_bytes)
        # Run/Stop 상태 정수 변환
        run_stop_bits = self.bytes_to_uint32(run_stop_bytes)
        # DO 0~15 상태 dictionary 생성
        do_status = {}
        # DO 0~15 반복
        for do_no in range(16):
            # 실제 출력은 프로토콜상 Output16~31에 매핑됨
            protocol_output_no = do_no + 16
            # 해당 bit ON/OFF 확인
            do_status[do_no] = bool(output_bits & (1 << protocol_output_no))
        # DO 상태와 원본 bit 정보 return
        return do_status, output_bits, run_stop_bits
    # 특정 DO 출력 Read 함수 선언
    def read_do(self,do_no):
        # DO 번호 범위 확인
        if do_no < 0 or do_no > 15:
            raise ValueError("DO 번호는 0~15 사이여야 합니다.")
        # 전체 DO 상태 읽기
        do_status, output_bits, run_stop_bits = self.read_all_do()
        # 특정 DO 상태 return
        return do_status[do_no]
    # 특정 DO 출력 제어 함수 선언
    def set_do(self,do_no,flag):
        # 인자로 받은 DO 번호 범위가 프로토콜 규약과 다르다면
        if do_no < 0 or do_no > 15:
            # 에러 발생
            raise ValueError("DO 번호는 0~15 사이여야 합니다.")
        # 프로토콜에 맞는 번호로 DIO로 변경
        protocol_output_no = do_no+16
        # flag가 Ture라면
        if flag == True:
            # Set Output bit 생성
            set_mask = 1 << protocol_output_no
            # Reset Output bit 없음
            reset_mask = 0
        # flag가 False라면
        else:
            # Set Output bit 없음
            set_mask = 0
            # Reset Output bit 생성
            reset_mask = 1 << protocol_output_no
        # 송신 Data 생성
        # Data = Set Output 4 bytes + Reset Output 4 bytes
        data = self.uint32_to_bytes(set_mask) + self.uint32_to_bytes(reset_mask)
        # SetOutput 명령 송신
        self.send_dio_frame(self.control_do, data)
    # 특정 DO On 상태로 제어하는 함수 선언
    def set_do_on(self,do_no):
        # 인자로 받은 do_no On 상태로 제어
        return self.set_do(do_no,True)
    # 특정 DO Off 상태로 제어하는 함수 선언
    def set_do_off(self,do_no):
        # 인자로 받은 do_no Off 상태로 제어
        return self.set_do(do_no,False)
    

    




            
            

