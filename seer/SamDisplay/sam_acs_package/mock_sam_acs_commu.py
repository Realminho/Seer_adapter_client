# ----------------------- 범용 라이브러리 import -----------------------
# 소켓 통신을 위한 socket 라이브러리 import
import socket
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 윈도우용 키 입력 처리를 위한 msvcrt import
import msvcrt
# ------------------- 사용자 정의 라이브러리 import -----------------------
# 기존 ACS C 명령 클래스 import
from sam_acs_package.sam_acs_commu import ACS_C_Command_commu

# ----------------------------------------------------------------------
# Mock ACS C 명령 구성 및 파싱 클래스 선언
class Mock_ACS_C_Command_commu(ACS_C_Command_commu):
    # 클래스 초기화 함수 선언
    def __init__(self, amr_id="444"):
        # 부모 클래스 초기화
        super().__init__(amr_id=amr_id)
    # Mock ACS에서 AMR로 보낼 C 명령 프레임 구성 함수 선언
    def build_c_command_frame(self, target_node, work_type="00"):
        # 목적지 노드를 4자리 문자열로 변환
        target_node = str(target_node).zfill(4)
        # work_type을 2자리 문자열로 변환
        work_type = str(work_type).zfill(2)
        # C 명령 Body frame 생성
        c_command_body_frame = f"{self.amr_id}AC{target_node}{work_type}"
        # C 명령 Body frame ASCII로 변환
        c_command_body_frame = c_command_body_frame.encode("ascii")
        # C 명령 프레임 구성 후 return
        return self.make_frame(c_command_body_frame)
    # AMR이 회신한 C 응답 프레임 파싱 함수 선언
    def parse_c_response(self, frame_data):
        # 인자로 받은 frame_data checksum 검증 수행
        checksum_ok, recv_body, recv_checksum, cal_checksum = self.check_checksum(frame_data)
        # checksum 검증 실패 시
        if not checksum_ok:
            # 디버그 문구 print
            print("[MOCK ACS] checksum 불일치")
            # 수신 checksum print
            print(f"[MOCK ACS] 수신한 checksum : {recv_checksum.decode('ascii', errors='ignore') if recv_checksum else ''}")
            # 계산한 checksum print
            print(f"[MOCK ACS] 계산한 checksum : {cal_checksum.decode('ascii', errors='ignore') if cal_checksum else ''}")
            # None return
            return None
        # 에러가 없으면
        try:
            # 수신한 body를 ascii 문자열로 변환
            body_frame = recv_body.decode("ascii")
        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print(f"[MOCK ACS] Body frame ASCII 변환 중 에러 발생 : {error}")
            # None return
            return None
        # body 길이가 부족하면
        if len(body_frame) < 11:
            # 디버그 문구 print
            print(f"[MOCK ACS] 응답 frame 길이 부족 : {body_frame}")
            # None return
            return None
        # body 프레임 첫 글자가 A가 아니면
        if body_frame[0] != "A":
            # 디버그 문구 print
            print(f"[MOCK ACS] 응답 A 위치 오류 : {body_frame}")
            # None return
            return None
        # 5번째 문자가 C가 아니면
        if body_frame[4] != "C":
            # 디버그 문구 print
            print(f"[MOCK ACS] 응답 C 명령 위치 오류 : {body_frame}")
            # None return
            return None
        # 수신한 C 명령 응답 파싱 결과 dict type으로 정리
        parse_data = {
            "header": body_frame[0],
            "amr_id": body_frame[1:4],
            "command_code": body_frame[4],
            "target_node": body_frame[5:9],
            "work_type": body_frame[9:11],
        }
        # 파싱 데이터 return
        return parse_data        
