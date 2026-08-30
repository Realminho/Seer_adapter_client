# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------------------- 사용자 정의 라이브러리 import -----------------------
# 삼성 디스플레이 ACS 연동 클래스 import
from custom_package.sam_acs_amr_client import SAM_ACS_AMR_Client
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
            # 디버그 문구 print
            print("==================================================")
            print(f"[ACS DRIVER] SEER Landmark 주행 시작 : {landmark}")
            print("==================================================")
            # 수신한 랜드마크로 주행 시작
            nav_ok = self.seer.gotargetblock(landmark)
            # 주행 성공 시
            if nav_ok:
                # 디버그 문구 print
                print("==================================================")
                print(f"[ACS DRIVER] 목적지 도착 완료 : {landmark}")
                print("==================================================")
                # True return
                return True
            # 주행 실패 시
            print("==================================================")
            print(f"[ACS DRIVER] 목적지 주행 실패 : {landmark}")
            print("==================================================")
            # False return
            return False
        # 예외 발생 시
        except Exception as error:
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
        # C 명령 수신 후 자동 응답 수행
        recv_c_command = self.acs_client.recv_and_reply_c_command(wait_timeout_sec=self.wait_timeout_sec)
        # C 명령 수신 실패 시
        if recv_c_command is None:
            # 디버그 문구 print
            print("[ACS DRIVER] C 명령 수신 또는 응답 실패")
            # False return
            return False
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
            # 무한 반복
            while True:
                # C 명령 수신 후 자동 응답 수행
                recv_c_command = self.acs_client.recv_and_reply_c_command(wait_timeout_sec=self.wait_timeout_sec)
                # C 명령 수신 실패 시   
                if recv_c_command is None:
                    # 디버그 문구 print
                    print("[ACS DRIVER] C 명령 수신 실패 -> 1초 후 다음 명령 대기")
                    # 1초 대기
                    time.sleep(1.0)
                    # 다음 loop 진행, 재시도
                    continue
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
                # 1초 대기
                time.sleep(1.0)
        # ctrl+c가 눌렸으면
        except KeyboardInterrupt:
            # 디버그 문구 print
            print("[ACS DRIVER] 사용자 종료 요청")
        # 최종적으로
        finally:
            # ACS 소켓 종료
            self.acs_client.close_socket()
            # SEER 소켓 종료
            self.seer.socket_close()
            # 디버그 문구 print
            print("[ACS DRIVER] ACS 기반 주행 종료 완료")

