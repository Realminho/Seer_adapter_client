# ----------------------------- 범용 라이브러리 import -----------------------------
# 시간 핸들링을 위한 time 라이브러리 import
import time
# Json 핸들링을 위한 json 라이브러리 import
import json
# 파일 디렉토리 핸들링을 위한 Path 라이브러리 import
from pathlib import Path
# 수학적 연산을 위한 math 라이브러리 import
import math
# 이미지 핸들링을 위한 cv2 라이브러리 import
import cv2
# 배열 핸들링을 위한 numpy 라이브러리 import
import numpy as np
# Intel Realsense 카메라 제어를 위한 pyrealsense2 라이브러리 import
import pyrealsense2 as rs


# 정답 아루코마커 저장 및 정보 로드 클래스 정의
class Answer_Aruco_commu():
    # 클래스 초기화 함수 init 선언
    def __init__(self, answer_aruco_path="/home/fullmoon34213/Desktop/SamDisplay/answer_aruco_data/answer_aruco.json"):
        # 정답 아루코마커 정보를 저장할 경로를 Path 객체 형태로 저장
        self.save_path = Path(answer_aruco_path)

    # 인자로 받은 아루코마커 정보를 json 형태로 저장하는 함수 선언
    def save_target_aruco(self, target_aruco_info):
        # 아루코마커 정보를 dict 형태로 구성
        aruco_data = {
            # 아루코마커 정보 저장 시간 저장
            "target_save_time": time.time(),
            # 아루코마커 id 저장
            "aruco_id": target_aruco_info.get("aruco_id"),
            # 화면 중심 대비 x축 offset 픽셀값 저장
            "target_offset_px": target_aruco_info.get("offset_px"),
            # 아루코마커 떨어진 거리 저장
            "target_dist_m": target_aruco_info.get("dist_m"),
            # 아루코마커 yaw 각도 값 저장
            "target_yaw_deg": target_aruco_info.get("yaw_deg"),
            # 아루코마커 x 좌표 값 저장
            "target_x_cord": target_aruco_info.get("marker_x"),
            # 아루코마커 y 좌표 값 저장
            "target_y_cord": target_aruco_info.get("marker_y")
        }

        # 저장 경로의 상위 폴더가 없으면 자동 생성
        self.save_path.parent.mkdir(parents=True, exist_ok=True)

        # 저장 경로에 있는 파일 쓰기 모드로 open
        with open(self.save_path, "w", encoding="utf-8") as file:
            # aruco_data를 json 파일로 들여쓰기 적용하여 저장
            json.dump(aruco_data, file, ensure_ascii=False, indent=4)

        # 저장 완료 메시지 출력
        print(f"[ARUCO_MARKER] 아루코마커 정보 저장 완료 : {self.save_path}")

    # 저장된 아루코마커 정보 로드 함수 선언
    def load_aruco_info(self):
        # 아루코마커 저장 파일이 존재하지 않으면
        if not self.save_path.exists():
            # 디버그 문구 print
            print(f"[ARUCO_MARKER] {self.save_path} 경로에 아루코마커 정보 저장 파일 존재 X")
            # None return
            return None
        # 저장된 파일 읽기 모드로 open
        with open(self.save_path, "r", encoding="utf-8") as file:
            # JSON으로 저장된 아루코마커 정보를 dict 형태로 로드
            aruco_info = json.load(file)
        # 정보 로드 완료 문구 출력
        print("[ARUCO_MARKER] 아루코마커 정보 로드 완료")
        # 로드한 정보 return
        return aruco_info
# ======================================================================================================================

