# ----------------------------- 범용 라이브러리 import -----------------------------
# 배열 핸들링을 위해서 numpy 라이브러리 import
import numpy as np
# 이미지 핸들링을 위해서 cv2 라이브러리 import
import cv2
# Intel RealSense(D435 등) 카메라 스트림(color/depth) 제어를 위해 pyrealsense2 라이브러리 import
import pyrealsense2 as rs
# 객체 인식을 위해 yolo 클래스 import
from ultralytics import YOLO
# 시간 핸들을 위한 time 라이브러리 import
import time
# 아르코마커 정보를 json으로 송신하기 위한 json 라이브러리 import
import json
# ----------------------------- 커스텀 라이브러리 import -----------------------------
# MQTT 사용을 위해 MQTT_Util 클래스 import
from custom_package.mqtt_commu import MQTT_Util

# 바운딩 박스 내부의 객체 거리 연산 함수
def cal_distance(depth, x1,y1,x2,y2):
    # 깊이 이미지에서 높이와 너비 받아와서 저장
    depth_h,depth_w = depth.shape
    # 바운딩 박스가 깊이 이미지를 벗어나지 못하도록 좌측 상단 x 좌표 조정
    x1 = max(0, min(depth_w-1,x1))
    # 바운딩 박스가 깊이 이미지를 벗어나지 못하도록 좌측 상단 y 좌표 조정
    y1 = max(0, min(depth_h-1,y1))
    # 바운딩 박스가 깊이 이미지를 벗어나지 못하도록 우측 하단 x 좌표 조정
    x2 = max(0, min(depth_w - 1, x2))
    # 바운딩 박스가 깊이 이미지를 벗어나지 못하도록 우측 하단 y 좌표 조정
    y2 = max(0, min(depth_h-1,y2))
    # bbox가 유효하지 않다면(폭/높이가 0 이하), 거리 계산이 불가능하므로 None return
    if x2 <= x1 or y2 <= y1:
        return None
    # 깊이 이미지에서 bbox 영역만 잘라 ROI(region of interest)로 저장
    roi = depth[y1:y2, x1:x2]
    # ROI에서 유효한 depth 값만 선택
    # roi가 0이상인 값과 NaN,INF가 아닌 값만 선택
    use_depth = roi[(roi > 0.0) & np.isfinite(roi)]
    # 유효한 값이 하나도 없다면 거리 계산 불가이므로 None 반환
    if use_depth.size == 0:
        return None
    # 유효한 값의 중앙값을 대표 거리로 반환
    return float(np.median(use_depth))

