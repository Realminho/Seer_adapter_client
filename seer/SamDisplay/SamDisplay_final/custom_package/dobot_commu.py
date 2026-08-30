# ----------------------- 범용 라이브러리 import -----------------------
# TCP/IP 소켓 통신을 사용하기 위한 socket 라이브러리 import
import socket


# Dobot TCP Server 통신 클래스 선언
class Dobot_Commu:
    # 클래스 초기화 함수 선언
    def __init__(self, ip="0.0.0.0", port=12321, timeout_sec=3600, max_cmd_id=9999):
        # 서버로 바인드할 IP 주소 저장
        # 모든 네트워크 인터페이스에서 접속을 받으려면 "0.0.0.0"을 사용
        self.ip = ip
        # 서버로 열 포트 번호 저장
        self.port = port
        # 소켓 타임아웃 변수 선언
        self.timeout_sec = timeout_sec
        # CMD_ID 최대 번호 저장 변수 선언
        self.max_cmd_id = max_cmd_id
        # 서버 소켓 객체를 저장할 변수 선언
        self.server_socket = None
        # 클라이언트 소켓 객체를 저장할 변수 선언
        self.client_socket = None
        # 접속한 클라이언트 주소 정보를 저장할 변수 선언
        self.client_info = None
        # CRLF 단위 수신 처리를 위한 버퍼 선언
        self.rx_buffer = bytearray()
        # CMD_ID 숫자 인덱스 선언
        self.cmd_index = 1
        # STATUS 코드 dict 선언
        self.status_map = {
            "1": "IDLE",
            "2": "BUSY",
            "3": "INVALID_CMD",
            "4": "ROBOT_ERROR",
            "5": "NO_TEACHING_DATA",
            "6": "CMD_ERROR"}
        # RESULT 코드 dict 선언
        self.result_map = {
            "1000": "SUCCESS",
            "2000": "FAIL"}
    # ------------------------------------------------------------------
    # 소켓 연결 관련 함수 선언
    # ------------------------------------------------------------------
    # 서버 소켓 시작 함수 선언
    def start_server(self):
        # 이미 서버 소켓이 열려있으면 True return
        if self.server_socket is not None:
            return True
        # 에러가 없으면
        try:
            # TCP 소켓 객체 생성
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 프로그램 재시작 시 같은 포트를 바로 다시 사용할 수 있도록 설정
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 서버 소켓 타임아웃 설정
            self.server_socket.settimeout(self.timeout_sec)
            # 지정한 IP, PORT로 바인드
            self.server_socket.bind((self.ip, self.port))
            # 클라이언트 접속 대기 시작
            # Dobot 1대 접속 기준, backlog 1로 설정
            self.server_socket.listen(1)
            # 서버 open 로그 출력
            print("----------------------------------------")
            print("---------- Dobot TCP Server Open -------")
            print(f"[SERVER] {self.ip}:{self.port}")
            print("----------------------------------------")
            # 성공 시 True return
            return True
        # 서버 open 중 에러 발생 시
        except Exception as error:
            # 에러 로그 출력
            print("----------------------------------------")
            print(f"[ERROR] 서버 open 중 에러 발생 : {error}")
            print("----------------------------------------")
            # 소켓 전체 정리
            self.close_all()
            # 실패 시 False return
            return False

    # 클라이언트 접속 대기 함수 선언
    def wait_client(self):
        # 서버 소켓이 아직 없으면 서버 소켓 먼저 open
        if self.server_socket is None:
            # 서버 시작 함수 호출
            server_ok = self.start_server()
            # 서버 시작 실패 시 False return
            if not server_ok:
                return False
        # 에러가 없으면
        try:
            # 접속 대기 로그 출력
            print("---------- Robot Client 접속 대기 중 ----")
            print("----------------------------------------")
            # 클라이언트 접속 수락
            self.client_socket, self.client_info = self.server_socket.accept()
            # 클라이언트 소켓 타임아웃 설정
            self.client_socket.settimeout(self.timeout_sec)
            # 이전 데이터가 남지 않도록 수신 버퍼 초기화
            self.rx_buffer = bytearray()
            # 접속 완료 로그 출력
            print("---------- Robot Client 접속 완료 -------")
            print(f"[CLIENT] {self.client_info}")
            print("----------------------------------------")
            # 성공 시 True return
            return True
        # 접속 대기 중 timeout 발생 시
        except socket.timeout:
            # timeout 로그 출력
            print("----------------------------------------")
            print("[ERROR] Client 접속 대기 Time out")
            print("----------------------------------------")
            # 소켓 전체 정리
            self.close_all()
            # 실패 시 False return
            return False
        # 그 외 예외 발생 시
        except Exception as error:
            # 에러 로그 출력
            print("----------------------------------------")
            print(f"[ERROR] Client 접속 대기 중 에러 발생 : {error}")
            print("----------------------------------------")
            # 소켓 전체 정리
            self.close_all()
            # 실패 시 False return
            return False

    # 현재 Dobot Client 연결 여부 확인 함수 선언
    def is_connected(self):
        # 클라이언트 소켓이 있으면 True, 없으면 False return
        return self.client_socket is not None

    # 모든 소켓 닫기 함수 선언
    def close_all(self):
        # 클라이언트 소켓이 있으면 닫기
        if self.client_socket is not None:
            try:
                self.client_socket.close()
            except Exception:
                pass
            self.client_socket = None
        # 서버 소켓이 있으면 닫기
        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None
        # 클라이언트 정보 초기화
        self.client_info = None
        # 수신 버퍼 초기화
        self.rx_buffer = bytearray()
        # 종료 로그 출력
        print("----------------------------------------")
        print("[INFO] Dobot TCP Socket Close")
        print("----------------------------------------")

    # ------------------------------------------------------------------
    # 기본 송수신 함수 선언
    # ------------------------------------------------------------------
    # CMD_ID 생성 함수 선언
    def make_cmd_id(self):
        # 현재 번호로 CMD_ID 문자열 생성
        cmd_id = f"S{self.cmd_index}"
        # 다음 메시지를 위해 번호 1 증가
        self.cmd_index += 1
        # 최대 번호를 넘으면 다시 1부터 시작
        if self.cmd_index > self.max_cmd_id:
            self.cmd_index = 1
        # 생성한 CMD_ID return
        return cmd_id

    # 한 줄 메시지 송신 함수 선언
    def send_data(self, data):
        # 클라이언트 소켓이 없으면 송신 불가
        if self.client_socket is None:
            print("----------------------------------------")
            print("[ERROR] 연결된 Robot Client가 없습니다.")
            print("----------------------------------------")
            return False
        # 에러가 없으면
        try:
            # 문자열이면 CRLF를 붙이고 utf-8 bytes로 변환
            if isinstance(data, str):
                tx_data = (data + "\r\n").encode("utf-8")
            # bytes 또는 bytearray이면 bytes로 변환 후 CRLF 확인
            else:
                tx_data = bytes(data)
                # CRLF가 없으면 추가
                if not tx_data.endswith(b"\r\n"):
                    tx_data += b"\r\n"
            # 데이터 전체 송신
            self.client_socket.sendall(tx_data)
            # 송신 로그 출력
            print(f"[TX] {tx_data!r}")
            print("----------------------------------------")
            # 성공 시 True return
            return True
        # 송신 중 예외 발생 시
        except Exception as error:
            # 에러 로그 출력
            print("----------------------------------------")
            print(f"[ERROR] 데이터 송신 중 에러 발생 : {error}")
            print("----------------------------------------")
            # 소켓 전체 정리
            self.close_all()
            # 실패 시 False return
            return False

    # CRLF 기준 한 줄 수신 함수 선언
    def recv_data(self):
        # 클라이언트 소켓이 없으면 빈 문자열 return
        if self.client_socket is None:
            return ""
        # 에러가 없으면
        try:
            # CRLF가 나올 때까지 반복
            while True:
                # 수신 버퍼 안에 CRLF가 있으면 한 줄 분리
                if b"\r\n" in self.rx_buffer:
                    # 첫 번째 CRLF 기준으로 line과 rest 분리
                    line, _, rest = self.rx_buffer.partition(b"\r\n")
                    # 남은 데이터는 다시 버퍼에 저장
                    self.rx_buffer = bytearray(rest)
                    # bytes를 문자열로 변환
                    rx_text = line.decode("utf-8", errors="replace").strip()
                    # 빈 줄이면 무시하고 다음 데이터 계속 대기
                    if rx_text == "":
                        print("[RX] 빈 줄 수신 - 무시")
                        print("----------------------------------------")
                        continue
                    # 수신 로그 출력
                    print(f"[RX] {rx_text}")
                    print("----------------------------------------")
                    # 수신 문자열 return
                    return rx_text
                # 소켓에서 데이터 수신
                chunk = self.client_socket.recv(1024)
                # 디버깅용 raw 수신 데이터 출력
                print(f"[RX RAW] {chunk!r}")
                # 수신 데이터가 없으면 상대방 연결 종료로 판단
                if not chunk:
                    print("----------------------------------------")
                    print("[INFO] Dobot Client 연결 종료 감지")
                    print("----------------------------------------")
                    # 소켓 전체 정리
                    self.close_all()
                    # 빈 문자열 return
                    return ""
                # 수신한 데이터를 버퍼에 누적
                self.rx_buffer.extend(chunk)
        # 수신 timeout 발생 시
        except socket.timeout:
            print("----------------------------------------")
            print("[ERROR] 데이터 수신 Time out")
            print("----------------------------------------")
            return ""
        # 그 외 예외 발생 시
        except Exception as error:
            print("----------------------------------------")
            print(f"[ERROR] 데이터 수신 중 에러 발생 : {error}")
            print("----------------------------------------")
            # 소켓 전체 정리
            self.close_all()
            return ""

    # ------------------------------------------------------------------
    # 메시지 생성/파싱 함수 선언
    # ------------------------------------------------------------------
    # 수신 메시지 파싱 함수 선언
    def parse_message(self, data):
        # 빈 데이터이면 빈 dict return
        if not data:
            return {}
        # 콤마 기준으로 문자열 분리
        parts = [item.strip() for item in data.split(",")]
        # CMD_ID만 있거나 형식이 이상한 경우도 raw data 확인 가능하게 return
        if len(parts) < 2:
            return {
                "raw": data,
                "cmd_id": parts[0] if parts else "",
                "body": "",
                "parts": parts}
        # 정상 파싱 결과 return
        return {
            "raw": data,
            "cmd_id": parts[0],
            "body": ",".join(parts[1:]),
            "parts": parts}

    # 요청 명령 메시지 생성 함수 선언
    def build_request(self, cmd_id, task_type, tag=None):
        # task_type을 대문자 문자열로 변환
        task_type = str(task_type).upper().strip()
        # tag가 필요한 작업이면 CMD_ID,TASK,TAG 형식으로 생성
        if tag is not None and str(tag).strip() != "":
            return f"{cmd_id},{task_type},{tag}"
        # tag가 없는 작업이면 CMD_ID,TASK 형식으로 생성
        return f"{cmd_id},{task_type}"

    # AMR 작업 결과 확인 응답 메시지 생성 함수 선언
    def build_task_answer(self, cmd_id, answer):
        # answer 값을 대문자로 변환
        answer = str(answer).upper().strip()
        # OK 또는 NG만 허용
        if answer not in ("OK", "NG"):
            # OK, NG가 아니면 Error 발생
            raise ValueError("answer는 'OK' 또는 'NG'만 사용 가능")
        # CMD_ID,OK 또는 CMD_ID,NG 형식으로 return
        return f"{cmd_id},{answer}"

    # ------------------------------------------------------------------
    # 응답 판별 함수 선언
    # ------------------------------------------------------------------
    # ACK 메시지 확인 함수 선언
    def check_ack(self, ack_text, expected_cmd_id):
        # 수신 메시지 파싱
        msg = self.parse_message(ack_text)
        # 형식이 맞지 않으면 실패 return
        if not msg or len(msg.get("parts", [])) < 2:
            return False, "ACK_FORMAT_ERROR", msg
        # CMD_ID 추출
        cmd_id = msg["parts"][0]
        # STATUS 코드 추출
        status = msg["parts"][1]
        # CMD_ID가 예상값과 다르면 실패 return
        if cmd_id != expected_cmd_id:
            return False, f"ACK_CMD_ID_MISMATCH:{cmd_id}", msg
        # STATUS 1이면 정상 수신 처리
        if status == "1":
            return True, self.status_map.get(status, "IDLE"), msg
        # STATUS 1이 아니면 실패 처리
        return False, self.status_map.get(status, f"UNKNOWN_STATUS:{status}"), msg

    # RESULT 메시지 확인 함수 선언
    def check_task_result(self, result_text, expected_cmd_id, expected_task_type=None):
        # 수신 메시지 파싱
        msg = self.parse_message(result_text)
        # 형식이 맞지 않으면 실패 return
        if not msg or len(msg.get("parts", [])) < 2:
            return False, "RESULT_FORMAT_ERROR", msg
        # CMD_ID 추출
        cmd_id = msg["parts"][0]
        # CMD_ID가 예상값과 다르면 실패 return
        if cmd_id != expected_cmd_id:
            return False, f"RESULT_CMD_ID_MISMATCH:{cmd_id}", msg
        # parts 추출
        parts = msg["parts"]
        # --------------------------------------------------
        # 형식 1 : S1,HEALTHCHECK,1000
        # 형식 2 : S2,HOME,1000
        # 형식 3 : S3,AQ,1000
        # 형식 4 : S4,DP,1000
        # --------------------------------------------------
        if len(parts) >= 3:
            # Task 이름 추출
            task_name = parts[1].strip().upper()
            # Result Code 추출
            result_code = parts[2].strip()
            # 특정 task_type을 기대하는 경우 task 이름 확인
            if expected_task_type is not None:
                expected_task_name = str(expected_task_type).upper().strip()
                # 기대한 Task 이름과 실제 Task 이름이 다르면 실패
                if task_name != expected_task_name:
                    return False, f"RESULT_TASK_MISMATCH:{task_name}", msg
            # 1000이면 성공
            if result_code == "1000":
                return True, self.result_map.get(result_code, "SUCCESS"), msg
            # 1000이 아니면 실패
            return False, self.result_map.get(
                result_code,
                f"ERROR_CODE:{task_name},{result_code}"
            ), msg
        # CMD_ID 이후 body 추출
        body = msg["body"]
        # --------------------------------------------------
        # 형식 5 : S1,HEALTHCHECK:1000
        # 형식 6 : S2,HOME:1000
        # --------------------------------------------------
        if ":" in body:
            # task_name과 result_code 분리
            task_name, result_code = body.split(":", 1)
            # 문자열 정리
            task_name = task_name.strip().upper()
            result_code = result_code.strip()
            # 특정 task_type을 기대하는 경우 task 이름 확인
            if expected_task_type is not None:
                expected_task_name = str(expected_task_type).upper().strip()
                # 기대한 Task 이름과 실제 Task 이름이 다르면 실패
                if task_name != expected_task_name:
                    return False, f"RESULT_TASK_MISMATCH:{task_name}", msg
            # 1000이면 성공
            if result_code == "1000":
                return True, self.result_map.get(result_code, "SUCCESS"), msg
            # 1000이 아니면 실패
            return False, self.result_map.get(
                result_code,
                f"ERROR_CODE:{task_name},{result_code}"
            ), msg

        # --------------------------------------------------
        # 형식 7 : S1,1000
        # --------------------------------------------------
        if body == "1000":
            return True, self.result_map.get(body, "SUCCESS"), msg
        # 그 외는 실패
        return False, self.result_map.get(body, f"ERROR_CODE:{body}"), msg

    # ------------------------------------------------------------------
    # 요청 송신 함수
    # ------------------------------------------------------------------
    # 공통 요청 함수 선언
    def request_task(self, task_type, tag=None):
        # CMD_ID 생성
        cmd_id = self.make_cmd_id()
        # 요청 메시지 생성
        request_text = self.build_request(cmd_id, task_type, tag)
        # 요청 메시지 송신
        if not self.send_data(request_text):
            return False, "REQ_SEND_FAIL", None
        # 요청 송신 성공 return
        return True, f"{str(task_type).upper()}_REQ_OK", cmd_id

    # AQ 요청 함수 선언
    def request_aq(self, tag):
        # AQ는 Material을 AMR 상부에 올려놓는 작업 요청
        return self.request_task("AQ", tag)

    # DP 요청 함수 선언
    def request_dp(self, tag):
        # DP는 AMR 상부 Material을 외부 장비로 내려놓는 작업 요청
        return self.request_task("DP", tag)

    # CV 작업(컨베이어에서 트레이 테이블로 옮기는 작업) 요청 함수 선언
    def request_cv(self, tag):
        # CV는 컨베이어에 있는 트레이를 테이블로 옮기는 작업
        return self.request_task("CV", tag)

    # STARTBT(시작버튼 누르기) 요청 함수 선언
    def request_startbt(self, tag):
        # STARTBT는 시작 버튼 누르는 작업
        return self.request_task("STARTBT", tag)

    # ENDBT(종료버튼 누르기) 요청 함수 선언
    def request_endbt(self, tag):
        # ENDBT는 종료 버튼 누르는 작업
        return self.request_task("ENDBT", tag)

    # SCAN 요청 함수 선언
    def request_scan(self):
        # SCAN은 AMR 상부 Material 정보 스캔 작업 요청
        return self.request_task("SCAN")

    # HOME 요청 함수 선언
    def request_home(self):
        # HOME은 AMR 주행 전 Robot End Effector가 안전 위치에 있는지 확인
        return self.request_task("HOME")

    # HEALTHCHECK 요청 함수 선언
    def request_healthcheck(self):
        # HEALTHCHECK는 Robot/Gripper/Vision 상태 확인
        return self.request_task("HEALTHCHECK")

    # 작업 결과에 대한 OK 응답 송신 함수 선언
    def send_answer_ok(self, cmd_id):
        # CMD_ID,OK 메시지 생성
        answer_text = self.build_task_answer(cmd_id, "OK")
        # 메시지 송신 결과 return
        return self.send_data(answer_text)

    # 작업 결과에 대한 NG 응답 송신 함수 선언
    def send_answer_ng(self, cmd_id):
        # CMD_ID,NG 메시지 생성
        answer_text = self.build_task_answer(cmd_id, "NG")
        # 메시지 송신 결과 return
        return self.send_data(answer_text)

    # 작업 결과에 대한 OK/NG 응답 송신 함수 선언
    def send_answer_by_result(self, cmd_id, result_ok):
        # result_ok가 True이면 OK 송신
        if result_ok:
            return self.send_answer_ok(cmd_id)
        # result_ok가 False이면 NG 송신
        return self.send_answer_ng(cmd_id)

    # 컨베이어 트레이 테이블로 옮기는 작업 수행


    # ------------------------------------------------------------------
    # 응답 대기 함수
    # ------------------------------------------------------------------
    # ACK 대기 함수 선언
    def wait_ack(self, cmd_id):
        # Robot ACK 수신
        ack_text = self.recv_data()
        # 수신 실패 시
        if not ack_text:
            return False, "ACK_RECV_FAIL"
        # ACK 판별
        ack_ok, ack_msg, _ = self.check_ack(ack_text, cmd_id)
        # ACK 결과 return
        return ack_ok, ack_msg

    # 작업 결과 대기 함수 선언
    def wait_result(self, cmd_id, expected_task_type=None, send_answer=True):
        # Robot RESULT 수신
        result_text = self.recv_data()
        # 수신 실패 시
        if not result_text:
            # 결과 수신 실패도 NG 응답을 보낼 수 있으면 송신
            if send_answer:
                self.send_answer_ng(cmd_id)
            return False, "RESULT_RECV_FAIL"
        # RESULT 판별
        result_ok, result_msg, _ = self.check_task_result(
            result_text,
            cmd_id,
            expected_task_type)
        # 작업성 명령이면 결과 확인 후 OK/NG 응답 송신
        if send_answer:
            self.send_answer_by_result(cmd_id, result_ok)
        # RESULT 결과 return
        return result_ok, result_msg

    # 작업 결과를 수신하고 받은 메시지를 그대로 다시 송신하는 함수 선언
    def wait_result_and_echo(self, cmd_id, expected_task_type=None):
        # Robot RESULT 수신
        result_text = self.recv_data()
        # 수신 실패 시
        if not result_text:
            return False, "RESULT_RECV_FAIL"
        # RESULT 판별
        result_ok, result_msg, _ = self.check_task_result(
            result_text,
            cmd_id,
            expected_task_type)
        # 수신한 RESULT 메시지를 그대로 다시 송신
        # 예: S3,AQ,1000 수신 시 S3,AQ,1000 그대로 송신
        echo_ok = self.send_data(result_text)
        # Echo 송신 실패 시
        if not echo_ok:
            return False, "RESULT_ECHO_SEND_FAIL"
        # RESULT 결과 return
        return result_ok, result_msg

    # HOME 결과 대기 함수 선언
    def wait_home_result(self, cmd_id):
        return self.wait_result(
            cmd_id,
            expected_task_type="HOME",
            send_answer=False)

    # HEALTHCHECK 결과 대기 함수 선언
    def wait_healthcheck_result(self, cmd_id):
        return self.wait_result(
            cmd_id,
            expected_task_type="HEALTHCHECK",
            send_answer=False)

    # SCAN 결과 대기 함수 선언
    def wait_scan_result(self, cmd_id, send_answer=True):
        return self.wait_result(
            cmd_id,
            expected_task_type=None,
            send_answer=send_answer)

    # ------------------------------------------------------------------
    # 한 번에 처리하는 편의 함수 선언
    # ------------------------------------------------------------------
    # 작업 명령 전체 프로세스 수행 함수 선언
    def do_work_task(self, task_type, tag):
        # 작업 명령 송신
        req_ok, req_msg, cmd_id = self.request_task(task_type, tag)
        # 요청 송신 실패 시 False return
        if not req_ok:
            return False, req_msg, cmd_id
        # ACK 대기
        # 예: S3,1 수신
        ack_ok, ack_msg = self.wait_ack(cmd_id)
        # ACK 실패 시 NG 응답 후 False return
        if not ack_ok:
            self.send_answer_ng(cmd_id)
            return False, f"ACK_FAIL:{ack_msg}", cmd_id
        # ACK 성공 시 즉시 OK 응답 송신
        # 예: S3,1 수신 후 S3,OK 송신
        answer_ok = self.send_answer_ok(cmd_id)
        # OK 응답 송신 실패 시 False return
        if not answer_ok:
            return False, "ACK_OK_SEND_FAIL", cmd_id
        # 작업 결과 대기 후 받은 RESULT를 그대로 Echo 송신
        # 예: S3,AQ,1000 수신 후 S3,AQ,1000 송신
        result_ok, result_msg = self.wait_result_and_echo(
            cmd_id,
            expected_task_type=task_type)
        # 결과 return
        return result_ok, result_msg, cmd_id

    # AQ 전체 프로세스 수행 함수 선언
    def do_aq(self, tag):
        # AQ 요청부터 결과 응답까지 전체 수행
        return self.do_work_task("AQ", tag)

    # DP 전체 프로세스 수행 함수 선언
    def do_dp(self, tag):
        # DP 요청부터 결과 응답까지 전체 수행
        return self.do_work_task("DP", tag)

    # CV 전체 프로세스 수행 함수 선언
    def do_cv(self, tag):
        # CV 요청부터 결과 응답까지 전체 수행
        return self.do_work_task("CV", tag)

    # STARTBT 전체 프로세스 수행 함수 선언
    def do_startbt(self, tag):
        # STARTBT 요청부터 결과 응답까지 전체 수행
        return self.do_work_task("STARTBT", tag)

    # ENDBT 전체 프로세스 수행 함수 선언
    def do_endbt(self, tag):
        # ENDBT 요청부터 결과 응답까지 전체 수행
        return self.do_work_task("ENDBT", tag)

    # HOME 확인 전체 수행 함수 선언
    def do_home_check(self):
        # HOME 요청 송신
        req_ok, req_msg, cmd_id = self.request_home()
        # 요청 실패 시 return
        if not req_ok:
            return False, req_msg, cmd_id
        # HOME 결과 대기
        result_ok, result_msg = self.wait_home_result(cmd_id)
        # 결과 return
        return result_ok, result_msg, cmd_id

    # HEALTHCHECK 전체 수행 함수 선언
    def do_healthcheck(self):
        # HEALTHCHECK 요청 송신
        req_ok, req_msg, cmd_id = self.request_healthcheck()
        # 요청 실패 시 return
        if not req_ok:
            return False, req_msg, cmd_id
        # HEALTHCHECK 결과 대기
        result_ok, result_msg = self.wait_healthcheck_result(cmd_id)
        # 결과 return
        return result_ok, result_msg, cmd_id
