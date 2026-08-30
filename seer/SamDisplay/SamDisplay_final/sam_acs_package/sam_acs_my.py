# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# Thread 처리를 위한 threading 라이브러리 import
import threading
# ----------------------- 사용자 정의 라이브러리 import -----------------------
# ACS Client 클래스 import
from sam_acs_package.sam_acs_client import ACS_Client
# SEER AMR 제어 클래스 import
from custom_package.seer_commu import SEER_commu
# Dobot 통신 클래스 import
from custom_package.dobot_commu import Dobot_Commu
# Ezi_Motor 제어 클래스 import
from custom_package.motor_commu import Ezi_Motor_Commu
# dio 모듈 제어 클래스 import
from custom_package.sam_dio_commu import DIO_Commu

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
    # acs_ip : 통신할 acs ip
    # acs_port : 통신할 acs port
    # amr_id : 현재 amr의 id
    # carry_flag : 적재 여부 (0: 적재 x, 1: 적재 O)
    # s_period_sec : S 명령 송신 주기
    # t_period_sec : T 명령 송신 주기
    # c_recv_timeout_sec : C 명령 수신 여부 확인 주기
    # status_check_period_sec : 주행 상태 확인 주기
    # node_check_period_sec : 노드 위치 확인 주기
    def __init__(
        self,
        acs_ip,
        acs_port,
        amr_id="444",
        carry_flag="0",
        s_period_sec=1.0,
        t_period_sec=None,
        c_recv_timeout_sec=0.2,
        status_check_period_sec=0.2,
        node_check_period_sec=0.2):
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
        # SEER task_status 확인 주기 저장
        self.status_check_period_sec = status_check_period_sec
        # SEER current_station 확인 주기 저장
        self.node_check_period_sec = node_check_period_sec
        # ACS Client 객체 생성
        self.acs_client = ACS_Client(
            acs_ip=self.acs_ip,
            acs_port=self.acs_port,
            amr_id=self.amr_id)
        # SEER 제어 객체 생성
        self.seer = SEER_commu()
        # 현재 Controller 상태 저장
        self.state = self.STATE_WAIT_C_COMMAND
        # 현재 target_node 저장 변수 선언
        self.current_target_node = "0000"
        # 현재 C Command WorkType 저장 변수 선언
        self.current_work_type = "00"
        # 현재 move_flag 저장 변수 선언
        self.current_move_flag = "0"
        # 현재 SEER landmark 저장 변수 선언
        self.current_landmark = None
        # 주행 시작 시간 저장 변수 선언
        self.drive_start_time = None
        # 마지막 T 명령 위치 노드 저장 변수 선언
        # 예) LM1 -> 0001
        self.last_t_location_node = None
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
        # S/T/C/L/U 응답이 동시에 send_frame을 호출하지 않도록 보호
        self.send_lock = threading.Lock()
        # SEER 상태 소켓 동시 접근 방지 Lock 선언
        # get_task_status(), get_amr_node()가 동시에 state_socket을 쓰지 않도록 보호
        self.seer_lock = threading.Lock()
        # Thread 객체 저장 변수 선언
        self.s_report_thread = None
        self.node_t_report_thread = None
        self.status_check_thread = None

    # Loading 동작 수행 함수 선언
    def loading_process(self):
        # seer dio 3번핀 High로 설정
        self.seer.setDO(3,True)
        # 3초 대기
        time.sleep(3)
        # seer dio 3번핀 Low로 설정
        self.seer.setDO(3,False)

    # UnLoading 동작 수행 함수 선언
    def unloading_process(self):
        # seer dio 4번핀 High로 설정
        self.seer.setDO(4,True)
        # 3초 대기
        time.sleep(3)
        # seer dio 4번핀 Low로 설정
        self.seer.setDO(4,False)

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

    # SEER AMR Node를 ACS Location Node로 변환하는 함수 선언
    def convert_amr_node_to_location_node(self, amr_node):
        # amr_node가 없으면
        if amr_node is None:
            # None return
            return None
        # 문자열 변환 및 공백 제거
        amr_node = str(amr_node).strip().upper()
        # 빈 문자열이면
        if amr_node == "":
            # None return
            return None
        # LM으로 시작하면
        if amr_node.startswith("LM"):
            # LM 뒤 숫자만 추출
            node_number = amr_node[2:]
        # LM 없이 숫자만 들어오면
        else:
            # 그대로 사용
            node_number = amr_node
        # 숫자가 아니면
        if not node_number.isdigit():
            # 디버그 문구 print
            print(f"[ACS CONTROLLER] AMR Node 변환 실패 : {amr_node}")
            # None return
            return None
        # ACS Location Node 4자리로 변환
        location_node = str(int(node_number)).zfill(4)
        # 변환 결과 return
        return location_node

    # 현재 SEER task 상태 확인 함수 선언
    def get_current_task_status(self):
        # 에러가 없으면
        try:
            # SEER 상태 소켓 동시 접근 방지
            with self.seer_lock:
                # SEER_commu에 get_task_status 함수가 있으면
                if hasattr(self.seer, "get_task_status"):
                    # get_task_status 호출 결과 저장
                    task_status = self.seer.get_task_status()
                # SEER_commu에 task_end 함수가 있으면
                elif hasattr(self.seer, "task_end"):
                    # task_end 호출 결과 저장
                    task_status = self.seer.task_end()
                # 둘 다 없으면 오류 출력
                else:
                    # 디버그 문구 print
                    print("[ACS CONTROLLER] SEER_commu에 task 상태 확인 함수가 없습니다.")
                    # None return
                    return None
        # 에러 발생 시
        except Exception as error:
            # 오류 출력
            print(f"[ACS CONTROLLER] SEER task 상태 확인 중 에러 발생 : {error}")
            # None return
            return None
        # 문자열 숫자이면 int로 변환
        try:
            # int 변환 후 return
            return int(task_status)
        # 변환 실패 시
        except Exception:
            # 원본 return
            return task_status

    # SEER 현재 AMR Node 안전 조회 함수 선언
    def get_amr_node_safe(self):
        # get_amr_node 함수가 없으면
        if not hasattr(self.seer, "get_amr_node"):
            # 디버그 문구 print
            print("[ACS CONTROLLER] SEER_commu에 get_amr_node 함수가 없습니다.")
            # None return
            return None
        # 에러가 없으면
        try:
            # SEER 상태 소켓 동시 접근 방지
            with self.seer_lock:
                # 현재 AMR Node 조회
                amr_node = self.seer.get_amr_node()
        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print(f"[ACS CONTROLLER] get_amr_node 호출 중 에러 발생 : {error}")
            # None return
            return None
        # False이면
        if amr_node is False:
            # None return
            return None
        # None이면
        if amr_node is None:
            # None return
            return None
        # 문자열 정리
        amr_node = str(amr_node).strip()
        # 빈 문자열이면
        if amr_node == "":
            # None return
            return None
        # 대문자로 return
        return amr_node.upper()

    # 현재 상태값 조회 함수 선언
    def get_report_state(self):
        # 상태 Lock 적용
        with self.state_lock:
            # 현재 상태값 복사 후 return
            return (
                self.current_target_node,
                self.current_carry_flag,
                self.current_move_flag)

    # 대기 상태로 변경하는 함수 선언
    def set_wait_state(self):
        # 상태 Lock 적용
        with self.state_lock:
            # move_flag 정지 상태 설정
            self.current_move_flag = "0"
            # 현재 work_type 초기화
            self.current_work_type = "00"
            # 현재 landmark 초기화
            self.current_landmark = None
            # 주행 시작 시간 초기화
            self.drive_start_time = None
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

    # ACS S 명령 송신 보호 함수 선언
    def send_s_command_safe(self, target_node, carry_flag, move_flag):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 S 명령 송신 호출
            return self.acs_client.send_s_command(
                target_node=target_node,
                carry_flag=carry_flag,
                move_flag=move_flag)

    # ACS T 명령 송신 보호 함수 선언
    def send_t_command_safe(self, location_node):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 T 명령 송신 호출
            return self.acs_client.send_t_command(location_node=location_node)

    # ACS L 명령 송신 보호 함수 선언
    def send_l_command_safe(self, loading_node):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 L 명령 송신 호출
            return self.acs_client.send_l_command(loading_node=loading_node)

    # ACS U 명령 송신 보호 함수 선언
    def send_u_command_safe(self, unloading_node):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 U 명령 송신 호출
            return self.acs_client.send_u_command(unloading_node=unloading_node)

    # ACS C 응답 송신 보호 함수 선언
    def send_c_response_safe(self, target_node, work_type="00"):
        # send Lock 적용
        with self.send_lock:
            # ACS Client의 C 응답 송신 호출
            return self.acs_client.send_c_response(
                target_node=target_node,
                work_type=work_type)

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
        send_ok = self.send_s_command_safe(
            target_node=target_node,
            carry_flag=carry_flag,
            move_flag=move_flag)
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
                    send_ok = self.send_s_command_safe(
                        target_node=target_node,
                        carry_flag=carry_flag,
                        move_flag=move_flag)
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

    # WorkType에 따른 L/U 명령 송신 함수 선언
    def process_lu_command_by_work_type(self):
        # 상태 Lock 적용
        with self.state_lock:
            # 현재 target_node 복사
            target_node = str(self.current_target_node).strip().zfill(4)
            # 현재 work_type 복사
            work_type = str(self.current_work_type).strip().zfill(2)
        # WorkType이 11이면 Loading 완료 L 명령 송신
        if work_type == "11":
            # 로그 출력
            print("--------------------------------------------------")
            print("[ACS CONTROLLER] WorkType 11 감지")
            print("[ACS CONTROLLER] 주행 완료 후 L Command 송신 요청")
            print(f"[ACS CONTROLLER] loading_node : {target_node}")
            print("--------------------------------------------------")
            # Loading 동작 수행
            self.loading_process()
            # Loading 동작이 종료되면 L 명령 송신
            l_send_ok = self.send_l_command_safe(loading_node=target_node)
            # 로그 출력
            print("--------------------------------------------------")
            print(f"[ACS CONTROLLER] L Command 송신 결과 : {l_send_ok}")
            print("--------------------------------------------------")
            # 송신 결과 return
            return l_send_ok
        # WorkType이 10이면 Unloading 완료 U 명령 송신
        if work_type == "10":
            # 로그 출력
            print("--------------------------------------------------")
            print("[ACS CONTROLLER] WorkType 10 감지")
            print("[ACS CONTROLLER] 주행 완료 후 U Command 송신 요청")
            print(f"[ACS CONTROLLER] unloading_node : {target_node}")
            print("--------------------------------------------------")
            # Unloading 동작 수행
            self.unloading_process()
            # Unloading 동작이 종료되면 ㅉU 명령 송신
            u_send_ok = self.send_u_command_safe(unloading_node=target_node)
            # 로그 출력
            print("--------------------------------------------------")
            print(f"[ACS CONTROLLER] U Command 송신 결과 : {u_send_ok}")
            print("--------------------------------------------------")
            # 송신 결과 return
            return u_send_ok
        # L/U 대상 WorkType이 아니면
        print(f"[ACS CONTROLLER] L/U 송신 대상 WorkType 아님 : {work_type}")
        # False return
        return False

    # SEER Node 기반 T 명령 Thread Loop 함수 선언
    def node_t_report_loop(self):
        # 종료 요청 전까지 반복
        while not self.stop_event.is_set():
            # 현재 AMR Node 조회
            amr_node = self.get_amr_node_safe()
            # AMR Node 값이 있으면
            if amr_node is not None:
                # AMR Node를 ACS Location Node로 변환
                location_node = self.convert_amr_node_to_location_node(amr_node)
                # 변환 성공 시
                if location_node is not None:
                    # 즉시 송신 여부 저장 변수
                    need_send_now = False
                    # 상태 Lock 적용
                    with self.state_lock:
                        # 마지막 T location과 다르면
                        if self.last_t_location_node != location_node:
                            # 마지막 T location 갱신
                            self.last_t_location_node = location_node
                            # 즉시 송신 필요
                            need_send_now = True
                    # 새 위치가 감지되었으면
                    if need_send_now:
                        # 로그 출력
                        print("--------------------------------------------------")
                        print(f"[ACS CONTROLLER] AMR Node 갱신 : {amr_node}")
                        print(f"[ACS CONTROLLER] T Location 반영 : {location_node}")
                        print("--------------------------------------------------")
                        # T 명령 즉시 송신
                        self.process_t_report_now(
                            location_node=location_node,
                            reason_text=f"AMR Node 갱신 : {amr_node}"
                        )

            # 마지막 위치 기준 T 명령 주기 송신 처리
            self.process_t_report_timer()
            # Node 확인 주기 대기
            time.sleep(self.node_check_period_sec)

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
            # 마지막 T location 복사
            last_t_location_node = self.last_t_location_node
            # 마지막 T location이 없으면
            if last_t_location_node is None:
                # 함수 종료
                return
            # T 명령 주기가 지나지 않았으면
            if now_time - self.last_t_send_time < self.t_period_sec:
                # 함수 종료
                return
        # 로그 출력
        print("--------------------------------------------------")
        print("[ACS CONTROLLER] T Command 주기 송신 요청")
        print(f"[ACS CONTROLLER] last_t_location_node : {last_t_location_node}")
        print("--------------------------------------------------")
        # 마지막 T location으로 T 명령 송신
        send_ok = self.send_t_command_safe(location_node=last_t_location_node)
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
        # WorkType에 따라 L/U 명령 송신
        self.process_lu_command_by_work_type()
        # 대기 상태 복귀
        self.set_wait_state()

    # C 명령 1회 수신 함수 선언
    def recv_c_command_once_safe(self):
        # ACS Client 함수명이 recv_c_command이면
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
        # work_type 추출
        work_type = str(c_command.get("work_type", "00")).strip().zfill(2)

        # C 응답 송신
        # C 응답 WorkType은 수신 WorkType과 관계없이 00 고정
        c_response_ok = self.send_c_response_safe(
            target_node=target_node,
            work_type="00")
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
            # 현재 WorkType 저장
            self.current_work_type = work_type
            # 현재 landmark 저장
            self.current_landmark = landmark
            # move_flag 주행 중으로 변경
            self.current_move_flag = "1"
        # 로그 출력
        print("==================================================")
        print(f"[ACS CONTROLLER] ACS target_node : {str(target_node).strip().zfill(4)}")
        print(f"[ACS CONTROLLER] WorkType       : {work_type}")
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
        # SEER Node + T Report Thread 생성
        self.node_t_report_thread = threading.Thread(
            target=self.node_t_report_loop,
            daemon=True
        )
        # Status Check Thread 생성
        self.status_check_thread = threading.Thread(
            target=self.status_check_loop,
            daemon=True
        )
        # S Report Thread 시작
        self.s_report_thread.start()
        # SEER Node + T Report Thread 시작
        self.node_t_report_thread.start()
        # Status Check Thread 시작
        self.status_check_thread.start()
        # 로그 출력
        print("==================================================")
        print("[ACS CONTROLLER] Worker Thread 시작 완료")
        print("[ACS CONTROLLER] - S Report Thread")
        print("[ACS CONTROLLER] - SEER Node + T Report Thread")
        print("[ACS CONTROLLER] - Status Check Thread")
        print("==================================================")

    # Worker Thread 종료 함수 선언
    def stop_worker_threads(self):
        # 종료 Event 설정
        self.stop_event.set()
        # Thread 리스트 선언
        thread_list = [
            self.s_report_thread,
            self.node_t_report_thread,
            self.status_check_thread
        ]
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