# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# 주기 보고 Thread 처리를 위한 threading 라이브러리 import
import threading
# ----------------------- 사용자 정의 라이브러리 import ----------------------
# ACS Client 클래스 import
from sam_acs_package.sam_acs_client import ACS_Client
# SEER AMR 제어 클래스 import
from custom_package.seer_commu import SEER_commu
# Dobot 통신 클래스 import
from custom_package.dobot_commu import Dobot_Commu
# DIO 제어 클래스 import
from custom_package.sam_dio_commu import DIO_Commu
# 모터 제어 클래스 import
from custom_package.motor_commu import Ezi_Motor_Commu
# D435 카메라 기반 ArUco 검출 클래스 import
from side_docking_package.aruco_detector import Aruco_Detector_Commu
# ArUco 정답 저장/로드 클래스 import
from side_docking_package.save_aruco_answer import Aruco_Answer_Commu
# 측면 카메라 기반 도킹 클래스 import
from side_docking_package.aruco_dock import Aruco_Dock_Commu

# 삼성 ACS + Dobot 연동 Controller 클래스 선언
class ACS_Controller:
    # ----------------------- Controller 상태 선언 -----------------------
    # ACS C Command를 기다리는 상태
    STATE_WAIT = "WAIT"
    # AMR 주행 중 상태
    STATE_DRIVING = "DRIVING"
    # 에러 상태
    STATE_ERROR = "ERROR"
    # ----------------------- SEER task_status 값 선언 -----------------------
    # SEER 주행 중 상태
    TASK_RUNNING = 2
    # SEER 정지/막힘 상태
    TASK_STOPPED = 3
    # SEER 주행 완료 상태
    TASK_COMPLETED = 4
    # ----------------------- Dobot 작업 매핑 선언 -----------------------
    # key   : (SEER landmark, ACS WorkType)
    # value : Dobot에 보낼 tag, Dobot 작업 종류, ACS 완료 보고 종류
    # WorkType 11 : Loading  -> Dobot AQ 완료 후 ACS L 송신
    # WorkType 10 : Unloading -> Dobot DP 완료 후 ACS U 송신
    DOBOT_JOB_MAP = {
        ("LM1", "11"):  {"tag": "MLMA001", "dobot_task": "AQ", "acs_done": "L"},
        ("LM2", "11"):  {"tag": "MLMB002", "dobot_task": "AQ", "acs_done": "L"},
        ("LM14", "11"): {"tag": "MACA001", "dobot_task": "AQ", "acs_done": "L"},
        ("LM14", "10"): {"tag": "MACA001", "dobot_task": "DP", "acs_done": "U"},
        ("LM4", "11"):  {"tag": "MACB002", "dobot_task": "AQ", "acs_done": "L"},
        ("LM4", "10"):  {"tag": "MACB002", "dobot_task": "DP", "acs_done": "U"}}

    # 클래스 초기화 함수 선언
    def __init__(
        self,
        acs_ip,
        acs_port,
        amr_id="444",
        carry_flag="0",
        s_period_sec=3.0,
        t_period_sec=3.0,
        c_recv_timeout_sec=0.2,
        status_check_period_sec=0.2,
        node_check_period_sec=0.2,
        seer_ip="192.168.0.104",
        dobot_server_ip="0.0.0.0",
        dobot_server_port=12321,
        dobot_timeout_sec=3600):
        # ------------------ ACS 연결 관련 변수 선언 ---------------
        # ACS IP 변수 선언
        self.acs_ip = acs_ip
        # ACS Port 변수 선언
        self.acs_port = acs_port
        # AMR ID 저장 변수 선언
        self.amr_id = str(amr_id).strip().zfill(3)
        # ------------ ACS 명령 및 상태 파악 주기 설정 관련 변수 선언 ------------
        # S 명령 송신 주기 변수 선언
        self.s_period_sec = s_period_sec
        # T 명령 송신 주기 변수 선언
        self.t_period_sec = t_period_sec
        # C 명령 수신 확인 주기 변수 선언
        self.c_recv_timeout_sec = c_recv_timeout_sec
        # AMR 상태 확인 변수 확인 주기 변수 선언
        self.status_check_period_sec = status_check_period_sec
        # 현재 AMR 위치 노드 확인 
        self.node_check_period_sec = node_check_period_sec
        # ------------ 현재 ACS 보고 관련 변수 선언 ----------------------------
        # AMR 현재 타겟 노드 변수 선언
        self.target_node = "0000"
        # AMR 화물 적재 상태 변수 선언
        self.carry_flag = str(carry_flag).strip()
        # AMR 주행 상태 변수 선언
        self.move_flag = "0"
        # ------------ 현재 작업 상태 관련 변수 선언 ----------------------------
        # 현재 작업 상태 변수 선언
        self.state = self.STATE_WAIT
        # 마지막 에러 원인 저장 변수 선언
        self.error_reason = ""
        # 현재 수신받은 work_type 변수 선언
        self.work_type = "00"
        # 현재 수신받은 랜드마크 변수 선언
        self.landmark = None
        # 두봇 작업 변수 선언
        self.dobot_job = None
        # 주행 시작 시간 저장 변수 선언
        self.drive_start_time = None
        # ------------- S/T 명령 관련 변수 선언 --------------------------------
        # 마지막 S 명령 송신 시간 변수 선언
        self.last_s_send_time = 0.0
        # 마지막 T 명령 송신 시간 변수 선언
        self.last_t_send_time = 0.0
        # 마지막으로 주행한 노드 관련 변수 선언
        self.last_location_node = None
        # ----------------- 각 제어 객체 선언 ------------------------------------
        # ACS Client 객체 생성
        self.acs_client = ACS_Client(acs_ip=self.acs_ip,acs_port=self.acs_port,amr_id=self.amr_id)
        # SEER 제어 객체 생성
        self.seer = SEER_commu(ip=seer_ip)
        # Dobot 통신 객체 생성
        self.dobot = Dobot_Commu(ip=dobot_server_ip,port=dobot_server_port,timeout_sec=dobot_timeout_sec)
        # 모터 드라이버 제어 객체 생성
        self.motor = Ezi_Motor_Commu()
        # ----------------- Thread 관련 변수 선언 ------------------------------
        # Thread 종료 Event 선언
        self.stop_event = threading.Event()
        # 동시 접근 방지 Lock 선언
        self.state_lock = threading.Lock()
        self.acs_send_lock = threading.Lock()
        self.seer_lock = threading.Lock()
        self.dobot_lock = threading.Lock()
        # 주기 보고 Thread 객체 선언
        self.s_report_thread = None
        self.t_report_thread = None

    # ==================================================================
    # 1. 메인 실행 / 초기 준비 / 종료 관련 함수 선언
    # ==================================================================
    # 메인 실행 함수 선언
    def run(self):
        # 에러가 없으면
        try:
            # 초기 연결 준비 프로세스 실행 후 실패 시
            if not self.setup():
                # 디버그 문구 print
                print("[ACS] 초기 준비 실패로 종료")
                # 종료
                return
            # 시작 문구 print
            print("==================================================")
            print("[ACS] 삼성 ACS + Dobot 연동 시작")
            print("[ACS] Dobot Healthcheck 실행")
            print("[ACS] C Command 수신 대기")
            print("[ACS] Dobot Home 확인")
            print("[ACS] C Command 명령에 따라 주행")
            print("[ACS] 목적지 도착 후 Dobot AQ/DP 등 동작 수행")
            print("[ACS] ACS L/U 송신")
            print("==================================================")
            # 종료 요청 전까지 반복
            while not self.stop_event.is_set():
                # 현재 상태 저장
                with self.state_lock:
                    state = self.state
                # AMR이 에러 상태이면
                if state == self.STATE_ERROR:
                    # 현재 에러 원인 복사
                    with self.state_lock:
                        error_reason = getattr(self, "error_reason", "")
                    # 에러 상태 출력
                    print("==================================================")
                    print("[ACS] Controller ERROR 상태")
                    print(f"[ACS] reason : {error_reason}")
                    print("==================================================")
                    # 같은 로그가 너무 빠르게 반복되지 않도록 1초 대기
                    time.sleep(1.0)
                    # 우선 계속 실행
                    continue
                # AMR이 대기 상태이면
                if state == self.STATE_WAIT:
                    # C Command 수신
                    self.process_c_command()
                    # C Command 확인 주기만큼 대기
                    time.sleep(self.c_recv_timeout_sec)
                    continue
                # AMR이 주행 중이면
                if state == self.STATE_DRIVING:
                    # 주행 상태 확인
                    self.process_driving_status()
                    # 주행 상태 확인 주기만큼 대기
                    time.sleep(self.status_check_period_sec)
                    continue
        # Ctrl+C가 눌리면 종료
        except KeyboardInterrupt:
            print("[ACS] 사용자 종료 요청")
        # 최종적으로
        finally:
            # 모든 소켓 종료
            self.close()

    # 초기 준비 함수 선언
    def setup(self):
        # ACS 연결 시도후 실패시
        if not self.acs_client.connect(timeout_sec=30.0):
            # 디버그 문구 Print
            print("[ACS] ACS 연결 실패")
            # False return
            return False
        # Dobot 연결 Server open 후 Client 접속 대기 실행 후 실패 시
        if not self.setup_dobot():
            # 디버그 문구 print
            print("[ACS] Dobot 연결 실패")
            # False return
            return False
        # 최초 Dobot Healthcheck 시도 후 실패 시
        if not self.check_dobot_healthcheck():
            # 디버그 문구 print 
            print("[ACS] 최초 Dobot Healthcheck 실패")
            # False return
            return False
        # 초기 상태 설정
        self.set_wait_state()
        # 주행하기 전 테이블 닫기
        self.motor.table_close()
        # S/T 보고 Thread 시작
        self.start_report_threads()
        # setup 완료 return
        return True

    # 종료 처리 함수 선언
    def close(self):
        # Thread 종료 요청
        self.stop_event.set()
        # Thread 종료 대기
        for one_thread in [self.s_report_thread, self.t_report_thread]:
            if one_thread is not None and one_thread.is_alive():
                one_thread.join(timeout=2.0)
        # 에러가 없으면
        try:
            # acs 연결 종료
            self.acs_client.close()
        # 예외 발생 시 
        except Exception:
            # 넘어가기
            pass
        # 에러가 없으면
        try:
            # dobot과 연결 종료
            self.dobot.close_all()
        # 예외 발생 시 
        except Exception:
            # 넘어가기
            pass
        # 에러가 없으면
        try:
            # seer 제어 객체어 socket_close 객체가 있으면
            if hasattr(self.seer, "socket_close"):
                # amr 연결 종료 함수 실행
                self.seer.socket_close()
        # 예외 발생 시 
        except Exception:
            # 넘어가기
            pass
        print("[ACS] 삼성 ACS + Dobot 연동 종료 완료")
    # ==================================================================
    # 2. Dobot 처리 관련 함수 선언
    # ==================================================================
    # Dobot TCP Server 시작 및 Client 접속 대기 함수 선언
    def setup_dobot(self):
        # dobot 서버 시작 후 실패시
        if not self.dobot.start_server():
            # 디버그 문구 print
            print("[DOBOT] TCP Server 시작 실패")
            # False return
            return False
        # dobot이 접속할때까지 대기 후 실패시
        if not self.dobot.wait_client():
            # 디버그 문구 print
            print("[DOBOT] Client 접속 실패")
            # False return
            return False
        # 문제가 없으면 True return
        return True

    # Dobot Healthcheck 함수 선언
    def check_dobot_healthcheck(self):
        # dobot lock을 실행하고
        with self.dobot_lock:
            # dobot 헬스체크 실행
            ok, msg, cmd_id = self.dobot.do_healthcheck()
        # HealthCheck 결과 print
        print("==================================================")
        print("[DOBOT] Healthcheck 결과")
        print(f"[DOBOT] ok     : {ok}")
        print(f"[DOBOT] cmd_id : {cmd_id}")
        print(f"[DOBOT] msg    : {msg}")
        print("==================================================")
        # ok return
        return ok

    # 주행 전 Home 확인 함수 선언
    def check_dobot_home(self):
        # dobot lock을 실행하고
        with self.dobot_lock:
            # dobot 헬스체크 실행
            ok, msg, cmd_id = self.dobot.do_home_check()
         # HealthCheck 결과 print
        print("==================================================")
        print("[DOBOT] Home 확인 결과")
        print(f"[DOBOT] ok     : {ok}")
        print(f"[DOBOT] cmd_id : {cmd_id}")
        print(f"[DOBOT] msg    : {msg}")
        print("==================================================")
        # ok return
        return ok

    # Dobot 작업 리스트 수행 함수 선언
    def run_dobot_job(self, dobot_job):
        # dobot_job 딕셔너리에서 "dobot_tasks" 키에 해당하는 작업 리스트 불러오기
        dobot_tasks = dobot_job.get("dobot_tasks", [])
        # 작업 리스트가 없으면 기존 방식 호환
        if not dobot_tasks:
            # Dobot에 보낼 tag 값 저장
            tag = dobot_job["tag"]
            # Dobot에 보낼 단일 task값 저장
            dobot_task = dobot_job["dobot_task"]
            # 단일 작업 구조를 새 작업 리스트 구조로 변환
            dobot_tasks = [{"task": dobot_task, "tag": tag}]
        # 작업 리스트 순서대로 실행
        for one_job in dobot_tasks:
            # 현재 실행할 작업의 task 이름을 추출
            task = one_job["task"]
            # 현재 실행할 작업의 tag 이름을 추출
            tag = one_job["tag"]
            # Dobot 작업 요청 print
            # 작업 시작 로그 구분선 출력
            print("==================================================")
            # Dobot 작업 요청 시작 로그 출력
            print("[DOBOT] 작업 요청 시작")
            # 현재 실행할 Dobot 작업 종류 출력
            print(f"[DOBOT] task : {task}")
            # 현재 Dobot 작업에 사용할 tag 출력
            print(f"[DOBOT] tag  : {tag}")
            # 작업 시작 로그 구분선 출력
            print("==================================================")
            # 이 블록 안에서는 한 번에 하나의 Dobot 명령만 실행되도록 lock설정
            with self.dobot_lock:
                # 현재 작업이 AQ라면
                if task == "AQ":
                    # Dobot에 AQ 요청을 보내고 결과를 저장
                    ok, msg, cmd_id = self.dobot.do_aq(tag)
                # 현재 작업이 DP라면
                elif task == "DP":
                    # Dobot에 DP 요청을 보내고 결과를 저장
                    ok, msg, cmd_id = self.dobot.do_dp(tag)
                # 현재 작업이 CV라면
                elif task == "CV":
                    # Dobot에 CV 요청을 보내고 결과를 저장
                    ok, msg, cmd_id = self.dobot.do_cv(tag)
                # 현재 작업이 STARTBT라면
                elif task == "STARTBT":
                    # Dobot에 STARTBT 요청을 보내고 결과를 저장
                    ok, msg, cmd_id = self.dobot.do_startbt(tag)
                # 현재 작업이 ENDBT라면
                elif task == "ENDBT":
                    # Dobot ENDBT 요청을 보내고 결과를 저장
                    ok, msg, cmd_id = self.dobot.do_endbt(tag)
                # 작업이 추가가 안되면
                else:
                    # 없은 작업이라는 문구 출력
                    return False, f"UNKNOWN_DOBOT_TASK:{task}", None
            # 작업이 ok가 아니라면
            if not ok:
                # False return
                return False, f"{task}_FAIL:{msg}", cmd_id
            # 작업 완료 print
            print("==================================================")
            print("[DOBOT] 작업 완료")
            print(f"[DOBOT] task   : {task}")
            print(f"[DOBOT] cmd_id : {cmd_id}")
            print(f"[DOBOT] msg    : {msg}")
            print("==================================================")
        return True, "ALL_DOBOT_TASK_DONE", None
    # ==================================================================
    # 3. ACS C Command 처리 관련 함수 선언
    # ==================================================================
    # C Command 처리 함수 선언
    def process_c_command(self):
        # C Command 수신 시도
        c_command = self.recv_c_command_once()
        # 수신된 C Command가 없으면 종료
        if c_command is None:
            return
        print("==================================================")
        print(f"[ACS] C Command 수신 : {c_command}")
        print("==================================================")
        # ACS C Command에서 target_node, work_type 추출
        target_node = str(c_command.get("target_node", "")).strip()
        work_type = str(c_command.get("work_type", "00")).strip().zfill(2)
        # ACS 숫자 target_node를 SEER landmark로 변환
        landmark = self.target_node_to_landmark(target_node)
        if landmark is None:
            self.set_error_state(f"target_node 변환 실패 : {target_node}")
            return
        # landmark + work_type 기준 Dobot 작업 조회
        dobot_job = self.find_dobot_job(landmark, work_type)
        # work_type이 10,11인에 dobot_job이 없다면
        if work_type in ["10", "11"] and dobot_job is None:
            # 에러 표출
            self.set_error_state(f"Dobot 작업 매핑 없음 : landmark={landmark}, work_type={work_type}")
            return
        # ACS에 C Command 응답 송신 후 실패시
        if not self.send_c_response(target_node=target_node, work_type="00"):
            # 에러 표출
            self.set_error_state("C Command 응답 송신 실패")
            return
        # 주행 전 Dobot Home 확인 후 실패시
        if not self.check_dobot_home():
            # 에러 표출
            self.set_error_state("Dobot Home 확인 실패")
            return
        # 현재 작업 정보 저장
        self.save_drive_job(target_node=target_node,work_type=work_type,landmark=landmark,dobot_job=dobot_job)
        # SEER 주행 시작
        self.start_seer_drive(landmark)

    # C Command 1회 수신 함수 선언
    def recv_c_command_once(self):
        # acs 클라이언트에 recv_c_command 함수가 있다면
        if hasattr(self.acs_client, "recv_c_command"):
            # 실행 결과 return
            return self.acs_client.recv_c_command()
        # 함수가 없으면 디버그 문구 print
        print("[ACS] ACS_Client에 recv_c_command 함수가 X")
        return None

    # 현재 주행 작업 정보 저장 함수 선언
    def save_drive_job(self, target_node, work_type, landmark, dobot_job):
        with self.state_lock:
            # ACS 보고용 target_node는 항상 4자리 숫자로 저장
            self.target_node = self.to_acs_node(target_node)
            # Work type 저장
            self.work_type = work_type
            # 랜드마크 저장
            self.landmark = landmark
            # dobot 작업 상태 저장
            self.dobot_job = dobot_job
            # 주행 상태 1로 변경
            self.move_flag = "1"
            # 주행 시작 시간 저장
            self.drive_start_time = None
        # 주행 정보 print
        print("==================================================")
        print("[ACS] 주행 작업 저장")
        print(f"[ACS] target_node : {self.target_node}")
        print(f"[ACS] work_type   : {work_type}")
        print(f"[ACS] landmark    : {landmark}")
        print(f"[ACS] dobot_job   : {dobot_job}")
        print("==================================================")

    # SEER 주행 시작 함수 선언 (주행 전 동작 추가 시 입력)
    def start_seer_drive(self, landmark):
        try:
            # SEER gotarget 송신
            self.seer.gotarget(landmark)
            # 상태 변경
            with self.state_lock:
                # AMR 상태를 주행 상태로 변경
                self.state = self.STATE_DRIVING
                # 주행 flag 주행중으로 변경
                self.move_flag = "1"
                # 주행 시작 시간 저장
                self.drive_start_time = time.time()
            # ACS에 주행 시작 상태 즉시 보고
            self.send_s_now(reason="주행 시작")
        except Exception as error:
            self.set_error_state(f"SEER gotarget 실패 : {error}")

    # ==================================================================
    # 4. 주행 상태 확인 / 장애물 확인 / 도착 처리 함수 선언
    # ==================================================================
    # 주행 상태 처리 함수 선언
    def process_driving_status(self):
        # ----------------------------------------------------------
        # 기존 SEER 주행 상태 확인
        # ----------------------------------------------------------
        # SEER task_status 조회
        task_status = self.get_task_status()
        # 조회한 AMR 상태 출력
        print(f"[SEER] task_status : {task_status}")
        # task_status 조회 실패 시 에러 처리
        if task_status is None:
            # 조회 실패 에러 표출
            self.set_error_state("SEER task_status 조회 실패")
            # 함수 종료
            return
        # task_status에 따라 move_flag 변경
        self.update_move_flag(task_status)
        # 완료 상태가 아니면 종료
        if task_status != self.TASK_COMPLETED:
            return
        # 주행 시작 시간 복사
        with self.state_lock:
            drive_start_time = self.drive_start_time
        # gotarget 직후 이전 완료값 4가 남아있을 수 있으므로 0.5초 방어
        if drive_start_time is not None:
            if time.time() - drive_start_time <= 0.5:
                return
        # 도착 완료 처리
        self.process_arrival()

    # 도착 완료 처리 함수 선언(목적지 도착 후 해야할일이 있으면 여기에 추가)
    def process_arrival(self):
        # 현재 작업 정보 복사
        with self.state_lock:
            landmark = self.landmark
            dobot_job = self.dobot_job
            self.move_flag = "0"
        print("==================================================")
        print(f"[ACS] 목적지 도착 완료 : {landmark}")
        print("==================================================")
        # 주행한 목적지가 LM14라면
        if landmark == "LM14":
            # 측면 D435 ArUco 검출 객체 생성
            detector = Aruco_Detector_Commu(aruco_length=0.05)
            # 정답 ArUco 저장/로드 객체 생성
            answer = Aruco_Answer_Commu(answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_2.json")
            # 측면 카메라 기반 ArUco 도킹 객체 생성
            docker = Aruco_Dock_Commu(seer=self.seer,detector=detector,answer=answer,yaw_tolerance_deg=0.1,x_tolerance_m=0.05,yaw_direction_sign=1.0,move_direction_sign=1.0)
            # D435 카메라 시작
            detector.camera_start()
            # 저장된 정답 ArUco JSON 기준으로 도킹 실행
            dock_ok = docker.align_to_answer_marker(max_step=300,search_w=0.08,show_window=True,path=answer_path)
            # 도킹 실패 시
            if not dock_ok:
                # 도킹 실패 로그 출력
                print("[AMR] 도킹 실패")
                # D435 카메라 종료
                detector.camera_end()
            # 도킹 성공 시
            else:
                # 도킹 성공 로그 출력
                print("[AMR] 도킹 성공")
                # D435 카메라 종료
                detector.camera_end()
                # 0.1초 대기
                time.sleep(0.1)
                # Ezi Motor 클래스의 Table Open 함수 실행
                table_open_ok = self.motor.table_open() 
                # Table Open 결과가 실패인 경우
                if not table_open_ok:
                    # Controller 에러 상태 설정
                    self.set_error_state("LM14 Table Open 실패")
                    # 이후 Dobot 작업을 실행하지 않고 종료
                    return 
             # Table Open 성공 로그 출력
            print("==================================================")
            print("[ACS] LM14 Table Open 완료")
            print("==================================================")
            # 테이블 동작 후 안정화를 위해 잠시 대기
            time.sleep(0.1)
        # 주행한 목적지가 LM22라면
        if landmark == "LM22":
            # 측면 D435 ArUco 검출 객체 생성
            detector = Aruco_Detector_Commu(aruco_length=0.05)
            # 정답 ArUco 저장/로드 객체 생성
            answer = Aruco_Answer_Commu(answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_3.json")
            # 측면 카메라 기반 ArUco 도킹 객체 생성
            docker = Aruco_Dock_Commu(seer=seer,detector=detector,answer=answer,yaw_tolerance_deg=0.1,x_tolerance_m=0.05,yaw_direction_sign=1.0,move_direction_sign=1.0)
            # D435 카메라 시작
            detector.camera_start()
            # 저장된 정답 ArUco JSON 기준으로 도킹 실행
            dock_ok = docker.align_to_answer_marker(max_step=300,search_w=0.06,show_window=True,path=answer_path)
            # 도킹 실패 시
            if not dock_ok:
                # 도킹 실패 로그 출력
                print("[AMR] 도킹 실패")
                # D435 카메라 종료
                detector.camera_end()
            # 도킹 성공 시
            else:
                # 도킹 성공 로그 출력
                print("[AMR] 도킹 성공")
                # D435 카메라 종료
                detector.camera_end()
                # 주행한 목적지가 LM22라면
        if landmark == "LM25":
            # 측면 D435 ArUco 검출 객체 생성
            detector = Aruco_Detector_Commu(aruco_length=0.05)
            # 정답 ArUco 저장/로드 객체 생성
            answer = Aruco_Answer_Commu(answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_1.json")
            # 측면 카메라 기반 ArUco 도킹 객체 생성
            docker = Aruco_Dock_Commu(seer=seer,detector=detector,answer=answer,yaw_tolerance_deg=0.1,x_tolerance_m=0.05,yaw_direction_sign=1.0,move_direction_sign=1.0)
            # D435 카메라 시작
            detector.camera_start()
            # 저장된 정답 ArUco JSON 기준으로 도킹 실행
            dock_ok = docker.align_to_answer_marker(max_step=300,search_w=0.06,show_window=True,path=answer_path)
            # 도킹 실패 시
            if not dock_ok:
                # 도킹 실패 로그 출력
                print("[AMR] 도킹 실패")
                # D435 카메라 종료
                detector.camera_end()
            # 도킹 성공 시
            else:
                # 도킹 성공 로그 출력
                print("[AMR] 도킹 성공")
                # D435 카메라 종료
                detector.camera_end()
        # 주행한 목적지가 LM33라면
        if landmark == "LM33":
            # 측면 D435 ArUco 검출 객체 생성
            detector = Aruco_Detector_Commu(aruco_length=0.05)
            # 정답 ArUco 저장/로드 객체 생성
            answer = Aruco_Answer_Commu(answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_4.json")
            # 측면 카메라 기반 ArUco 도킹 객체 생성
            docker = Aruco_Dock_Commu(seer=seer,detector=detector,answer=answer,yaw_tolerance_deg=0.1,x_tolerance_m=0.05,yaw_direction_sign=1.0,move_direction_sign=1.0)
            # D435 카메라 시작
            detector.camera_start()
            # 저장된 정답 ArUco JSON 기준으로 도킹 실행
            dock_ok = docker.align_to_answer_marker(max_step=300,search_w=0.06,show_window=True,path=answer_path)
            # 도킹 실패 시
            if not dock_ok:
                # 도킹 실패 로그 출력
                print("[AMR] 도킹 실패")
                # D435 카메라 종료
                detector.camera_end()
            # 도킹 성공 시
            else:
                # 도킹 성공 로그 출력
                print("[AMR] 도킹 성공")
                # D435 카메라 종료
                detector.camera_end()           
        # 주행한 목적지가 LM36라면
        if landmark == "LM36":
            # 측면 D435 ArUco 검출 객체 생성
            detector = Aruco_Detector_Commu(aruco_length=0.05)
            # 정답 ArUco 저장/로드 객체 생성
            answer = Aruco_Answer_Commu(answer_path="/home/mic-711/Desktop/SamDisplay/side_docking_package/aruco_answer_json/aruco_5.json")
            # 측면 카메라 기반 ArUco 도킹 객체 생성
            docker = Aruco_Dock_Commu(seer=seer,detector=detector,answer=answer,yaw_tolerance_deg=0.1,x_tolerance_m=0.05,yaw_direction_sign=1.0,move_direction_sign=1.0)
            # D435 카메라 시작
            detector.camera_start()
            # 저장된 정답 ArUco JSON 기준으로 도킹 실행
            dock_ok = docker.align_to_answer_marker(max_step=300,search_w=0.06,show_window=True,path=answer_path)
            # 도킹 실패 시
            if not dock_ok:
                # 도킹 실패 로그 출력
                print("[AMR] 도킹 실패")
                # D435 카메라 종료
                detector.camera_end()
            # 도킹 성공 시
            else:
                # 도킹 성공 로그 출력
                print("[AMR] 도킹 성공")
                # D435 카메라 종료
                detector.camera_end()        
        # ACS에 도착 완료 즉시 보고
        self.send_s_now(reason="목적지 도착 완료")
        # Dobot 작업이 없으면
        if dobot_job is None:
            # 대기 상태로 진입
            self.set_wait_state()
            return
        # Dobot AQ/DP 작업 수행
        ok, msg, cmd_id = self.run_dobot_job(dobot_job)
        # Dobot 작업이 ok가 아니라면
        if not ok:
            # 에러 표출
            self.set_error_state(f"Dobot 작업 실패 : cmd_id={cmd_id}, msg={msg}")
            return
        # 주행한 목적지가 LM14라면
        if landmark == "LM14":
            # Dobot 작업 후 테이블 닫기
            self.motor.table_close()
        # 직압 싱테 print
        print("==================================================")
        print("[DOBOT] 작업 완료")
        print(f"[DOBOT] cmd_id : {cmd_id}")
        print(f"[DOBOT] msg    : {msg}")
        print("==================================================")
        # Dobot 작업 성공 후 ACS L/U 송신 후 실패시
        if not self.send_acs_done(dobot_job):
            # 에러 표출
            self.set_error_state("ACS L/U 송신 실패")
            return
        # 대기 상태 복귀
        self.set_wait_state()

    # Dobot 완료 후 ACS L/U 송신 함수 선언
    def send_acs_done(self, dobot_job):
        # 현재 target_node 복사
        with self.state_lock:
            target_node = self.target_node
        # ACS 완료 명령 추출
        acs_done = dobot_job["acs_done"]
        # Loading 완료이면 L Command 송신
        if acs_done == "L":
            print(f"[ACS] L Command 송신 : loading_node={target_node}")
            ok = self.send_l_command(target_node)
            if ok:
                self.set_carry_flag("1")
            return ok
        # Unloading 완료이면 U Command 송신
        if acs_done == "U":
            print(f"[ACS] U Command 송신 : unloading_node={target_node}")
            ok = self.send_u_command(target_node)
            if ok:
                self.set_carry_flag("0")
            return ok
        print(f"[ACS] 알 수 없는 완료 명령 : {acs_done}")
        return False

    # ==================================================================
    # 5. ACS S/T/C/L/U 송신
    # ==================================================================
    # S Command 즉시 송신 함수 선언
    def send_s_now(self, reason=""):
        # ACS 연결이 없으면 실패
        if not self.acs_client.is_connected():
            return False
        # 현재 보고값 복사
        with self.state_lock:
            target_node = self.target_node
            carry_flag = self.carry_flag
            move_flag = self.move_flag
        print("--------------------------------------------------")
        print("[ACS] S Command 송신")
        # 상태 변경 이유가 있으면
        if reason:
            # print
            print(f"[ACS] reason      : {reason}")
        print(f"[ACS] target_node : {target_node}")
        print(f"[ACS] carry_flag  : {carry_flag}")
        print(f"[ACS] move_flag   : {move_flag}")
        print("--------------------------------------------------")
        # ACS로 S 명령 송신은 동시에 호출되지 않도록 Lock 적용
        with self.acs_send_lock:
            ok = self.acs_client.send_s_command(
                target_node=target_node,
                carry_flag=carry_flag,
                move_flag=move_flag)
        # 송신 성공 시 마지막 송신 시간 갱신
        if ok:
            self.last_s_send_time = time.time()
        # ok return
        return ok

    # T Command 즉시 송신 함수 선언
    def send_t_now(self, location_node, reason=""):
        # ACS 연결이 없으면
        if not self.acs_client.is_connected():
            # 실패
            return False
        # ACS 위치 노드는 4자리 숫자 문자열로 정리
        location_node = str(location_node).strip().zfill(4)
        print("--------------------------------------------------")
        print("[ACS] T Command 송신")
        if reason:
            print(f"[ACS] reason        : {reason}")
        print(f"[ACS] location_node : {location_node}")
        print("--------------------------------------------------")
        # ACS 송신은 동시에 호출되지 않도록 Lock 적용
        with self.acs_send_lock:
            ok = self.acs_client.send_t_command(location_node=location_node)
        # 송신 성공 시 마지막 송신 시간 갱신
        if ok:
            self.last_t_send_time = time.time()
        return ok

    # C Command 응답 송신 함수 선언
    def send_c_response(self, target_node, work_type="00"):
        with self.acs_send_lock:
            return self.acs_client.send_c_response(
                target_node=target_node,
                work_type=work_type)

    # L Command 송신 함수 선언
    def send_l_command(self, loading_node):
        with self.acs_send_lock:
            return self.acs_client.send_l_command(loading_node=loading_node)

    # U Command 송신 함수 선언
    def send_u_command(self, unloading_node):
        with self.acs_send_lock:
            return self.acs_client.send_u_command(unloading_node=unloading_node)

    # ==================================================================
    # 6. S/T 주기 보고 Thread 관련 함수
    # ==================================================================
    # S/T 보고 Thread 시작 함수 선언
    def start_report_threads(self):
        # S 명령 송신 Thread 객체 선언
        self.s_report_thread = threading.Thread(
            target=self.s_report_loop,
            daemon=True)
        # T 명령 송신 Thread 객체 선언
        self.t_report_thread = threading.Thread(
            target=self.t_report_loop,
            daemon=True)
        # S 명령 송신 시작
        self.s_report_thread.start()
        # T 명령 송신 시작
        self.t_report_thread.start()
        # 디버그 문구 Print
        print("==================================================")
        print("[ACS] S/T Report Thread 시작")
        print("==================================================")

    # S Command 주기 보고 Loop 함수 선언
    def s_report_loop(self):
        while not self.stop_event.is_set():
            if self.acs_client.is_connected():
                if time.time() - self.last_s_send_time >= self.s_period_sec:
                    self.send_s_now(reason="주기 보고")
            time.sleep(0.05)

    # T Command 주기 보고 Loop 함수 선언
    def t_report_loop(self):
        while not self.stop_event.is_set():
            # 현재 AMR Node 조회
            amr_node = self.get_amr_node()
            location_node = self.amr_node_to_location_node(amr_node)
            # 위치가 바뀌었으면 즉시 T 보고
            if location_node is not None and location_node != self.last_location_node:
                self.last_location_node = location_node
                self.send_t_now(location_node, reason=f"AMR Node 갱신 : {amr_node}")
            # 위치가 그대로여도 주기적으로 T 보고
            if self.last_location_node is not None:
                if time.time() - self.last_t_send_time >= self.t_period_sec:
                    self.send_t_now(self.last_location_node, reason="주기 보고")
            time.sleep(self.node_check_period_sec)

    # ==================================================================
    # 7. SEER 상태 조회
    # ==================================================================
    # SEER task_status 조회 함수 선언
    def get_task_status(self):
        try:
            with self.seer_lock:
                # 최신 SEER_commu 기준 get_task_status 우선 사용
                if hasattr(self.seer, "get_task_status"):
                    task_status = self.seer.get_task_status()
                # 구버전 호환용 task_end 사용
                elif hasattr(self.seer, "task_end"):
                    task_status = self.seer.task_end()
                else:
                    return None
            try:
                return int(task_status)
            except Exception:
                return task_status
        except Exception as error:
            print(f"[SEER] task_status 조회 실패 : {error}")
            return None

    # SEER 현재 AMR Node 조회 함수 선언
    def get_amr_node(self):
        try:
            if not hasattr(self.seer, "get_amr_node"):
                return None
            with self.seer_lock:
                amr_node = self.seer.get_amr_node()
            if amr_node is False or amr_node is None:
                return None
            amr_node = str(amr_node).strip().upper()
            if amr_node == "":
                return None
            return amr_node
        except Exception as error:
            print(f"[SEER] AMR Node 조회 실패 : {error}")
            return None

    # task_status 기준 move_flag 갱신 함수 선언
    def update_move_flag(self, task_status):
        # 정지/막힘이면 move_flag 0 보고
        if task_status == self.TASK_STOPPED:
            with self.state_lock:
                changed = self.move_flag != "0"
                self.move_flag = "0"
            if changed:
                self.send_s_now(reason="주행 중 정지 감지")
            return
        # 주행 중이면 move_flag 1 보고
        if task_status == self.TASK_RUNNING:
            with self.state_lock:
                changed = self.move_flag != "1"
                self.move_flag = "1"
            if changed:
                self.send_s_now(reason="주행 재개 감지")
            return

    # ==================================================================
    # 8. 변환 / 매핑 / 상태 변경
    # ==================================================================
    # ACS 숫자 target_node를 SEER landmark로 변환하는 함수 선언
    def target_node_to_landmark(self, target_node):
        # 문자열 정리
        target_node = str(target_node).strip().upper()
        # 빈 값이면 변환 실패
        if target_node == "":
            return None
        # 이미 LM으로 들어오면 그대로 사용
        if target_node.startswith("LM"):
            return target_node
        # ACS는 0001, 0014 같은 숫자 target_node를 보낸다는 기준
        if target_node.isdigit():
            return f"LM{int(target_node)}"
        # 숫자도 LM도 아니면 변환 실패
        return None

    # SEER AMR Node를 ACS Location Node 4자리로 변환하는 함수 선언
    def amr_node_to_location_node(self, amr_node):
        if amr_node is None:
            return None
        amr_node = str(amr_node).strip().upper()
        if amr_node == "":
            return None
        # ex) LM14이면 14 추출
        if amr_node.startswith("LM"):
            node_number = amr_node[2:]
        else:
            node_number = amr_node
        # 숫자가 아니면 변환 실패
        if not node_number.isdigit():
            return None
        # ACS Location Node는 4자리 숫자로 보고
        return str(int(node_number)).zfill(4)

    # ACS 송신용 node를 4자리 숫자로 변환하는 함수 선언
    def to_acs_node(self, node):
        node = str(node).strip().upper()
        # LM14이면 0014로 변환
        if node.startswith("LM") and node[2:].isdigit():
            return str(int(node[2:])).zfill(4)
        # 14, 0014 모두 0014로 변환
        if node.isdigit():
            return str(int(node)).zfill(4)
        # 그 외는 원본 반환
        return node

    # landmark + work_type 기준 Dobot 작업 조회 함수 선언
    def find_dobot_job(self, landmark, work_type):
        landmark = str(landmark).strip().upper()
        work_type = str(work_type).strip().zfill(2)
        return self.DOBOT_JOB_MAP.get((landmark, work_type))

    # carry_flag 설정 함수 선언
    def set_carry_flag(self, carry_flag):
        carry_flag = str(carry_flag).strip()
        # 허용값이 아니면 실패
        if carry_flag not in ["0", "1", "2", "3"]:
            print(f"[ACS] 잘못된 carry_flag : {carry_flag}")
            return False
        # carry_flag 갱신
        with self.state_lock:
            self.carry_flag = carry_flag
            self.last_s_send_time = 0.0
        return True

    # 대기 상태 전환 함수 선언
    def set_wait_state(self):
        with self.state_lock:
            self.state = self.STATE_WAIT
            self.move_flag = "0"
            self.work_type = "00"
            self.landmark = None
            self.dobot_job = None
            self.drive_start_time = None
            self.last_s_send_time = 0.0
        print("--------------------------------------------------")
        print("[ACS] 대기 상태 전환")
        print(f"[ACS] carry_flag : {self.carry_flag}")
        print(f"[ACS] move_flag  : {self.move_flag}")
        print("--------------------------------------------------")

    # 에러 상태 전환 함수 선언
    def set_error_state(self, reason=""):
        with self.state_lock:
            self.state = self.STATE_ERROR
            self.move_flag = "0"
        print("==================================================")
        print("[ACS] ERROR 상태 전환")
        if reason:
            print(f"[ACS] reason : {reason}")
        print("==================================================")
        # ACS에 정지 상태 즉시 보고
        self.send_s_now(reason="ERROR 상태 전환")
