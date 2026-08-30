# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# Thread 처리를 위한 threading 라이브러리 import
import threading

# ----------------------- 사용자 정의 라이브러리 import -----------------------
# ACS Client 클래스 import
from sam_acs_package.sam_acs_client import ACS_Client
# QR Reader 클래스 import
from sam_acs_package.sam_qr_reader import QR_Reader
# SEER AMR 제어 클래스 import
from custom_package.seer_commu import SEER_commu


# ACS Controller 클래스 선언
class ACS_Controller:
    # C 명령 대기 상태 선언
    STATE_WAIT_C_COMMAND = "WAIT_C_COMMAND"
    # 주행 중 상태 선언
    STATE_DRIVING = "DRIVING"
    # 에러 상태 선언
    STATE_ERROR = "ERROR"
    # SEER Task 주행 중 상태 선언
    TASK_STATUS_RUNNING = 2
    # SEER Task 정지 상태 선언
    # 일반적으로 SUSPENDED / BLOCKED / STOPPED 계열을 3으로 사용
    TASK_STATUS_STOPPED = 3
    # SEER Task 완료 상태 선언
    TASK_STATUS_COMPLETED = 4

    # 클래스 초기화 함수 선언
    def __init__(self,acs_ip,acs_port,amr_id="444",
        carry_flag="0",
        s_period_sec=1.0,
        t_period_sec=None,
        c_recv_timeout_sec=0.2,
        status_check_period_sec=0.2,
        qr_read_period_sec=0.05,
        show_qr_window=True):
        # ACS IP 저장
        self.acs_ip = acs_ip
        # ACS Port 저장
        self.acs_port = acs_port
        # AMR ID 저장
        self.amr_id = str(amr_id).strip().zfill(3)
        # 현재 적재 상태 저장
        self.current_carry_flag = str(carry_flag).strip()
        # S 명령 송신 주기 저장
        self.s_period_sec = s_period_sec
        # T 명령 송신 주기 저장
        # T 주기가 따로 입력되지 않으면 S 주기와 동일하게 사용
        self.t_period_sec = s_period_sec if t_period_sec is None else t_period_sec
        # C 명령 수신 timeout 저장
        self.c_recv_timeout_sec = c_recv_timeout_sec
        # SEER 상태 확인 주기 저장
        self.status_check_period_sec = status_check_period_sec
        # QR 읽기 루프 주기 저장
        self.qr_read_period_sec = qr_read_period_sec
        # QR 화면 표시 여부 저장
        self.show_qr_window = show_qr_window
        # ACS Client 객체 생성
        self.acs_client = ACS_Client(acs_ip=self.acs_ip,acs_port=self.acs_port,amr_id=self.amr_id)
        # QR Reader 객체 생성
        self.qr_reader = QR_Reader()
        # SEER 제어 객체 생성
        self.seer = SEER_commu()
        # 현재 Controller 상태 저장
        self.state = self.STATE_WAIT_C_COMMAND
        # 현재 target_node 저장 변수 선언
        self.current_target_node = "0000"
        # 현재 move_flag 저장 변수 선언
        self.current_move_flag = "0"
        # 현재 SEER landmark 저장 변수 선언
        self.current_landmark = None
        # 주행 시작 시간 저장 변수 선언
        self.drive_start_time = None
        # 마지막 QR location 저장 변수 선언
        self.last_qr_location_node = None
        # 마지막 S 명령 송신 시간 저장
        self.last_s_send_time = 0.0
        # 마지막 T 명령 송신 시간 저장
        self.last_t_send_time = 0.0
        # 종료 요청 Event 선언
        self.stop_event = threading.Event()
        # 주행 중 여부 Event 선언
        self.driving_event = threading.Event()
        # 상태값 동시 접근 방지 Lock 선언
        self.state_lock = threading.Lock()
        # ACS 송신 동시 접근 방지 Lock 선언
        # S/T/C 응답이 동시에 send_frame을 호출하지 않도록 보호
        self.send_lock = threading.Lock()
        # Thread 객체 저장 변수 선언
        self.s_report_thread = None
        self.qr_t_report_thread = None
        self.status_check_thread = None

    # carry_flag 설정 함수 선언
    def set_carry_flag(self, carry_flag):
        # 문자열 변환
        carry_flag = str(carry_flag).strip()
        # 허용값이 아니면
        if carry_flag not in ["0", "1", "2", "3"]:
            # 디버그 문구 print
            print(f"[ACS CONTROLLER] carry_flag 설정 실패 : {carry_flag}")
            # False return
            return False
        # 상태 Lock 적용
        with self.state_lock:
            # 현재 carry_flag 갱신
            self.current_carry_flag = carry_flag
            # 다음 루프에서 S 명령이 바로 나가도록 시간 초기화
            self.last_s_send_time = 0.0
        # True return
        return True

    # ACS target_node를 SEER landmark로 변환하는 함수 선언
    def convert_target_to_landmark(self, target_node):
        # target_node 문자열 변환
        target_node = str(target_node).strip()
        # 빈 값이면
        if not target_node:
            # None return
            return None
        # 이미 LM으로 시작하면
        if target_node.upper().startswith("LM"):
            # 대문자로 return
            return target_node.upper()
        # 숫자이면
        if target_node.isdigit():
            # 앞자리 0 제거 후 LM 붙여서 return
            return f"LM{int(target_node)}"
        # 그 외 문자열이면 LM 붙여서 return
        return f"LM{target_node}"

    # 현재 SEER task 상태 확인 함수 선언
    def get_current_task_status(self):
        # SEER_commu에 get_task_status 함수가 있으면
        if hasattr(self.seer, "get_task_status"):
            # get_task_status 호출 결과 return
            return self.seer.get_task_status()
        # SEER_commu에 task_end 함수가 있으면
        if hasattr(self.seer, "task_end"):
            # task_end 호출 결과 return
            return self.seer.task_end()
        # 둘 다 없으면 오류 출력
        print("[ACS CONTROLLER] SEER_commu에 task 상태 확인 함수가 없습니다.")
        # None return
        return None

    # 현재 상태값 조회 함수 선언
    def get_report_state(self):
        # 상태 Lock 적용
        with self.state_lock:
            # 현재 상태값 복사 후 return
            return (self.current_target_node,self.current_carry_flag,self.current_move_flag)

    # 대기 상태로 변경하는 함수 선언
    def set_wait_state(self):
        # 상태 Lock 적용
        with self.state_lock:
            # move_flag 정지 상태 설정
            self.current_move_flag = "0"
            # 현재 landmark 초기화
            self.current_landmark = None
            # 주행 시작 시간 초기화
            self.drive_start_time = None
            # 마지막 QR location 초기화
            self.last_qr_location_node = None
            # 마지막 T 송신 시간 초기화
            self.last_t_send_time = 0.0
            # 현재 상태를 C 명령 대기 상태로 변경
            self.state = self.STATE_WAIT_C_COMMAND
            # 마지막 S 송신 시간 초기화
            self.last_s_send_time = 0.0
            # 주행 중 Event 해제
            self.driving_event.clear()
        # 디버그 문구 print
        print("--------------------------------------------------")
        print("[ACS CONTROLLER] 대기 상태 전환")
        print(f"[ACS CONTROLLER] carry_flag  : {self.current_carry_flag}")
        print("[ACS CONTROLLER] move_flag   : 0")
        print("--------------------------------------------------")

    # ACS 송신 보호 함수 선언
    def send_s_command_safe(self, target_node, carry_flag, move_flag):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 S 명령 송신 호출
            return self.acs_client.send_s_command(target_node=target_node,carry_flag=carry_flag,move_flag=move_flag)

    # ACS T 명령 송신 보호 함수 선언
    def send_t_command_safe(self, location_node):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 T 명령 송신 호출
            return self.acs_client.send_t_command(location_node=location_node)

    # ACS C 응답 송신 보호 함수 선언
    def send_c_response_safe(self, target_node, work_type="00"):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 C 응답 송신 호출
            return self.acs_client.send_c_response(target_node=target_node,work_type=work_type)

    # S 명령 즉시 송신 함수 선언
    def process_s_report_now(self, reason_text=""):
        # ACS 연결이 없으면
        if not self.acs_client.is_connected():
            # 디버그 문구 print
            print("[ACS CONTROLLER] S Command 즉시 송신 생략 : ACS 연결 안됨")
            # False return
            return False
        # 현재 보고 상태 조회
        target_node, carry_flag, move_flag = self.get_report_state()
        # 로그 출력
        print("--------------------------------------------------")
        print("[ACS CONTROLLER] S Command 즉시 송신 요청")
        # 사유가 있으면
        if reason_text:
            # 사유 출력
            print(f"[ACS CONTROLLER] reason : {reason_text}")
        # 현재 상태 출력
        print(f"[ACS CONTROLLER] target_node : {target_node}")
        print(f"[ACS CONTROLLER] carry_flag  : {carry_flag}")
        print(f"[ACS CONTROLLER] move_flag   : {move_flag}")
        print("--------------------------------------------------")
        # S 명령 송신
        send_ok = self.send_s_command_safe(target_node=target_node,carry_flag=carry_flag,move_flag=move_flag)
        # 송신 성공 시
        if send_ok:
            # 상태 Lock 적용
            with self.state_lock:
                # 마지막 S 명령 송신 시간 갱신
                self.last_s_send_time = time.time()
        # 송신 결과 return
        return send_ok

    # S 명령 주기 송신 Thread Loop 함수 선언
    def s_report_loop(self):
        # 종료 요청 전까지 반복
        while not self.stop_event.is_set():
            # ACS 연결이 되어 있으면
            if self.acs_client.is_connected():
                # 현재 시간 저장
                now_time = time.time()
                # 상태 Lock 적용
                with self.state_lock:
                    # S 송신 주기 확인
                    need_send = (now_time - self.last_s_send_time) >= self.s_period_sec
                    # 현재 보고 상태 복사
                    target_node = self.current_target_node
                    carry_flag = self.current_carry_flag
                    move_flag = self.current_move_flag
                # S 송신 시간이 되었으면
                if need_send:
                    # 로그 출력
                    print("--------------------------------------------------")
                    print("[ACS CONTROLLER] S Command 주기 송신 요청")
                    print(f"[ACS CONTROLLER] target_node : {target_node}")
                    print(f"[ACS CONTROLLER] carry_flag  : {carry_flag}")
                    print(f"[ACS CONTROLLER] move_flag   : {move_flag}")
                    print("--------------------------------------------------")
                    # S 명령 송신
                    send_ok = self.send_s_command_safe(target_node=target_node,carry_flag=carry_flag,move_flag=move_flag)
                    # 송신 성공 시
                    if send_ok:
                        # 상태 Lock 적용
                        with self.state_lock:
                            # 마지막 S 명령 송신 시간 갱신
                            self.last_s_send_time = now_time
            # 짧게 대기
            time.sleep(0.05)

    # T 명령 즉시 송신 함수 선언
    def process_t_report_now(self, location_node, reason_text=""):
        # ACS 연결이 없으면
        if not self.acs_client.is_connected():
            # 디버그 문구 print
            print("[ACS CONTROLLER] T Command 즉시 송신 생략 : ACS 연결 안됨")
            # False return
            return False
        # location_node가 없으면
        if location_node is None:
            # False return
            return False
        # location_node 문자열 정리
        location_node = str(location_node).strip().zfill(4)
        # 로그 출력
        print("--------------------------------------------------")
        print("[ACS CONTROLLER] T Command 즉시 송신 요청")
        # 사유가 있으면
        if reason_text:
            # 사유 출력
            print(f"[ACS CONTROLLER] reason : {reason_text}")
        # 위치 출력
        print(f"[ACS CONTROLLER] location_node : {location_node}")
        print("--------------------------------------------------")
        # T 명령 송신
        send_ok = self.send_t_command_safe(location_node=location_node)
        # 송신 성공 시
        if send_ok:
            # 상태 Lock 적용
            with self.state_lock:
                # 마지막 T 명령 송신 시간 갱신
                self.last_t_send_time = time.time()
        # 송신 결과 return
        return send_ok

    # QR + T 명령 Thread Loop 함수 선언
    def qr_t_report_loop(self):
        # 종료 요청 전까지 반복
        while not self.stop_event.is_set():
            # QR 1회 읽기
            # 주행 중이 아니어도 화면 갱신을 위해 QR은 계속 읽음
            qr_text = self.read_qr_once_safe()

            # q 키가 입력되었으면
            if qr_text == "q":
                # 종료 요청 설정
                self.stop_event.set()

                # 반복 종료
                break

            # 현재 주행 중 여부 확인
            is_driving = self.driving_event.is_set()

            # QR 값이 새로 읽혔으면
            if qr_text is not None:
                # QR 값 정리
                qr_location_node = str(qr_text).strip().zfill(4)

                # 주행 중일 때만 T 명령 관련 처리
                if is_driving:
                    # 상태 Lock 적용
                    with self.state_lock:
                        # 마지막 QR location 갱신
                        self.last_qr_location_node = qr_location_node
                    # 로그 출력
                    print("--------------------------------------------------")
                    print(f"[ACS CONTROLLER] 새 QR 읽음 : {qr_location_node}")
                    print("--------------------------------------------------")
                    # 새 QR은 즉시 T 명령 송신
                    self.process_t_report_now(
                        location_node=qr_location_node,
                        reason_text="새 QR 읽음")
                # 주행 중이 아니면 QR 화면만 갱신하고 T 명령은 송신하지 않음
                else:
                    # 로그 출력
                    print(f"[ACS CONTROLLER] QR 읽음, 대기 상태라 T 송신 생략 : {qr_location_node}")
            # 주행 중일 때만 마지막 QR 기준 T 주기 송신 처리
            if is_driving:
                # 마지막 QR 기준 T 주기 송신 처리
                self.process_t_report_timer()
            # QR 읽기 주기 대기
            time.sleep(self.qr_read_period_sec)

    # QR 1회 읽기 함수 선언
    def read_qr_once_safe(self):
        # QR Reader 함수명이 read_qr이면
        if hasattr(self.qr_reader, "read_qr"):
            # read_qr 호출
            return self.qr_reader.read_qr(show_window=self.show_qr_window,only_new_qr=True)
        # 함수가 없으면 오류 출력
        print("[ACS CONTROLLER] QR Reader에 read_once/read_qr 함수가 없습니다.")
        # None return
        return None

    # T 명령 주기 송신 처리 함수 선언
    def process_t_report_timer(self):
        # ACS 연결이 없으면
        if not self.acs_client.is_connected():
            # 함수 종료
            return
        # 현재 시간 저장
        now_time = time.time()
        # 상태 Lock 적용
        with self.state_lock:
            # 마지막 QR location 복사
            last_qr_location_node = self.last_qr_location_node
            # 마지막 QR location이 없으면
            if last_qr_location_node is None:
                # 함수 종료
                return
            # T 명령 주기가 지나지 않았으면
            if now_time - self.last_t_send_time < self.t_period_sec:
                # 함수 종료
                return
        # 로그 출력
        print("--------------------------------------------------")
        print("[ACS CONTROLLER] T Command 주기 송신 요청")
        print(f"[ACS CONTROLLER] last_qr_location_node : {last_qr_location_node}")
        print("--------------------------------------------------")
        # 마지막 QR location으로 T 명령 송신
        send_ok = self.send_t_command_safe(location_node=last_qr_location_node)
        # 송신 성공 시
        if send_ok:
            # 상태 Lock 적용
            with self.state_lock:
                # 마지막 T 명령 송신 시간 갱신
                self.last_t_send_time = now_time

    # 현재 task_status에 따라 move_flag 처리 함수 선언
    def process_move_flag_by_task_status(self, current_task_status):
        # 현재 task 상태가 정지 상태이면
        if current_task_status == self.TASK_STATUS_STOPPED:
            # 상태 변경 여부 저장
            changed = False
            # 상태 Lock 적용
            with self.state_lock:
                # 현재 move_flag가 이미 정지 상태가 아니면
                if self.current_move_flag != "0":
                    # move_flag 정지 상태 변경
                    self.current_move_flag = "0"
                    # 변경 여부 True
                    changed = True
            # 변경되었으면
            if changed:
                # 로그 출력
                print("==================================================")
                print("[ACS CONTROLLER] 주행 중 정지 상태 감지")
                print("[ACS CONTROLLER] move_flag = 0 변경")
                print("==================================================")
                # 정지 상태를 ACS에 바로 반영하기 위해 S 명령 즉시 송신
                self.process_s_report_now(reason_text="주행 중 정지 상태 감지")
            # 함수 종료
            return
        # 현재 task 상태가 주행 중 상태이면
        if current_task_status == self.TASK_STATUS_RUNNING:
            # 상태 변경 여부 저장
            changed = False
            # 상태 Lock 적용
            with self.state_lock:
                # 현재 move_flag가 주행 상태가 아니면
                if self.current_move_flag != "1":
                    # move_flag 주행 상태 변경
                    self.current_move_flag = "1"
                    # 변경 여부 True
                    changed = True
            # 변경되었으면
            if changed:
                # 로그 출력
                print("==================================================")
                print("[ACS CONTROLLER] 주행 재개 상태 감지")
                print("[ACS CONTROLLER] move_flag = 1 변경")
                print("==================================================")
                # 주행 재개 상태를 ACS에 바로 반영하기 위해 S 명령 즉시 송신
                self.process_s_report_now(reason_text="주행 재개 상태 감지")
            # 함수 종료
            return

    # Status 확인 Thread Loop 함수 선언
    def status_check_loop(self):
        # 종료 요청 전까지 반복
        while not self.stop_event.is_set():
            # 주행 중이 아니면
            if not self.driving_event.is_set():
                # 짧게 대기
                time.sleep(0.1)
                # 다음 반복
                continue
            # 현재 task 상태 확인
            current_task_status = self.get_current_task_status()
            # 상태 출력
            print(f"[ACS CONTROLLER] current_task_status : {current_task_status}")
            # task 상태 확인 실패 시
            if current_task_status is None:
                # 오류 출력
                print("[ACS CONTROLLER] task 상태 확인 실패")
                # 대기 상태 복귀
                self.set_wait_state()
                # 다음 반복
                continue
            # 주행 중 정지/재개 상태에 따라 move_flag 처리
            self.process_move_flag_by_task_status(current_task_status=current_task_status)
            # 도착 상태 확인
            self.process_arrival_check(current_task_status=current_task_status)
            # 상태 확인 주기 대기
            time.sleep(self.status_check_period_sec)

    # 주행 도착 상태 확인 함수 선언
    def process_arrival_check(self, current_task_status):
        # 현재 task_status가 완료가 아니면
        if current_task_status != self.TASK_STATUS_COMPLETED:
            # 함수 종료
            return
        # 상태 Lock 적용
        with self.state_lock:
            # 주행 시작 시간이 없으면
            if self.drive_start_time is None:
                # 현재 시간으로 설정
                self.drive_start_time = time.time()
            # 주행 경과 시간 계산
            drive_elapsed_time = time.time() - self.drive_start_time
            # gotarget 직후 이전 4 상태가 남아있는 경우 방지
            if drive_elapsed_time <= 0.5:
                # 함수 종료
                return
            # 현재 landmark 복사
            arrived_landmark = self.current_landmark
            # move_flag 정지 상태 변경
            self.current_move_flag = "0"
        # 도착 로그 출력
        print("==================================================")
        print(f"[ACS CONTROLLER] 목적지 도착 완료 : {arrived_landmark}")
        print("==================================================")
        # 도착 상태를 ACS에 바로 반영하기 위해 S 명령 즉시 송신
        self.process_s_report_now(reason_text="목적지 도착 완료")
        # 대기 상태 복귀
        self.set_wait_state()

    # C 명령 1회 수신 함수 선언
    def recv_c_command_once_safe(self):
        # ACS Client 함수명이 recv_c_command_once이면
        if hasattr(self.acs_client, "recv_c_command"):
            # recv_c_command 호출
            return self.acs_client.recv_c_command()
        # 함수가 없으면 오류 출력
        print("[ACS CONTROLLER] ACS Client에 recv_c_command 함수가 없습니다.")
        # None return
        return None

    # C 명령 처리 함수 선언
    def process_c_command(self):
        # 현재 주행 중이면
        if self.driving_event.is_set():
            # 함수 종료
            return
        # C 명령 수신 확인
        c_command = self.recv_c_command_once_safe()
        # C 명령이 없으면
        if c_command is None:
            # 함수 종료
            return
        # 로그 출력
        print("==================================================")
        print(f"[ACS CONTROLLER] C Command 수신 성공 : {c_command}")
        print("==================================================")
        # target_node 추출
        target_node = c_command["target_node"]
        # C 응답 송신
        c_response_ok = self.send_c_response_safe(target_node=target_node,work_type="00")
        # C 응답 송신 실패 시
        if not c_response_ok:
            # 오류 출력
            print("[ACS CONTROLLER] C Command 응답 송신 실패")
            # 상태 Lock 적용
            with self.state_lock:
                # 에러 상태 변경
                self.state = self.STATE_ERROR
            # 종료 요청 설정
            self.stop_event.set()
            # 함수 종료
            return
        # target_node를 SEER landmark로 변환
        landmark = self.convert_target_to_landmark(target_node)
        # landmark 변환 실패 시
        if landmark is None:
            # 오류 출력
            print("[ACS CONTROLLER] Landmark 변환 실패")
            # 대기 상태 복귀
            self.set_wait_state()
            # 함수 종료
            return
        # 상태 Lock 적용
        with self.state_lock:
            # 현재 target_node 저장
            self.current_target_node = str(target_node).strip().zfill(4)
            # 현재 landmark 저장
            self.current_landmark = landmark
            # move_flag 주행 중으로 변경
            self.current_move_flag = "1"
            # 주행 시작 시 이전 QR/T 상태 초기화
            self.last_qr_location_node = None
            self.last_t_send_time = 0.0
        # 로그 출력
        print("==================================================")
        print(f"[ACS CONTROLLER] ACS target_node : {str(target_node).strip().zfill(4)}")
        print(f"[ACS CONTROLLER] SEER landmark   : {landmark}")
        print("==================================================")
        # 에러가 없으면
        try:
            # 현재 랜드마크로 SEER gotarget 명령 송신
            self.seer.gotarget(landmark)
            # 상태 Lock 적용
            with self.state_lock:
                # 주행 시작 시간 저장
                self.drive_start_time = time.time()
                # 상태를 주행 중으로 변경
                self.state = self.STATE_DRIVING
                # 주행 Event 설정
                self.driving_event.set()
            # 주행 시작 상태를 ACS에 바로 반영하기 위해 S 명령 즉시 송신
            self.process_s_report_now(reason_text="주행 시작")
        # 에러 발생 시
        except Exception as error:
            # 오류 출력
            print("==================================================")
            print(f"[ACS CONTROLLER] SEER gotarget 명령 실패 : {error}")
            print("==================================================")
            # 대기 상태 복귀
            self.set_wait_state()

    # Worker Thread 시작 함수 선언
    def start_worker_threads(self):
        # S Report Thread 생성
        self.s_report_thread = threading.Thread(
            target=self.s_report_loop,
            daemon=True)
        # QR + T Report Thread 생성
        self.qr_t_report_thread = threading.Thread(
            target=self.qr_t_report_loop,
            daemon=True)
        # Status Check Thread 생성
        self.status_check_thread = threading.Thread(
            target=self.status_check_loop,
            daemon=True)
        # S Report Thread 시작
        self.s_report_thread.start()
        # QR + T Report Thread 시작
        self.qr_t_report_thread.start()
        # Status Check Thread 시작
        self.status_check_thread.start()
        # 로그 출력
        print("==================================================")
        print("[ACS CONTROLLER] Worker Thread 시작 완료")
        print("[ACS CONTROLLER] - S Report Thread")
        print("[ACS CONTROLLER] - QR + T Report Thread")
        print("[ACS CONTROLLER] - Status Check Thread")
        print("==================================================")

    # Worker Thread 종료 함수 선언
    def stop_worker_threads(self):
        # 종료 Event 설정
        self.stop_event.set()
        # Thread 리스트 선언
        thread_list = [
            self.s_report_thread,
            self.qr_t_report_thread,
            self.status_check_thread]
        # Thread 순회
        for one_thread in thread_list:
            # Thread가 존재하고 살아있으면
            if one_thread is not None and one_thread.is_alive():
                # 최대 2초 대기
                one_thread.join(timeout=2.0)
        # 로그 출력
        print("[ACS CONTROLLER] Worker Thread 종료 완료")

    # 초기 준비 함수 선언
    def setup(self):
        # ACS 연결 실패 시
        if not self.acs_client.connect(timeout_sec=5.0):
            # False return
            return False
        # QR 카메라 시작 실패 시
        if not self.qr_reader.start():
            # False return
            return False
        # 최초 대기 상태 설정
        self.set_wait_state()
        # Worker Thread 시작
        self.start_worker_threads()
        # True return
        return True

    # 종료 처리 함수 선언
    def close(self):
        # Worker Thread 종료
        self.stop_worker_threads()
        # QR 카메라 종료
        self.qr_reader.stop()
        # ACS 소켓 종료
        self.acs_client.close()
        # SEER 소켓 종료 함수가 있으면
        if hasattr(self.seer, "socket_close"):
            # SEER 소켓 종료
            self.seer.socket_close()
        # 로그 출력
        print("[ACS CONTROLLER] ACS 기반 주행 종료 완료")

    # 메인 실행 함수 선언
    def run(self):
        # 에러가 없으면
        try:
            # 초기 준비 실패 시
            if not self.setup():
                # 오류 출력
                print("[ACS CONTROLLER] 초기 준비 실패로 종료")

                # 함수 종료
                return
            # 시작 로그 출력
            print("==================================================")
            print("[ACS CONTROLLER] Threading 기반 ACS 주행 시작")
            print("[ACS CONTROLLER] Main Thread : C Command 대기/응답")
            print("==================================================")
            # 종료 요청 전까지 반복
            while not self.stop_event.is_set():
                # 현재 상태 Lock 적용
                with self.state_lock:
                    # 현재 상태 복사
                    current_state = self.state
                # 에러 상태이면
                if current_state == self.STATE_ERROR:
                    # 오류 출력
                    print("[ACS CONTROLLER] ERROR 상태 진입")
                    # 반복 종료
                    break
                # 주행 중이 아니면 C 명령 처리
                if not self.driving_event.is_set():
                    # C 명령 처리
                    self.process_c_command()
                # 주행 중이면 다음 C 명령은 받지 않고 대기
                else:
                    # 짧게 대기
                    time.sleep(0.1)
        # Ctrl+C 발생 시
        except KeyboardInterrupt:
            # 로그 출력
            print("[ACS CONTROLLER] 사용자 종료 요청")
        # 최종적으로
        finally:
            # 종료 처리
            self.close()
