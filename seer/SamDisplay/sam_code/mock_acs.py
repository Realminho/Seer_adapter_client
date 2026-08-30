# ----------------------- 범용 라이브러리 import -----------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ---------------------- custom 라이브러리 import ----------------------
# AMR과 연동하여 명령을 송신하기 위한 Mock_SAM_ACS_Server_commu 클래스 import
from sam_acs_package.mock_sam_acs_commu import Mock_SAM_ACS_Server_commu
# -------------------------- 필요 변수들 선언 ----------------------------
# Server IP 선언
SERVER_IP = "192.168.1.197"
# Server Port 선언
SERVER_PORT = 1331
# 응답 대기 timeout 선언
RESPONSE_WAIT_TIMEOUT = 60.0

# 메인 함수 선언
def main():
    # Mock ACS 서버 객체 선언
    mock_acs = Mock_SAM_ACS_Server_commu(server_ip=SERVER_IP,server_port=SERVER_PORT)
    # 서버 시작 실패 시
    if not mock_acs.start_acs_server():
        # 코드 종료
        return
    # 클라이언트 접속 대기 실패 시
    if not mock_acs.wait_client():
        # 소켓 모두 닫기
        mock_acs.close_all()
        # 코드 종료
        return
    # 사용자에게 AMR ID 입력 받기
    amr_id = mock_acs.input_amr_id()
    # 사용자에게 목적지 번호 입력 받기
    target_node = mock_acs.input_target_node()
    # 안내 문구 출력
    print("=========================================================")
    print("[MOCK ACS] 테스트 준비 완료")
    print(f"[MOCK ACS] AMR ID      : {amr_id}")
    print(f"[MOCK ACS] TARGET NODE : {target_node}")
    print("[MOCK ACS] 스페이스바 → C 명령 송신")
    print("[MOCK ACS] ESC → 종료")
    print("=========================================================")
    # 에러가 없으면
    try:
        # 무한 반복
        while True:
            # 키 입력 상태 확인
            key_status = mock_acs.check_keyboard_input()
            # 스페이스바 입력이면
            if key_status == "space":
                # C 명령 송신 후 응답 수신
                response_data = mock_acs.send_c_command_and_wait_response(
                    amr_id=amr_id,
                    target_node=target_node,
                    work_type="40",
                    wait_timeout_sec=RESPONSE_WAIT_TIMEOUT,
                )
                # 응답 파싱 성공 시
                if response_data is not None:
                    print("=========================================================")
                    print(f"[MOCK ACS] 응답 파싱 결과 : {response_data}")
                    print("=========================================================")
                # 응답 수신 실패 시
                else:
                    print("=========================================================")
                    print("[MOCK ACS] 응답 수신 실패 또는 파싱 실패")
                    print("=========================================================")
            # ESC 입력이면
            elif key_status == "esc":
                print("[MOCK ACS] 사용자 종료 요청")
                break
            # 0.1초 대기
            time.sleep(0.1)
    # Ctrl + C 종료 시
    except KeyboardInterrupt:
        print("[MOCK ACS] KeyboardInterrupt 종료")
    # 최종 종료 처리
    finally:
        mock_acs.close_all()
        print("[MOCK ACS] 프로그램 종료 완료")

# 메인 함수 실행
if __name__ == "__main__":
    main()


