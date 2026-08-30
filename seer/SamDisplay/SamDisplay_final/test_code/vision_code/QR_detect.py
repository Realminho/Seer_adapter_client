#  ----------- 범용 라이브러리 import ---------------------
# 이미지 핸들링을 위한 CV2 라이브러리 import
import cv2
# 배열 핸들링을 위한 numpy 라이브러리 import
import numpy as np
# Intel RealSense(D435 등) 카메라 스트림(color/depth) 제어를 위해 pyrealsense2 라이브러리 import
import pyrealsense2 as rs

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
    # 에러가 없으면
    try:
        # 카메라 스트리밍 시작
        cam.start(cam_config)
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
            # QR 코드 검출 및 디코딩
            qr_text,points,_ = qr_detector.detectAndDecode(color_array)
            # QR 코드에 저장된 text가 읽혔다면
            if qr_text:
                # 이전에 읽은 QR 텍스트와 다르면 출력 
                if qr_text != last_read_qr_info: 
                    print(f"[QR] read QR info : {qr_text}") 
                    last_read_qr_info = qr_text
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
                    cv2.putText(color_array,qr_text,(points[0][0], points[0][1] - 10),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0, 255, 0),2)
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
        # 스트리밍 종료
        cam.stop()
        # 창 닫기
        cv2.destroyAllWindows()

# 메인 함수 실행
if __name__ == "__main__":
    main()