# 메인함수 선언
def main():
    # yolo 모델 객체 선언
    model = YOLO("yolov8n.pt")
    # 카메라 제어 객체 선언
    cam = rs.pipeline()
    # 카메라 설정 객체 선언
    cam_config = rs.config()
    # mqtt 객체 선언
    # 브로커서버 ip = 192.168.0.26
    # 브로커서버 포트 = 1883
    mqtt = MQTT_Util(broker_ip="127.0.0.1", broker_port=1883)
    # 장애물 감지 시 송신할 mqtt 토픽 설정
    obstacle_topic = "camera/obstacle"
    # 장애물 감지 시 송신할 mqtt payload 설정
    obstacle_payload = "stop"
    # 아르코마커 감지 시 송신할 mqtt 토픽 설정
    aruco_topic = "camera/aruco"
    # ---------------- 카메라 관련 설정 선언 -----------------------------
    # rgb 이미지 스트리밍 설정
    cam_config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8,30)
    # depth 이미지 스트리밍 설정
    cam_config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    # ----------- 장애물 감지 관련 설정 (depth, mqtt) ---------------------
    # 설정대로 이미지 스트리밍 시작 후 장치/스트리밍 정보 저장
    stream_profile = cam.start(cam_config)
    # 깊이 센서 이미지 저장
    depth_sensor = stream_profile.get_device().first_depth_sensor()
    # 깊이 센서 값을 m로 변환
    depth_distance = depth_sensor.get_depth_scale()
    # depth 이미지를 rgb 이미지와 맞추기
    aline_img = rs.align(rs.stream.color)
    # 초반 프레임은 버려서 안정화
    for _ in range(30):
        cam.wait_for_frames()
    # 시작 안내 메시지를 출력합니다.
    print("[INFO] Started. Press 'q' to quit.")
    # 장애물 판단 거리 임계값을 1.50m로 설정
    obstacle_threshold_m = 1.50
    # mqtt가 너무 자주 호출되는 것을 방지하기 위한 쿨다운 시간을 5초로 설정
    mqtt_cooldown_sec = 5.0
    # 마지막으로 mqtt를 수신하기 위핸 시간을 저장할 변수 선언
    last_mqtt_time = 0.0
    # 직전 프레임에서 이미 장애물이 감지되었는지 판단하는 플래그 선언
    close_flag = False
    # ---------------- 아루코마커 관련 설정 -----------------------------
    # 감지할 아루코마커 id 4로 설정
    aruco_id = 4
    # 감지할 아루코마커 딕셔너리 설정
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    # 아루코마커 파라미터 선언
    aruco_params = cv2.aruco.DetectorParameters()
    # 아루코마커 디덱터 선언
    aruco_detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)
    # 아루코마커 정보 송신 간격 설정(0.1초)
    aruco_publish_time = 1.0
    # 마지막 송신 시간 저장 변수 선언
    last_aruco_publish_time = 0.0
    # 아루코마커 중앙 픽셀 허용 오차[pixel]
    aruco_center_pix_tolerance = 60
    # 에러가 없으면 
    try:
        # 반복해서 실행
        while True:
            # ---------------- 각 이미지 프레임 저장 ----------------------------
            # rgb,깊이 이미지 프레임 저장
            frame = cam.wait_for_frames()
            # 깊이 이미지를 rgb에 맞춘 프레임 저장
            aline_frmae = aline_img.process(frame)
            # 맞춰진 깊이 이미지 프레임 저장
            depth_frame = aline_frmae.get_depth_frame()
            # 맞춰진 rgb 이미지 프레임 저장
            rgb_frame = aline_frmae.get_color_frame()
            # 프레임이 없거나 비정상이면 다음 루프 진행
            if not depth_frame or not rgb_frame:
                continue
            # ---------------- 장애물(의자, 사람) 감지 및 시각화 ----------------------------
            # cv2 적용을 위해 컬러 프레임을 numpy 배열 brg 형태로 변환
            rgb_img = np.asanyarray(rgb_frame.get_data())
            # 깊이 프레임을 깊이 계산을 위해 numpy 배열로 변환한 뒤 float32로 변환(uint16 -> float)
            depth_raw = np.asanyarray(depth_frame.get_data()).astype(np.float32)
            # depth_raw에 depth_scale을 곱하여 미터(m) 단위의 depth 맵 제작
            depth_m = depth_raw * float(depth_distance)
            # yolo를 활용하여 rgb 이미지에서 객체 인식 진행
            object_detect = model.predict(source=rgb_img, conf=0.4, verbose=False)
            # 첫번째 결과 저장 (현재 프레임 객체 인식 결과)
            object_detect_result = object_detect[0]
            # bbox를 그리기 위해 rgb_image 복사
            copy_img = rgb_img.copy()
            # 프레임에서 가장 가까운 객체의 거리(min)를 저장할 변수 선언 (여러 거리 값 중 최소값 저장할 변수)
            min_dist_m = None
            # 박스가 존재하고, 박스 개수가 1개 이상일 때
            if object_detect_result.boxes is not None and len(object_detect_result.boxes) > 0:
                # 박스 좌표(x1,y1,x2,y2)를 numpy 배열로 가져와 int로 저장
                bbox_coord = object_detect_result.boxes.xyxy.cpu().numpy().astype(int)
                # 각 박스의 confidence(신뢰도)를 numpy 배열로 저장
                bbox_confidence = object_detect_result.boxes.conf.cpu().numpy()
                # 각 박스의 클래스 id를 numpy 배열로 가져와 int로 저장
                bbox_class = object_detect_result.boxes.cls.cpu().numpy().astype(int)
                # 클래스 id → 클래스 이름(라벨 문자열) 매핑 정보 불러오기
                class_name = model.names
                # 각 박스에 대하여
                for (x1, y1, x2, y2), conf, cls_id in zip(bbox_coord, bbox_confidence, bbox_class):
                    # 클래스 id가 사람, 의자가 아니면
                    if int(cls_id) not in (0,56):
                        # 다음 루프 진행
                        continue
                    # bbox 영역 내 depth 중앙값으로 장애물까지 거리 연산
                    dist_m = cal_distance(depth_m, x1, y1, x2, y2)
                    # 클래스 id를 라벨 문자열로 변환(없으면 id를 문자열로 표시)
                    label = class_name.get(int(cls_id), str(cls_id))
                    # 거리 계산이 실패(None)하면 dist:N/A로 표시
                    if dist_m is None:
                        text = f"{label} distance:N/A"
                    # 거리 계산이 성공하면 소수 둘째자리까지 표시
                    else:
                        text = f"{label} distacne:{dist_m:.2f}[m]"
                        # 현재 dist_m이 지금까지의 최소 거리보다 작으면 최소 거리 갱신
                        if (min_dist_m is None) or (dist_m < min_dist_m):
                            min_dist_m = dist_m
                    # 결과 이미지에 bbox 표시 (빨간색)
                    cv2.rectangle(copy_img, (x1, y1), (x2, y2), (0, 0, 255), 2)
                    # 결과 이미지에 text 표시
                    cv2.putText(copy_img,text,(x1, max(20, y1 - 8)),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0, 255, 0),2)
            # ---------------- 장애물(의자, 사람) 감지 시 mqtt로 메시지 발행 ----------------------------
            # 최소 거리(min_dist_m)가 존재하고 임계값 이하이면 장애물으로 판단
            obstacle_detect = (min_dist_m is not None) and (min_dist_m <= obstacle_threshold_m)
            # 현재 시간 저장
            now = time.time()
            # 장애물이 존재하고 이전 프레임에 장애물이 감지되지 않았고 쿨다운 시간이 넘었으면
            if obstacle_detect and (not close_flag):
                # 현재 시간과 마지막으로 mqtt가 실행된 시간의 차이가 mqtt_cooldown_sec(5초) 이상이라면 
                if now-last_mqtt_time >= mqtt_cooldown_sec:
                    # 마지막 mqtt 실행 시간 현재 시간으로 업데이트
                    last_mqtt_time = now
                    # 장애물 감지 mqtt 메시지 발행
                    mqtt.publish_string(obstacle_topic,obstacle_payload)
                # 이미 재생 중이면 메시지 발행 x
                else:    
                    pass
            # 이번 프레임의 가까움 여부를 저장하여 다음 프레임에서 진입 순간을 판단
            close_flag = obstacle_detect
            # ---------------- 아루코마커 감지 및 시각화 ----------------------------
            # 아루코마커 탐지를 위해 현재 rgb 이미지 gray scale로 변환
            gray = cv2.cvtColor(rgb_img, cv2.COLOR_BGR2GRAY)
            # 도킹 코드에서 활용할 이번 프레임 아루코마커 상태 초기화
            # 아루코마커 탐색 flag False로 선언
            aruco_found = False
            # 아루코마커 정보 변수 초기화
            aruco_info = None
            # 현재 이미지에서 아루코마커 디텍터를 활용하여 마커 검출
            # aruco_corners : 검출된 각 마커 꼭짓점 좌표(코너 4개) 리스트
            # aruco_ids     : 검출된 각 마커 ID 배열
            aruco_corners, aruco_ids, _rejected = aruco_detector.detectMarkers(gray)
            # 마커 id가 None이 아니고 길이가 0 이상이면(아루코마커가 감지되었으면)
            if aruco_ids is not None and len(aruco_ids) >0:
                # 검출된 아르코마커 갯수만큼 반복
                for i in range(len(aruco_ids)):
                    # 아루코마커 id 배열에서 i번째 마커 id를 int 형태로 변환
                    marker_id =(aruco_ids[i][0])
                    # 마커 id가 4가 아니면 
                    if marker_id != aruco_id:
                        # 다음 마커로 넘어감
                        continue
                    # 아루코마커 감지 flag True로 설정
                    aruco_found = True
                    #  i번째 마커의 코너 좌표 저장
                    corners = aruco_corners[i].reshape(4, 2)
                    # 마커의 코너 4개의 x좌표 중 최소/최대를 이용해 bbox의 x좌표 범위 설정
                    x_min = int(np.clip(np.min(corners[:, 0]), 0, rgb_img.shape[1] - 1))
                    x_max = int(np.clip(np.max(corners[:, 0]), 0, rgb_img.shape[1] - 1))
                    # 마커의 코너 4개의 y좌표 중 최소/최대를 이용해 bbox의 x좌표 범위 설정
                    y_min = int(np.clip(np.min(corners[:, 1]), 0, rgb_img.shape[0] - 1))
                    y_max = int(np.clip(np.max(corners[:, 1]), 0, rgb_img.shape[0] - 1))
                    # 설정한 bbox(x_min,y_min,x_max,y_max)를 ROI로 잡아서 depth_m에서 중앙값(median) 기반 거리(대표 거리) 연산
                    aruco_dist_m = cal_distance(depth_m, x_min, y_min, x_max, y_max)
                    # 아루코마커 중심점 계산
                    cx = int(np.mean(corners[:, 0]))
                    cy = int(np.mean(corners[:, 1]))
                    # 마커 외곽선을 그리기 위해 코너 좌표를 int32로 변환
                    corners_int32 = corners.astype(np.int32).reshape((-1, 1, 2))
                    # 시각화 이미지에 마커 외곽선 초록색으로 표시
                    cv2.polylines(copy_img, [corners_int32], isClosed=True, color=(0, 255, 0), thickness=2)
                    # 마커 중심점에 파란 원 표시
                    cv2.circle(copy_img, (cx, cy), 4, (255, 0, 0), -1)
                    # 디스플레이 화면 중앙 x좌표 저장
                    img_center_x = int(rgb_img.shape[1]/2)
                    # 마커 중심점 픽셀이 화면 중앙 픽셀에서 얼마나 벗어났는지 연산
                    offset_px = int(cx-img_center_x)
                    # 거리 계산이 실패하면
                    if aruco_dist_m is None:
                            # dist:N/A로 아루코마커 거리 설정
                            aruco_distance = f"Aruco ID:{marker_id} dist:N/A"
                    # 거리 계산에 성공하면
                    else:
                        # 소수점 2째자리까지 아루커마커 거리 설정 
                        aruco_distance = f"Aruco ID:{marker_id} dist:{aruco_dist_m:.2f}[m]"
                    # 시각화 이미지에 텍스트 추가
                    cv2.putText(copy_img, aruco_distance, (x_min, max(20, y_min - 10)),cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                    # ---------------------------------------- 아루코마커 정보 mqtt로 송신 ------------------------------------------------
                    # 도킹 코드에 전송할 아루코마커 정보 구성
                    aruco_info = {
                        # 아루코마커 탐색 여부
                        "found": True,
                        # 탐색한 아루코마커 id
                        "aruco_id": int(marker_id),
                        # 아루코마커 중심 x좌표
                        "cx": int(cx),
                        # 아루코마커 중심 y좌표
                        "cy": int(cy),
                        # 디스플레이 중심 x좌표
                        "img_center_x": int(img_center_x),
                        # 아루코마커와 디스플레이 픽셀 차이
                        "offset_px": int(offset_px),
                        # 아루코마커까지의 거리
                        "dist_m": None if aruco_dist_m is None else float(aruco_dist_m),
                        # 시간
                        "timestamp": float(now)}
                    # 목표 아루코마커 정보를 얻었으면 루프 종료
                    break
            # 아루코마커 송신 시간를 넘었으면
            if now-last_aruco_publish_time >= aruco_publish_time:
                # 마지막 송신 현재 시간으로 업데이트
                last_aruco_publish_time = now
                # 이번 프레임에 아루코마커가 없으면
                if aruco_info is None:
                    aruco_info = {
                        "found": False,
                        "aruco_id": int(aruco_id),
                        "cx": None,
                        "cy": None,
                        "img_center_x": int(rgb_img.shape[1] / 2),
                        "offset_px": None,
                        "dist_m": None,
                        "timestamp": float(now)}
                # JSON 문자열로 변환 후 아루코마커 정보 송신
                mqtt.publish_string(aruco_topic, json.dumps(aruco_info, ensure_ascii=False))
            # ----------------------------------- 이미지 cv2로 표시 및 종료 조건 선언 -------------------------------------------------------
            # 시각화 창에 결과를 표시
            cv2.imshow("D435 YOLO + Distance + ArUco", copy_img)
            # 1ms 동안 키 입력 대기
            key = cv2.waitKey(1) & 0xFF 
            # 'q' 키 또는 ESC(27)를 누르면 루프를 종료
            if key == ord('q') or key == 27:
                break
    # ctrl+c 가 발생하였으면
    except KeyboardInterrupt:
        print("종료")
    # 최종적으로
    finally:
        # 카메라 중지
        cam.stop()
        # 열려있는 OpenCV 창을 모두 종료
        cv2.destroyAllWindows()
        # mqtt 통신 종료
        mqtt.shutdown_mqtt()

# 메인함수 실행
if __name__ == '__main__':
    main()