#  ----------- 범용 라이브러리 import ---------------------
# 이미지 핸들링을 위한 CV2 라이브러리 import
import cv2
# 배열 핸들링을 위한 numpy 라이브러리 import
import numpy as np
# Intel RealSense(D435 등) 카메라 스트림(color/depth) 제어를 위해 pyrealsense2 라이브러리 import
import pyrealsense2 as rs

# ---------------------------- custom 라이브러리 import -------------------
# sam_acs_commu.py에 이미 선언된 T Command 구성 클래스 import
from sam_acs_package.sam_acs_commu import ACS_T_Command_commu


# QR 기반 T 명령 송신 클래스 선언
class ACS_T_commu:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # AMR_ID 저장
        self.amr_id = str(amr_id).zfill(3)

        # T_command 명령어 프레임 구성 클래스 객체 선언
        self.t_command = ACS_T_Command_commu(amr_id=self.amr_id)

        # 카메라 제어 객체 저장 변수 선언
        self.cam = None

        # 카메라 설정 객체 저장 변수 선언
        self.cam_config = None

        # QR_detector 객체 선언
        self.qr_detector = cv2.QRCodeDetector()

        # 카메라 시작 상태 저장 변수 선언
        self.camera_started = False

        # 마지막으로 읽은 QR 정보 저장 변수 선언
        self.last_read_qr_info = None

    # 카메라 시작 함수 선언
    def camera_start(self):
        # 이미 카메라가 시작 되었으면
        if self.camera_started:
            # True return
            return True

        # 에러가 없으면
        try:
            # 카메라 제어 객체 선언
            self.cam = rs.pipeline()

            # 카메라 설정 객체 선언
            self.cam_config = rs.config()

            # rgb 이미지 스트리밍 설정
            self.cam_config.enable_stream(rs.stream.color,640,480,rs.format.bgr8,30)

            # 카메라 스트리밍 시작
            self.cam.start(self.cam_config)

            # 카메라 시작 상태 True로 설정
            self.camera_started = True

            # 디버그 문구 print
            print("---------------------")
            print("[T Command] 카메라 시작")
            print("---------------------")

            # True return
            return True

        # 예외 발생 시
        except Exception as error:
            # 디버그 문구 print
            print("---------------------")
            print(f"[T Command] 카메라 시작 실패 : {error}")
            print("---------------------")

            # False return
            return False

    # D435 카메라 종료 함수 선언
    def camera_stop(self):
        # 에러가 없으면
        try:
            # 카메라 객체가 있으면
            if self.cam is not None:
                # 카메라 스트리밍 종료
                self.cam.stop()

                # 디버그 문구 print
                print("---------------------")
                print("[T Command] 카메라 종료")
                print("---------------------")

        # 예외 발생 시
        except Exception as error:
            # 디버그 문구 print
            print("---------------------")
            print(f"[T Command] 카메라 종료 중 에러 발생 : {error}")
            print("---------------------")

        # 최종적으로
        finally:
            # 카메라 객체 초기화
            self.cam = None

            # 카메라 설정 객체 초기화
            self.cam_config = None

            # 카메라 시작 변수 초기화
            self.camera_started = False

            # 카메라 창 닫기
            cv2.destroyAllWindows()

    # QR Text 체크 함수 선언
    def qr_text_check(self,qr_text):
        # qr_text 값이 없다면
        if qr_text is None:
            # False return
            return False

        # QR 값을 문자열로 변환 후 공백 제거
        qr_text = str(qr_text).strip()

        # QR 값이 4자리가 아니거나 숫자가 아니면
        if len(qr_text) != 4 or not qr_text.isdigit():
            # 디버그 문구 print
            print(f"[T Command] QR 값 형식 오류 : {qr_text}")

            # False return
            return False

        # True return
        return True

    # QR 검출 결과 화면 표시 함수 선언
    def draw_qr_read_result(self,color_array,qr_text,points):
        # QR 꼭지점 좌표가 읽혔으면
        if points is not None:
            # points를 정수형으로 변환
            points = points[0].astype(int)

            # 꼭지점 4개 선으로 연결
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
                qr_text,
                (points[0][0], points[0][1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2
            )

        # QR read 결과가 반영된 이미지 return
        return color_array

    # QR 1회 읽기 함수 선언
    def read_qr_once(self,show_window=True):
        # 카메라가 시작되지 않았다면
        if not self.camera_started:
            # 카메라 시작 실패 시
            if not self.camera_start():
                # None return
                return None

        # 프레임 수신 대기
        frames = self.cam.wait_for_frames()

        # color_frame만 저장
        color_frame = frames.get_color_frame()

        # color_frame이 없으면
        if not color_frame:
            # None return
            return None

        # color_frame을 numpy 배열로 변환
        color_array = np.asanyarray(color_frame.get_data())

        # QR 코드 검출 및 디코딩
        qr_text,points,_ = self.qr_detector.detectAndDecode(color_array)

        # QR 값이 읽혔으면
        if qr_text:
            # QR read 결과 현재 이미지에 반영
            color_array = self.draw_qr_read_result(
                color_array=color_array,
                qr_text=qr_text,
                points=points
            )

        # 화면 표시 옵션이 True면
        if show_window:
            # 영상 출력
            cv2.imshow("D435 QR reader", color_array)

            # OpenCV 창 갱신을 위한 키 입력 처리
            key = cv2.waitKey(1) & 0xFF

            # q 키를 누르면
            if key == ord("q"):
                # q return
                return "q"

        # qr_text return
        return qr_text

    # QR 값을 반영하여 T 프레임 구성 함수 선언
    def build_t_frame_with_qr_text(self,qr_text):
        # QR 값을 문자열로 변환 후 공백 제거
        qr_text = str(qr_text).strip()

        # QR Text 유효성 검사에 실패하였다면
        if not self.qr_text_check(qr_text):
            # None return
            return None

        # QR에서 읽은 값을 Location Node로 사용
        location_node = qr_text

        # T 명령 frame 구성
        t_frame = self.t_command.build_t_command_frame(location_node=location_node)

        # 디버그 문구 print
        print("--------------------------------------------------")
        print("[T Command] T Command Frame 생성 완료")
        print(f"[T Command] QR Text       : {qr_text}")
        print(f"[T Command] Location Node : {location_node}")
        print(f"[T Command] HEX Frame     : {self.t_command.byte_to_hex_string(t_frame)}")
        print("--------------------------------------------------")

        # T Command Frame return
        return t_frame

    # QR 값을 읽어서 T Command Frame 구성 함수 선언
    def read_qr_and_build_t_frame(self,show_window=True,only_new_qr=True):
        # QR 1회 읽기
        qr_text = self.read_qr_once(show_window=show_window)

        # q 키 입력이면
        if qr_text == "q":
            # q return
            return "q"

        # QR 값이 없으면
        if not qr_text:
            # None return
            return None

        # QR 값을 문자열로 변환 후 공백 제거
        qr_text = str(qr_text).strip()

        # QR Text 유효성 확인 실패 시
        if not self.qr_text_check(qr_text):
            # None return
            return None

        # 새 QR만 처리하는 옵션이 True이면
        if only_new_qr:
            # 이전 QR과 동일하면
            if qr_text == self.last_read_qr_info:
                # None return
                return None

        # 마지막 QR 정보 갱신
        self.last_read_qr_info = qr_text

        # 디버그 문구 print
        print(f"[QR] read QR info : {qr_text}")

        # QR Text로 T Command Frame 구성 후 return
        return self.build_t_frame_with_qr_text(qr_text=qr_text)

    # T Command Frame 송신 함수 선언
    def send_t_frame(self,acs_client,t_frame):
        # T Frame이 없으면
        if t_frame is None:
            # False return
            return False

        # ACS Client 객체가 없으면
        if acs_client is None:
            # 디버그 문구 print
            print("[T Command] T 명령 송신 실패 : ACS Client 객체 없음")

            # False return
            return False

        # ACS 소켓이 연결되어 있지 않으면
        if acs_client.client_socket is None:
            # 디버그 문구 print
            print("[T Command] T 명령 송신 실패 : ACS 소켓 연결 안됨")

            # False return
            return False

        # 디버그 문구 print
        print("--------------------------------------------------")
        print(f"[T Command] T Command 송신: {t_frame}")
        print(f"[T Command] HEX Frame : {self.t_command.byte_to_hex_string(t_frame)}")
        print("--------------------------------------------------")

        # 기존 ACS Client의 send_frame 함수로 송신
        return acs_client.send_frame(t_frame)

    # QR Text로 T Command Frame 구성 후 송신하는 함수 선언
    def send_t_command_by_qr_text(self,acs_client,qr_text):
        # QR Text로 T Command Frame 구성
        t_frame = self.build_t_frame_with_qr_text(qr_text=qr_text)

        # T Frame이 없으면
        if t_frame is None:
            # False return
            return False

        # T Command Frame 송신 결과 return
        return self.send_t_frame(
            acs_client=acs_client,
            t_frame=t_frame
        )

    # QR을 읽어서 T Command Frame 구성 후 송신하는 함수 선언
    def read_qr_and_send_t_command(self,acs_client,show_window=True,only_new_qr=True):
        # QR을 읽어서 T Command Frame 구성
        t_frame = self.read_qr_and_build_t_frame(
            show_window=show_window,
            only_new_qr=only_new_qr
        )

        # q 입력이면
        if t_frame == "q":
            # q return
            return "q"

        # T Frame이 없으면
        if t_frame is None:
            # None return
            return None

        # T Command Frame 송신 결과 return
        return self.send_t_frame(
            acs_client=acs_client,
            t_frame=t_frame
        )

    
        



    


