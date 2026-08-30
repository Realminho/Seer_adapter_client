#  ----------- 범용 라이브러리 import ---------------------
# 이미지 핸들링을 위한 CV2 라이브러리 import
import cv2
# 배열 핸들링을 위한 numpy 라이브러리 import
import numpy as np
# Intel RealSense(D435 등) 카메라 스트림(color/depth) 제어를 위해 pyrealsense2 라이브러리 import
import pyrealsense2 as rs
# 시간 처리를 위한 time 라이브러리 import
import time
# NEW : 최근 여러 프레임 QR 결과 다수결 계산을 위한 Counter, deque import
from collections import Counter, deque

# 메인 함수 선언
def main():
    # 카메라 제어 객체 선언
    cam = rs.pipeline()
    # 카메라 설정 객체 선언
    cam_config = rs.config()
    # rgb 이미지 스트리밍 설정
    cam_config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8,30)
    # qr_detector 객체 선언
    qr_detector = cv2.QRCodeDetector()
    # 마지막으로 읽은 qr에 저장된 text 정보 저장 변수 선언
    last_read_qr_info = None

    # NEW : 카메라 시작 여부 저장 변수 선언
    cam_started = False
    # NEW : 마지막으로 성공한 QR text 저장 변수 선언
    last_qr_text = None
    # NEW : 마지막으로 성공한 QR 꼭지점 좌표 저장 변수 선언
    last_qr_points = None
    # NEW : 마지막으로 QR 검출에 성공한 시간 저장 변수 선언
    last_qr_detect_time = 0.0
    # NEW : QR 검출 성공 결과를 잠시 유지할 시간 선언
    qr_hold_time = 0.5
    # NEW : 최근 여러 프레임 QR 결과 저장용 deque 선언
    qr_text_history = deque(maxlen=10)

    # 에러가 없으면
    try:
        # 카메라 스트리밍 시작
        profile = cam.start(cam_config)
        # NEW : 카메라 시작 성공 flag 저장
        cam_started = True

        # NEW : Color Sensor를 가져와 주행 중 blur를 줄이기 위한 노출 설정 시도
        try:
            # 장치에서 센서 목록 저장
            sensors = profile.get_device().query_sensors()
            # 컬러 센서 저장 변수 선언
            color_sensor = None
            # 각 센서에 대해 반복
            for sensor in sensors:
                # 센서 이름 저장
                sensor_name = sensor.get_info(rs.camera_info.name)
                # RGB Camera 센서이면
                if "RGB" in sensor_name or "Color" in sensor_name:
                    # 컬러 센서 저장
                    color_sensor = sensor
                    # 반복 종료
                    break

            # 컬러 센서를 찾았으면
            if color_sensor is not None:
                # 자동 노출 기능을 끔
                color_sensor.set_option(rs.option.enable_auto_exposure, 0)
                # NEW : 노출 시간을 너무 길지 않게 설정하여 주행 중 blur 완화
                color_sensor.set_option(rs.option.exposure, 120.0)
                # NEW : 노출을 줄인 대신 gain을 조금 올려 밝기 보완
                color_sensor.set_option(rs.option.gain, 32.0)
                # 디버그 문구 print
                print("[INFO] Color Sensor 수동 노출 설정 완료")
        # 수동 노출 설정 중 에러 발생 시
        except Exception as sensor_error:
            # 디버그 문구 print
            print(f"[INFO] 수동 노출 설정 실패 : {sensor_error}")

        # 디버그 문구 print
        print("--------------------")
        print("Color Streaming 시작")
        print("종료 : q 키")
        print("--------------------")

        # 무한 반복
        while True:
            # 프레임 수신 대기
            frames = cam.wait_for_frames()
            # 컬러 프레임만 저장
            color_frames = frames.get_color_frame()
            # 컬러 프레임이 없으면
            if not color_frames:
                # 다음 루프로 이동
                continue

            # 수신한 컬러 프레임을 numpy 배열로 변환
            color_array = np.asanyarray(color_frames.get_data())
            # NEW : OpenCV 내부 처리 안정화를 위해 contiguous array로 변환
            color_array = np.ascontiguousarray(color_array)

            # NEW : 현재 시간 저장
            now = time.time()

            # NEW : QR 검출 안정화를 위해 gray 이미지로 변환
            gray_img = cv2.cvtColor(color_array, cv2.COLOR_BGR2GRAY)
            # NEW : 약한 blur 적용으로 노이즈 완화
            gray_img = cv2.GaussianBlur(gray_img, (3, 3), 0)
            # NEW : 히스토그램 평활화로 명암 대비 향상
            gray_img = cv2.equalizeHist(gray_img)

            # NEW : Otsu threshold 기반 binary 이미지 생성
            _, binary_img = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # NEW : 이번 프레임 QR 결과 초기화
            qr_text = ""
            points = None

            # NEW : 1차로 gray 이미지에서 QR 코드 검출 및 디코딩
            try:
                qr_text, points, _ = qr_detector.detectAndDecode(gray_img)
            # NEW : QR 검출 예외 발생 시
            except Exception as qr_error:
                # 디버그 문구 print
                print("--------------------")
                print(f"gray QR 검출 예외 발생 : {qr_error}")
                print("--------------------")
                # qr_text 초기화
                qr_text = ""
                # points 초기화
                points = None

            # NEW : gray 이미지에서 실패하면 binary 이미지에서 한 번 더 시도
            if not qr_text:
                try:
                    qr_text, points, _ = qr_detector.detectAndDecode(binary_img)
                # NEW : QR 검출 예외 발생 시
                except Exception as qr_error:
                    # 디버그 문구 print
                    print("--------------------")
                    print(f"binary QR 검출 예외 발생 : {qr_error}")
                    print("--------------------")
                    # qr_text 초기화
                    qr_text = ""
                    # points 초기화
                    points = None

            # NEW : 이번 프레임에서 읽은 QR 결과를 history에 저장
            if qr_text:
                # qr_text_history에 읽은 text 저장
                qr_text_history.append(qr_text)
            else:
                # qr_text_history에 None 저장
                qr_text_history.append(None)

            # NEW : 최근 프레임들 중 유효한 QR text만 추출
            valid_qr_texts = [one_text for one_text in qr_text_history if one_text is not None]

            # NEW : 최근 프레임에서 3회 이상 읽힌 QR text가 있으면 다수결 결과로 사용
            if len(valid_qr_texts) >= 3:
                # 가장 많이 나온 QR text 추출
                voted_qr_text = Counter(valid_qr_texts).most_common(1)[0][0]
            else:
                # 다수결 결과가 없으면 None 저장
                voted_qr_text = None

            # NEW : 이번 프레임 QR 검출 성공 시 마지막 성공 정보 갱신
            if qr_text:
                # 마지막 QR text 저장
                last_qr_text = qr_text
                # 마지막 QR 꼭지점 저장
                last_qr_points = None if points is None else points.copy()
                # 마지막 QR 성공 시간 저장
                last_qr_detect_time = now

            # NEW : 이번 프레임에서 못 읽었더라도 최근 성공 결과를 잠시 유지
            elif (last_qr_text is not None) and ((now - last_qr_detect_time) <= qr_hold_time):
                # 마지막 성공 QR text 사용
                qr_text = last_qr_text
                # 마지막 성공 QR points 사용
                points = last_qr_points

            # NEW : 다수결 결과가 있으면 출력용 text를 다수결 결과로 대체
            display_qr_text = voted_qr_text if voted_qr_text is not None else qr_text

            # QR 코드에 저장된 text가 읽혔다면
            if display_qr_text:
                # 이전에 읽은 QR 텍스트와 다르면 출력
                if display_qr_text != last_read_qr_info:
                    print(f"[QR] read text : {display_qr_text}")
                    last_read_qr_info = display_qr_text

                # 검출된 QR 꼭지점 좌표가 있으면
                if points is not None:
                    # points를 정수형으로 변환
                    points = points[0].astype(int)
                    # 꼭지점 선으로 연결
                    for i in range(4):
                        # 현재 꼭지점을 시작 좌표로 설정
                        pt1 = tuple(points[i])
                        # 다음 연결되는 꼭지점을 연결될 좌표로 설정
                        pt2 = tuple(points[(i + 1) % 4])
                        # 시작 좌표와 연결 좌표를 선으로 표시
                        cv2.line(color_array, pt1, pt2, (0, 255, 0), 2)

                    # 화면에 텍스트 표시
                    cv2.putText(
                        color_array,
                        display_qr_text,
                        (points[0][0], max(20, points[0][1] - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2
                    )
                # NEW : points가 없어도 읽은 text는 좌측 상단에 표시
                else:
                    # 화면 좌측 상단에 텍스트 표시
                    cv2.putText(
                        color_array,
                        display_qr_text,
                        (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9,
                        (0, 255, 0),
                        2
                    )

            # 영상 출력
            cv2.imshow("D435 QR reader", color_array)
            # 키 입력 대기
            key = cv2.waitKey(1) & 0xFF
            # q 키를 누르면 종료
            if key == ord("q"):
                break

    # 예외 발생 시 
    except Exception as e:
        print("--------------------")
        print(f"예외 발생 : {e}")
        print("--------------------")

    # 최종적으로
    finally:
        # NEW : 스트리밍이 실제 시작된 경우에만 종료
        if cam_started:
            # 스트리밍 종료
            cam.stop()
        # 창 닫기
        cv2.destroyAllWindows()

# 메인 함수 실행
if __name__ == "__main__":
    main()