# 아루코마커 감지 클래스 선언
class Detect_Aruco_commu():
    # 클래스 초기화 함수 선언
    def __init__(self):
        # 아루코마커 한 변 길이 설정[m]
        self.aruco_length_m = 0.05
        # 아루코마커 딕셔너리 종류 설정
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        # 아루코마커 인식 파라미터 객체 선언
        self.aruco_params = cv2.aruco.DetectorParameters()
        # 아루코마커 디텍터 객체 선언
        self.aruco_detector = cv2.aruco.ArucoDetector(self.aruco_dict, self.aruco_params)
        # RealSense 파이프라인 객체 선언
        self.pipeline = rs.pipeline()
        # RealSense 스트림 설정 객체 생성
        self.config = rs.config()
        # 컬러 스트림을 640x480, BGR8 포맷, 30fps로 설정
        self.config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        # depth 스트림을 640x480, z16 포맷, 30fps로 설정
        self.config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        # depth 영상을 color 영상 기준으로 정렬하기 위한 align 객체 생성
        self.align = rs.align(rs.stream.color)
        # 카메라 시작 Flag False로 초기화
        self.camera_flag = False
        # 카메라 매트릭스 저장 변수 선언
        self.camera_matrix = None
        # 렌즈 왜곡 계수 저장 변수 선언
        self.dist_coeffs = None
        # depth 값을 m로 변환하기 위한 스케일 값 저장 변수 선언
        self.depth_scale = None

    # 카메라 시작 함수 선언
    def camera_start(self):
        # 카메라 flag가 True라면
        if self.camera_flag:
            # 바로 종료
            return
        # 설정한 스트림 정보로 카메라 파이프라인 시작
        profile = self.pipeline.start(self.config)
        # 스트림 정보에서 depth 객체 불러오기
        depth_stream = profile.get_device().first_depth_sensor()
        # depth를 m 단위로 변환할 scale 값 저장
        self.depth_scale = depth_stream.get_depth_scale()
        # color 스트림 프로파일 객체 가져오기
        color_stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
        # color 카메라 내부 파라미터 추출
        intr = color_stream.get_intrinsics()
        # OpenCV solvePnP용 카메라 매트릭스 구성
        self.camera_matrix = np.array([
            [intr.fx, 0.0, intr.ppx],
            [0.0, intr.fy, intr.ppy],
            [0.0, 0.0, 1.0]
        ], dtype=np.float64)
        # 렌즈 왜곡 계수 저장
        self.dist_coeffs = np.array(intr.coeffs, dtype=np.float64)
        # 카메라 시작 flag를 True로 변경
        self.camera_flag = True
        # 디버그 문구 출력
        print("[ARUCO_CAMERA] D435 카메라 시작 완료")

    # 카메라 종료 함수 선언
    def camera_stop(self):
        # 카메라가 시작된 상태라면
        if self.camera_flag:
            # 파이프라인 정지
            self.pipeline.stop()
            # flag False로 변경
            self.camera_flag = False
            # 디버그 문구 출력
            print("[ARUCO_CAMERA] D435 카메라 종료 완료")
        # 열려있는 OpenCV 창 닫기
        cv2.destroyAllWindows()

    # roi 영역의 depth 중앙값을 거리값으로 반환하는 함수 선언
    def cal_distance(self, depth_m, x1, y1, x2, y2):
        # 좌측 상단 x 좌표 x1 좌표가 이미지 범위를 벗어나지 않도록 보정 후 int로 변환
        x1 = int(np.clip(x1, 0, depth_m.shape[1] - 1))
        # 좌측 상단 y좌표 y1 좌표가 이미지 범위를 벗어나지 않도록 보정 후 int로 변환
        y1 = int(np.clip(y1, 0, depth_m.shape[0] - 1))
        # 우측 하단 x2 좌표가 이미지 범위를 벗어나지 않도록 보정 후 int로 변환
        x2 = int(np.clip(x2, 0, depth_m.shape[1] - 1))
        # 우측 하단 y2 좌표가 이미지 범위를 벗어나지 않도록 보정 후 int로 변환
        y2 = int(np.clip(y2, 0, depth_m.shape[0] - 1))
        # 입력된 사각형 영역의 ROI를 추출
        roi = depth_m[y1:y2 + 1, x1:x2 + 1]
        # ROI가 비어 있으면
        if roi.size == 0:
            # None return
            return None
        # roi에서 depth 값이 0보다 큰 것만 유효 데이터로 추출
        valid_depth = roi[roi > 0.0]
        # 유효 depth가 하나도 없으면
        if valid_depth.size == 0:
            # None return
            return None
        # roi depth 값의 중앙값 return
        return float(np.median(valid_depth))

    # 회전행렬에서 yaw degree를 연산하는 함수 선언
    def cal_yaw_deg(self, rotation_matrix):
        # 회전행렬의 x-y 성분을 이용해 yaw(rad)를 계산
        yaw_rad = math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])
        # rad 단위를 deg 단위로 변환
        yaw_deg = math.degrees(yaw_rad)
        # 연산한 yaw_deg return
        return float(yaw_deg)

    # 현재 프레임에서 아루코마커를 검출하는 함수 선언
    def detect_aruco(self, target_id=None, show_window=True):
        # 카메라가 시작되지 않았다면
        if not self.camera_flag:
            # 카메라 시작
            self.camera_start()
        # 카메라로부터 최신 프레임 수신
        camera_frame = self.pipeline.wait_for_frames()
        # depth frame을 color frame 기준으로 정렬
        aligned_frame = self.align.process(camera_frame)
        # 정렬된 depth frame 추출
        depth_frame = aligned_frame.get_depth_frame()
        # 정렬된 color frame 추출
        color_frame = aligned_frame.get_color_frame()
        # 두 프레임 중 1개라도 없으면
        if not depth_frame or not color_frame:
            # None return
            return None
        # color frame을 numpy 배열 형태의 BGR 이미지로 변환
        color_img = np.asanyarray(color_frame.get_data())
        # depth frame을 numpy 배열로 변환 후 float32로 형변환
        depth_raw = np.asanyarray(depth_frame.get_data()).astype(np.float32)
        # depth raw 값에 scale 값을 곱해 meter 단위 depth 이미지 생성
        depth_m = depth_raw * float(self.depth_scale)
        # 아루코마커 검출
        corners_list, ids, _ = self.aruco_detector.detectMarkers(color_img)
        # 검출 결과 저장용 변수 선언
        marker_info = None
        # ids가 None이 아니고 1개 이상 검출되었다면
        if ids is not None and len(ids) > 0:
            # ids를 1차원 배열 형태로 정리
            ids = ids.flatten()
            # 검출된 각 마커에 대해 반복
            for corners, marker_id in zip(corners_list, ids):
                # target_id가 지정되었고 현재 marker_id가 target_id와 다르면
                if target_id is not None and int(marker_id) != int(target_id):
                    # 다음 마커로 넘어감
                    continue
                # corners 좌표를 (4,2) 형태로 정리
                corners = corners.reshape((4, 2))
                # 마커 bbox의 x 최소값 계산
                x_min = int(np.min(corners[:, 0]))
                # 마커 bbox의 y 최소값 계산
                y_min = int(np.min(corners[:, 1]))
                # 마커 bbox의 x 최대값 계산
                x_max = int(np.max(corners[:, 0]))
                # 마커 bbox의 y 최대값 계산
                y_max = int(np.max(corners[:, 1]))
                # 마커 중심 x좌표 계산
                cx = int(np.mean(corners[:, 0]))
                # 마커 중심 y좌표 계산
                cy = int(np.mean(corners[:, 1]))
                # 화면 중심 x좌표 계산
                img_center_x = int(color_img.shape[1] / 2)
                # 마커 중심이 화면 중심에서 얼마나 벗어났는지 픽셀 차이 계산
                offset_px = int(cx - img_center_x)
                # bbox 영역 기준 대표 거리 계산
                dist_m = self.cal_distance(depth_m, x_min, y_min, x_max, y_max)
                # yaw 값 초기화
                yaw_deg = None
                # rvec 저장 변수 선언
                rvec = None
                # tvec 저장 변수 선언
                tvec = None
                # 마커 x 좌표 저장 변수 선언
                x_cord = None
                # 마커 y 좌표 저장 변수 선언
                y_cord = None
                # 에러가 없으면
                try:
                    # 실제 아루코마커 4개 꼭짓점의 3차원 좌표 정의
                    obj_points = np.array([
                        [-self.aruco_length_m / 2,  self.aruco_length_m / 2, 0],
                        [ self.aruco_length_m / 2,  self.aruco_length_m / 2, 0],
                        [ self.aruco_length_m / 2, -self.aruco_length_m / 2, 0],
                        [-self.aruco_length_m / 2, -self.aruco_length_m / 2, 0]
                    ], dtype=np.float32)
                    # solvePnP를 사용하여 마커 pose 추정
                    success, rvec_est, tvec_est = cv2.solvePnP(
                        obj_points,
                        corners.astype(np.float32),
                        self.camera_matrix,
                        self.dist_coeffs,
                        flags=cv2.SOLVEPNP_IPPE_SQUARE
                    )
                    # pose 추정에 성공하면
                    if success:
                        # rvec를 1차원 리스트로 저장
                        rvec = rvec_est.reshape(-1).tolist()
                        # tvec를 1차원 리스트로 저장
                        tvec = tvec_est.reshape(-1).tolist()
                        # tvec x 성분 아루코마커 x 좌표로 저장[m]
                        x_cord = float(tvec_est[0][0])
                        # tvec y 성분을 아루코마커 y 좌표로 저장[m]
                        y_cord = float(tvec_est[1][0])                        
                        # Rodrigues 변환으로 회전행렬 계산
                        rotation_matrix, _ = cv2.Rodrigues(rvec_est)
                        # 회전행렬에서 yaw degree 계산
                        yaw_deg = self.cal_yaw_deg(rotation_matrix)
                        # 이미지 위에 좌표축 시각화
                        cv2.drawFrameAxes(
                            color_img,
                            self.camera_matrix,
                            self.dist_coeffs,
                            rvec_est,
                            tvec_est,
                            self.aruco_length_m * 0.5
                        )
                # 예외 발생 시
                except Exception:
                    # 다음 마커로 이동
                    pass
                # 마커 외곽선 시각화를 위한 int32 좌표 생성
                corners_int = corners.astype(np.int32).reshape((-1, 1, 2))
                # 이미지에 마커 외곽선 표시
                cv2.polylines(color_img, [corners_int], True, (0, 255, 0), 2)
                # 이미지에 마커 중심점 표시
                cv2.circle(color_img, (cx, cy), 4, (255, 0, 0), -1)
                # 표시용 텍스트1 구성
                text_1 = f"ID:{int(marker_id)} offset:{offset_px}px"
                # 표시용 텍스트2 구성
                text_2 = f"dist:{dist_m:.2f}m" if dist_m is not None else "dist:N/A"
                # 표시용 텍스트3 구성
                text_3 = f"yaw:{yaw_deg:.2f}deg" if yaw_deg is not None else "yaw:N/A"
                # 표시용 텍스트4 구성
                text_4 = f"x:{x_cord:.3f}m y:{y_cord:.3f}m" if x_cord is not None and y_cord is not None else "x:N/A y:N/A"
                # 첫 번째 텍스트 출력
                cv2.putText(
                    color_img,
                    text_1,
                    (x_min, max(20, y_min - 40)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2
                )
                # 두 번째 텍스트 출력
                cv2.putText(
                    color_img,
                    text_2,
                    (x_min, max(20, y_min - 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2
                )
                # 세 번째 텍스트 출력
                cv2.putText(
                    color_img,
                    text_3,
                    (x_min, max(20, y_min)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 255),
                    2
                )
                # 마커 중심 아래에 x,y 좌표 텍스트 출력
                cv2.putText(
                    color_img,
                    text_4,
                    (cx - 70, cy + 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (255, 255, 0),
                    2
                )
                # 현재 마커 정보를 dict 형태로 저장
                marker_info = {
                    # 아루코마커를 찾았으므로 True
                    "found": True,
                    # 검출된 아루코마커 ID 저장
                    "aruco_id": int(marker_id),
                    # 아루코마커 중심 x좌표 저장
                    "cx": int(cx),
                    # 아루코마커 중심 y좌표 저장
                    "cy": int(cy),
                    # 화면 중심 x좌표 저장
                    "img_center_x": int(img_center_x),
                    # 화면 중심 대비 픽셀 차이 저장
                    "offset_px": int(offset_px),
                    # 아루코마커까지의 거리 저장
                    "dist_m": None if dist_m is None else float(dist_m),
                    # 아루코마커 yaw 각도 저장
                    "yaw_deg": None if yaw_deg is None else float(yaw_deg),
                    # 아루코마커 x 좌표 저장
                    "marker_x": None if x_cord is None else float(x_cord),
                    # 아루코마커 y 좌표 저장
                    "marker_y": None if y_cord is None else float(y_cord),
                    # 회전벡터 저장
                    "rvec": rvec,
                    # 이동벡터 저장
                    "tvec": tvec,
                    # 현재 시간 저장
                    "timestamp": float(time.time())
                }

                # 목표 마커를 찾았으면 반복 종료
                break
        # 이번 프레임에서 마커를 찾지 못했다면
        if marker_info is None:
            # 마커 미검출 정보를 dict 형태로 저장
            marker_info = {
                # 아루코마커를 찾지 못했으므로 False
                "found": False,
                # target_id가 있으면 해당 id 저장, 없으면 None 저장
                "aruco_id": None if target_id is None else int(target_id),
                # 중심 x좌표 없음
                "cx": None,
                # 중심 y좌표 없음
                "cy": None,
                # 화면 중심 x좌표는 저장
                "img_center_x": int(color_img.shape[1] / 2),
                # offset 없음
                "offset_px": None,
                # 거리 없음
                "dist_m": None,
                # yaw 없음
                "yaw_deg": None,
                # 아루코마커 x 좌표 없음
                "marker_x": None,
                # 아루코마커 y 좌표 없음
                "marker_y": None,
                # rvec 없음
                "rvec": None,
                # tvec 없음
                "tvec": None,
                # 현재 시간 저장
                "timestamp": float(time.time())
            }
        # 화면 출력이 필요하면
        if show_window:
            # 화면 중심 세로선 표시용 x좌표 계산
            img_center_x = int(color_img.shape[1] / 2)
            # 이미지 중앙에 세로 기준선 표시
            cv2.line(color_img, (img_center_x, 0), (img_center_x, color_img.shape[0]), (255, 255, 0), 1)
            # 결과 영상을 화면에 출력
            cv2.imshow("D435 Aruco Detector", color_img)
            # OpenCV 창 업데이트
            cv2.waitKey(1)
        # 최종 마커 정보 return
        return marker_info
    # 현재 인식되는 아루코마커의 평균값으로 정답 아루코마커 정보를 저장하는 함수 선언
    def save_current_aruco_info(self, storage, target_id, sample_count=10, show_window=True):
        # 여러 마커 값을 모아 평균을 내기 위한 리스트 선언
        sample_list = []
        # 원하는 샘플 개수만큼 모일 때까지 반복
        while len(sample_list) < sample_count:
            # 현재 프레임에서 target_id 마커를 검출
            marker_info = self.detect_aruco(target_id=target_id, show_window=show_window)
            # 마커를 찾았고 yaw와 거리도 정상 계산되었다면
            if marker_info["found"] and marker_info["yaw_deg"] is not None and marker_info["dist_m"] is not None:
                # 샘플 리스트에 현재 마커 정보 추가
                sample_list.append(marker_info)
                # 현재 몇 개까지 저장되었는지 진행 상황 출력
                print(f"[ARUCO_SAVE] 샘플 저장 중. {len(sample_list)}/{sample_count}")
            # 0.1초 대기
            time.sleep(0.1)
        # 저장된 샘플들의 offset_px 평균값 계산
        avg_offset_px = int(np.mean([item["offset_px"] for item in sample_list]))
        # 저장된 샘플들의 dist_m 평균값 계산
        avg_dist_m = float(np.mean([item["dist_m"] for item in sample_list]))
        # 저장된 샘플들의 yaw_deg 평균값 계산
        avg_yaw_deg = float(np.mean([item["yaw_deg"] for item in sample_list]))
        # 저장된 샘플들의 x 좌표 평균값 계산
        avg_x_cord = float(np.mean([item["marker_x"] for item in sample_list]))
        # 저장된 샘플들의 y 좌표 평균값 계산
        avg_y_cord = float(np.mean([item["marker_y"] for item in sample_list]))
        # 최종 저장할 기준 마커 정보를 dict 형태로 구성
        target_info = {
            # 기준 아루코 ID 저장
            "aruco_id": int(target_id),
            # 평균 offset 저장
            "offset_px": int(avg_offset_px),
            # 평균 거리 저장
            "dist_m": float(avg_dist_m),
            # 평균 yaw 저장
            "yaw_deg": float(avg_yaw_deg),
            # 평균 x 좌표 저장
            "marker_x": float(avg_x_cord),
            # 평균 y 좌표 저장
            "marker_y": float(avg_y_cord),
        }
        # storage 객체를 이용해 기준값을 파일로 저장
        storage.save_target_aruco(target_info)
        # 저장 완료 메시지 출력
        print("[ARUCO_SAVE] 기준 아루코 저장 완료")
        # 저장된 target_info 내용 출력
        print(target_info)
        # 저장한 기준 데이터를 반환
        return target_info
# ======================================================================================================================
# 저장된 아루코마커를 기준으로 상태에 맞춰 AMR을 정렬하는 클래스 선언
class AMR_Aruco_dock_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,seer,detector,storage,yaw_tolerance_deg=0.2,offset_tolerance_px=50,dist_tolerance_m=0.03):
        # 인자로 받은 SEER_commu 객체 저장
        self.seer = seer
        # 인자로 받은 아루코마커 검출 객체 저장
        self.detector = detector
        # 인자로 받은 파일 저장/로드 객체 저장
        self.storage = storage
        # 인자로 받은 yaw 허용 오차 저장
        self.yaw_tolerance_deg = yaw_tolerance_deg
        # 인자로 받은 중심 offset 허용 오차 저장
        self.offset_tolerance_px = offset_tolerance_px
        # 인자로 받은 거리 허용 오차 저장
        self.dist_tolerance_m = dist_tolerance_m

    # 각도 값을 -180 ~ 180 범위로 정규화하는 함수 선언
    def normalize_angle_deg(self, angle_deg):
        # 입력 각도가 180도보다 크면
        while angle_deg > 180.0:
            # 360을 빼서 각도 조절
            angle_deg -= 360.0
        # 입력 각도가 -180도보다 작으면 360을 더해
        while angle_deg < -180.0:
            # 각도 조절
            angle_deg += 360.0
        # 정규화된 각도 return
        return angle_deg

    # 저장된 기준 상태와 현재 마커 상태를 비교하여 AMR을 정렬하는 함수 선언
    def align_to_saved_target(self, max_step=500, search_rotate_speed=0.5, show_window=True):
        # 저장된 정답 아루코 정보를 파일에서 로드
        target_info = self.storage.load_aruco_info()
        # 기준 정보가 없으면 정렬 불가이므로 실패 처리
        if target_info is None:
            # 디버그 문구 출력
            print("[DOCK] 저장된 아루코 기준 정보가 없습니다.")
            # False return
            return False
        # 저장된 정답 아루코마커 ID를 추출
        target_id = target_info["aruco_id"]
        # 저장된 정답 아루코마커 yaw값을 추출
        target_yaw_deg = target_info["target_yaw_deg"]
        # 저장된 정답 아루코마커 중심값 픽셀값을 추출
        target_offset_px = target_info["target_offset_px"]
        # 저장된 정답 아루코마커 거리값을 추출
        target_dist_m = target_info["target_dist_m"]
        # 정렬 시작 안내 문구 출력
        print("[DOCK] AMR 정렬 시작")
        # 현재 사용할 기준 정보를 출력
        print(f"[DOCK] target_id={target_id}, target_yaw={target_yaw_deg}, target_offset={target_offset_px}, target_dist={target_dist_m}")
        # 최대 반복 횟수만큼 보정 루프 수행
        for step in range(max_step):
            # 현재 프레임에서 target_id 마커를 검출
            marker_info = self.detector.detect_aruco(target_id=target_id, show_window=show_window)
            # 현재 아루코마커 중심 offset 값을 추출
            current_offset_px = marker_info["offset_px"]
            # 현재 아루코마커 거리 값을 추출
            current_dist_m = marker_info["dist_m"]
            # 현재 아루코마커 yaw 값을 추출
            current_yaw_deg = marker_info["yaw_deg"]
            # 현재 yaw와 기준 yaw의 차이를 계산 후 -180~180 범위로 정규화
            yaw_error_deg = self.normalize_angle_deg(current_yaw_deg - target_yaw_deg)
            # 현재 offset과 기준 offset의 차이를 계산
            offset_error_px = current_offset_px - target_offset_px
            # 현재 거리와 기준 거리의 차이를 계산
            dist_error_m = None if current_dist_m is None else (current_dist_m - target_dist_m)
            # 구분선 출력
            print("-" * 60)
            # 현재 step 번호 출력
            print(f"[DOCK] step={step + 1}")
            # 현재 yaw 출력
            print(f"[DOCK] current_yaw_deg   : {current_yaw_deg:.2f}")
            # 기준 yaw 출력
            print(f"[DOCK] target_yaw_deg    : {target_yaw_deg:.2f}")
            # yaw 오차 출력
            print(f"[DOCK] yaw_error_deg     : {yaw_error_deg:.2f}")
            # 현재 offset 출력
            print(f"[DOCK] current_offset_px : {current_offset_px}")
            # 기준 offset 출력
            print(f"[DOCK] target_offset_px  : {target_offset_px}")
            # offset 오차 출력
            print(f"[DOCK] offset_error_px   : {offset_error_px}")
            # 현재 거리 출력
            print(f"[DOCK] current_dist_m    : {current_dist_m}")
            # 기준 거리 출력
            print(f"[DOCK] target_dist_m     : {target_dist_m}")
            # 거리 오차 출력
            print(f"[DOCK] dist_error_m      : {dist_error_m}")
            # yaw 오차가 허용 범위 이내인지 확인
            yaw_ok = abs(yaw_error_deg) <= self.yaw_tolerance_deg
            # 중심 offset 오차가 허용 범위 이내인지 확인
            offset_ok = abs(offset_error_px) <= self.offset_tolerance_px
            # 거리 오차가 없거나 허용 범위 이내인지 확인
            dist_ok = (dist_error_m is None) or (abs(dist_error_m) <= self.dist_tolerance_m)
            # 모든 조건을 만족하면 정렬 성공
            if yaw_ok and offset_ok and dist_ok:
                # 정렬 완료 메시지 출력
                print("[DOCK] 정렬 완료")
                # 정지 명령 전송
                self.seer.motion_control(vx=0.0, vy=0.0, w=0.0, duration=1)
                # True return
                return True
            # ----------------- 1순위: yaw 보정 -----------------
            # yaw 오차가 허용 범위를 넘으면 먼저 회전부터 보정
            if abs(yaw_error_deg) > self.yaw_tolerance_deg:
                # yaw 오차에 비례하여 회전 속도 계산
                w_cmd = -1.8 * yaw_error_deg
                # 회전 속도를 최소/최대 범위로 제한
                w_cmd = max(min(w_cmd, 0.2), -0.2)
                # 속도가 너무 작으면 최소 회전속도 보장
                if 0 < abs(w_cmd) < 0.1:
                    w_cmd = 0.1
                # 회전 명령 출력
                print(f"[DOCK] yaw 보정 -> w={w_cmd:.3f}")
                # 회전 명령 전송
                self.seer.motion_control(vx=0.0, vy=0.0, w=w_cmd, duration=1)
                # 0.1초 대기
                time.sleep(0.1)
                # 다음 반복으로 이동
                continue
            # ----------------- 2순위: 중심 보정 -----------------
            if current_offset_px is not None and abs(offset_error_px) > self.offset_tolerance_px:
                # offset 오차가 80 픽셀 이상 시 
                if abs(offset_error_px) >= 80:
                    # 0.45s만큼 회전
                    rotate_time = 0.45
                # offset 오차가 40 픽셀 이상 시 
                elif abs(offset_error_px) >= 40:
                    # 0.30s만큼 회전
                    rotate_time = 0.30
                # 이 외에는 
                else:
                    # 0.18s만큼 회전
                    rotate_time = 0.18
                # 마커 중심이 화면 왼쪽이면
                if offset_error_px < 0:
                    # 좌회전
                    w_cmd = +0.1
                # 마커 중심이 화면 왼쪽이면
                else:
                    # 우회전
                    w_cmd = -0.1
                # 회전 명령 출력
                print(f"[DOCK] 좌우 보정(회전) -> w={w_cmd:.3f}, duration={rotate_time:.2f}")
                # 회전 명령 전송
                self.seer.motion_control(vx=0.0, vy=0.0, w=w_cmd, duration=rotate_time)
                # 0.1초 대기
                time.sleep(0.15)
                # 다음 반복으로 이동
                continue    
            # ----------------- 3순위: 거리 보정 -----------------
            # 거리 오차가 존재하고 허용 범위를 넘으면 전후 보정
            if dist_error_m is not None and abs(dist_error_m) > self.dist_tolerance_m:
                # 거리 오차에 비례하여 x축 이동 속도 계산
                vx_cmd = 2.5 * dist_error_m
                # x축 이동 속도를 최소/최대 범위로 제한
                vx_cmd = max(min(vx_cmd, 0.08), -0.08)
                # 속도가 너무 작으면 최소 이동속도 보장
                if 0 < abs(vx_cmd) < 0.1:
                    vx_cmd = 0.1 if vx_cmd > 0 else -0.1
                # 전후 이동 명령 출력
                print(f"[ALIGN] 거리 보정 -> vx={vx_cmd:.3f}")
                # 전후 이동 명령 전송
                self.seer.motion_control(vx=vx_cmd, vy=0.0, w=0.0, duration=1)
                # 0.1초 대기
                time.sleep(0.1)
                # 다음 반복으로 이동
                continue
        # 최대 반복 횟수를 모두 사용했는데도 정렬이 끝나지 않으면 실패 처리
        print("[ALIGN] 최대 보정 횟수를 초과하여 정렬 실패")
        # 마지막으로 정지 명령 전송
        self.seer.motion_control(vx=0.0, vy=0.0, w=0.0, duration=1)
        # False return
        return False