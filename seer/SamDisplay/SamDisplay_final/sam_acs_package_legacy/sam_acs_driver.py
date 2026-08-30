# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------------------- 사용자 정의 라이브러리 import -----------------------
# 삼성 디스플레이 ACS 연동 클래스 import
from sam_acs_package.sam_acs_commu import SAM_ACS_AMR_Client
# S 명령 주기 보고 클래스 import
from sam_acs_package.sam_acs_s_commu import S_Command_Reporter
# T 명령 핸들링 클래스 import
from sam_acs_package.sam_acs_t_commu import ACS_T_commu
# SEER AMR 제어 클래스 import
from custom_package.seer_commu import SEER_commu

# 삼성디스플레이 ACS C 명령 기반 SEER 주행 클래스 선언
class ACS_Driver:
    # 클래스 초기화 함수 선언
    def __init__(self,acs_ip,acs_port,amr_id="444",wait_timeout_sec=600.0):
        # ACS IP 저장
        self.acs_ip = acs_ip
        # ACS Port 저장
        self.acs_port = acs_port
        # amr id 저장
        self.amr_id = str(amr_id).zfill(3)
        # C 명령 대기 timeout 시간 저장
        self.wait_timeout_sec = wait_timeout_sec
        # ACS TCP Client 객체 선언
        self.acs_client = SAM_ACS_AMR_Client(
            acs_ip=self.acs_ip,
            acs_port=self.acs_port,
            amr_id=self.amr_id,
            timeout_sec=self.wait_timeout_sec,
            recv_buffer_size=1024)
        # SEER 제어 객체 선언
        self.seer = SEER_commu()
        # S 명령 주기 보고 객체 선언
        self.s_reporter = S_Command_Reporter(acs_client=self.acs_client,send_period_sec=1.0)
        # T 명령 송신 객체 선언
        self.t_reporter = ACS_T_commu(amr_id=self.amr_id)
        # 대기 상태일 경우 target_node 선언
        self.default_wait_target_node = "0000"

    # C 명령에서 수신한 노드 번호를 SEER Landmark로 변환하는 함수 선언
    def convert_target_to_landmark(self, target_node):
        # 인자로 받은 목적지 노드 번호를 문자열로 변환
        target_node = str(target_node).strip()
        # 목적지 노드가 빈 문자열이면
        if not target_node:
            # None return
            return None
        # 목적지 노드가 숫자 문자열이면
        if target_node.isdigit():
            # 정수형으로 변환하여 앞의 0 제거 후 LM 붙여서 return
            return f"LM{int(target_node)}"
        # 숫자가 아닌 문자가 섞여 있으면 LM만 붙여서 return
        return f"LM{target_node}"

    # C 명령에서 목적지를 파싱하고 SEER Landmark 형식으로 변환하는 함수 선언
    def parse_landmark_from_c_command(self, c_command_data):
        # 수신한 C 명령 데이터가 없으면
        if c_command_data is None:
            # 디버그 문구 print
            print("[ACS DRIVER] C 명령 데이터가 없음")
            # None return
            return None
        # 수신한 c명령에서 target_node 키가 없으면
        if "target_node" not in c_command_data:
            # 디버그 문구 print
            print("[DRIVER] C 명령에 target_node 키가 없음")
            # None return
            return None
        # 수신한 c 명령에서 목적지 노드 번호 추출
        target_node = c_command_data["target_node"]
        # 추출한 노드 번호를 SEER용 landmark 형식으로 변환
        seer_landmark = self.convert_target_to_landmark(target_node)
        # 디버그 문구 print
        print("--------------------------------------------------")
        print(f"[ACS DRIVER] ACS target_node : {target_node}")
        print(f"[ACS DRIVER] SEER landmark   : {seer_landmark}")
        print("--------------------------------------------------")
        # 변환 결과 return
        return seer_landmark

    # 대기 상태 설정 함수 선언
    def set_wait_state(self):
        # S 명령 보고 객체 상태 갱신
        self.s_reporter.update_state(target_node=self.default_wait_target_node,move_flag="0")

    # NEW 현재 SEER Task 상태 확인 함수 선언
    def get_current_task_status(self):
        # get_task_status 함수가 있으면
        if hasattr(self.seer, "get_task_status"):
            # get_task_status 결과 return
            return self.seer.get_task_status()

        # get_task_status 함수가 없으면 기존 task_end 함수 사용
        if hasattr(self.seer, "task_end"):
            # task_end 결과 return
            return self.seer.task_end()

        # task 상태 확인 함수가 없으면
        print("[ACS DRIVER] task 상태 확인 함수가 없습니다.")
        # None return
        return None

    # SEER Landmark 주행 함수 선언
    def drive_to_landmark(self, landmark):
        # 목적지 landmark가 없으면
        if landmark is None:
            # 디버그 문구 print
            print("[ACS DRIVER] 주행 실패 : 수신한 landmark 없음")
            # False return
            return False

        # 에러가 없으면
        try:
            # 이동 상태 반영 S 명령 상태 보고 작성
            self.s_reporter.update_state(move_flag="1")

            # 디버그 문구 print
            print("==================================================")
            print(f"[ACS DRIVER] SEER Landmark 주행 시작 : {landmark}")
            print("==================================================")

            # 수신한 랜드마크로 주행 시작
            self.seer.gotarget(landmark)

            # NEW gotarget 명령 반영 대기
            time.sleep(0.5)

            # NEW 주행 시작 시간 저장
            drive_start_time = time.time()

            # 주행 완료 전까지 반복
            while True:
                # QR이 읽히면 T Command 송신
                t_result = self.t_reporter.read_qr_and_send_t_command(
                    acs_client=self.acs_client,
                    show_window=True,
                    only_new_qr=False
                )

                # T Command 처리 결과 확인
                print(f"[ACS DRIVER] T Command 처리 결과 : {t_result}")

                # 현재 Task 상태 확인
                current_task_status = self.get_current_task_status()

                # 현재 Task 상태 출력
                print(f"[ACS DRIVER] current_task_status : {current_task_status}")

                # NEW 주행 시작 후 경과 시간 계산
                drive_elapsed_time = time.time() - drive_start_time

                # 주행 완료 상태이면
                # NEW gotarget 직후 이전 task_status=4가 남아있는 경우를 막기 위해 0.5초 이후부터 도착 인정
                if current_task_status == 4 and drive_elapsed_time > 0.5:
                    # 정지 상태 반영 S 명령 상태 보고
                    self.s_reporter.update_state(move_flag="0")

                    # 디버그 문구 print
                    print("==================================================")
                    print(f"[ACS DRIVER] 목적지 도착 완료 : {landmark}")
                    print("==================================================")

                    # True return
                    return True

                # 주행 실패나 알 수 없는 상태 처리는 필요 시 추가
                if current_task_status is None:
                    # 디버그 문구 print
                    print("[ACS DRIVER] task 상태 확인 실패")

                    # 정지 상태 반영 S 명령 상태 보고
                    self.s_reporter.update_state(move_flag="0")

                    # False return
                    return False

                # 0.1초 대기
                time.sleep(0.1)

        # 예외 발생 시
        except Exception as error:
            # 정지 상태 반영 S 명령 상태 보고
            self.s_reporter.update_state(move_flag="0")

            # 디버그 문구 print
            print("==================================================")
            print(f"[ACS DRIVER] 주행 중 에러 발생 : {error}")
            print("==================================================")

            # False return
            return False

    # C 명령과 1회 수신 후 목적지로 주행하는 함수 선언
    def run_once(self):
        # ACS와 연결이 안되었으면
        if not self.acs_client.connect_to_server():
            # 디버그 문구 print
            print("[ACS DRIVER] ACS 연결 실패")
            # False return
            return False
        # S명령 초기 대기 상태 반영
        self.set_wait_state()
        # S명령 주기 송신 시작
        self.s_reporter.s_command_report_start()
        # 에러가 없으면
        try:
            # C 명령 수신 후 자동 응답 수행
            recv_c_command = self.acs_client.recv_and_reply_c_command(wait_timeout_sec=self.wait_timeout_sec)
            # C 명령 수신 실패 시
            if recv_c_command is None:
                # 디버그 문구 print
                print("[ACS DRIVER] C 명령 수신 또는 응답 실패")
                # False return
                return False
            # 수신한 C 명령에서 주행 노드 S 명령에 반영
            self.s_reporter.update_state(target_node=recv_c_command["target_node"]) 
            # 수신한 C 명령에서 SEER 목적지 landmark 추출
            landmark = self.parse_landmark_from_c_command(recv_c_command)
            # 목적지 landmark 추출 실패 시
            if landmark is None:
                # 디버그 문구 print
                print("[DRIVER] 목적지 landmark 변환 실패")
                # False return
                return False
            # 목적지로 주행 수행 후 결과 return
            return self.drive_to_landmark(landmark)
        # 최종적으로 
        finally:
            # S 명령 주기 송신 종료
            self.s_reporter.s_command_report_stop()

    # 반복해서 C 명령을 기반으로 주행하는 함수 선언
    def run_repeat(self):
        # 에러가 없으면
        try:
            # ACS 연결 시도 후 실패하였다면
            if not self.acs_client.connect_to_server():
                # 디버그 문구 print
                print("[ACS DRIVER] ACS 연결 실패로 종료")
                # 코드 종료
                return
            # T 명령용 QR 카메라 시작
            if not self.t_reporter.camera_start():
                # 디버그 문구 print
                print("[ACS DRIVER] T 명령용 QR 카메라 시작 실패로 종료")
                # 코드 종료
                return
            # 최초 대기 상태 반영
            self.set_wait_state()
            # S 명령 주기 송신 시작
            self.s_reporter.s_command_report_start()
            # 무한 반복
            while True:
                # C 명령 수신 후 자동 응답 수행
                recv_c_command = self.acs_client.recv_and_reply_c_command(wait_timeout_sec=self.wait_timeout_sec)
                # C 명령 수신 실패 시   
                if recv_c_command is None:
                    # 디버그 문구 print
                    print("[ACS DRIVER] C 명령 수신 실패 -> 1초 후 다음 명령 대기")
                    # S 명령 대기 상태로 변환
                    self.set_wait_state()
                    # 1초 대기
                    time.sleep(1.0)
                    # 다음 loop 진행, 재시도
                    continue
                # 수신한 C 명령에서 주행 노드 S 명령에 반영
                self.s_reporter.update_state(target_node=recv_c_command["target_node"])   
                # 수신한 C 명령에서 목적지 landmark 추출
                landmark = self.parse_landmark_from_c_command(recv_c_command)
                # landmark 추출 실패 시
                if landmark is None:
                    # 디버그 문구 print
                    print("[ACS DRIVER] 목적지 landmark 변환 실패 -> 1초 후 다음 명령 대기")
                    # 1초 대기
                    time.sleep(1.0)
                    # 다음 loop 진행
                    continue
                # 목적지로 주행 수행
                drive_ok = self.drive_to_landmark(landmark)
                # 주행 실패 시
                if not drive_ok:
                    # 디버그 문구 print
                    print("[ACS DRIVER] 주행 실패 -> 다음 명령 대기")
                # 다음 명령 대기 상태 반영
                self.set_wait_state()
                # 1초 대기
                time.sleep(1.0)
        # ctrl+c가 눌렸으면
        except KeyboardInterrupt:
            # 디버그 문구 print
            print("[ACS DRIVER] 사용자 종료 요청")
        # 최종적으로
        finally:
            # S 명령 송신 종료
            self.s_reporter.s_command_report_stop()
            #  T 명령 송신 종료
            self.t_reporter.camera_stop()
            # ACS 소켓 종료
            self.acs_client.close_socket()
            # SEER 소켓 종료
            self.seer.socket_close()
            # 디버그 문구 print
            print("[ACS DRIVER] ACS 기반 주행 종료 완료")

    # ACS C 명령 통신 테스트 1회 실행 함수 선언
    def run_comm_test_once(self, monitor_sec=5.0):
        # ACS와 연결이 안되었으면
        if not self.acs_client.connect_to_server():
            # 디버그 문구 print
            print("[ACS DRIVER] ACS 연결 실패")
            # False return
            return False
    
        # 에러가 없으면
        try:
            # C 명령 수신 후 자동 응답 수행
            recv_c_command = self.acs_client.recv_and_reply_c_command(
                wait_timeout_sec=self.wait_timeout_sec)
    
            # C 명령 수신 실패 시
            if recv_c_command is None:
                # 디버그 문구 print
                print("[ACS DRIVER] C 명령 수신 또는 응답 실패")
                # False return
                return False
    
            # 수신한 target_node를 S 명령 상태에 반영
            self.s_reporter.update_state(
                target_node=recv_c_command["target_node"],
                move_flag="0")
    
            # 여기서부터 S 명령 주기 송신 시작
            self.s_reporter.s_command_report_start()
    
            # 수신 결과 print
            print("==================================================")
            print(f"[ACS DRIVER] 수신한 C 명령 : {recv_c_command}")
            print("==================================================")
    
            # 반영된 상태로 잠시 유지
            start_time = time.time()
            while time.time() - start_time < monitor_sec:
                time.sleep(0.1)
    
            # True return
            return True
    
        # 최종적으로
        finally:
            # S 명령 주기 송신 종료
            self.s_reporter.s_command_report_stop()

