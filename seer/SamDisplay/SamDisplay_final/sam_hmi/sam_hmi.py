# ----------------------- 범용 라이브러리 import -----------------------
# pygame UI 구성을 위한 pygame 라이브러리 import
import pygame
# 프로그램 종료 처리를 위한 sys 라이브러리 import
import sys
# 파일 경로 확인을 위한 os 라이브러리 import
import os
# thread 처리를 위한 threading 라이브러리 import
import threading
# 시간 처리를 위한 time 라이브러리 import
import time

# ----------------------- custom 라이브러리 import -----------------------
# SEER AMR 제어를 위한 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# Ezi-IO DIO 제어를 위한 DIO_Commu 클래스 import
from custom_package.dio_commu import DIO_Commu

# ----------------------- 화면 크기 설정 -----------------------
# HMI 화면 가로 크기 설정
SCREEN_WIDTH = 1600
# HMI 화면 세로 크기 설정
SCREEN_HEIGHT = 900

# ----------------------- UI 적용 색상 설정 변수 선언 -----------------------
# HMI 외부 배경색 설정
BLUE = (0, 120, 190)
# HMI 내부 배경색 설정
WHITE = (245, 245, 245)
# 기본 검정색 설정
BLACK = (0, 0, 0)
# 제목 및 주요 문구 색상 설정
DARK_NAVY = (5, 10, 30)
# 상태 표시 박스 외곽선 색상 설정
DARK_BLUE = (0, 90, 170)
# 패널 외곽선 색상 설정
PANEL_BLUE = (30, 115, 190)
# 좌측 패널 구분선 색상 설정
LIGHT_BLUE = (170, 205, 235)
# 배터리 정상 상태 색상 설정
GREEN = (0, 190, 0)
# 배터리 주의 상태 색상 설정
ORANGE = (255, 140, 0)
# 배터리 부족 상태 색상 설정
RED = (220, 0, 0)
# 회색 설정
GRAY = (180, 180, 180)

# ----------------------- DIO 설정 변수 선언 -----------------------
# DIO 모듈 IP 선언
DIO_IP = "192.168.0.12"
# DIO 모듈 포트 선언
DIO_PORT = 2001
# DIO 통신 Timeout 설정
DIO_TIMEOUT = 5.0

# 버튼별 제어 DO 번호 설정
# Release Break 버튼 클릭 시 High로 만들 DO 번호 설정
DO_RELEASE_BRAKE = 31
# 2F 버튼 클릭 시 High로 만들 DO 번호 설정
DO_2F = 23
# 3F 버튼 클릭 시 High로 만들 DO 번호 설정
DO_3F = 24
# 4F 버튼 클릭 시 High로 만들 DO 번호 설정
DO_4F = 25

# 제어에 사용하는 DO 목록
CONTROL_DO_LIST = [
    # Release Break 버튼에서 사용하는 DO 번호
    DO_RELEASE_BRAKE,
    # 2F 버튼에서 사용하는 DO 번호
    DO_2F,
    # 3F 버튼에서 사용하는 DO 번호
    DO_3F,
    # 4F 버튼에서 사용하는 DO 번호
    DO_4F,
]

# ----------------------- 각 컴포넌트 별 주기 설정 -----------------------
# 배터리 상태 업데이트 주기 설정
BATTERY_UPDATE_INTERVAL_SEC = 2.0

# ----------------------- HMI 사용 이미지 경로 설정 -----------------------
# L2M 로고 경로 설정
LOGO_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/lab2m_logo.png"
# BATTERY 아이콘 경로 설정
BATTERY_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/battery.png"
# AMR 아이콘 경로 설정
AMR_ICON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/amr_icon.png"
# AMR 미는 아이콘 경로 설정
PUSH_ICON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/push_icon.png"
# AMR 브레이크 해제 버튼 경로 설정
BREAK_BUTTON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/break_button.png"
# 엘리베이터 아이콘 이미지 경로 설정
FLOOR_ICON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/floor_icon.png"
# 2층 선택 버튼 이미지 경로 설정
BUTTON_2F_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/2f_button.png"
# 3층 선택 버튼 이미지 경로 설정
BUTTON_3F_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/3f_button.png"
# 4층 선택 버튼 이미지 경로 설정
BUTTON_4F_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_hmi/image/4f_button.png"

