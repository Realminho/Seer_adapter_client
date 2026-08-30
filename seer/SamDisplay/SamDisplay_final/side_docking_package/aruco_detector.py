# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 각도 계산 등을 위한 math 라이브러리를 import
import math
# ArUco 마커 검출과 화면 출력을 위한 cv2 라이브러리 import
import cv2
# 배열 연산과 중앙값 계산 등을 위한 numpy 라이브러리 import
import numpy as np
# Intel RealSense D435 카메라 제어를 위한 pyrealsense2 라이브러리 import
import pyrealsense2 as rs

# ======================================================================================================================
# D435 측면 카메라 ArUco 검출 클래스 선언
# ======================================================================================================================
# 아루코마커 감지 클래스 선언
class Aruco_Detector_Commu:
    # 클래스 초기화 함수 선언
    def __init__(self,aruco_length=0.10):
        # 실제 아루코마커 한변의 길이 변수 저장[m]
        self.aruco_length = aruco_length
        # 실제 아루코마커 딕셔너리 설정
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        # ArUco 검출 파라미터 객체 생성
        self.aruco_params = cv2.aruco.DetectorParameters()
        # ArUco 검출기 생성
        self.aruco_detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.aruco_params)
        # RealSense 카메라 파이프라인 객체 생성
        self.pipeline = rs.pipeline()
        # RealSense 카메라 설정 객체 생성
        self.config = rs.config()
        # RealSense 카메라 Color stream을 640x480, BGR8, 30fps로 설정
        self.config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        # RealSense 카메 Depth stream을 640x480, z16, 30fps로 설정
        self.config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        # Depth frame을 Color frame 기준으로 정렬하기 위한 객체 생성
        self.align = rs.align(rs.stream.color)
        # 카메라가 on/off flag False로 초기화
        self.camera_flag = False
        # solvePnP에서 사용할 카메라 내부 파라미터 행렬 저장 변수 선언
        self.camera_matrix = None
        # solvePnP에서 사용할 렌즈 왜곡 계수 저장 변수 선언
        self.dist_coeffs = None
        # Depth raw 값을 meter 단위로 변환하기 위한 scale 값 저장 변수 선언
        self.depth_scale = None
    # ====================================
    # 카메라 시작/종료 관련 함수 선언
    # ====================================
    # 카메라 시작 함수 선언
    def camera_start(self):
        # 카메라 on/off flag가 이미 True라면
        if self.camera_flag:
            # 중복으로 시작하지 않고 바로 종료
            return
        # 설정한 stream 정보로 RealSense pipeline 시작
        profile = self.pipeline.start(self.config)
        # 장치에서 depth sensor 객체 저장
        depth_sensor = profile.get_device().first_depth_sensor()
        # depth raw 값을 meter로 변환하기 위한 scale 값 저장
        self.depth_scale = depth_sensor.get_depth_scale()
        # color stream profile 불러오기
        color_stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
        # color 카메라 내부 파라미터 불러오기
        intr = color_stream.get_intrinsics()
        # OpenCV solvePnP용 카메라 행렬 생성
        self.camera_matrix = np.array([
            [intr.fx, 0.0, intr.ppx],
            [0.0, intr.fy, intr.ppy],
            [0.0, 0.0, 1.0]
        ], dtype=np.float64)
        # 렌즈 왜곡 계수를 numpy 배열로 저장
        self.dist_coeffs = np.array(intr.coeffs, dtype=np.float64)
        # 카메라 시작 flag를 True로 변경
        self.camera_flag = True
        # 카메라 시작 완료 로그 출력
        print("[SIDE_CAMERA] D435 카메라 시작 완료")
    
    # 카메라 종료 함수 선언
    def camera_end(self):
        # 카메라 on/off flag가 True라면
        if self.camera_flag:
            # RealSense pipeline 정지
            self.pipeline.stop()
            # 카메라 시작 flag를 False로 변경
            self.camera_flag = False
            # 카메라 종료 완료 로그 출력
            print("[SIDE_CAMERA] D435 카메라 종료 완료")
        # cv2 창 모두 종료
        cv2.destroyAllWindows()
    # ==============================================================
    # Solve PnP용 연산 관련 함수 선언
    # ==============================================================
    # 회전행렬에서 yaw 각도 계산 함수 선언
    def cal_yaw_deg(self, rotation_matrix):
        # 회전행렬의 x-y 성분을 이용해 yaw radian 값을 연산
        yaw_rad = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
        # radian 값을 degree 값으로 변환하여 return
        return float(math.degrees(yaw_rad))
    # ==============================================================
    # 아루코마커 검출 관련 함수 선언
    # ==============================================================
    # 현재 카메라 프레임에서 target_id 해당 마커 검출 함수 선언
    def detect_marker(self,target_id=None,show_window=True):
        # 카메라가 on/off falg가 True가 아니라면
        if not self.camera_flag:
            # 카메라 시작
            self.camera_start()
        # RealSense 카메라에서 최신 프레임 불러오기
        frames = self.pipeline.wait_for_frames()
        # Depth frame을 Color frame 기준으로 정렬
        aligned_frames = self.align.process(frames)
        # 정렬된 Color frame 저장
        color_frame = aligned_frames.get_color_frame()
        # 정렬된 Depth frame 저장
        depth_frame = aligned_frames.get_depth_frame()
        # Color frame 이나 Depth frame이 정상적으로 저장되지 않았다면
        if not color_frame or not depth_frame:
            # None return
            return None
        # Color frame을 OpenCV에서 사용할 수 있는 numpy 이미지로 변환
        color_img = np.asanyarray(color_frame.get_data())
        # Color 이미지에서 ArUco 마커 검출
        corners_list, ids, _ = self.aruco_detector.detectMarkers(color_img)
        # 최종 반환할 마커 정보를 None으로 초기화
        marker_info = None
        # 하나 이상의 ArUco 마커가 검출되었다면
        if ids is not None and len(ids) > 0:
            # ids 배열을 1차원 배열로 변환
            ids = ids.flatten()
            # 검출된 각 마커에 대해 반복
            for corners, marker_id in zip(corners_list, ids):
                # 현재 마커 ID를 int 형태로 변환
                marker_id = int(marker_id)
                # target_id가 지정되어 있고 현재 마커 ID가 target_id와 다르면
                if target_id is not None and marker_id != int(target_id):
                    # 현재 마커는 무시하고 다음 마커 확인
                    continue
                # 마커 꼭짓점 좌표를 4x2 형태로 정리
                corners = corners.reshape((4, 2))
                # 이미지 화면안에서의 마커 중심 x 픽셀 좌표 계산
                cx = int(np.mean(corners[:, 0]))
                # 이미지 화면안에서의 마커 중심 y 픽셀 좌표 계산
                cy = int(np.mean(corners[:, 1]))
                # 이미지 중심 x 좌표를 계산
                img_center_x = int(color_img.shape[1] / 2)
                # 마커 중심이 이미지 중심에서 얼마나 벗어났는지 픽셀 단위로 계산
                offset_px = int(cx - img_center_x)
                # solvePnP에서 사용할 실제 마커 꼭짓점 3D 좌표 정의
                obj_points = np.array([
                    [-self.aruco_length / 2,  self.aruco_length / 2, 0],
                    [ self.aruco_length / 2,  self.aruco_length / 2, 0],
                    [ self.aruco_length / 2, -self.aruco_length / 2, 0],
                    [-self.aruco_length / 2, -self.aruco_length / 2, 0]
                ], dtype=np.float32)
                # yaw 각도 저장 변수 None으로 초기화
                yaw_deg = None
                # 실제 마커 x 좌표 저장 변수 None으로 초기화
                marker_x = None
                # 마커 y 좌표 저장 변수 None으로 초기화
                marker_y = None
                # 마커 z 좌표 저장 변수 None으로 초기화
                marker_z = None
                # 회전벡터 저장 변수 None으로 초기화
                rvec_list = None
                # 이동벡터 저장 변수 None으로 초기화
                tvec_list = None
                # 에러가 없으면
                try:
                    # solvePnP를 사용하여 카메라 기준 마커의 위치와 자세 추정
                    success, rvec, tvec = cv2.solvePnP(
                        obj_points,
                        corners.astype(np.float32),
                        self.camera_matrix,
                        self.dist_coeffs,
                        flags=cv2.SOLVEPNP_IPPE_SQUARE)
                    # solvePnP가 성공했다면
                    if success:
                        # 회전 벡터를 회전 행렬로 변환
                        rotation_matrix, _ = cv2.Rodrigues(rvec)
                        # 회전 행렬에서 yaw 각도를 계산
                        yaw_deg = self.cal_yaw_deg(rotation_matrix)
                        # tvec의 x 성분을 marker_x로 저장
                        marker_x = float(tvec[0][0])
                        # tvec의 y 성분을 marker_y로 저장
                        marker_y = float(tvec[1][0])
                        # tvec의 z 성분을 marker_z로 저장
                        marker_z = float(tvec[2][0])
                        # 회전 벡터 list 형태로 변환
                        rvec_list = rvec.reshape(-1).tolist()
                        # 이동 벡터 list 형태로 변환
                        tvec_list = tvec.reshape(-1).tolist()
                        # 이미지 위에 마커 기준 좌표축 표시
                        cv2.drawFrameAxes(
                            color_img,
                            self.camera_matrix,
                            self.dist_coeffs,
                            rvec,
                            tvec,
                            self.aruco_length * 0.5)
                # solvePnP 계산 중 예외가 발생하면
                except Exception as error:
                    # solvePnP 실패 로그 출력
                    print(f"[SIDE_ARUCO_DETECT] solvePnP 실패 : {error}")
                # 마커 외곽선을 그리기 위해 꼭짓점 좌표 int32 형태로 변환
                corners_int = corners.astype(np.int32).reshape((-1, 1, 2))
                # 검출된 마커 외곽선을 이미지에 초록색 선으로 표시
                cv2.polylines(color_img, [corners_int], True, (0, 255, 0), 2)
                # 검출된 마커 중심점을 이미지에 파란색 점으로 표시
                cv2.circle(color_img, (cx, cy), 5, (255, 0, 0), -1)
                # 마커 bbox 기준 x 최소값을 계산
                x_min = int(np.min(corners[:, 0]))
                # 마커 bbox 기준 y 최소값을 계산
                y_min = int(np.min(corners[:, 1]))
                # 마커 ID와 화면 중심 offset 정보 이미지에 표시
                cv2.putText(
                    color_img,
                    f"ID:{marker_id} offset:{offset_px}px",
                    (x_min, max(20, y_min - 60)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2)
                # yaw 값이 계산되었으면 yaw 값을 표시하고, 아니면 N/A로 표시
                cv2.putText(
                    color_img,
                    f"yaw:{yaw_deg:.2f}" if yaw_deg is not None else "yaw:N/A",
                    (x_min, max(20, y_min - 40)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2)
                # marker_x, marker_z 값이 있으면 이미지에 표시, 아니면 N/A로 표시
                cv2.putText(
                    color_img,
                    f"x:{marker_x:.3f} z:{marker_z:.3f}" if marker_x is not None else "x:N/A z:N/A",
                    (x_min, max(20, y_min - 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 0),
                    2)
                # 현재 마커 정보를 dict 형태로 구성
                marker_info = {
                    # 마커 검출 성공 여부 True로 저장
                    "found": True,
                    # 검출된 아루코마커 ID 저장
                    "aruco_id": marker_id,
                    # 마커 중심 x 좌표 저장
                    "cx": cx,
                    # 마커 중심 y 좌표 저장
                    "cy": cy,
                    # 이미지 중심 x 좌표 저장
                    "img_center_x": img_center_x,
                    # 화면 중심 대비 마커 중심 offset 값 저장
                    "offset_px": offset_px,
                    # 계산된 yaw 각도 저장
                    "yaw_deg": yaw_deg,
                    # solvePnP 기준 x 좌표 저장
                    "marker_x": marker_x,
                    # solvePnP 기준 y 좌표 저장
                    "marker_y": marker_y,
                    # solvePnP 기준 z 좌표 저장
                    "marker_z": marker_z,
                    # 회전 벡터 저장
                    "rvec": rvec_list,
                    # 이동 벡터 저장
                    "tvec": tvec_list,
                    # 현재 시간 저장
                    "timestamp": time.time()}
                # target_id에 해당하는 마커를 찾았으므로 반복 종료
                break
        # 마커 정보가 없다면
        if marker_info is None:
            # 마커 미검출 정보를 dict 형태로 구성합니다.
            marker_info = {
                "found": False,
                "aruco_id": target_id,
                "cx": None,
                "cy": None,
                "img_center_x": int(color_img.shape[1] / 2),
                "offset_px": None,
                "yaw_deg": None,
                "marker_x": None,
                "marker_y": None,
                "marker_z": None,
                "rvec": None,
                "tvec": None,
                "timestamp": time.time()}
        # 화면 출력 옵션이 True라면
        if show_window:
            # 이미지 중심 x 좌표 계산
            img_center_x = int(color_img.shape[1] / 2)
            # 이미지 중앙 기준선을 표시
            cv2.line(color_img, (img_center_x, 0), (img_center_x, color_img.shape[0]), (255, 255, 0), 1)
            # 검출 결과 이미지를 OpenCV 창으로 출력
            cv2.imshow("Side D435 Aruco Detector", color_img)
            # OpenCV 창 갱신을 위해 1ms 대기
            # cv2.waitKey(1)
        # 최종 마커 정보를 반환
        return marker_info
    
    # 여러 프레임을 읽어서 중앙값 기반으로 안정적인 마커 정보 생성 함수 선언
    def get_stable_marker(self, target_id, sample_count=10, show_window=True):
        # 유효한 마커 정보를 저장할 리스트를 생성
        valid_list = []
        # sample_count 횟수만큼 반복
        for _ in range(sample_count):
            # 현재 프레임에서 target_id 마커 검출
            marker_info = self.detect_marker(target_id=target_id, show_window=show_window)
            # 마커 정보가 있고 검출 성공 상태라면
            if marker_info is not None and marker_info.get("found"):
                # yaw와 marker_x가 정상 계산된 경우만 사용
                if marker_info.get("yaw_deg") is not None and marker_info.get("marker_x") is not None:
                    # 유효 샘플 리스트에 추가
                    valid_list.append(marker_info)
            # 다음 프레임을 읽기 전 0.05초 대기
            time.sleep(0.05)
        # 유효한 샘플이 하나도 없다면
        if len(valid_list) == 0:
            # None 반환
            return None

        # 특정 key의 중앙값 계산 함수 선언
        def median_value(key):
            # valid_list에서 해당 key 값이 None이 아닌 값 추출
            values = [item[key] for item in valid_list if item.get(key) is not None]
            # 추출된 값이 없다면
            if len(values) == 0:
                # None을 반환
                return None
            # 중앙값을 계산해서 float 형태로 반환
            return float(np.median(values))
        # 안정화된 마커 정보를 dict 형태로 구성
        stable_info = {
            "found": True,
            "aruco_id": int(target_id),
            "offset_px": int(median_value("offset_px")),
            "yaw_deg": median_value("yaw_deg"),
            "marker_x": median_value("marker_x"),
            "marker_y": median_value("marker_y"),
            "marker_z": median_value("marker_z"),
            "timestamp": time.time()}
        # 안정화된 마커 정보를 반환
        return stable_info    


                


