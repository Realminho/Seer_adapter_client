# ----------------------- 범용 라이브러리 import -----------------------
# 이미지 핸들링을 위한 CV2 라이브러리 import
import cv2
# 배열 핸들링을 위한 numpy 라이브러리 import
import numpy as np
# Intel RealSense(D435 등) 카메라 스트림(color/depth) 제어를 위해 pyrealsense2 라이브러리 import
import pyrealsense2 as rs


# QR Reader 클래스 선언
class QR_Reader:
    # 클래스 초기화 함수 선언
    def __init__(self):
        # 카메라 제어 객체 선언
        self.cam = None

        # 카메라 설정 객체 선언
        self.cam_config = None

        # qr_detector 객체 선언
        self.qr_detector = cv2.QRCodeDetector()

        # 카메라 시작 상태 저장 변수 선언
        self.camera_started = False

        # 마지막으로 읽은 QR 값 저장 변수 선언
        self.last_qr_text = None

        # CV2 창 이름 설정 변수 선언
        self.window_name = "D435 QR reader"

    # 카메라 시작 함수 선언
    def start(self):
        # 이미 카메라가 시작했으면
        if self.camera_started:
            # True return
            return True

        # 에러가 없으면
        try:
            # 카메라 제어 객체 생성
            self.cam = rs.pipeline()

            # 카메라 설정 객체 생성
            self.cam_config = rs.config()

            # 카메라 스트리밍 설정
            self.cam_config.enable_stream(
                rs.stream.color,
                640,
                480,
                rs.format.bgr8,
                30
            )

            # 카메라 시작
            self.cam.start(self.cam_config)

            # 카메라 시작 상태 True로 설정
            self.camera_started = True

            # 디버그 문구 print
            print("[QR READER] 카메라 시작 완료")

            # True return
            return True

        # 에러 발생 시
        except Exception as error:
            # 오류 출력
            print(f"[QR READER] 카메라 시작 실패 : {error}")

            # 카메라 시작 상태 False 저장
            self.camera_started = False

            # False return
            return False

    # 카메라 종료 함수 선언
    def stop(self):
        # 카메라가 시작되지 않았으면
        if not self.camera_started:
            # 카메라 객체 초기화
            self.cam = None

            # 카메라 설정 객체 초기화
            self.cam_config = None

            # 마지막 QR 값 초기화
            self.last_qr_text = None

            # OpenCV 창 닫기
            cv2.destroyAllWindows()

            # 함수 종료
            return

        # 에러가 없으면
        try:
            # 카메라 객체가 있으면
            if self.cam is not None:
                # 카메라 정지
                self.cam.stop()

                # 디버그 문구 print
                print("[QR READER] 카메라 종료 완료")

        # 에러 발생 시
        except Exception as error:
            # 디버그 문구 print
            print(f"[QR READER] 카메라 종료 중 에러 발생 : {error}")

        # 최종적으로
        finally:
            # 카메라 객체 초기화
            self.cam = None

            # 카메라 설정 객체 초기화
            self.cam_config = None

            # 카메라 시작 상태 False 저장
            self.camera_started = False

            # 마지막 QR 값 초기화
            self.last_qr_text = None

            # OpenCV 창 닫기
            cv2.destroyAllWindows()

    # QR 텍스트 유효성 확인 함수 선언
    def check_qr_text(self, qr_text):
        # qr_text가 없으면
        if qr_text is None:
            # False return
            return False

        # 문자열 변환 및 공백 제거
        qr_text = str(qr_text).strip()

        # 숫자가 아니거나 4자리 숫자가 아니면
        if len(qr_text) != 4 or not qr_text.isdigit():
            # 오류 출력
            print(f"[QR READER] QR 값 형식 오류 : {qr_text}")

            # False return
            return False

        # 조건을 만족하면 True return
        return True

    # QR 텍스트 유효성 확인 호환 함수 선언
    def is_valid_qr_text(self, qr_text):
        # 기존 QR 텍스트 확인 함수 호출 결과 return
        return self.check_qr_text(qr_text)

    # QR 검출 결과를 이미지에 표시하는 함수 선언
    def draw_qr_result(self, color_array, qr_text, points):
        # points가 있으면
        if points is not None:
            # 에러가 없으면
            try:
                # points 배열 차원 확인 후 정리
                if len(points.shape) == 3:
                    # 일반적으로 points shape는 (1, 4, 2)
                    draw_points = points[0].astype(int)
                else:
                    # 혹시 shape가 (4, 2)로 들어오면 그대로 사용
                    draw_points = points.astype(int)

                # 꼭지점이 4개 이상이면
                if len(draw_points) >= 4:
                    # 4개 꼭지점 선으로 연결
                    for i in range(4):
                        # 시작 좌표
                        pt1 = tuple(draw_points[i])

                        # 끝 좌표
                        pt2 = tuple(draw_points[(i + 1) % 4])

                        # 선 표시
                        cv2.line(color_array, pt1, pt2, (0, 255, 0), 2)

                    # QR Text 표시
                    cv2.putText(
                        color_array,
                        qr_text,
                        (draw_points[0][0], draw_points[0][1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2
                    )

            # 그리기 중 에러 발생 시
            except Exception as error:
                # 디버그 문구 print
                print(f"[QR READER] QR 결과 표시 중 에러 발생 : {error}")

        # points가 없거나 그리기 실패해도 원본 이미지 return
        return color_array

    # QR 읽기 함수 선언
    def read_qr(self, show_window=True, only_new_qr=False):
        # 카메라가 시작되지 않았으면
        if not self.camera_started:
            # 카메라 시작 실패 시
            if not self.start():
                # None return
                return None

        # 에러가 없으면
        try:
            # 카메라 프레임 수신
            frames = self.cam.wait_for_frames(timeout_ms=100)

        # 프레임 수신 실패 시
        except Exception:
            # OpenCV 창 갱신 처리
            if show_window:
                cv2.waitKey(1)

            # None return
            return None

        # Color Frame 추출
        color_frame = frames.get_color_frame()

        # Color Frame이 없으면
        if not color_frame:
            # None return
            return None

        # numpy 배열로 변환
        color_array = np.asanyarray(color_frame.get_data())

        # OpenCV 처리를 위해 연속 메모리 uint8 배열로 보정
        color_array = np.ascontiguousarray(color_array, dtype=np.uint8)

        # QR 검출 및 디코딩
        try:
            # QR 검출 및 디코딩
            qr_text, points, _ = self.qr_detector.detectAndDecode(color_array)

        # OpenCV 내부 QR 검출 에러 발생 시
        except cv2.error as error:
            # 디버그 문구 print
            print(f"[QR READER] QR 검출 중 OpenCV 에러 발생 : {error}")

            # QR 인식 실패로 처리하고 Thread는 계속 유지
            if show_window:
                # 화면 출력
                cv2.imshow(self.window_name, color_array)

                # 키 입력 처리
                key = cv2.waitKey(1) & 0xFF

                # q 키 입력 시
                if key == ord("q"):
                    # q return
                    return "q"

            # None return
            return None

        # 그 외 예외 발생 시
        except Exception as error:
            # 디버그 문구 print
            print(f"[QR READER] QR 검출 중 예외 발생 : {error}")

            # QR 인식 실패로 처리
            if show_window:
                # 화면 출력
                cv2.imshow(self.window_name, color_array)
                # 키 입력 처리
                key = cv2.waitKey(1) & 0xFF
                # q 키 입력 시
                if key == ord("q"):
                    # q return
                    return "q"
            # None return
            return None

        # QR이 검출되었으면
        if qr_text:
            # 이미지에 QR 결과 표시
            color_array = self.draw_qr_result(
                color_array=color_array,
                qr_text=qr_text,
                points=points
            )

        # 화면 표시 옵션이 True이면
        if show_window:
            # 화면 출력
            cv2.imshow(self.window_name, color_array)

            # 키 입력 처리
            key = cv2.waitKey(1) & 0xFF

            # q 키 입력 시
            if key == ord("q"):
                # q return
                return "q"

        # qr_text 값이 없으면
        if not qr_text:
            # None return
            return None

        # qr_text값이 있으면 문자열로 정리
        qr_text = str(qr_text).strip()

        # QR 유효성 확인 실패 시
        if not self.check_qr_text(qr_text):
            # None return
            return None

        # 새 QR만 처리하는 옵션이면
        if only_new_qr:
            # 이전 QR과 같으면
            if qr_text == self.last_qr_text:
                # None return
                return None
        # 마지막으로 읽은 QR 값 저장
        self.last_qr_text = qr_text
        # 로그 출력
        print(f"[QR READER] QR read : {qr_text}")
        # qr_text 값 return
        return qr_text

    # 기존 코드 호환용 QR 읽기 함수 선언
    def read_once(self, show_window=True, only_new_qr=False):
        # read_qr 함수 호출 결과 return
        return self.read_qr(
            show_window=show_window,
            only_new_qr=only_new_qr
        )
    

        