# -----------------------HMI 내부 영역 설정 -------------------------------------
# 전체 내부 흰색 영역 설정
INNER_RECT = pygame.Rect(25, 25, 1550, 850)

# 상단 영역 설정
# L2M 로고 출력 위치 설정
LOGO_POS = (70, 55)
# L2M 로고 출력 크기 설정
LOGO_SIZE = (210, 180)

# 제목 영역 설정
TITLE_CENTER_POS = (800, 135)

# 배터리 아이콘 위치
BATTERY_POS = (1385, 60)
# 배터리 아이콘 크기
BATTERY_SIZE = (130, 180)

# 좌측 패널 영역 설정
LEFT_PANEL_RECT = pygame.Rect(60, 255, 860, 590)
# 우측 패널 영역 설정
RIGHT_PANEL_RECT = pygame.Rect(950, 255, 590, 590)

# 좌측 상단 MoMa 상태 영역 설정
# AMR 아이콘 위치 설정
AMR_ICON_POS = (125, 325)
# AMR 아이콘 크기 설정
AMR_ICON_SIZE = (175, 175)

# MoMa 상태 표시 위치 설정
MOMA_STATE_TITLE_POS = (495, 330)
# Moma 상태 표시 박스 위치 설정
MOMA_STATE_BOX_RECT = pygame.Rect(335, 380, 535, 125)

# 좌측 구분선 위치 설정
LEFT_DIVIDER_START = (95, 548)
LEFT_DIVIDER_END = (885, 548)

# 좌측 하단 수동 이동 아이콘 위치 설정
PUSH_ICON_POS = (100, 620)
# 좌측 하단 수동 이동 아이콘 크기 설정
PUSH_ICON_SIZE = (210, 155)

# 수동 이동 문구 위치 설정
PUSH_TEXT_CENTER_POS = (570, 640)

# 수동 이동 버튼 위치 설정
BREAK_BUTTON_POS = (345, 695)
# 수동 이동 버튼 크기 설정
BREAK_BUTTON_SIZE = (500, 100)

# 우측 층 선택 아이콘 위치 설정
FLOOR_ICON_POS = (1005, 282)
# 우측 층 선택 아이콘 크기 설정
FLOOR_ICON_SIZE = (95, 95)

# 층 선택 문구 위치 설정
FLOOR_TITLE_CENTER_POS = (1270, 330)

# 2층 선택 버튼 위치 설정
BUTTON_2F_POS = (990, 392)
# 2층 선택 버튼 크기 설정
BUTTON_2F_SIZE = (515, 115)

# 3층 선택 버튼 위치 설정
BUTTON_3F_POS = (990, 530)
# 3층 선택 버튼 크기 설정
BUTTON_3F_SIZE = (515, 115)

# 4층 선택 버튼 위치 설정
BUTTON_4F_POS = (990, 668)
# 4층 선택 버튼 크기 설정
BUTTON_4F_SIZE = (515, 115)

# ----------------------- 한글 지원 폰트 반환 함수 선언 -----------------------
def get_korean_font(size, bold=False):
    # 한글폰트 후보 목록 선언
    font_candidates = [
        "malgungothic",
        "맑은고딕",
        "nanumgothic",
        "nanumbarungothic",
        "gulim",
        "dotum",
        "applegothic",
    ]

    # 폰트 후보 목록을 각각 확인
    for one_font_name in font_candidates:
        # 현재 폰트를 pygame 폰트 경로에서 탐색
        font_path = pygame.font.match_font(one_font_name)

        # 해당 폰트가 있으면
        if font_path:
            # Font 객체 생성
            font = pygame.font.Font(font_path, size)

            # 볼드 처리 적용
            font.set_bold(bold)

            # 폰트 객체 return
            return font

    # 폰트 목록에 없다면 arial 폰트 객체 생성
    fallback_font = pygame.font.SysFont("arial", size, bold=bold)

    # arial 폰트 객체 return
    return fallback_font

