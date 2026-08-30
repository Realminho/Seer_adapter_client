# ----------------------- 범용 라이브러리 import -----------------------
# 소켓 통신을 위한 socket 라이브러리 import
import socket
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------------------- Custom 라이브러리 import -----------------------
# ACS Protocol 클래스 import
from sam_acs_package.sam_acs_protocol import ACS_Protocol

# 삼성 ACS TCP Client 클래스 선언
class ACS_Client:
    # 클래스 초기화 함수 선언
    def __init__(self,acs_ip,acs_port,amr_id="444",recv_buffer_size=1024):
        # ACS IP 저장 변수 선언
        self.acs_ip = acs_ip
        # ACS Port 저장 변수 선언
        self.acs_port = acs_port
        # amr id 저장 변수 선언
        self.amr_id = str(amr_id).zfill(3)
        # 수신 버퍼 크기 변수 선언
        self.recv_buffer_size = recv_buffer_size
        # ACS Client Socket 저장 변수 선언
        self.acs_client_socket = None
        # 수신 데이터 누적 버퍼 선언
        self.recv_queue = b""
        # STX 설정
        self.stx = b"\x02"
        # ETX 설정
        self.etx = b"\x03"
        # ACS 프로토콜 객체 선언
        self.acs_protocol = ACS_Protocol(amr_id=self.amr_id)
    # -------------------------------------- ACS 연결 관련 함수 ------------------------------------------------------
    # ACS 서버 연결 함수 선언
    def connect(self,timeout_sec=60,retry_count=5,retry_delay_time=3.0):
        # 이미 ACS와 연결이 되었으면
        if self.acs_client_socket is not None:
            # True return
            return True
        # 재접속 시도 만큼 반복
        for retry_index in range(1,retry_count+1):
            # 에러가 없으면
            try:
                # Client_socket 생성
                self.acs_client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # 연결 Time out 설정
                self.acs_client_socket.settimeout(timeout_sec)
                # ACS 서버 접속
                self.acs_client_socket.connect((self.acs_ip, self.acs_port))
                # 디버그 문구 print
                print(f"[ACS Client] ACS 연결 성공 : {self.acs_ip}:{self.acs_port}")
                # True return
                return True
            # 에러 발생 시 
            except Exception as error:
                # 디버그 문구 print
                print(f"[ACS Client] ACS 연결 실패 : {error}")
                # 소켓 종료
                self.close()
                # 마지막 시도가 아니라면
                if retry_index < retry_count:
                    # 재섭속 대기 문구 print
                    print(f"[ACS Client] {retry_delay_time}초 후 ACS 재접속 시도")
                    # 설정한 대기 시간만큼 대기
                    time.sleep(retry_delay_time)
        # 재섭속 시간 초과 시 
        print("==================================================")
        print(f"[ACS Client] ACS 연결 최종 실패 : 최대 {retry_count}회 재시도 초과")
        print("==================================================")
        # False return
        return False

    # ACS 연결 상태 확인 함수 선언
    def is_connected(self):
        # 소켓이 존재하면 True return
        return self.acs_client_socket is not None

    # ACS 소켓 종료 함수
    def close(self):
        # 소켓이 존재할 때만 실행
        if self.acs_client_socket is not None:
            # 에러가 없으면
            try:
                # acs 클라이언트 소켓 종료
                self.acs_client_socket.close()
            # 에러발생 시
            except Exception as error:
                print(f"[ACS Client] ACS 닫기 실패 : {error}")
        # 소켓 변수 초기화
        self.acs_client_socket = None
        # 수신 버퍼 초기화
        self.recv_queue = b""
    # -------------------------------------- Frame 송수신 관련 함수 ------------------------------------------------------
    # Frame 송신 함수 선언
    def send_frame(self,frame_data):
        # 소켓이 없으면
        if self.acs_client_socket is None:
            # 디버그 문구 print
            print("[ACS Client] ACS 연결 안됨, Frame 송신 실패")
            # False return
            return False
        # 에러가 없으면
        try:
            # 인자로 받은 Frame 송신
            self.acs_client_socket.sendall(frame_data)
            # 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS CLIENT] 송신 완료 : {frame_data}")
            print(f"[ACS CLIENT] HEX : {self.acs_protocol.byte_to_hex_string(frame_data)}")
            print("---------------------------------------------------------")
            # True return
            return True
        # 에러 발생 시 
        except Exception as error:
            # 디버그 문구 print
            print(f"[ACS Client] Frame 송신 중 에러 발생 : {error}")
            # 소켓 종료
            self.close()
            # False return
            return False

    # Frame 수신 함수 선언
    def recv_frame(self,timeout_sec=0.02):
        # 소켓이 없으면
        if self.acs_client_socket is None:
            # 디버그 문구 print
            print("[ACS Client] ACS 연결 안됨, Frame 수신 실패")
            # 빈 byte return
            return b""
        # 기존 timeout 저장
        old_timeout = self.acs_client_socket.gettimeout()
        # 에러가 없으면
        try:
            # 수신 timeout 설정
            self.acs_client_socket.settimeout(timeout_sec)
            # 데이터 수신
            recv_frame = self.acs_client_socket.recv(self.recv_buffer_size)
            # 수신 데이터가 없으면
            if not recv_frame:
                # 디버그 문구 print
                print("[ACS Client] 수신 데이터 없음, 소켓 종료")
                # 소켓 종료
                self.close()
                # 빈 바이트 return
                return b""
            # 정상 수신 하였으면 디버그 문구 print
            print("---------------------------------------------------------")
            print(f"[ACS Client] 데이터 수신 완료 : {recv_frame}")
            print(f"[ACS Client] HEX : {self.acs_protocol.byte_to_hex_string(recv_frame)}")
            print("---------------------------------------------------------")
            # 수신 데이터 return
            return recv_frame
        # timeout이면 데이터 없음으로 처리
        except socket.timeout:
            # 빈 바이트 return
            return b""
        # 에러 발생 시 
        except Exception as error:
            # 디버그 문구 print
            print(f"[ACS Client] Frame 수신 중 에러 발생 : {error}")
            # 소켓 종료
            self.close()
            # 빈 바이트 return 
            return b""
        # 최종적으로
        finally:
            # 소켓이 살아있으면 기존 timeout 복구
            if self.acs_client_socket is not None:
                try:
                    self.acs_client_socket.settimeout(old_timeout)
                except Exception:
                    pass
    # 수신 버퍼에서 Frame 추출 함수 선언
    def extract_frame(self):
        # recv_queue가 혹시 문자열이면 bytes로 보정
        if isinstance(self.recv_queue, str):
            self.recv_queue = self.recv_queue.encode("ascii", errors="ignore")
        # 수신한 버퍼에 STX가 없으면
        if self.stx not in self.recv_queue:
            # 쓰레기 데이터가 있으면 버퍼 초기화
            if len(self.recv_queue) > 0:
                self.recv_queue = b""
            # None return
            return None
        # 수신 버퍼에서 STX 위치 찾기
        stx_index = self.recv_queue.find(self.stx)
        # stx_index 앞에 데이터가 있으면
        if stx_index > 0:
            # stx_index 앞 데이터는 제거
            self.recv_queue = self.recv_queue[stx_index:]
            # stx_index를 0으로 설정
            stx_index = 0
        # 수신 버퍼에서 ETX 위치 찾기
        etx_index = self.recv_queue.find(self.etx, stx_index + 1)
        # ETX가 아직 없으면
        if etx_index == -1:
            # None return
            return None
        # STX에서 ETX까지 Frame 추출
        one_frame = self.recv_queue[stx_index:etx_index + 1]
        # 추출한 Frame 이후 데이터 queue에 남기기
        self.recv_queue = self.recv_queue[etx_index + 1:]
        # 추출한 Frame return
        return one_frame

    # Frame 1개 수신 함수 선언
    def recv_frame_once(self,timeout_sec=0.02):
        # 기존 버퍼에서 Frame 추출 시도
        one_frame = self.extract_frame()
        # Frame이 있으면
        if one_frame is not None:
            # Frame return
            return one_frame
        # Socket에서 데이터 1회 수신
        recv_data = self.recv_frame(timeout_sec=timeout_sec)
        # 수신 데이터가 있으면
        if recv_data:
            # 수신 버퍼에 누적
            self.recv_queue += recv_data
        # 누적 후 다시 Frame 추출 시도
        one_frame = self.extract_frame()
        # Frame return
        return one_frame
    # -------------------------------------- ACS 프로토콜 관련 송신 함수 ------------------------------------------------------
    # C Command 수신 함수 선언
    def recv_c_command_once(self,timeout_sec=0.02):
        # 수신 버퍼에서 Frame 1개 수신
        frame_data = self.recv_frame_once(timeout_sec=timeout_sec)
        # 수신된 Frame이 없으면
        if frame_data is None:
            # None return
            return None
        # 수신된 데이터가 있으면 C Command 파싱
        c_command = self.acs_protocol.parse_c_command(frame_data)
        # 파싱 결과 return
        return c_command

    # 기존 코드 호환용 C Command 수신 함수 선언
    def recv_c_command(self):
        # recv_c_command_once 호출 결과 return
        return self.recv_c_command_once(timeout_sec=0.02)

    # C Command 응답 송신 함수 선언
    def send_c_response(self,target_node,work_type="00"):
        # C 응답 Frame 생성
        c_response_frame_data = self.acs_protocol.build_c_command_response_frame(target_node,work_type)
        # C 응답 Frame 송신 후 결과 return
        return self.send_frame(c_response_frame_data)

    # S Command 송신 함수 선언
    def send_s_command(self,target_node,carry_flag,move_flag):
        # S Command Frame 생성
        s_frame_data = self.acs_protocol.build_s_command_frame(target_node,carry_flag,move_flag)
        # S 명령 송신 후 결과 return
        return self.send_frame(s_frame_data)

    # T Command 송신 함수 선언
    def send_t_command(self,location_node):
        # T Command Frame 생성
        t_frame_data = self.acs_protocol.build_t_command_frame(location_node)
        # T 명령 송신 후 결과 return
        return self.send_frame(t_frame_data)

    # L Command 송신 함수 선언
    def send_l_command(self,loading_node):
        # L Command Frame 생성
        l_frame_data = self.acs_protocol.build_l_command_frame(loading_node)
        # L 명령 송신 후 결과 return
        return self.send_frame(l_frame_data)

    # U Command 송신 함수 선언
    def send_u_command(self,unloading_node):
        # U Command Frame 생성
        u_frame_data = self.acs_protocol.build_u_command_frame(unloading_node)
        # U 명령 송신 후 결과 return
        return self.send_frame(u_frame_data)


    
        
    

