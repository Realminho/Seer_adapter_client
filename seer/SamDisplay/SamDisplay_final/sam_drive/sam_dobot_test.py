# ----------------------- 범용 라이브러리 import -----------------------
# 시간 처리를 위한 time 라이브러리 import
import time
# ----------------------- 사용자 정의 라이브러리 import -----------------------
# Dobot 통신 클래스 import
from custom_package.dobot_commu import Dobot_Commu
# ----------------------- Dobot TCP Server 설정 -----------------------
# Dobot 접속을 받을 AMR 서버 IP
# 0.0.0.0 : 현재 장비의 모든 네트워크 인터페이스에서 접속 허용
DOBOT_SERVER_IP = "0.0.0.0"
# Dobot 접속을 받을 AMR 서버 Port
DOBOT_SERVER_PORT = 12321
# Dobot 통신 Timeout
DOBOT_TIMEOUT_SEC = 3600
# 테스트용 Tag
TEST_TAG = "MLMA001"

# ----------------------- 메인 함수 선언 -----------------------
def main():
    # Dobot 통신 객체 생성
    dobot = Dobot_Commu(
        ip=DOBOT_SERVER_IP,
        port=DOBOT_SERVER_PORT,
        timeout_sec=DOBOT_TIMEOUT_SEC)
    try:
        # Dobot TCP Server 시작
        server_ok = dobot.start_server()
        # Server 시작 실패 시 종료
        if not server_ok:
            print("[TEST] Dobot TCP Server 시작 실패")
            return

        # Dobot Client 접속 대기
        client_ok = dobot.wait_client()

        # Client 접속 실패 시 종료
        if not client_ok:
            print("[TEST] Dobot Client 접속 실패")
            return

        # 접속 성공 출력
        print("==================================================")
        print("[TEST] Dobot Client 접속 성공")
        print("==================================================")

        # ----------------------- 1. HEALTHCHECK 테스트 -----------------------
        print("\n========== HEALTHCHECK 테스트 시작 ==========")

        health_ok, health_msg, health_cmd_id = dobot.do_healthcheck()

        print("[TEST] HEALTHCHECK 결과")
        print(f"[TEST] ok     : {health_ok}")
        print(f"[TEST] msg    : {health_msg}")
        print(f"[TEST] cmd_id : {health_cmd_id}")

        if not health_ok:
            print("[TEST] HEALTHCHECK 실패로 테스트 종료")
            return

        # 잠시 대기
        time.sleep(1.0)

        # ----------------------- 2. HOME 테스트 -----------------------
        print("\n========== HOME 테스트 시작 ==========")
        home_ok, home_msg, home_cmd_id = dobot.do_home_check()
        print("[TEST] HOME 결과")
        print(f"[TEST] ok     : {home_ok}")
        print(f"[TEST] msg    : {home_msg}")
        print(f"[TEST] cmd_id : {home_cmd_id}")
        if not home_ok:
            print("[TEST] HOME 실패로 테스트 종료")
            return
        # 잠시 대기
        time.sleep(1.0)
        # ----------------------- 3. AQ 테스트 -----------------------
        print("\n========== AQ 테스트 시작 ==========")
        aq_ok, aq_msg, aq_cmd_id = dobot.do_aq(TEST_TAG)
        print("[TEST] AQ 결과")
        print(f"[TEST] ok     : {aq_ok}")
        print(f"[TEST] msg    : {aq_msg}")
        print(f"[TEST] cmd_id : {aq_cmd_id}")
        # 잠시 대기
        time.sleep(1.0)

        # ----------------------- 4. DP 테스트 -----------------------
        print("\n========== DP 테스트 시작 ==========")

        dp_ok, dp_msg, dp_cmd_id = dobot.do_dp(TEST_TAG)

        print("[TEST] DP 결과")
        print(f"[TEST] ok     : {dp_ok}")
        print(f"[TEST] msg    : {dp_msg}")
        print(f"[TEST] cmd_id : {dp_cmd_id}")

        # 테스트 완료 출력
        print("\n==================================================")
        print("[TEST] Dobot 통신 테스트 완료")
        print("==================================================")

    except KeyboardInterrupt:
        # 사용자 종료 처리
        print("\n[TEST] 사용자가 테스트를 중단했습니다.")

    except Exception as error:
        # 예외 출력
        print("\n==================================================")
        print(f"[TEST] Dobot 통신 테스트 중 예외 발생 : {error}")
        print("==================================================")

    finally:
        # Dobot TCP Socket 종료
        dobot.close_all()

        # 종료 출력
        print("[TEST] Dobot 통신 테스트 종료")

# ----------------------- Python 파일 직접 실행 시 main 함수 실행 -----------------------
if __name__ == "__main__":
    main()