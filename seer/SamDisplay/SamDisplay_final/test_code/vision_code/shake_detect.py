#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# Dobot과 통신을 위한 TCP_server_commu_sam 클래스 import
from custom_package.tcp_server_commu_sam import TCP_server_commu_sam
# 정답 아루코마커 정보 로드을 위한 Answer_Aruco_commu 클래스 import
from custom_package.dock_commu import Answer_Aruco_commu
# 아루코마커 감지를 위한 Detect_Aruco_commu 클래스 import
from custom_package.dock_commu import Detect_Aruco_commu

# ----------- DOBOT과 통신을 위한 TCP 파라미터 선언 -------------
# TCP 서버 IP 선언
TCP_SERVER_IP = "0.0.0.0"
# TCP 서버 Port 선언
TCP_SERVER_PORT = 12321

# ----------------------- Shake 작업 파라미터 선언 -----------------------
# Shake 감지 요청 파라미터 선언
SHAKE_FLAG = 1


# 흔들림 감지 함수 선언
def detect_shake(tcp):
    # 정답 아루코 정보 로드 객체 선언
    storage = Answer_Aruco_commu()
    # 현재 아루코 감지 객체 선언
    detector = Detect_Aruco_commu()

    # 저장된 정답 아루코 정보 로드
    target_info = storage.load_aruco_info()

    # 저장된 정답 아루코 정보가 없으면
    if target_info is None:
        # 에러 문구 출력
        print("[ERROR] 저장된 정답 아루코 정보가 없습니다.")
        # False return
        return False

    # 정답 아루코 ID 추출
    target_id = target_info.get("aruco_id")
    # 정답 yaw 값 추출
    target_yaw_deg = target_info.get("target_yaw_deg")
    # 정답 x 좌표 추출
    target_x_cord = target_info.get("target_x_cord")
    # 정답 y 좌표 추출
    target_y_cord = target_info.get("target_y_cord")

    # 정답 아루코 ID가 없으면
    if target_id is None:
        # 에러 문구 출력
        print("[ERROR] 저장된 정답 아루코 ID가 없습니다.")
        # False return
        return False

    # 카메라 시작
    detector.camera_start()

    # 예외가 없으면
    try:
        # 무한 반복
        while True:
            # TCP 데이터 수신
            rx_data = tcp.recv_data()

            # 수신 데이터가 없으면
            if rx_data is None:
                # 잠시 대기
                time.sleep(0.05)
                # 다음 반복 진행
                continue

            # 수신 데이터가 bytes면 문자열로 변환
            if isinstance(rx_data, bytes):
                # utf-8 문자열로 decode 후 공백 제거
                rx_text = rx_data.decode("utf-8").strip()
            # 문자열이면 그대로 사용
            else:
                # 문자열로 변환 후 공백 제거
                rx_text = str(rx_data).strip()

            # 빈 문자열이면 다음 반복 진행
            if rx_text == "":
                # 잠시 대기
                time.sleep(0.05)
                # 다음 반복 진행
                continue

            # 수신 데이터 출력
            print(f"[TCP] 수신 데이터 : {rx_text}")

            # 수신 데이터가 shake 요청이 아니면
            if rx_text != str(SHAKE_FLAG):
                # 안내 문구 출력
                print("[TCP] Shake 요청이 아니므로 대기합니다.")
                # 다음 반복 진행
                continue

            # shake 요청 수신 안내 문구 출력
            print("[TCP] Shake 요청 수신 -> 현재 아루코 정보 확인 시작")

            # 현재 프레임에서 정답 ID와 같은 아루코마커 검출
            current_info = detector.detect_aruco(target_id=target_id, show_window=True)

            # 현재 검출 정보가 없으면
            if current_info is None:
                # 에러 문구 출력
                print("[ARUCO] detect_aruco 결과가 None 입니다.")
                # 다음 반복 진행
                continue

            # 현재 프레임에서 정답 아루코를 찾지 못했으면
            if not current_info.get("found", False):
                # 에러 문구 출력
                print("[ARUCO] 현재 프레임에서 정답 아루코마커를 찾지 못했습니다.")
                # 다음 반복 진행
                continue

            # 현재 yaw 값 추출
            current_yaw_deg = current_info.get("yaw_deg")
            # 현재 x 좌표 추출
            current_x_cord = current_info.get("marker_x")
            # 현재 y 좌표 추출
            current_y_cord = current_info.get("marker_y")

            # yaw 차이 저장 변수 선언
            yaw_diff_deg = None
            # x 차이 저장 변수 선언
            x_diff_m = None
            # y 차이 저장 변수 선언
            y_diff_m = None

            # 정답 yaw와 현재 yaw가 모두 있으면
            if target_yaw_deg is not None and current_yaw_deg is not None:
                # yaw 차이 계산
                yaw_diff_deg = current_yaw_deg - target_yaw_deg

                # yaw 차이를 -180 ~ 180 범위로 보정
                if yaw_diff_deg > 180:
                    yaw_diff_deg -= 360
                elif yaw_diff_deg < -180:
                    yaw_diff_deg += 360

            # 정답 x와 현재 x가 모두 있으면
            if target_x_cord is not None and current_x_cord is not None:
                # x 차이 계산
                x_diff_m = current_x_cord - target_x_cord

            # 정답 y와 현재 y가 모두 있으면
            if target_y_cord is not None and current_y_cord is not None:
                # y 차이 계산
                y_diff_m = current_y_cord - target_y_cord

            # 구분선 출력
            print("=" * 70)
            # 제목 출력
            print("[ARUCO] 정답 아루코 / 현재 아루코 차이 결과")
            # 구분선 출력
            print("=" * 70)
            # 정답 아루코 ID 출력
            print(f"[ARUCO] target_id         : {target_id}")
            # 현재 아루코 ID 출력
            print(f"[ARUCO] current_id        : {current_info.get('aruco_id')}")
            # 정답 yaw 출력
            print(f"[ARUCO] target_yaw_deg    : {target_yaw_deg}")
            # 현재 yaw 출력
            print(f"[ARUCO] current_yaw_deg   : {current_yaw_deg}")
            # yaw 차이 출력
            print(f"[ARUCO] yaw_diff_deg      : {yaw_diff_deg}")
            # 정답 x 출력
            print(f"[ARUCO] target_x_cord     : {target_x_cord}")
            # 현재 x 출력
            print(f"[ARUCO] current_x_cord    : {current_x_cord}")
            # x 차이 출력
            print(f"[ARUCO] x_diff_m          : {x_diff_m}")
            # 정답 y 출력
            print(f"[ARUCO] target_y_cord     : {target_y_cord}")
            # 현재 y 출력
            print(f"[ARUCO] current_y_cord    : {current_y_cord}")
            # y 차이 출력
            print(f"[ARUCO] y_diff_m          : {y_diff_m}")
            # 구분선 출력
            print("=" * 70)

    # 사용자가 Ctrl + C 로 종료한 경우
    except KeyboardInterrupt:
        # 종료 문구 출력
        print("[INFO] 사용자 종료")

    # 그 외 예외 발생 시
    except Exception as e:
        # 에러 문구 출력
        print(f"[ERROR] detect_shake 실행 중 에러 발생 : {e}")

    # 종료 처리
    finally:
        # 카메라 종료
        detector.camera_stop()

    # 함수 종료
    return True


# main 함수 선언
def main():
    # TCP_server_commu_sam 객체 선언
    tcp = TCP_server_commu_sam(ip=TCP_SERVER_IP, port=TCP_SERVER_PORT)

    # 서버 오픈 실패 시
    if not tcp.start_server():
        # 에러 문구 출력
        print("[ERROR] TCP 서버 오픈 실패")
        # 함수 종료
        return

    # 클라이언트 접속 대기 실패 시
    if not tcp.wait_client():
        # 에러 문구 출력
        print("[ERROR] TCP 클라이언트 접속 실패")
        # 전체 소켓 닫기
        tcp.close_all()
        # 함수 종료
        return

    # 예외가 없으면
    try:
        # 흔들림 감지 함수 실행
        detect_shake(tcp)

    # 종료 시 항상 소켓 정리
    finally:
        # 전체 소켓 닫기
        tcp.close_all()


# 직접 실행 시 main 함수 호출
if __name__ == "__main__":
    main()


        
    