# ----------------------------------------------------------------------------
# 삼성 mock acs 서버 클래스 선언
class Mock_SAM_ACS_Server_commu:
    # 클래스 초기화 함수 선언
    def __init__(self, server_ip="0.0.0.0", server_port=1331, recv_buffer_size=1024):
        # 서버 IP 저장
        self.server_ip = server_ip
        # 서버 Port 저장
        self.server_port = int(server_port)
        # 수신 버퍼 크기 저장
        self.recv_buffer_size = recv_buffer_size
        # 서버 소켓 저장 변수 선언
        self.server_socket = None
        # 클라이언트 소켓 저장 변수 선언
        self.client_socket = None
        # 클라이언트 주소 저장 변수 선언
        self.client_address = None
        # 수신 버퍼 저장 변수 선언
        self.recv_queue = b""
        # STX 저장 변수 선언
        self.stx = b"\x02"
        # ETX 저장 변수 선언
        self.etx = b"\x03"
        # C 명령 클래스 저장 변수 선언
        self.c_command = None
    # ACS 서버 시작 함수 선언
    def start_acs_server(self):
        # 에러가 없으면
        try:
            # TCP 서버 소켓 생성
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 주소 재사용 옵션 설정
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 서버 주소 바인드
            self.server_socket.bind((self.server_ip, self.server_port))
            # 클라이언트 접속 대기 시작
            self.server_socket.listen(1)
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 모의 ACS 서버 시작 완료 : {self.server_ip}:{self.server_port}")
            print("---------------------------------------------------------")
            # True return
            return True
        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 서버 시작 실패 : {error}")
            print("---------------------------------------------------------")
            # 전체 종료
            self.close_all()
            # False return
            return False
    # 클라이언트 접속 대기 함수 선언
    def wait_client(self):
        # 서버 소켓이 없으면
        if self.server_socket is None:
            # 디버그 문구 print
            print("[MOCK ACS] 서버 소켓 X")
            # False return
            return False
        # 에러가 없으면
        try:
            # 클라이언트 접속 대기
            self.client_socket, self.client_address = self.server_socket.accept()
            # 수신 timeout 설정
            self.client_socket.settimeout(10.0)
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 클라이언트 접속 완료 : {self.client_address}")
            print("---------------------------------------------------------")
            # True return
            return True
        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 클라이언트 접속 실패 : {error}")
            print("---------------------------------------------------------")
            # False return
            return False
    # 클라이언트 접속 종료 함수 선언
    def close_client(self):
        # 클라이언트 소켓이 있고
        if self.client_socket is not None:
            # 클라이언트 소켓 종료
            self.client_socket.close()
        # 클라이언트 소켓 변수 초기화
        self.client_socket = None
        # 클라이언트 주소 변수 초기화
        self.client_address = None
        # 수신 버퍼 초기화
        self.recv_queue = b""
    # 전체 소켓 종료 함수 선언
    def close_all(self):
        # 클라이언트 종료
        self.close_client()
        # 서버 소켓이 있으면
        if self.server_socket is not None:
            # 서버 소켓 종료
            self.server_socket.close()
        # 서버 소켓 초기화
        self.server_socket = None
    # C 명령 클래스 객체 설정 함수 선언
    def set_c_command(self, amr_id):
        # Mock용 C 명령 클래스 객체 선언
        self.c_command = Mock_ACS_C_Command_commu(amr_id=amr_id)
    # 데이터 송신 함수 선언
    def send_frame(self, data):
        # 클라이언트 소켓이 없으면
        if self.client_socket is None:
            # 디버그 문구 print
            print("[MOCK ACS] 송신 실패 : 연결된 클라이언트 X")
            # False return
            return False
        # 에러가 없으면
        try:
            # 인자로 받은 Frame 전체 송신
            self.client_socket.sendall(data)
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 송신 완료 : {data}")
            print(f"[MOCK ACS] HEX : {self.c_command.byte_to_hex_string(data)}")
            print("---------------------------------------------------------")
            # True return
            return True
        # 에러가 있으면
        except Exception as error:
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[MOCK ACS] 데이터 송신 중 error 발생 : {error}")
            print("---------------------------------------------------------")
            # 클라이언트 종료
            self.close_client()
            # False return
            return False
    # 데이터 수신 함수 선언
    def recv_data(self):
        # 클라이언트 소켓이 없으면
        if self.client_socket is None:
            # 빈 바이트 return
            return b""
        # 에러가 없으면
        try:
            # 소켓 데이터 수신
            recv_data = self.client_socket.recv(self.recv_buffer_size)
            # 수신 데이터가 없으면
            if not recv_data:
                # 디버그 문구 print
                print("[MOCK ACS] 클라이언트 수신 데이터 X")
                # 클라이언트 종료
                self.close_client()
                # 빈 바이트 return
                return b""
            # 수신 데이터 return
            return recv_data
        # timeout이면 빈 바이트 return
        except socket.timeout:
            return b""
        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print(f"[MOCK ACS] 데이터 수신 중 error 발생 : {error}")
            # 클라이언트 종료
            self.close_client()
            # 빈 바이트 return
            return b""
    # STX,ETX 기준 1프레임 추출 함수 선언
    def extract_one_frame(self):
        # STX가 수신 버퍼에 없으면
        if self.stx not in self.recv_queue:
            # None return
            return None
        # 수신 버퍼에서 첫 STX 위치 찾기
        stx_index = self.recv_queue.find(self.stx)
        # 첫 STX 다음에 오는 ETX 위치 찾기
        etx_index = self.recv_queue.find(self.etx, stx_index + 1)
        # ETX가 아직 없으면
        if etx_index == -1:
            # None return
            return None
        # STX ~ ETX까지 1프레임 추출
        one_frame = self.recv_queue[stx_index:etx_index + 1]
        # 남은 버퍼 업데이트
        self.recv_queue = self.recv_queue[etx_index + 1:]
        # 추출한 1frame return
        return one_frame
    # 1개 프레임 수신 대기 함수 선언
    def recv_one_frame(self, wait_timeout_sec=600.0):
        # 시작 시간 저장
        start_time = time.time()
        # 무한 반복
        while True:
            # 기존 버퍼에서 1 frame 추출
            one_frame = self.extract_one_frame()
            # 추출 성공 시
            if one_frame is not None:
                # 추출 frame return
                return one_frame
            # timeout 초과 시
            if time.time() - start_time > wait_timeout_sec:
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
    # C 명령 송신 후 응답 수신 함수 선언
    def send_c_command_and_wait_response(self, amr_id, target_node, work_type="00", wait_timeout_sec=10.0):
        # 사용할 C 명령 클래스 설정
        self.set_c_command(amr_id=amr_id)
        # C 명령 프레임 구성
        c_command_frame = self.c_command.build_c_command_frame(
            target_node=target_node,
            work_type=work_type)
        # 프레임 송신 실패 시
        if not self.send_frame(c_command_frame):
            # None return
            return None
        # 응답 프레임 1개 수신 대기
        recv_frame = self.recv_one_frame(wait_timeout_sec=wait_timeout_sec)
        # 응답 수신 실패 시
        if recv_frame is None:
            # 디버그 문구 print
            print("[MOCK ACS] 응답 수신 timeout")
            # None return
            return None
        # 디버그 문구 print
        print("---------------------------------------------------------")
        print(f"[MOCK ACS] 응답 수신 완료 : {recv_frame}")
        print(f"[MOCK ACS] HEX : {self.c_command.byte_to_hex_string(recv_frame)}")
        print("---------------------------------------------------------")
        # 응답 파싱 결과 return
        return self.c_command.parse_c_response(recv_frame)
    # 윈도우용 키 입력 확인 함수 선언
    def check_keyboard_input(self):
        # 눌린 키가 있으면
        if msvcrt.kbhit():
            # 1글자 읽기
            key = msvcrt.getch()
            # 스페이스바 입력이면
            if key == b" ":
                return "space"
            # ESC 입력이면
            if key == b"\x1b":
                return "esc"
        # 입력 없으면 None return
        return None
    # 사용자 AMR 정보 입력 함수 선언
    def input_amr_id(self):
        # 무한 반복
        while True:
            # 사용자 입력 받기
            amr_id = input("AMR ID : ").strip()
            # 빈 문자열이면
            if not amr_id:
                # 안내 문구 print
                print("AMR ID를 다시 입력해주세요.")
                # 다음 loop 진행
                continue
            # 숫자가 아니면
            if not amr_id.isdigit():
                # 안내 문구 print
                print("AMR ID는 숫자로 입력해주세요.")
                # 다음 loop 진행
                continue
            # 3자리 0패딩 후 return
            return amr_id.zfill(3)
    # 사용자 목적지 입력 함수 선언
    def input_target_node(self):
        # 무한 반복
        while True:
            # 사용자 입력 받기
            target_node = input("목적지 입력 : ").strip()
            # 빈 문자열이면
            if not target_node:
                # 안내 문구 print
                print("목적지 번호를 다시 입력해주세요.")
                # 다음 loop 진행
                continue
            # 숫자가 아니면
            if not target_node.isdigit():
                # 안내 문구 print
                print("목적지 번호는 숫자로 입력해주세요.")
                # 다음 loop 진행
                continue
            # 4자리 0패딩 후 return
            return target_node.zfill(4)


    
    




