# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 이미지 처리를 위한 cv2 라이브러리 import
import cv2
# =============================================================
# 측면 카메라 기반 아루코마커 도킹 클래스 선언
# =============================================================
class Aruco_Dock_Commu_Only_Yaw:
    # 클래스 초기화 함수 선언 
    def __init__(self,seer,detector,answer,yaw_tolerance_deg=1.0,yaw_direction_sign=1.0):
        # AMR 제어 객체 저장 변수 선언
        self.seer = seer
        # ArUco 검출 객체 저장 변수 선언
        self.detector = detector
        # 정답 ArUco 저장/로드 객체를 저장
        self.answer = answer
        # yaw 허용 오차 저장 변수 선언
        self.yaw_tolerance_deg = yaw_tolerance_deg
        # yaw 회전 방향 설정 변수 저장
        self.yaw_direction_sign = yaw_direction_sign
    # =========================================
    # 제어에 필요한 값들 연산 및 보정 관련 함수 선언
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
    
    # 특정 값에 대해서 최소/최대 절댓값을 제한 함수 선언
    def clamp_signed(self, value, min_abs, max_abs):
        # 값이 0이면
        if value == 0:
            # 그대로 0 반환
            return 0.0
        # 값이 양수면 sign을 1.0로 설정, 음수면 -1.0로 설정
        sign = 1.0 if value > 0 else -1.0
        # 절댓값을 min_abs 이상 max_abs 이하로 제한
        limited_abs = max(min(abs(value), max_abs), min_abs)
        # 제한된 값에 원래 부호를 다시 적용해서 반환
        return sign * limited_abs
    # =========================================
    # AMR 제어 관련 함수 선언
    # =========================================
    # AMR 정지 명령 함수
    def stop_motion(self, duration=0.2):
        # AMR에 정지 명령을 전송합니다.
        self.seer.motion_control(vx=0.0, vy=0.0, w=0.0, duration=duration)
        # 명령 적용 후 0.1초 대기
        time.sleep(0.1)
    
    # 정답 마커 기준으로 AMR 정렬 함수 선언
    def align_to_answer_marker(self, max_step=300, search_w=0.06, show_window=True,path=None):
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
        # 정답 yaw 값 불러오기
        target_yaw_deg = answer_data.get("target_yaw_deg")
        # 도킹에 필요한 필수 정답 정보(마커 각도) 중 하나라도 없다면
        if target_id is None or target_yaw_deg is None:
            # 정답 정보 부족 로그 출력
            print("[ARUCO_DOCK] 정답 마커 정보 부족.")
            # 현재 정답 데이터 출력
            print(answer_data)
            # 정렬 실패이므로 False 반환
            return False
        # 도킹 시작 로그 출력
        print("[ARUCO_DOCK] 측면 카메라 ArUco 도킹 시작")
        # 정답 ArUco ID 출력
        print(f"[ARUCO_DOCK] target_id: {target_id}")
        # 정답 yaw 값 출력
        print(f"[ARUCO_DOCK] target_yaw_deg  : {target_yaw_deg}")
        # 최대 보정 횟수만큼 반복합니다.
        for step in range(max_step):
            # 현재 마커 정보를 여러 프레임 중앙값 기반으로 안정화해서 불러오기
            current_marker = self.detector.get_stable_marker(
                target_id=target_id,
                sample_count=10,
                show_window=show_window)
            if show_window:
                cv2.waitKey(1)
            # 현재 마커를 찾지 못했다면
            if current_marker is None:
                # 마커 미검출 로그 출력
                print(f"[ARUCO_DOCK] step={step + 1}, 마커 미검출 -> 탐색 회전")
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
            # 현재 yaw 값 불러오기
            current_yaw_deg = current_marker.get("yaw_deg")
            # 현재 yaw 또는 marker_x가 없다면
            if current_yaw_deg is None:
                # 계산 실패 로그를 출력
                print(f"[ARUCO_DOCK] step={step + 1}, yaw 계산 실패")
                # 다음 반복으로 넘어가기
                continue
            # 현재 yaw와 정답 yaw의 차이를 계산
            yaw_error_deg = current_yaw_deg - float(target_yaw_deg)
            # yaw 오차를 -180도 ~ 180도 범위로 정규화
            yaw_error_deg = self.normalize_angle_deg(yaw_error_deg)
            # yaw 오차가 허용 범위 안인지 확인
            yaw_ok = abs(yaw_error_deg) <= self.yaw_tolerance_deg
            # 로그 구분선 출력
            print("-" * 60)
            # 현재 step 번호 출력
            print(f"[ARUCO_DOCK] step={step + 1}")
            # 현재 yaw 값 출력
            print(f"[ARUCO_DOCK] current_yaw_deg  : {current_yaw_deg:.3f}")
            # 정답 yaw 값 출력
            print(f"[ARUCO_DOCK] target_yaw_deg   : {target_yaw_deg:.3f}")
            # yaw 오차 출력
            print(f"[ARUCO_DOCK] yaw_error_deg    : {yaw_error_deg:.3f}")
            # yaw와 marker_x가 모두 허용 오차 안이라면
            if yaw_ok:
                # 정렬 완료 로그 출력
                print("[ARUCO_DOCK] 정렬 완료")
                # AMR을 정지
                self.stop_motion(duration=0.3)
                # 정렬 성공이므로 True 반환
                return True
            # yaw가 허용 범위를 벗어났다면
            if not yaw_ok:
                # yaw 오차에 비례하여 회전 속도 계산
                w_cmd = -0.012 * yaw_error_deg
                # 회전 방향 적용
                w_cmd *= self.yaw_direction_sign
                # 회전 속도를 최소/최대 범위로 제한
                w_cmd = self.clamp_signed(w_cmd, min_abs=0.035, max_abs=0.08)
                # yaw 보정 로그를 출력
                print(f"[ARUCO_DOCK] yaw 보정 -> w={w_cmd:.3f}")
                # AMR에 회전 명령을 전송
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
        print("[ARUCO_DOCK] 최대 보정 횟수 초과로 정렬 실패")
        # AMR 정지
        self.stop_motion(duration=0.5)
        # 정렬 실패이므로 False 반환
        return False            