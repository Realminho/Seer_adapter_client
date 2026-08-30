# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 병렬 처리를 위한 threading 라이브러리 import
import threading
# ----------------------- 사용자 정의 라이브러리 import -----------------------
# 삼성 디스플레이 ACS 연동 클래스 import
from sam_acs_package.sam_acs_commu import SAM_ACS_AMR_Client

# 삼성 디스플레이 S 명령 주기 보고 클래스 선언
class S_Command_Reporter:
    # 클래스 초기화 함수 선언
    def __init__(self,acs_client,send_period_sec=1.0):
        # ACS client 객체 저장
        self.acs_client = acs_client
        # S 명령 송신 주기 저장
        self.send_period_sec = send_period_sec
        # 현재 target_node 저장 변수 선언
        self.current_target_node = "0000"
        # 현재 carry_flag 저장 변수 선언
        self.current_carry_flag = "0"
        # 현재 move_flag 저장 변수 선언
        self.current_move_flag = "0"
        # 변수 핸들링 중복 방지를 위해 lock 선언
        self.state_lock = threading.Lock()
        # S 명령 송신 Thread 종료 flag 선언
        self.stop_flag = False
        # S 명령 송신 Thread 저장 변수 선언
        self.report_thread = None
    # 상태값 갱신 함수 선언
    def update_state(self,target_node=None,carry_flag=None,move_flag=None):
        # 아래 변수에 lock 적용
        with self.state_lock:
            # target_node가 입력되면
            if target_node is not None:
                # 4자리 문자열로 변환
                self.current_target_node = str(target_node).strip().zfill(4)
            # carry_flag가 입력되면
            if carry_flag is not None:
                # 문자열로 저장
                self.current_carry_flag = str(carry_flag).strip()
            # move_flag가 입력되면
            if move_flag is not None:
                # 문자열로 저장
                self.current_move_flag = str(move_flag).strip()
        # 디버그 문구 print
        print("--------------------------------------------------")
        print("[S REPORTER] 상태값 갱신 완료")
        print(f"[S REPORTER] target_node : {self.current_target_node}")
        print(f"[S REPORTER] carry_flag  : {self.current_carry_flag}")
        print(f"[S REPORTER] move_flag   : {self.current_move_flag}")
        print("--------------------------------------------------")
    # 적재 상태 설정 함수 선언
    def set_carry_flag(self,carry_flag):
        # 인자로 받은 적재 상태를 문자열로 변환
        carry_flag = str(carry_flag).strip()
        # 허용된 값이 아니면
        if carry_flag not in ["0","1","2","3"]:
            # 디버그 문구 print
            print(f"[S REPORTER] carry_flag 설정 실패 : {carry_flag}")
            # False return
            return False
        # 적재 상태 갱신
        self.update_state(carry_flag=carry_flag)
        # True return
        return True
    # 현재 S 명령 구성 변수 조회 함수 선언
    def get_s_command_state(self):
        # lock 적용
        with self.state_lock:
            # S 명령 구성 요소 return
            return (self.current_target_node, self.current_carry_flag, self.current_move_flag)
    # S 명령 1회 송신 함수 선언
    def send_s_command(self):
        # 현재 상태 확인
        target_node, carry_flag, move_flag = self.get_s_command_state()
        # 디버그 문구 print
        print("--------------------------------------------------")
        print("[S REPORTER] S 명령 송신")
        print(f"[S REPORTER] target_node : {target_node}")
        print(f"[S REPORTER] carry_flag  : {carry_flag}")
        print(f"[S REPORTER] move_flag   : {move_flag}")
        print("--------------------------------------------------")
        # ACS와 연결되어있지 않다면
        if self.acs_client.client_socket is None:
            # 디버그 문구 print
            print("[S REPORTER] S 명령 송신 생략 : ACS 소켓 연결 안됨")
            # False return
            return False
        # S 명령 송신 후 결과 return
        return self.acs_client.send_s_status_report(target_node=target_node,carry_flag=carry_flag,move_flag=move_flag)
    # S 명령 송신 루프 함수 선언
    def s_command_report_loop(self):
        # 종료 flag가 False인 동안 반복
        while not self.stop_flag:
            # S 명령 1회 송신
            self.send_s_command()
            # 설정한 주기만큼 대기
            time.sleep(self.send_period_sec)
    # S 명령 주기 송신 시작 함수 선언
    def s_command_report_start(self):
        # 이미 스레드가 동작중이면
        if self.report_thread is not None and self.report_thread.is_alive():
            # 디버그 문구 print
            print("[S REPORTER] S 명령 송신 스레드 이미 실행 중")
            # 함수 종료
            return
        # 종료 flag 초기화
        self.stop_flag = False
        # 스레드 생성
        self.report_thread = threading.Thread(target=self.s_command_report_loop, daemon=True)
        # 스레드 시작
        self.report_thread.start()
        # 디버그 문구 print
        print("[S REPORTER] S 명령 송신 스레드 정상 시작 완료")
    # S 명령 주기 송신 종료 함수 선언
    def s_command_report_stop(self):
        # 종료 flag 설정
        self.stop_flag = True
        # 스레드가 존재하고 살아있으면
        if self.report_thread is not None and self.report_thread.is_alive():
            # 최대 2초 대기
            self.report_thread.join(timeout=2.0)
        # 디버그 문구 print
        print("[S REPORTER] S 명령 송신 스레드 종료 완료")    
    



