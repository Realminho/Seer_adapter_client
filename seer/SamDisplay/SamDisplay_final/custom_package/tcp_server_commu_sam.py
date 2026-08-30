# ----------------------- 범용 라이브러리 import -----------------------
import socket

# TCP 통신을 위한 TCP_commu 클래스 선언
class TCP_server_commu_sam:
    # 클래스 초기화 함수 선언
    def __init__(self,ip,port):
        # 인자로 받은 ip를 접속허용할 ip 변수에 저장
        self.ip = ip
        # 인자로 받은 port를 접속허용할 port 변수에 저장
        self.port = port
        # timeout 시간 설정(3초)
        self.timeout_sec = 3600
        # 서버 소켓 객체를 저장할 변수 선언
        self.server_socket = None
        # 클라이언트 소켓 객체를 저장할 변수 선언
        self.client_socket = None
        # 접속한 클라이언트 주소 및 포트를 저장할 변수 선언
        self.client_info = None
        # 수신 버퍼 선언
        self.rx_buffer = bytearray()
        # CMD_ID 변수 선언
        self.cmd_id = 1

    # 서버 소켓 시작 함수 선언
    def start_server(self):
        # 이미 서버 소켓이 열려있다면
        if self.server_socket is not None:
            # True return
            return True
        # 에러가 없다면
        try:
            # 서버 소켓 객체 생성
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 재실행 시 포트 재사용 허용
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 소켓 통신 타임아웃을 타임아웃 변수로 설정
            self.server_socket.settimeout(self.timeout_sec)
            # 인자로 받은 ip, port로 서버 소켓 바인드 준비
            # 바인드 : 해당 주소/포트에서 연결을 받을 준비
            self.server_socket.bind((self.ip,self.port))
            # listen 시작(client 1개 허용)
            self.server_socket.listen(1)
            # Server Open 디버그 문구 print
            print("----------------------------------------")
            print("---------- tcp 통신 Server Open -------- ")
            print("----------------------------------------")
            # True return
            return True
        # 에러가 발생하였으면
        except Exception as e:
            # Error 디버그 문구 print
            print(f"----- 서버 open 중 에러발생 : {e} -------")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # False return
            return False
        
    # 클라이언트 접속 대기 함수 선언
    def wait_client(self):
        # 서버 소켓이 없다면
        if self.server_socket is None:
            # 서버 open 수행
            server_ok = self.start_server()
            # 서버 시작이 실패하였다면
            if not server_ok:
                # tcp 소켓 닫기
                self.close_all()
                # 클라이언트 접속 대기도 실패
                return False
        # 에러가 없다면
        try:
            # 클라이언트 대기 문구 print
            print("---------- Client 접속 대기 중.. -------- ")
            print("----------------------------------------")
            # 클라이언트가 접속할 때까지 대기 후 클라이언트 소켓 객체 및 주소 반환
            self.client_socket, self.client_info = self.server_socket.accept()
            # 클라이언트 소켓 timeout 설정
            self.client_socket.settimeout(self.timeout_sec)
            # 수신 버퍼 초기화
            # 이전 통신 데이터가 남지 않도록 버퍼를 비우기
            self.rx_buffer = bytearray()
            # 클라이언트 접속 완료 문구 print
            print("---------- Client 접속 완료!! ---------- ")
            print("----------------------------------------")
            # Client 정보 print
            print(f"[Client] : {self.client_info}")
            print("----------------------------------------")
            # True return
            return True
        # timeout이 발생하였다면
        except socket.timeout:
            # timeout 문구 print
            print("------------- Time out 발생 ------------ ")
            print("----------------------------------------")
            self.close_all()
            return False
        # 그외 에러 발생 시 
        except Exception as e:
            # Error 디버그 문구 print
            print(f"---Client 접속 대기 중 에러발생 : {e} ----")
            print("----------------------------------------")
            # 전체 소켓 닫기
            self.close_all()
            # False return
            return False

    # CMD_ID 생성 함수 선언
    def make_cmd_id(self):
        # __init__ 함수에서 설정한 cmd_Id 변수를 cmd_id 형태로 변환
        cmd_id = f"S{self.cmd_id}"
        # 다음 요청을 위해 cmd_index 1 증가
        self.cmd_id += 1
        # 9999를 넘으면 다시 1부터 시작하도록 처리
        if self.cmd_id > 9999:
            self.cmd_id = 1
        # 변환한 cmd_id return
        return cmd_id
    
    # 문자열 데이터를 CRLF 형태로 송신하는 함수 선언
    def send_line(self, data):
        # 클라이언트 소켓이 없으면
        if self.client_socket is None:
            # 현재 연결된 클라이언트가 없다는 에러 문구 출력
            print("[ERROR] 연결된 Client가 없습니다.")
            # False return
            return False
        # 에러가 없으면
        try:
            # 인자로 받은 data가 str 형태라면
            if isinstance(data, str):
                # 문자열 끝에 \r\n 을 붙인 뒤 utf-8 bytes로 변환
                tx_data = (data + "\r\n").encode("utf-8")
            # str이 아니라면
            else:
                # data를 bytes로 변환
                tx_data = bytes(data)
                # bytes 데이터 끝에 CRLF가 없으면
                if not tx_data.endswith(b"\r\n"):
                    # CRLF를 붙여서 한 줄 메시지 형태로 설정
                    tx_data += b"\r\n"
            # 데이터 송신
            self.client_socket.sendall(tx_data)
            # 송신 완료 디버그 문구 출력
            print(f"-- 데이터 송신 완료 : {tx_data!r}")
            print("----------------------------------------")
            # True return
            return True
        # 송신 도중 에러 발생 시 
        except Exception as e:
            # 디버그 문구 print
            print(f"- Client로 데이터 송신 중 에러발생 : {e} --")
            print("----------------------------------------")
            # 소켓 자원 정리
            self.close_all()
            # False return
            return False
        
    # CRLF 기준으로 데이터를 수신하는 함수 선언
    def recv_data(self):
        # 아직 클라이언트 소켓이 없다면
        if self.client_socket is None:
            # 수신할 수 없으므로 빈 문자열 return
            return ""
        # 에러가 없다면
        try:
            # 무한 반복
            while True:
                # 수신 버퍼안에 \r\n이 있다면
                if b"\r\n" in self.rx_buffer:
                    # 첫 번째 CRLF 기준으로 앞부분(line)과 나머지(rest) 분리
                    line, _, rest = self.rx_buffer.partition(b"\r\n")
                    # 남은 데이터를 다시 수신 버퍼에 저장
                    self.rx_buffer = bytearray(rest)
                    # 잎부분 데이터를 utf-8 문자열로 변환
                    rx_text = line.decode("utf-8", errors="replace").strip()
                    # 수신 완료 문구 출력
                    print(f"- Client 데이터 수신 완료 : {rx_text}")
                    print("----------------------------------------")
                    # 수신한 문자열 return
                    return rx_text
                # 클라이언트 소켓에서 데이터 수신
                # 아직 CRLF가 안 들어왔으면 추가 데이터를 계속 수신
                chunk = self.client_socket.recv(1024)
                # 데이터가 없는 경우
                if not chunk:
                    # 상대방이 소켓을 끊었다는 안내 문구 출력
                    print("[INFO] Client 연결 종료 감지")
                    print("----------------------------------------")
                    # 소켓 자원 정리
                    self.close_all()
                    # 더 이상 받을 수 없으므로 빈 문자열 반환
                    return ""
                # 수신한 데이터 버퍼에 누적
                self.rx_buffer.extend(chunk)
        # timeout 발생 시
        except socket.timeout:
            # timeout 발생 문구 출력
            print("------------- Time out 발생 ------------")
            print("----------------------------------------")
            # 빈 문자열 return
            return ""
        # 그 외 예외 발생 시
        except Exception as e:
            # 에러 내용 출력
            print(f"-- Client 데이터 수신 중 에러발생 : {e} --")
            print("----------------------------------------")
            # 소켓 자원 정리
            self.close_all()
            # 수신 실패이므로 빈 문자열 return
            return ""
        
    # 수신 데이터 파싱 함수 선언
    def parse_message(self, data):
        # 인자로 받은 값이 빈 문자열이면 빈 dict 반환
        if not data:
            # 유효한 데이터가 없으므로 빈 dict 반환
            return {}
        # , 기준으로 data 분리
        parts = [item.strip() for item in data.split(",")]
        # 분리한 값이 최소 2개 미만이면 비정상
        if len(parts) < 2:
            # 비정상 형식 메시지라도 raw 데이터는 확인 가능하게 dict로 반환
            return {
                "raw": data,
                "cmd_id": "",
                "body": data,
                "parts": parts,
            }
        # 길이에 이상이 없다면 dict 형태로 정리해서 반환
        return {
            # 원본 메시지 문자열 저장
            "raw": data,
            # 첫 번째 값은 CMD_ID로 저장
            "cmd_id": parts[0],
            # 두 번째 이후 값들은 body 문자열로 저장
            "body": ",".join(parts[1:]),
            # 분리된 전체 항목 리스트 저장
            "parts": parts
        }
    
    # 명령 응답 확인 함수 선언
    def check_order_answer(self,answer_order_text,expected_cmd_id):
        # 수신한 명령 응답 파싱 함수로 분석
        msg = self.parse_message(answer_order_text)
        # 수신한 응답이 CMD_ID, STATUS 형태
        # 수신한 응답이 없거나 응답 길이가 2 미만이면
        if not msg or len(msg["parts"]) < 2:
            # 응답 형식이 맞지 않으면 False return
            return False, "ACK 형식 오류"
        # 응답의 첫 번째 항목에서 CMD_ID 추출
        cmd_id = msg["parts"][0]
        # 두 번째 항목은 STATUS 코드
        status = msg["parts"][1]
        # 만약 수신한 cmd_id가 송신한 명령의 cmd_id와 다르다면
        if cmd_id != expected_cmd_id:
            # 송신한 명령의 CMD_ID와 다르므로 False return
            return False, f"[명령 응답] CMD_ID 불일치 : {cmd_id}"
        # 만약 수신한 cmd_id가 일치하고 STATUS가 1이라면
        if status == "1":
            # 명 정상 수신 성공 처리
            return True, "ACCEPTED"
        # 오류로 처리할 status dict 형태로 선언
        status_map = {
            "2": "BUSY",
            "3": "INVALID_CMD",
            "4": "ROBOT_ERROR",
            "5": "NO_TEACHING_DATA",
            "6": "CMD_ERROR"}
        # 수신한 id가 일치하지만 status_map에 정의한 status 수신 시 False 및 status return
        return False, status_map.get(status, f"UNKNOWN_STATUS:{status}")
    
    # 작업 결과 확인 함수 선언
    def check_work_result(self,result_text,expected_cmd_id):
        # 수신한 작업 결과 파싱 함수로 분석
        msg = self.parse_message(result_text)
        # 수신한 응답이 없거나 응답 길이가 2 미만이면
        if not msg or len(msg["parts"]) < 2:
            # 응답 형식이 맞지 않으면 False return
            return False, "ACK 형식 오류"
        # 응답의 첫 번째 항목에서 CMD_ID 추출
        cmd_id = msg["parts"][0]
        # CMD_ID 이후 두 번째 이후 데이터를 body에 저장
        body = ",".join(msg["parts"][1:])
        # 만약 수신한 cmd_id가 송신한 명령의 cmd_id와 다르다면
        if cmd_id != expected_cmd_id:
            # 송신한 명령의 CMD_ID와 다르므로 False return
            return False, f"[작업 결과] CMD_ID 불일치 : {cmd_id}"
        # 성공 응답이 1000 이라면
        if body == "1000":
            # True return
            return True, msg["raw"]
        # 성공 응답 끝이 :1000 이라면
        if body.endswith(":1000"):
            # True return
            return True, msg["raw"]
        # 위 조건이 아니라면 False return
        return False, body
    
    # AQ/DP 명령 문자열 생성 함수 선언
    def build_task_request(self, cmd_id, task_type, tag, slot_no, stage_no, material_id):
        # 작업 요청 메시지 return
        return f"{cmd_id},{task_type},{tag},{slot_no},{stage_no},{material_id}"
    
    # AQ/DP 작업 시작 문자열 생성 함수 선언
    def build_task_start(self,cmd_id, status_code):
        # 작업 요청 메시지 return
        return f"{cmd_id},{status_code}"

    # HOME 요청 문자열 생성 함수 선언
    def build_home_request(self, cmd_id):
        # HOME 요청용 메시지 return
        return f"{cmd_id},HOME"

    # HEALTHCHECK 요청 문자열 생성 함수 선언
    def build_healthcheck_request(self, cmd_id):
        # HEALTHCHECK 요청 메시지 return
        return f"{cmd_id},HEALTHCHECK"

    # SCAN 요청 문자열 생성 함수 선언
    def build_scan_request(self, cmd_id):
        # SCAN 요청용 메시지 return
        return f"{cmd_id},SCAN"
    
     # HEALTHCHECK 요청 함수 선언
    def request_healthcheck(self):
        # CMD_ID 생성
        cmd_id = self.make_cmd_id()
        # HEALTHCHECK 요청 문자열 생성
        request_text = self.build_healthcheck_request(cmd_id)
        # 요청 송신 실패 시
        if not self.send_line(request_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True, "HEALTHCHECK_REQ_OK",cmd_id

    # Home 요청 함수 선언
    def request_home(self):
        # cmd_id 생성
        cmd_id = self.make_cmd_id()
        # Home 요청 메시지 생성
        request_message = self.build_home_request(cmd_id)
        # 요청 송신 후 실패 시
        if not self.send_line(request_message):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True, "HOME_REQ_OK",cmd_id
    
    # SCAN 요청 함수 선언
    def request_scan(self):
        # CMD_ID 생성
        cmd_id = self.make_cmd_id()
        # SCAN 요청 문자열 생성
        request_text = self.build_scan_request(cmd_id)
        # 요청 송신 실패 시
        if not self.send_line(request_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True, "SCAN_REQ_OK",cmd_id
    
    # AQ 요청 함수 선언
    def request_aq(self, tag, slot_no, stage_no, material_id):
        # CMD_ID 생성
        cmd_id = self.make_cmd_id()
        # AQ 요청 문자열 생성
        request_text = self.build_task_request(cmd_id, "AQ", tag, slot_no, stage_no, material_id)
        # 요청 송신 실패 시
        if not self.send_line(request_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True,"AQ_REQ_OK",cmd_id
    
    # AQ 작업 시작 요청 함수
    def start_aq(self,cmd_id):
        # AQ 작업 시작 문자열 생성
        start_work_text = self.build_task_start(cmd_id, 100)
        # 요청 송신 실패 시
        if not self.send_line(start_work_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True,"AQ_START_OK",cmd_id
    
    # AQ 작업 실패 반응 함수
    def fail_aq(self,cmd_id):
        # AQ 작업 실패 문자열 생성
        fail_work_text = self.build_task_start(cmd_id, 200)
        # 요청 송신 실패 시
        if not self.send_line(fail_work_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True,"AQ_FAIL_OK",cmd_id
    
    # DP 요청 함수 선언
    def request_dp(self, tag, slot_no, stage_no, material_id):
        # CMD_ID 생성
        cmd_id = self.make_cmd_id()
        # DP 요청 문자열 생성
        request_text = self.build_task_request(cmd_id, "DP", tag, slot_no, stage_no, material_id)
        # 요청 송신 실패 시
        if not self.send_line(request_text):
            # False return
            return False, "REQ_SEND_FAIL", None
        # 성공 시 True return
        return True, "DP_REQ_OK",cmd_id
    
    # HEALTHCHECK 응답 대기 함수 선언
    def wait_healthcheck_result(self, cmd_id):
        # Dobot 응답 수신
        result_text = self.recv_data()
        # 수신 실패 시
        if not result_text:
            return False, "HEALTHCHECK 응답 수신 실패"
        # 작업 결과 판별 후 return
        return self.check_work_result(result_text, cmd_id)
    
    # HOME 응답 대기 함수 선언
    def wait_home_result(self, cmd_id):
        # Dobot 응답 수신
        result_text = self.recv_data()
        # 수신 실패 시
        if not result_text:
            return False, "HOME 응답 수신 실패"
        # 작업 결과 판별 후 return
        return self.check_work_result(result_text, cmd_id)
    
     # 작업 요청 응답 대기 함수 선언
    def wait_task_order_answer(self, cmd_id):
        # Dobot 응답 수신
        answer_order_text = self.recv_data()
        # 수신 실패 시
        if not answer_order_text:
            return False, "작업 요청 응답 수신 실패"
        # 명령 응답 체크
        answer_ok, answer_msg = self.check_order_answer(answer_order_text, cmd_id)
        # 명령 응답이 비정상이면
        if not answer_ok:
            return False, f"작업 요청 응답 실패 : {answer_msg}"
        # 성공 시 True return
        return True, answer_msg
    
    # 작업 결과 대기 함수 선언
    def wait_task_work_result(self, cmd_id):
        # Dobot 메시지 수신
        result_text = self.recv_data()
        # 수신 실패 시
        if not result_text:
            return False, "작업 최종 결과 수신 실패"
        # 작업 결과 메시지 체크
        result_ok, result_msg = self.check_work_result(result_text, cmd_id)
        # 최종 결과가 비정상이면
        if not result_ok:
            return False, f"작업 최종 결과 실패 : {result_msg}"
        # 성공 시 True return
        return True, result_msg
    
    # 모든 소켓 닫는 함수 선언
    def close_all(self):
        # 클라이언트 소켓이 있으면
        if self.client_socket is not None:
            # 에러가 없으면
            try:
                # 클라이언트 소켓 닫기
                self.client_socket.close()
            # 에러 발생 시
            except Exception:
                # 무시
                pass
            # 닫은 뒤 클라이언트 소켓 비우기
            self.client_socket = None
        # 만약 서버 소켓이 있으면
        if self.server_socket is not None:
            # 에러가 없으면
            try:
                # 서버 소켓 닫기
                self.server_socket.close()
            # 에러 발생 시 
            except Exception:
                # 무신
                pass
            # 닫은 뒤 서버 소켓 비우기
            self.server_socket = None
        # 클라이언트 정보 초기화
        self.client_info = None
        # 수신 버퍼 초기화
        self.rx_buffer = bytearray()