# ----------------------- HMI에 텍스트 출력 함수 선언 -----------------------
def draw_text(screen, text, font, color, center_pos):
    # 출력할 텍스트 surface 생성
    text_surface = font.render(text, True, color)

    # 텍스트가 중심 좌표 기준으로 배치되도록 rect 생성
    text_rect = text_surface.get_rect(center=center_pos)

    # HMI 화면에 텍스트 출력
    screen.blit(text_surface, text_rect)

# ----------------------- 단일 이미지 로드 함수 선언 -----------------------
def load_ui_image(image_path, image_size):
    # 이미지 파일이 실제로 존재하지 않으면
    if not os.path.exists(image_path):
        # 이미지 파일 없음 문구 출력
        print(f"[UI] 이미지 파일을 찾을 수 없습니다 : {image_path}")

        # None return
        return None

    # 이미지 파일 로드
    image = pygame.image.load(image_path).convert_alpha()

    # 이미지 크기 조절
    image = pygame.transform.smoothscale(image, image_size)

    # 이미지 return
    return image

# ----------------------- 전체 UI 이미지 로드 함수 선언 -----------------------
def load_all_ui_images():
    # HMI에 활용될 이미지 전체 출력
    ui_images = {
        "logo": load_ui_image(LOGO_IMAGE_PATH, LOGO_SIZE),
        "amr_icon": load_ui_image(AMR_ICON_IMAGE_PATH, AMR_ICON_SIZE),
        "push_icon": load_ui_image(PUSH_ICON_IMAGE_PATH, PUSH_ICON_SIZE),
        "break_button": load_ui_image(BREAK_BUTTON_IMAGE_PATH, BREAK_BUTTON_SIZE),
        "floor_icon": load_ui_image(FLOOR_ICON_IMAGE_PATH, FLOOR_ICON_SIZE),
        "2f_button": load_ui_image(BUTTON_2F_IMAGE_PATH, BUTTON_2F_SIZE),
        "3f_button": load_ui_image(BUTTON_3F_IMAGE_PATH, BUTTON_3F_SIZE),
        "4f_button": load_ui_image(BUTTON_4F_IMAGE_PATH, BUTTON_4F_SIZE),
    }

    # 이미지 딕셔너리 return
    return ui_images

# ----------------------- 이미지 출력 함수 선언 -----------------------
def draw_image(screen, image, image_pos):
    # 이미지가 정상적으로 로드되었으면
    if image is not None:
        # HMI 화면에 이미지 출력
        screen.blit(image, image_pos)

# ----------------------- 버튼 클릭 영역 생성 함수 선언 -----------------------
def make_button_rects():
    # 버튼 클릭 영역 딕셔너리 생성
    button_rects = {
        "RELEASE_BRAKE": pygame.Rect(BREAK_BUTTON_POS, BREAK_BUTTON_SIZE),
        "2F": pygame.Rect(BUTTON_2F_POS, BUTTON_2F_SIZE),
        "3F": pygame.Rect(BUTTON_3F_POS, BUTTON_3F_SIZE),
        "4F": pygame.Rect(BUTTON_4F_POS, BUTTON_4F_SIZE),
    }

    # 버튼 클릭 영역 딕셔너리 return
    return button_rects

# ----------------------- 배터리 잔량 숫자 반환 함수 선언 -----------------------
def get_battery_percent_value(battery_text):
    # 배터리 문자열이 에러 또는 미수신 상태면
    if battery_text in ["ERR", "N/A", "BAT"]:
        # 0.0 return
        return 0.0

    # 에러가 없으면
    try:
        # 배터리 문자열에서 % 문자를 제거하고 float로 변환
        battery_value = float(str(battery_text).replace("%", ""))

        # 배터리 값이 0보다 작으면
        if battery_value < 0:
            # 0.0 return
            return 0.0

        # 배터리 값이 100보다 크면
        if battery_value > 100:
            # 100.0 return
            return 100.0

        # 정상 배터리 값 return
        return battery_value

    # 변환 중 에러가 발생하면
    except Exception:
        # 0.0 return
        return 0.0

