# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 이미지 처리를 위한 cv2 라이브러리 import
import cv2

# =============================================================
# 측면 카메라 기반 아루코마커 Yaw 전용 도킹 클래스 선언
# =============================================================
class Aruco_Dock_Commu_New:
    # 클래스 초기화 함수 선언
    def __init__(
        self,
        seer,
        detector,
        answer,
        yaw_tolerance_deg=1.0,
        yaw_direction_sign=1.0):
        # AMR 제어 객체 저장 변수 선언
        self.seer = seer
        # ArUco 검출 객체 저장 변수 선언
        self.detector = detector
        # 정답 ArUco 저장/로드 객체 저장 변수 선언
        self.answer = answer
        # Yaw 허용 오차 저장 변수 선언
        self.yaw_tolerance_deg = yaw_tolerance_deg
        # Yaw 회전 방향 설정 변수 저장
        self.yaw_direction_sign = yaw_direction_sign

    # =========================================
    # 제어에 필요한 값 연산 및 보정 관련 함수 선언
    # =========================================

    # 각도를 -180도 ~ 180도 범위로 정규화하는 함수 선언
    def normalize_angle_deg(self, angle_deg):
        # 입력 각도가 None이면
        if angle_deg is None:
            # 계산할 수 없으므로 None 반환
            return None
        # 각도가 180도보다 크면
        while angle_deg > 180.0:
            # 360도를 빼서 범위 안으로 설정
            angle_deg -= 360.0
        # 각도가 -180도보다 작으면
        while angle_deg < -180.0:
            # 360도를 더해서 범위 안으로 설정
            angle_deg += 360.0
        # 정규화된 각도 반환
        return angle_deg

    # 특정 값에 대해서 최소/최대 절댓값을 제한하는 함수 선언
    def clamp_signed(self, value, min_abs, max_abs):
        # 값이 0이면
        if value == 0:
            # 그대로 0 반환
            return 0.0
        # 값이 양수면 sign을 1.0으로 설정하고,
        # 음수면 sign을 -1.0으로 설정
        sign = 1.0 if value > 0 else -1.0
        # 절댓값을 min_abs 이상 max_abs 이하로 제한
        limited_abs = max(min(abs(value), max_abs), min_abs)
        # 제한된 값에 원래 부호를 다시 적용해서 반환
        return sign * limited_abs

    # 출력할 값이 None인지 확인하고 문자열로 변환하는 함수 선언
    def format_value(self, value, decimal_places=4):
        # 값이 None이면
        if value is None:
            # None 문자열 반환
            return "None"
        # 숫자로 변환할 수 있다면
        try:
            # 지정한 소수점 자리수에 맞게 문자열 반환
            return f"{float(value):.{decimal_places}f}"
        # 숫자로 변환할 수 없다면
        except (TypeError, ValueError):
            # 원래 값을 문자열로 변환해서 반환
            return str(value)

    # 두 값의 차이를 계산하는 함수 선언
    def calculate_error(self, current_value, target_value):
        # 현재 값 또는 정답 값이 None이면
        if current_value is None or target_value is None:
            # 차이를 계산할 수 없으므로 None 반환
            return None
        # 숫자 변환 및 차이 계산 시도
        try:
            # 현재 값 - 정답 값 반환
            return float(current_value) - float(target_value)
        # 숫자로 변환할 수 없다면
        except (TypeError, ValueError):
            # 차이를 계산할 수 없으므로 None 반환
            return None

    # =========================================
    # AMR 제어 관련 함수 선언
    # =========================================

    # AMR 정지 명령 함수 선언
    def stop_motion(self, duration=0.2):
        # AMR에 정지 명령 전송
        self.seer.motion_control(
            vx=0.0,
            vy=0.0,
            w=0.0,
            duration=duration)
        # 명령 적용 후 0.1초 대기
        time.sleep(0.1)

    # 정답 마커 기준으로 AMR의 Yaw만 정렬하는 함수 선언
    def align_to_answer_marker(
        self,
        max_step=300,
        search_w=0.06,
        show_window=True,
        path=None):
        # 저장된 정답 마커 정보 로드
        answer_data = self.answer.load_answer_aruco(path=path)
        # 정답 마커 정보가 없다면
        if answer_data is None:
            # 정답 정보 없음 로그 출력
            print("[ARUCO_DOCK] 저장된 정답 마커가 없습니다.")
            # 정렬 실패이므로 False 반환
            return False
        # 정답 ArUco ID 불러오기
        target_id = answer_data.get("aruco_id")
        # 정답 마커 X 좌표 불러오기
        target_marker_x = answer_data.get("target_marker_x")
        # 정답 마커 Y 좌표 불러오기
        target_marker_y = answer_data.get("target_marker_y")
        # 정답 마커 Z 좌표 불러오기
        target_marker_z = answer_data.get("target_marker_z")
        # 정답 마커 Yaw 값 불러오기
        target_yaw_deg = answer_data.get("target_yaw_deg")
        # 도킹에 반드시 필요한 ArUco ID 또는 Yaw 값이 없다면
        if target_id is None or target_yaw_deg is None:
            # 정답 정보 부족 로그 출력
            print("[ARUCO_DOCK] 정답 마커 정보가 부족합니다.")
            # 현재 정답 데이터 출력
            print(answer_data)
            # 정렬 실패이므로 False 반환
            return False
        # 도킹 시작 로그 출력
        print("=" * 70)
        print("[ARUCO_DOCK] 측면 카메라 ArUco Yaw 전용 도킹 시작")
        print("=" * 70)
        # 정답 ArUco ID 출력
        print(f"[ARUCO_DOCK] target_id       : {target_id}")
        # 정답 마커 X 좌표 출력
        print(
            f"[ARUCO_DOCK] target_marker_x : "
            f"{self.format_value(target_marker_x)}")
        # 정답 마커 Y 좌표 출력
        print(
            f"[ARUCO_DOCK] target_marker_y : "
            f"{self.format_value(target_marker_y)}")
        # 정답 마커 Z 좌표 출력
        print(
            f"[ARUCO_DOCK] target_marker_z : "
            f"{self.format_value(target_marker_z)}")
        # 정답 마커 Yaw 값 출력
        print(
            f"[ARUCO_DOCK] target_yaw_deg  : "
            f"{self.format_value(target_yaw_deg, 3)}")
        # Yaw만 제어한다는 안내 로그 출력
        print("[ARUCO_DOCK] 제어 대상은 Yaw이며 X, Y, Z는 출력만 합니다.")
        # 최대 보정 횟수만큼 반복
        for step in range(max_step):
            # 현재 마커 정보를 여러 프레임 중앙값 기반으로 안정화해서 불러오기
            current_marker = self.detector.get_stable_marker(
                target_id=target_id,
                sample_count=10,
                show_window=show_window)
            # 카메라 화면을 표시하는 경우
            if show_window:
                # OpenCV 화면 이벤트 처리
                key = cv2.waitKey(1) & 0xFF
                # ESC 키가 눌렸다면
                if key == 27:
                    # 사용자 중단 로그 출력
                    print("[ARUCO_DOCK] ESC 입력으로 도킹을 중단합니다.")
                    # AMR 정지
                    self.stop_motion(duration=0.3)
                    # 정렬 실패로 처리
                    return False
            # 현재 마커를 찾지 못했다면
            if current_marker is None:
                # 마커 미검출 로그 출력
                print(
                    f"[ARUCO_DOCK] step={step + 1}, "
                    f"마커 미검출 -> 탐색 회전")
                # 제자리 회전으로 마커 탐색
                self.seer.motion_control(
                    vx=0.0,
                    vy=0.0,
                    w=search_w,
                    duration=0.25)
                # 회전 후 잠시 정지
                self.stop_motion(duration=0.1)
                # 다음 반복으로 넘어가기
                continue
            # 현재 마커 X 좌표 불러오기
            current_marker_x = current_marker.get("marker_x")
            # 현재 마커 Y 좌표 불러오기
            current_marker_y = current_marker.get("marker_y")
            # 현재 마커 Z 좌표 불러오기
            current_marker_z = current_marker.get("marker_z")
            # 현재 마커 Yaw 값 불러오기
            current_yaw_deg = current_marker.get("yaw_deg")
            # 현재 마커 중심의 이미지 중심 기준 픽셀 오차 불러오기
            current_offset_px = current_marker.get("offset_px")
            # 현재 Yaw 값을 계산할 수 없다면
            if current_yaw_deg is None:
                # 계산 실패 로그 출력
                print(
                    f"[ARUCO_DOCK] step={step + 1}, "
                    f"Yaw 계산 실패")
                # 다음 반복으로 넘어가기
                continue
            # 현재 X 좌표와 정답 X 좌표의 차이 계산
            x_error_m = self.calculate_error(
                current_value=current_marker_x,
                target_value=target_marker_x)
            # 현재 Y 좌표와 정답 Y 좌표의 차이 계산
            y_error_m = self.calculate_error(
                current_value=current_marker_y,
                target_value=target_marker_y)
            # 현재 Z 좌표와 정답 Z 좌표의 차이 계산
            z_error_m = self.calculate_error(
                current_value=current_marker_z,
                target_value=target_marker_z)
            # 현재 Yaw와 정답 Yaw의 차이 계산
            yaw_error_deg = (
                float(current_yaw_deg)
                - float(target_yaw_deg))
            # Yaw 오차를 -180도 ~ 180도 범위로 정규화
            yaw_error_deg = self.normalize_angle_deg(yaw_error_deg)
            # Yaw 오차가 허용 범위 안인지 확인
            yaw_ok = (
                abs(yaw_error_deg)
                <= self.yaw_tolerance_deg)
            # 로그 구분선 출력
            print("-" * 70)
            # 현재 Step 번호 출력
            print(f"[ARUCO_DOCK] step : {step + 1}")
            # ArUco ID 출력
            print(f"[ARUCO_DOCK] aruco_id : {target_id}")
            # 표 제목 출력
            print(
                "[ARUCO_DOCK] 항목       "
                "정답 값       "
                "현재 값       "
                "차이")
            # X 좌표의 정답 값, 현재 값, 차이 출력
            print(
                "[ARUCO_DOCK] X(m)       "
                f"{self.format_value(target_marker_x):>10}    "
                f"{self.format_value(current_marker_x):>10}    "
                f"{self.format_value(x_error_m):>10}")
            # Y 좌표의 정답 값, 현재 값, 차이 출력
            print(
                "[ARUCO_DOCK] Y(m)       "
                f"{self.format_value(target_marker_y):>10}    "
                f"{self.format_value(current_marker_y):>10}    "
                f"{self.format_value(y_error_m):>10}")
            # Z 좌표의 정답 값, 현재 값, 차이 출력
            print(
                "[ARUCO_DOCK] Z(m)       "
                f"{self.format_value(target_marker_z):>10}    "
                f"{self.format_value(current_marker_z):>10}    "
                f"{self.format_value(z_error_m):>10}")
            # Yaw의 정답 값, 현재 값, 차이 출력
            print(
                "[ARUCO_DOCK] Yaw(deg)   "
                f"{self.format_value(target_yaw_deg, 3):>10}    "
                f"{self.format_value(current_yaw_deg, 3):>10}    "
                f"{self.format_value(yaw_error_deg, 3):>10}")
            # 현재 픽셀 오차 출력
            print(
                f"[ARUCO_DOCK] current_offset_px : "
                f"{self.format_value(current_offset_px, 2)}")
            # 현재 Yaw 허용 오차 상태 출력
            print(
                f"[ARUCO_DOCK] yaw_ok            : "
                f"{yaw_ok}")
            # 현재 Yaw 오차가 허용 범위 안이라면
            if yaw_ok:
                # 정렬 완료 로그 출력
                print(
                    f"[ARUCO_DOCK] Yaw 정렬 완료 "
                    f"({yaw_error_deg:.3f} deg)")
                # AMR 정지
                self.stop_motion(duration=0.3)
                # 정렬 성공이므로 True 반환
                return True
            # Yaw 오차가 허용 범위를 벗어났다면
            if not yaw_ok:
                # Yaw 오차에 비례하여 회전 속도 계산
                w_cmd = -0.012 * yaw_error_deg
                # 회전 방향 설정값 적용
                w_cmd *= self.yaw_direction_sign
                # 회전 속도를 최소/최대 범위로 제한
                w_cmd = self.clamp_signed(
                    value=w_cmd,
                    min_abs=0.035,
                    max_abs=0.08)
                # Yaw 보정 로그 출력
                print(
                    f"[ARUCO_DOCK] Yaw 보정 -> "
                    f"w={w_cmd:.3f}")
                # AMR에 회전 명령 전송
                self.seer.motion_control(
                    vx=0.0,
                    vy=0.0,
                    w=w_cmd,
                    duration=0.25)
                # 회전 후 잠시 정지
                self.stop_motion(duration=0.15)
                # 다음 반복으로 넘어가기
                continue
        # 최대 보정 횟수를 초과했다면 실패 로그 출력
        print("[ARUCO_DOCK] 최대 보정 횟수 초과로 Yaw 정렬 실패")
        # AMR 정지
        self.stop_motion(duration=0.5)
        # 정렬 실패이므로 False 반환
        return False