# ----------------------- 배터리 잔량 색상 반환 함수 선언 -----------------------
def get_battery_color(battery_percent):
    # 배터리 잔량이 100~70이면
    if 70 <= battery_percent <= 100:
        # 초록색 return
        return GREEN

    # 배터리 잔량이 50~70이면
    elif 50 <= battery_percent < 70:
        # 오랜지색 return
        return ORANGE

    # 50 미만이면 RED return
    else:
        return RED

# ----------------------- 배터리 위젯 출력 함수 선언 -----------------------
def draw_battery_widget(screen, battery_text, percent_font):
    # 인자로 받은 배터리 문자열을 숫자 %값으로 변환
    battery_percent = get_battery_percent_value(battery_text)

    # 배터리 잔량에 따른 채움 색상을 계산
    fill_color = get_battery_color(battery_percent)

    # 배터리 아이콘 몸체 부분 x,y,w,h 설정
    body_x = BATTERY_POS[0] + 25
    body_y = BATTERY_POS[1] + 20
    body_w = 90
    body_h = 150

    # 배터리 아이콘 머리 부분 w,h 크기 설정 및 좌표 연산
    cap_w = 36
    cap_h = 14
    cap_x = body_x + (body_w - cap_w) // 2
    cap_y = body_y - cap_h + 2

    # 배터리 아이콘 몸체 rect 생성
    body_rect = pygame.Rect(body_x, body_y, body_w, body_h)

    # 배터리 아이콘 머리 rect 생성
    cap_rect = pygame.Rect(cap_x, cap_y, cap_w, cap_h)

    # 배터리 아이콘 내부 여백 설정
    inner_padding = 8

    # 배터리 아이콘 내부 x,y 좌표,가로,세로 크기 연산
    inner_x = body_x + inner_padding
    inner_y = body_y + inner_padding
    inner_w = body_w - (inner_padding * 2)
    inner_h = body_h - (inner_padding * 2)

    # 배터리 잔량에 따른 채움 높이 연산
    fill_h = int(inner_h * (battery_percent / 100.0))

    # 배터리 채움 영역 rect 설정
    fill_rect = pygame.Rect(
        inner_x,
        inner_y + (inner_h - fill_h),
        inner_w,
        fill_h,
    )

    # 배터리 내부을 흰색으로 아이콘 hmi 화면에 생성
    pygame.draw.rect(
        screen,
        WHITE,
        pygame.Rect(inner_x, inner_y, inner_w, inner_h),
        border_radius=10,
    )

    # 연산한 배터리 내부 채움 높이가 0보다 크면
    if fill_h > 0:
        # 높이만큼 색상 채우기
        pygame.draw.rect(
            screen,
            fill_color,
            fill_rect,
            border_radius=10,
        )

    # 배터리 몸체 외곽선 검정색으로 생성
    pygame.draw.rect(screen, BLACK, body_rect, width=6, border_radius=14)

    # 배터리 머리 외곽선 검정색으로 생성
    pygame.draw.rect(screen, BLACK, cap_rect, border_radius=4)

    # 배터리 잔량 문자열이 ERR, N/A,BAT 중 하나면
    if battery_text in ["ERR", "N/A", "BAT"]:
        # 그대로 표시
        display_text = battery_text

    # 배터리 잔량이 정상적으로 표시되면
    else:
        # 배터리 잔량 값 퍼센트 문자열로 표시
        display_text = f"{battery_percent:.0f}%"

    # 배터리 중앙에 배터리 잔량 표시
    draw_text(
        screen=screen,
        text=display_text,
        font=percent_font,
        color=BLACK,
        center_pos=(body_x + body_w // 2, body_y + body_h // 2),
    )

# ----------------------- HMI 제어 클래스 선언 -----------------------
# AMR 배터리 조회, DIO 출력을 담당하는 클래스 선언
class MoMa_HMI_Controller:
    # 클래스 초기화 함수 선언
    def __init__(self):
        # SEER 제어 객체 초기화
        self.seer = None

        # DIO 통신 객체 초기화
        self.dio = None

        # DIO 제어가 동시에 겹치지 않도록 Lock 생성
        self.ctrl_lock = threading.Lock()

        # 배터리 값 접근을 보호하기 위한 Lock 생성
        self.battery_lock = threading.Lock()

        # thread 반복 실행 여부 저장 변수 True로 설정
        self.running = True

        # 배터리 잔량 저장 변수 초기화
        self.battery_level = None

        # 배터리 표시 문자열을 초기값 BAT으로 설정
        self.battery_status_text = "BAT"

        # SEER AMR 연결
        self.connect_amr()

        # DIO 모듈 연결
        self.connect_dio()

        # 배터리 갱신 thread를 시작
        self.start_battery_thread()

    # --------------------- 각 모듈(SEER, DIO)과 통신하는 함수 선언 ----------------------
    # amr과 연결 함수 선언
    def connect_amr(self):
        # 에러가 없으면
        try:
            # seer_commu 객체 생성
            self.seer = SEER_commu()

            # 연결 완료 문구 print
            print("[UI] SEER AMR 연결 완료")

        # 에러가 발생하면
        except Exception as e:
            # SEER 통신 객체를 None으로 설정
            self.seer = None

            # 디버그 문구 print
            print(f"[UI] SEER AMR 연결 실패 : {e}")

    # dio 모듈과 연결 함수 선언
    def connect_dio(self):
        # 에러가 없으면
        try:
            # DIO_Commu 객체 생성
            self.dio = DIO_Commu(ip=DIO_IP, port=DIO_PORT, timeout=DIO_TIMEOUT)

            # DIO 모듈과 TCP 연결
            self.dio.dio_connect()

            # 에러가 없으면
            try:
                # 연결된 DIO 모듈 타입과 버전 정보 read
                dio_type, version = self.dio.get_dio_module_info()

                # 연결된 DIO 모듈 정보 출력
                print(f"[UI] DIO 모듈 정보 : dio_type={dio_type}, version={version}")

            # DI 정보 확인 중 에러 발생 시
            except Exception as info_error:
                # DIO 모듈 정보 읽기 실패 메시지 출력
                print(f"[UI] DIO 모듈 정보 읽기 실패 : {info_error}")

            # 연결 성공 문구 print
            print("[UI] DIO 연결 완료")

        # DIO 연결 중 에러 발생 시
        except Exception as e:
            # DIO 통신 객체를 None으로 설정
            self.dio = None

            # DIO 연결 실패 문구 출력
            print(f"[UI] DIO 연결 실패 : {e}")

    # ------------------------------ 배터리 잔량 처리 관련 함수 선언 --------------------------------------
    # 배터리 잔량 갱신 thread를 시작 함수 선언
    def start_battery_thread(self):
        # battery_update_loop 함수 실행 thread 생성
        battery_thread = threading.Thread(target=self.battery_update_loop, daemon=True)

        # 배터리 갱신 thread를 시작
        battery_thread.start()

    # 배터리 정보를 주기적으로 갱신 함수 선언
    def battery_update_loop(self):
        # 프로그램이 실행 중인 동안 반복
        while self.running:
            # SEER 객체가 없지 않으면
            if self.seer is not None:
                # 에러가 없으면
                try:
                    # SEER AMR 배터리 정보 저장
                    battery_info = self.seer.get_battery_info()

                    # 배터리 정보가 tuple이고 값이 1개 이상인지 확인
                    if isinstance(battery_info, tuple) and len(battery_info) >= 1:
                        # tuple의 첫 번째 값을 배터리 잔량으로 사용
                        current_battery_level = battery_info[0]

                        # 배터리 값이 None이 아니면
                        if current_battery_level is not None:
                            # 배터리 값을 float로 변환
                            current_battery_level = float(current_battery_level)

                            # 배터리 값이 0~1 사이이면
                            if 0 <= current_battery_level <= 1:
                                # 비율값을 퍼센트 값으로 변환
                                display_battery_level = current_battery_level * 100

                            # 배터리 값이 1보다 크면
                            else:
                                # 원본 값을 그대로 사용
                                display_battery_level = current_battery_level

                            # 배터리 변수 접근을 Lock으로 보호
                            with self.battery_lock:
                                # 배터리 숫자 값을 저장
                                self.battery_level = display_battery_level

                                # 화면 표시용 배터리 문자열을 저장
                                self.battery_status_text = f"{display_battery_level:.0f}%"

                        # 배터리 값이 None이면
                        else:
                            # 배터리 변수 접근을 Lock으로 보호
                            with self.battery_lock:
                                # 배터리 표시 문자열을 N/A로 설정
                                self.battery_status_text = "N/A"

                    # 배터리 정보 형식이 예상과 다르면
                    else:
                        # 배터리 변수 접근을 Lock으로 보호
                        with self.battery_lock:
                            # 배터리 표시 문자열을 ERR로 설정
                            self.battery_status_text = "ERR"

                        # 배터리 정보 형식 이상 문구 출력
                        print(f"[UI] 배터리 정보 형식 이상 : {battery_info}")

                # 배터리 조회 중 오류가 발생하면
                except Exception as e:
                    # 배터리 정보 수신 실패 문구 출력
                    print(f"[UI] 배터리 정보 수신 실패 : {e}")

                    # 배터리 변수 접근을 Lock으로 보호
                    with self.battery_lock:
                        # 배터리 표시 문자열을 ERR로 설정
                        self.battery_status_text = "ERR"

            # 배터리 갱신 주기만큼 대기
            time.sleep(BATTERY_UPDATE_INTERVAL_SEC)

    # 현재 배터리 표시 문자열을 반환하는 함수를 선언
    def get_battery_text(self):
        # 배터리 변수 접근을 Lock으로 보호
        with self.battery_lock:
            # 현재 배터리 표시 문자열 return
            return self.battery_status_text

    # ------------------------------ DIO 출력 제어 관련 함수 선언 --------------------------------------
    # DO High 제어를 thread로 실행하는 함수 선언
    def set_do_high_async(self, do_no):
        # DO 제어 함수 실행 thread 생성
        do_thread = threading.Thread(target=self.set_do_high, args=(do_no,), daemon=True)

        # DO 제어 thread 시작
        do_thread.start()

    # 실제 DO High 제어 함수 선언
    def set_do_high(self, do_no):
        # DIO 객체가 없으면
        if self.dio is None:
            # DIO 연결 안 됨 문구 출력
            print(f"[UI] DIO가 연결되지 않아 DO {do_no}번을 High로 만들 수 없습니다.")

            # 함수 종료
            return

        # 에러가 없으면
        try:
            # DIO 명령이 동시에 겹치지 않도록 Lock 사용
            with self.ctrl_lock:
                # 인자로 받은 DO 번호를 High로 설정
                self.dio.set_do_on(do_no)

            # DO High 완료 문구 출력
            print(f"[UI] DO {do_no}번 High 완료")

        # DO 제어 중 에러 발생 시
        except Exception as e:
            # DO High 실패 문구 출력
            print(f"[UI] DO {do_no}번 High 실패 : {e}")

    # 프로그램 종료 시 통신과 DO 출력을 정리하는 함수 선언
    def close(self):
        # thread 반복 실행을 중단
        self.running = False

        # DIO 객체가 존재하면
        if self.dio is not None:
            # 에러가 없으면
            try:
                # UI에서 사용한 DO 번호들을 하나씩 반복
                for do_no in CONTROL_DO_LIST:
                    # 개별 DO Low 처리 중 에러가 발생해도 다음 DO 처리를 계속하기 위해 try 사용
                    try:
                        # 현재 DO 번호를 Low로 설정
                        self.dio.set_do_off(do_no)

                        # DO Low 완료 문구 출력
                        print(f"[UI] 종료 처리 : DO {do_no}번 Low 완료")

                    # 개별 DO Low 처리 중 에러 발생 시
                    except Exception as off_error:
                        # DO Low 실패 문구 출력
                        print(f"[UI] 종료 처리 : DO {do_no}번 Low 실패 : {off_error}")

                # DIO TCP 연결 종료
                self.dio.dio_close()

            # DIO 종료 처리 중 에러 발생 시
            except Exception as e:
                # DIO 종료 실패 문구 출력
                print(f"[UI] DIO 종료 실패 : {e}")

        # SEER AMR 통신 객체가 존재하면
        if self.seer is not None:
            # 에러가 없으면
            try:
                # SEER AMR 소켓 종료
                self.seer.socket_close()

                # 종료 완료 문구 출력
                print("[UI] SEER AMR 소켓 종료 완료")

            # SEER 소켓 종료 중 에러 발생 시
            except Exception as e:
                # 종료 실패 문구 출력
                print(f"[UI] SEER AMR 소켓 종료 실패 : {e}")

# ----------------------- 전체 UI 출력 함수 선언 -----------------------
# 현재 상태에 맞춰 전체 UI 화면을 그리는 함수 선언
def draw_ui(screen, title_font, section_title_font, battery_font, ui_images, hmi_controller):
    # 전체 화면을 파란색으로 채움
    screen.fill(BLUE)

    # 내부 흰색 메인 영역 생성
    pygame.draw.rect(screen, WHITE, INNER_RECT, border_radius=24)

    # 로고 이미지 출력
    draw_image(screen, ui_images["logo"], LOGO_POS)

    # 상단 제목 출력
    draw_text(
        screen=screen,
        text="L2M Mobile Manipulator",
        font=title_font,
        color=DARK_NAVY,
        center_pos=TITLE_CENTER_POS,
    )

    # 현재 배터리 표시 문자열 가져오기
    battery_text = hmi_controller.get_battery_text()

    # 배터리 위젯 출력
    draw_battery_widget(screen, battery_text, battery_font)

    # 좌측 패널 내부 흰색 생성
    pygame.draw.rect(screen, WHITE, LEFT_PANEL_RECT, border_radius=18)

    # 좌측 패널 외곽선 생성
    pygame.draw.rect(screen, PANEL_BLUE, LEFT_PANEL_RECT, width=3, border_radius=18)

    # 우측 패널 내부 흰색 생성
    pygame.draw.rect(screen, WHITE, RIGHT_PANEL_RECT, border_radius=18)

    # 우측 패널 외곽선 생성
    pygame.draw.rect(screen, PANEL_BLUE, RIGHT_PANEL_RECT, width=3, border_radius=18)

    # AMR 아이콘 출력
    draw_image(screen, ui_images["amr_icon"], AMR_ICON_POS)

    # MoMa State 제목 출력
    draw_text(
        screen=screen,
        text="MoMa State",
        font=section_title_font,
        color=DARK_NAVY,
        center_pos=MOMA_STATE_TITLE_POS,
    )

    # MoMa State 표시 박스 내부 흰색 생성
    pygame.draw.rect(screen, WHITE, MOMA_STATE_BOX_RECT, border_radius=16)

    # MoMa State 표시 박스 외곽선 생성
    pygame.draw.rect(screen, DARK_BLUE, MOMA_STATE_BOX_RECT, width=4, border_radius=16)

    # 상태 표시 기능은 현재 사용하지 않으므로 MoMa State 박스 안에는 문구를 출력하지 않음

    # 좌측 패널 내부 구분선 생성
    pygame.draw.line(screen, LIGHT_BLUE, LEFT_DIVIDER_START, LEFT_DIVIDER_END, width=3)

    # Push 아이콘 출력
    draw_image(screen, ui_images["push_icon"], PUSH_ICON_POS)

    # Push MoMa by hands 문구 출력
    draw_text(
        screen=screen,
        text="Push MoMa by hands",
        font=section_title_font,
        color=DARK_NAVY,
        center_pos=PUSH_TEXT_CENTER_POS,
    )

    # Release Brake 버튼 이미지 출력
    draw_image(screen, ui_images["break_button"], BREAK_BUTTON_POS)

    # Floor 아이콘 출력
    draw_image(screen, ui_images["floor_icon"], FLOOR_ICON_POS)

    # Floor Selection 제목 출력
    draw_text(
        screen=screen,
        text="Floor Selection",
        font=section_title_font,
        color=DARK_NAVY,
        center_pos=FLOOR_TITLE_CENTER_POS,
    )

    # 2F 버튼 이미지 출력
    draw_image(screen, ui_images["2f_button"], BUTTON_2F_POS)

    # 3F 버튼 이미지 출력
    draw_image(screen, ui_images["3f_button"], BUTTON_3F_POS)

    # 4F 버튼 이미지 출력
    draw_image(screen, ui_images["4f_button"], BUTTON_4F_POS)

# ----------------------- 마우스 클릭 처리 함수 선언 -----------------------
# 마우스 클릭 좌표를 확인해서 해당 버튼 기능을 실행하는 함수 선언
def handle_mouse_down(mouse_pos, button_rects, hmi_controller):
    # Release Brake 버튼을 클릭했으면
    if button_rects["RELEASE_BRAKE"].collidepoint(mouse_pos):
        # DO 31번 High
        hmi_controller.set_do_high_async(DO_RELEASE_BRAKE)

    # 2F 버튼을 클릭했으면
    elif button_rects["2F"].collidepoint(mouse_pos):
        # DO 23번 High
        hmi_controller.set_do_high_async(DO_2F)

    # 3F 버튼을 클릭했으면
    elif button_rects["3F"].collidepoint(mouse_pos):
        # DO 24번 High
        hmi_controller.set_do_high_async(DO_3F)

    # 4F 버튼을 클릭했으면
    elif button_rects["4F"].collidepoint(mouse_pos):
        # DO 25번 High
        hmi_controller.set_do_high_async(DO_4F)

# ----------------------- 메인 함수 선언 -----------------------
# Pygame UI 프로그램의 시작점이 되는 메인 함수 선언
def main():
    # pygame 전체 기능 초기화
    pygame.init()

    # pygame 폰트 기능 초기화
    pygame.font.init()

    # 설정한 크기로 pygame 화면 객체 생성
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

    # pygame 창 제목 설정
    pygame.display.set_caption("L2M Mobile Manipulator HMI")

    # FPS 제어를 위한 Clock 객체 생성
    clock = pygame.time.Clock()

    # 상단 제목용 폰트 생성
    title_font = get_korean_font(62, bold=True)

    # 섹션 제목용 폰트 생성
    section_title_font = get_korean_font(48, bold=True)

    # 배터리 표시용 폰트 생성
    battery_font = get_korean_font(26, bold=True)

    # UI에서 사용할 모든 이미지 로드
    ui_images = load_all_ui_images()

    # 마우스 클릭 감지를 위한 버튼 영역 생성
    button_rects = make_button_rects()

    # HMI 제어 객체 생성
    hmi_controller = MoMa_HMI_Controller()

    # 메인 루프 실행 여부 True로 설정
    running = True

    # 메인 루프 시작
    while running:
        # UI가 초당 최대 60번 갱신되도록 제한
        clock.tick(60)

        # pygame 이벤트를 하나씩 확인
        for event in pygame.event.get():
            # 사용자가 창 닫기 버튼을 눌렀으면
            if event.type == pygame.QUIT:
                # 메인 루프 종료
                running = False

            # 사용자가 마우스 버튼을 눌렀으면
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # 현재 마우스 좌표 가져오기
                mouse_pos = pygame.mouse.get_pos()

                # 클릭 좌표에 맞는 버튼 기능 실행
                handle_mouse_down(mouse_pos, button_rects, hmi_controller)

        # 전체 UI 화면 다시 그리기
        draw_ui(
            screen=screen,
            title_font=title_font,
            section_title_font=section_title_font,
            battery_font=battery_font,
            ui_images=ui_images,
            hmi_controller=hmi_controller,
        )

        # 그린 화면을 실제 창에 반영
        pygame.display.flip()

    # 프로그램 종료 전에 DIO와 AMR 통신 정리
    hmi_controller.close()

    # pygame 종료
    pygame.quit()

    # Python 프로그램 종료
    sys.exit()

# ----------------------- 메인 함수 실행 -----------------------
# 이 파일을 직접 실행했을 때만 main 함수를 실행
if __name__ == "__main__":
    # 메인 함수 호출
    main()



