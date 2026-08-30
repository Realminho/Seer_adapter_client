# ----------------------- 범용 라이브러리 import -----------------------
# pygame UI 구성을 위한 pygame 라이브러리 import
import pygame
# 프로그램 종료 처리를 위한 sys 라이브러리 import
import sys
# 파일 경로 존재 여부 확인을 위한 os 라이브러리 import
import os
# Thread 처리를 위한 threading 라이브러리 import
import threading
# 시간 처리를 위한 time 라이브러리 import
import time
# ----------------------- custom 라이브러리 import -----------------------
# SEER AMR 제어를 위한 SEER_commu 클래스 import
from custom_package.seer_commu import SEER_commu
# DIO 모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.dio_commu import DIO_Commu

# ----------------------- 화면 크기 설정 -----------------------
# pygame 화면 가로 크기 설정
SCREEN_WIDTH = 1600
# pygame 화면 세로 크기 설정
SCREEN_HEIGHT = 900
# ----------------------- UI 색상 설정 -----------------------
# 전체 외곽 배경 파란색 설정
BLUE = (0, 120, 190)
# 내부 메인 패널 흰색 설정
WHITE = (250, 250, 250)
# 완전 흰색 설정
PURE_WHITE = (255, 255, 255)
# 검정색 설정
BLACK = (0, 0, 0)
# 제목용 진한 남색 설정
NAVY = (5, 12, 35)
# 테두리 파란색 설정
BORDER_BLUE = (0, 105, 200)
# 그림자 색상 설정
SHADOW = (205, 215, 230)
# 이미지가 없을 때 표시할 회색 설정
GRAY = (180, 180, 180)
# placeholder 텍스트 색상 설정
DARK_GRAY = (80, 80, 80)
# 배터리 정상 색상 설정
GREEN = (0, 190, 0)
# 배터리 주의 색상 설정
ORANGE = (255, 140, 0)
# 배터리 부족 색상 설정
RED = (220, 0, 0)
# ----------------------- UI 사용 이미지 경로 설정 -----------------------
# Lab2m 로고 이미지 경로 설정
LAB2M_LOGO_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/lab2m_logo.png"
# 게임 컨트롤러 아이콘 이미지 경로 설정
GAME_CONTROLLER_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/game-controller.png"
# 엘리베이터 아이콘 이미지 경로 설정
ELEVATOR_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/elevator.png"
# 직진 버튼 이미지 경로 설정
GO_STRAIGHT_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/go_straight.png"
# 후진 버튼 이미지 경로 설정
GO_BACKWARD_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/go_backward.png"
# 좌회전 버튼 이미지 경로 설정
TURN_LEFT_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/turn_left.png"
# 우회전 버튼 이미지 경로 설정
TURN_RIGHT_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_ui/image/turn_right.png"
# ----------------------- UI 영역 위치 설정 -----------------------
# 전체 흰색 메인 패널 영역 설정
MAIN_PANEL_RECT = pygame.Rect(25, 25, 1550, 850)
# 좌측 AMR Control 영역 설정
LEFT_PANEL_RECT = pygame.Rect(65, 230, 830, 600)
# 우측 Floor Selection 영역 설정
RIGHT_PANEL_RECT = pygame.Rect(930, 230, 605, 600)
# ----------------------- 이미지 위치 및 크기 설정 -----------------------
# 로고 이미지 출력 위치 및 크기 설정
LOGO_POS = (65, 55)
LOGO_SIZE = (190, 150)
# 배터리 위젯 출력 기준 위치 및 크기 설정
# 배터리는 이미지가 아니라 잔량에 따라 색이 채워지는 위젯으로 직접 그림
BATTERY_WIDGET_POS = (1375, 65)
BATTERY_WIDGET_SIZE = (120, 165)
# 게임 컨트롤러 아이콘 출력 위치 및 크기 설정
GAME_CONTROLLER_POS = (120, 250)
GAME_CONTROLLER_SIZE = (190, 125)
# 엘리베이터 아이콘 출력 위치 및 크기 설정
ELEVATOR_POS = (955, 275)
ELEVATOR_SIZE = (105, 105)
# ----------------------- 텍스트 위치 설정 -----------------------
# 메인 제목 중심 좌표 설정
TITLE_CENTER_POS = (800, 125)
# AMR Control 제목 중심 좌표 설정
AMR_CONTROL_TITLE_CENTER_POS = (535, 300)
# Floor Selection 제목 중심 좌표 설정
FLOOR_TITLE_CENTER_POS = (1285, 335)
# ----------------------- 버튼 이미지 위치 및 크기 설정 -----------------------
# 직진 버튼 이미지 위치 및 크기 설정
GO_STRAIGHT_POS = (405, 360)
GO_STRAIGHT_SIZE = (180, 145)
# 좌회전 버튼 이미지 위치 및 크기 설정
TURN_LEFT_POS = (205, 500)
TURN_LEFT_SIZE = (185, 140)
# 우회전 버튼 이미지 위치 및 크기 설정
TURN_RIGHT_POS = (600, 500)
TURN_RIGHT_SIZE = (185, 140)
# 후진 버튼 이미지 위치 및 크기 설정
GO_BACKWARD_POS = (405, 635)
GO_BACKWARD_SIZE = (180, 145)
# Relocation 버튼 위치 및 크기 설정
RELOCATION_RECT = pygame.Rect(95, 715, 265, 95)
# 2층 버튼 위치 및 크기 설정
FLOOR_2_RECT = pygame.Rect(970, 390, 510, 115)
# 3층 버튼 위치 및 크기 설정
FLOOR_3_RECT = pygame.Rect(970, 540, 510, 115)
# 4층 버튼 위치 및 크기 설정
FLOOR_4_RECT = pygame.Rect(970, 690, 510, 115)
# ----------------------- 버튼 클릭 영역 설정 -----------------------
# 직진 버튼 클릭 영역 설정
GO_STRAIGHT_RECT = pygame.Rect(GO_STRAIGHT_POS, GO_STRAIGHT_SIZE)
# 좌회전 버튼 클릭 영역 설정
TURN_LEFT_RECT = pygame.Rect(TURN_LEFT_POS, TURN_LEFT_SIZE)
# 우회전 버튼 클릭 영역 설정
TURN_RIGHT_RECT = pygame.Rect(TURN_RIGHT_POS, TURN_RIGHT_SIZE)
# 후진 버튼 클릭 영역 설정
GO_BACKWARD_RECT = pygame.Rect(GO_BACKWARD_POS, GO_BACKWARD_SIZE)
# 버튼 클릭 영역 딕셔너리 설정
BUTTON_RECTS = {
    "GO_STRAIGHT": GO_STRAIGHT_RECT,
    "GO_BACKWARD": GO_BACKWARD_RECT,
    "TURN_LEFT": TURN_LEFT_RECT,
    "TURN_RIGHT": TURN_RIGHT_RECT,
    "RELOCATION": RELOCATION_RECT,
    "FLOOR_2": FLOOR_2_RECT,
    "FLOOR_3": FLOOR_3_RECT,
    "FLOOR_4": FLOOR_4_RECT,}
# ----------------------- AMR 수동 조작 속도 설정 -----------------------
# 직진 속도 설정
FORWARD_VX = 0.3
# 후진 속도 설정
BACKWARD_VX = -0.3
# 좌측 회전 각속도 설정
TURN_LEFT_W = 0.5
# 우측 회전 각속도 설정
TURN_RIGHT_W = -0.5
# motion_control 명령 반복 송신 주기 설정
MOTION_SEND_INTERVAL_SEC = 0.1
# motion_control duration 설정
MOTION_DURATION = 500
# STOP motion_control duration 설정
STOP_DURATION = 100
# 배터리 정보 갱신 주기 설정
BATTERY_UPDATE_INTERVAL_SEC = 2.0
# ----------------------- DIO 모듈 설정 -----------------------
# DIO 모듈 IP 설정
DIO_IP = "192.168.0.13"
# DIO 모듈 Port 설정
DIO_PORT = 2001
# DIO 통신 timeout 설정
DIO_TIMEOUT = 5.0
# 2F 선택 시 High로 만들 DO 번호 설정
FLOOR_2_DO = 24
# 3F 선택 시 High로 만들 DO 번호 설정
FLOOR_3_DO = 25
# 4F 선택 시 High로 만들 DO 번호 설정
FLOOR_4_DO = 26

# ----------------------- 한글 지원 폰트 반환 함수 선언 -----------------------
def get_korean_font(size, bold=False):
    # 우선 탐색할 한글 지원 폰트 이름 목록 선언
    font_candidates = [
        "malgungothic",
        "맑은고딕",
        "nanumgothic",
        "nanumbarungothic",
        "dejavusans",
        "arial",
        "gulim",
        "dotum",
        "applegothic"]

    # 후보 폰트들을 순서대로 탐색
    for font_name in font_candidates:
        # 시스템 폰트 경로 찾기
        font_path = pygame.font.match_font(font_name)
        # 폰트 경로를 찾았으면
        if font_path:
            # 폰트 객체 생성
            font = pygame.font.Font(font_path, size)
            # bold 설정
            font.set_bold(bold)
            # 폰트 객체 반환
            return font
    # 후보 폰트를 찾지 못하면 기본 Arial 사용
    return pygame.font.SysFont("arial", size, bold=bold)

# ----------------------- 텍스트 출력 함수 선언 -----------------------
def draw_text(screen, text, font, color, center_pos):
    # 텍스트 Surface 생성
    text_surface = font.render(text, True, color)
    # 텍스트 Rect 생성
    text_rect = text_surface.get_rect(center=center_pos)
    # 화면에 텍스트 출력
    screen.blit(text_surface, text_rect)


# ----------------------- 배터리 잔량 숫자 반환 함수 선언 -----------------------
def get_battery_percent_value(battery_text):
    # 배터리 상태 문자열이 에러 계열이면
    if battery_text in ["ERR", "N/A", "BAT"]:
        # 0 반환
        return 0.0
    # 에러가 없으면
    try:
        # % 문자를 제거하고 숫자로 변환
        battery_value = float(str(battery_text).replace("%", ""))
        # 배터리 값이 0보다 작으면
        if battery_value < 0:
            # 0 반환
            return 0.0
        # 배터리 값이 100보다 크면
        if battery_value > 100:
            # 100 반환
            return 100.0
        # 정상 값 반환
        return battery_value
    # 숫자 변환 실패 시
    except Exception:
        # 0 반환
        return 0.0

# ----------------------- 배터리 잔량 색상 반환 함수 선언 -----------------------
def get_battery_color(battery_percent):
    # 배터리 잔량이 70 이상이면 초록색 반환
    if 70 <= battery_percent <= 100:
        return GREEN
    # 배터리 잔량이 50 이상 70 미만이면 주황색 반환
    elif 50 <= battery_percent < 70:
        return ORANGE
    # 배터리 잔량이 50 미만이면 빨간색 반환
    else:
        return RED

# ----------------------- 이미지 비율 유지 로드 함수 선언 -----------------------
def load_ui_image_keep_ratio(image_path, max_size, allow_upscale=True):
    # 이미지 경로가 비어있으면
    if image_path is None or image_path == "":
        # None 반환
        return None
    # 이미지 파일이 존재하지 않으면
    if not os.path.exists(image_path):
        # 경고 출력
        print(f"[UI] 이미지 파일을 찾을 수 없습니다 : {image_path}")
        # None 반환
        return None
    # 이미지 로드
    image = pygame.image.load(image_path).convert_alpha()
    # 원본 크기 가져오기
    original_w, original_h = image.get_size()
    # 최대 크기 가져오기
    max_w, max_h = max_size
    # 비율 유지 스케일 계산
    scale_ratio = min(max_w / original_w, max_h / original_h)
    # 확대를 허용하지 않는 경우
    if not allow_upscale:
        # 1.0 이상 확대 방지
        scale_ratio = min(scale_ratio, 1.0)
    # 새 크기 계산
    new_w = max(1, int(original_w * scale_ratio))
    new_h = max(1, int(original_h * scale_ratio))
    # 이미지 크기 변경
    image = pygame.transform.smoothscale(image, (new_w, new_h))
    # 이미지 반환
    return image

# ----------------------- 이미지 강제 크기 로드 함수 선언 -----------------------
def load_ui_image_exact(image_path, image_size):
    # 이미지 경로가 비어있으면
    if image_path is None or image_path == "":
        # None 반환
        return None
    # 이미지 파일이 존재하지 않으면
    if not os.path.exists(image_path):
        # 경고 출력
        print(f"[UI] 이미지 파일을 찾을 수 없습니다 : {image_path}")
        # None 반환
        return None
    # 이미지 로드
    image = pygame.image.load(image_path).convert_alpha()
    # 지정한 크기로 변경
    image = pygame.transform.smoothscale(image, image_size)
    # 이미지 반환
    return image

# ----------------------- 전체 이미지 로드 함수 선언 -----------------------
def load_all_ui_images():
    # UI 이미지 딕셔너리 생성
    ui_images = {
        # 로고 이미지 로드
        "lab2m_logo": load_ui_image_keep_ratio(
            LAB2M_LOGO_IMAGE_PATH,
            LOGO_SIZE,
            allow_upscale=True),
        # 컨트롤러 이미지 로드
        "game_controller": load_ui_image_keep_ratio(
            GAME_CONTROLLER_IMAGE_PATH,
            GAME_CONTROLLER_SIZE,
            allow_upscale=True),
        # 엘리베이터 이미지 로드
        "elevator": load_ui_image_keep_ratio(
            ELEVATOR_IMAGE_PATH,
            ELEVATOR_SIZE,
            allow_upscale=True),
        # 직진 버튼 이미지 로드
        "go_straight": load_ui_image_exact(
            GO_STRAIGHT_IMAGE_PATH,
            GO_STRAIGHT_SIZE),
        # 후진 버튼 이미지 로드
        "go_backward": load_ui_image_exact(
            GO_BACKWARD_IMAGE_PATH,
            GO_BACKWARD_SIZE),
        # 좌회전 버튼 이미지 로드
        "turn_left": load_ui_image_exact(
            TURN_LEFT_IMAGE_PATH,
            TURN_LEFT_SIZE),
        # 우회전 버튼 이미지 로드
        "turn_right": load_ui_image_exact(
            TURN_RIGHT_IMAGE_PATH,
            TURN_RIGHT_SIZE)
    }
    # UI 이미지 딕셔너리 반환
    return ui_images

# ----------------------- 이미지 중앙 출력 함수 선언 -----------------------
def draw_image_centered(screen, image, center_pos):
    # 이미지가 정상적으로 로드되었으면
    if image is not None:
        # 이미지 Rect 생성
        image_rect = image.get_rect(center=center_pos)
        # 화면에 이미지 출력
        screen.blit(image, image_rect)

# ----------------------- 이미지 위치 출력 함수 선언 -----------------------
def draw_image(screen, image, image_pos):
    # 이미지가 정상적으로 로드되었으면
    if image is not None:
        # 화면에 이미지 출력
        screen.blit(image, image_pos)

# ----------------------- 이미지 없을 때 placeholder 출력 함수 선언 -----------------------
def draw_image_placeholder(screen, rect, label, font):
    # placeholder 배경 출력
    pygame.draw.rect(screen, (235, 240, 245), rect, border_radius=18)
    # placeholder 테두리 출력
    pygame.draw.rect(screen, GRAY, rect, width=2, border_radius=18)
    # placeholder 텍스트 출력
    draw_text(
        screen=screen,
        text=label,
        font=font,
        color=DARK_GRAY,
        center_pos=rect.center)

# ----------------------- 그림자 있는 둥근 사각형 출력 함수 선언 -----------------------
def draw_rounded_rect_with_shadow(screen, rect, fill_color, border_color=None, border_width=0, radius=25):
    # 그림자 영역 생성
    shadow_rect = pygame.Rect(rect.x + 4, rect.y + 5, rect.w, rect.h)
    # 그림자 출력
    pygame.draw.rect(screen, SHADOW, shadow_rect, border_radius=radius)
    # 실제 사각형 출력
    pygame.draw.rect(screen, fill_color, rect, border_radius=radius)
    # 테두리가 필요한 경우
    if border_color is not None and border_width > 0:
        # 테두리 출력
        pygame.draw.rect(screen, border_color, rect, width=border_width, border_radius=radius)

# ----------------------- 세로 그라데이션 버튼 출력 함수 선언 -----------------------
def draw_gradient_button(screen, rect, text, font):
    # 버튼 상단 색상 설정
    top_color = (0, 135, 225)
    # 버튼 하단 색상 설정
    bottom_color = (0, 90, 180)
    # 그림자 영역 생성
    shadow_rect = pygame.Rect(rect.x + 5, rect.y + 6, rect.w, rect.h)
    # 그림자 출력
    pygame.draw.rect(screen, SHADOW, shadow_rect, border_radius=22)
    # 그라데이션 Surface 생성
    gradient_surface = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    # 세로 방향으로 색상 보간
    for y in range(rect.h):
        # 현재 위치 비율 계산
        ratio = y / max(rect.h - 1, 1)
        # R 값 계산
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        # G 값 계산
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        # B 값 계산
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        # 현재 줄 출력
        pygame.draw.line(gradient_surface, (r, g, b), (0, y), (rect.w, y))
    # 둥근 모서리 마스크 생성
    mask_surface = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    # 둥근 사각형 마스크 출력
    pygame.draw.rect(
        mask_surface,
        (255, 255, 255, 255),
        mask_surface.get_rect(),
        border_radius=22)
    # 마스크 적용
    gradient_surface.blit(mask_surface, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    # 그라데이션 버튼 출력
    screen.blit(gradient_surface, rect.topleft)
    # 버튼 테두리 출력
    pygame.draw.rect(screen, (70, 170, 245), rect, width=3, border_radius=22)
    # 텍스트 출력
    draw_text(
        screen=screen,
        text=text,
        font=font,
        color=PURE_WHITE,
        center_pos=rect.center)

# ----------------------- 배터리 위젯 출력 함수 선언 -----------------------
def draw_battery_widget(screen, battery_text, percent_font):
    # 배터리 잔량 숫자 계산
    battery_percent = get_battery_percent_value(battery_text)
    # 배터리 채움 색상 계산
    fill_color = get_battery_color(battery_percent)
    # 배터리 위젯 기준 좌표 가져오기
    widget_x, widget_y = BATTERY_WIDGET_POS
    # 배터리 위젯 기준 크기 가져오기
    widget_w, widget_h = BATTERY_WIDGET_SIZE
    # 배터리 외곽 바디 크기 계산
    body_w = int(widget_w * 0.62)
    body_h = int(widget_h * 0.78)
    # 배터리 외곽 바디 위치 계산
    body_x = widget_x + (widget_w - body_w) // 2
    body_y = widget_y + int(widget_h * 0.15)
    # 배터리 머리 부분 크기 계산
    cap_w = int(body_w * 0.38)
    cap_h = int(widget_h * 0.08)
    # 배터리 머리 부분 위치 계산
    cap_x = body_x + (body_w - cap_w) // 2
    cap_y = body_y - cap_h + 2
    # 배터리 외곽 Rect 생성
    body_rect = pygame.Rect(body_x, body_y, body_w, body_h)
    # 배터리 머리 Rect 생성
    cap_rect = pygame.Rect(cap_x, cap_y, cap_w, cap_h)
    # 배터리 내부 여백 설정
    inner_padding = 8
    # 배터리 내부 영역 계산
    inner_x = body_x + inner_padding
    inner_y = body_y + inner_padding
    inner_w = body_w - (inner_padding * 2)
    inner_h = body_h - (inner_padding * 2)
    # 배터리 잔량에 따른 실제 채움 높이 계산
    fill_h = int(inner_h * (battery_percent / 100.0))
    # 배터리 채움 영역 Rect 생성
    fill_rect = pygame.Rect(
        inner_x,
        inner_y + (inner_h - fill_h),
        inner_w,
        fill_h)
    # 배터리 내부 바탕 출력
    pygame.draw.rect(
        screen,
        PURE_WHITE,
        pygame.Rect(inner_x, inner_y, inner_w, inner_h),
        border_radius=8)
    # 잔량이 0보다 크면 채움 출력
    if fill_h > 0:
        pygame.draw.rect(
            screen,
            fill_color,
            fill_rect,
            border_radius=8)
    # 배터리 외곽선 출력
    pygame.draw.rect(screen, BLACK, body_rect, width=6, border_radius=14)
    # 배터리 머리 부분 출력
    pygame.draw.rect(screen, BLACK, cap_rect, border_radius=4)
    # 배터리 퍼센트 표시 문자열 결정
    if battery_text in ["ERR", "N/A", "BAT"]:
        display_text = battery_text
    else:
        display_text = f"{battery_percent:.0f}%"
    # 배터리 중앙 텍스트 출력
    draw_text(
        screen=screen,
        text=display_text,
        font=percent_font,
        color=BLACK,
        center_pos=(body_x + body_w // 2, body_y + body_h // 2))

# ----------------------- AMR UI 제어 클래스 선언 -----------------------
class AMR_UI_Controller:
    # 클래스 초기화 함수 선언
    def __init__(self):
        # SEER_commu 객체 저장 변수 선언
        self.seer = None
        # DIO_Commu 객체 저장 변수 선언
        self.dio = None
        # AMR 제어 명령 충돌 방지를 위한 Lock 선언
        self.ctrl_lock = threading.Lock()
        # DIO 제어 명령 충돌 방지를 위한 Lock 선언
        self.dio_lock = threading.Lock()
        # 배터리 값 보호를 위한 Lock 선언
        self.battery_lock = threading.Lock()
        # 현재 누르고 있는 조작 명령 저장 변수 선언
        self.active_motion = None
        # 프로그램 실행 상태 변수 선언
        self.running = True
        # 마지막 motion 명령 송신 시간 저장 변수 선언
        self.last_motion_send_time = 0.0
        # 현재 배터리 잔량 저장 변수 선언
        self.battery_level = None
        # 배터리 통신 상태 메시지 저장 변수 선언
        self.battery_status_text = "BAT"
        # AMR 연결 시도
        self.connect_amr()
        # DIO 모듈 연결 시도
        self.connect_dio()
        # 배터리 갱신 Thread 시작
        self.start_battery_thread()

    # AMR 연결 함수 선언
    def connect_amr(self):
        # 에러가 없으면
        try:
            # SEER_commu 객체 생성
            self.seer = SEER_commu(ip="192.168.0.100")
            # 연결 완료 출력
            print("[UI] SEER AMR 연결 완료")
        # 예외가 발생하면
        except Exception as error:
            # SEER_commu 객체 None 처리
            self.seer = None
            # 에러 출력
            print(f"[UI] SEER AMR 연결 실패 : {error}")

    # DIO 모듈 연결 함수 선언
    def connect_dio(self):
        # 에러가 없으면
        try:
            # DIO_Commu 객체 생성
            self.dio = DIO_Commu(
                ip=DIO_IP,
                port=DIO_PORT,
                timeout=DIO_TIMEOUT)
            # DIO 모듈 연결
            self.dio.dio_connect()
            # 연결 완료 출력
            print("[UI] DIO 모듈 연결 완료")
        # 예외가 발생하면
        except Exception as error:
            # DIO 객체 None 처리
            self.dio = None
            # 에러 출력
            print(f"[UI] DIO 모듈 연결 실패 : {error}")

    # 배터리 Thread 시작 함수 선언
    def start_battery_thread(self):
        # 배터리 갱신 Thread 생성
        battery_thread = threading.Thread(
            target=self.battery_update_loop,
            daemon=True)
        # 배터리 갱신 Thread 시작
        battery_thread.start()

    # 배터리 갱신 반복 함수 선언
    def battery_update_loop(self):
        # 프로그램이 실행 중이면 반복
        while self.running:
            # SEER_commu 객체가 있으면
            if self.seer is not None:
                # 에러가 없으면
                try:
                    # 배터리 정보 수신
                    battery_info = self.seer.get_battery_info()
                    # 배터리 정보가 정상 tuple이면
                    if isinstance(battery_info, tuple) and len(battery_info) >= 1:
                        # 배터리 잔량 원본값 추출
                        current_battery_level = battery_info[0]
                        # 배터리 원본값 출력
                        print(f"[UI] 현재 배터리 원본 값 : {current_battery_level}")
                        # 배터리 값이 None이 아니면
                        if current_battery_level is not None:
                            # 배터리 값을 float으로 변환
                            current_battery_level = float(current_battery_level)
                            # 배터리 값이 0~1 사이 비율값이면
                            if 0 <= current_battery_level <= 1:
                                # 0~1 값을 0~100 퍼센트로 변환
                                display_battery_level = current_battery_level * 100
                            # 배터리 값이 이미 0~100 퍼센트 값이면
                            else:
                                # 그대로 사용
                                display_battery_level = current_battery_level
                            # battery lock 사용
                            with self.battery_lock:
                                # 현재 배터리 잔량 저장
                                self.battery_level = display_battery_level
                                # 현재 배터리 상태 문자열 저장
                                self.battery_status_text = f"{display_battery_level:.0f}%"
                        # 배터리 값이 None이면
                        else:
                            # battery lock 사용
                            with self.battery_lock:
                                # 상태 문자열 변경
                                self.battery_status_text = "N/A"
                    # 배터리 정보가 비정상이면
                    else:
                        # battery lock 사용
                        with self.battery_lock:
                            # 상태 문자열 변경
                            self.battery_status_text = "ERR"
                        # 디버그 출력
                        print(f"[UI] 배터리 정보 형식 이상 : {battery_info}")
                # 예외가 발생하면
                except Exception as error:
                    # 에러 출력
                    print(f"[UI] 배터리 정보 수신 실패 : {error}")
                    # battery lock 사용
                    with self.battery_lock:
                        # 상태 문자열 변경
                        self.battery_status_text = "ERR"
            # 배터리 갱신 주기 대기
            time.sleep(BATTERY_UPDATE_INTERVAL_SEC)

    # 현재 배터리 표시 문자열 반환 함수 선언
    def get_battery_text(self):
        # battery lock 사용
        with self.battery_lock:
            # 배터리 표시 문자열 반환
            return self.battery_status_text

    # motion 명령 설정 함수 선언
    def set_active_motion(self, motion_name):
        # 현재 조작 명령 저장
        self.active_motion = motion_name
        # 마지막 송신 시간 초기화
        self.last_motion_send_time = 0.0
        # 디버그 출력
        print(f"[UI] active_motion : {motion_name}")

    # motion 명령 해제 함수 선언
    def clear_active_motion(self):
        # 현재 조작 명령이 있으면
        if self.active_motion is not None:
            # 디버그 출력
            print("[UI] 버튼 해제 -> 정지 명령 송신")
            # 현재 조작 명령 제거
            self.active_motion = None
            # 즉시 정지 명령 송신
            self.send_stop_async()

    # active motion 처리 함수 선언
    def process_active_motion(self):
        # 현재 조작 명령이 없으면
        if self.active_motion is None:
            # 함수 종료
            return
        # 현재 시간 저장
        now_time = time.time()
        # 마지막 송신 후 주기가 지나지 않았으면
        if now_time - self.last_motion_send_time < MOTION_SEND_INTERVAL_SEC:
            # 함수 종료
            return
        # 마지막 송신 시간 갱신
        self.last_motion_send_time = now_time
        # 직진 명령이면
        if self.active_motion == "GO_STRAIGHT":
            # motion 명령 비동기 송신
            self.send_motion_async(vx=FORWARD_VX, w=0.0)
        # 후진 명령이면
        elif self.active_motion == "GO_BACKWARD":
            # motion 명령 비동기 송신
            self.send_motion_async(vx=BACKWARD_VX, w=0.0)
        # 좌측 회전 명령이면
        elif self.active_motion == "TURN_LEFT":
            # motion 명령 비동기 송신
            self.send_motion_async(vx=0.0, w=TURN_LEFT_W)
        # 우측 회전 명령이면
        elif self.active_motion == "TURN_RIGHT":
            # motion 명령 비동기 송신
            self.send_motion_async(vx=0.0, w=TURN_RIGHT_W)

    # motion 명령 비동기 송신 함수 선언
    def send_motion_async(self, vx=0.0, w=0.0):
        # motion Thread 생성
        motion_thread = threading.Thread(
            target=self.send_motion,
            args=(vx, w),
            daemon=True)
        # motion Thread 시작
        motion_thread.start()

    # motion 명령 송신 함수 선언
    def send_motion(self, vx=0.0, w=0.0):
        # SEER_commu 객체가 없으면
        if self.seer is None:
            # 디버그 출력
            print("[UI] AMR이 연결되지 않아 motion 명령을 보낼 수 없습니다.")
            # 함수 종료
            return
        # 에러가 없으면
        try:
            # 제어 Lock 사용
            with self.ctrl_lock:
                # motion_control 명령 송신
                success = self.seer.motion_control(
                    vx=vx,
                    vy=0.0,
                    w=w,
                    duration=MOTION_DURATION)
            # 디버그 출력
            print(f"[UI] motion_control vx={vx}, w={w}, duration={MOTION_DURATION}, success={success}")
        # 예외가 발생하면
        except Exception as error:
            # 에러 출력
            print(f"[UI] motion_control 실패 : {error}")

    # 정지 명령 비동기 송신 함수 선언
    def send_stop_async(self):
        # stop Thread 생성
        stop_thread = threading.Thread(
            target=self.send_stop,
            daemon=True)
        # stop Thread 시작
        stop_thread.start()

    # 정지 명령 송신 함수 선언
    def send_stop(self):
        # SEER_commu 객체가 없으면
        if self.seer is None:
            # 디버그 출력
            print("[UI] AMR이 연결되지 않아 정지 명령을 보낼 수 없습니다.")
            # 함수 종료
            return
        # 에러가 없으면
        try:
            # 제어 Lock 사용
            with self.ctrl_lock:
                # 정지 motion_control 명령 송신
                success = self.seer.motion_control(
                    vx=0.0,
                    vy=0.0,
                    w=0.0,
                    duration=STOP_DURATION)
            # 디버그 출력
            print(f"[UI] STOP 명령 송신 완료 duration={STOP_DURATION}, success={success}")
        # 예외가 발생하면
        except Exception as error:
            # 에러 출력
            print(f"[UI] STOP 명령 실패 : {error}")

    # relocation 명령 비동기 송신 함수 선언
    def send_relocation_async(self):
        # relocation Thread 생성
        relocation_thread = threading.Thread(
            target=self.send_stop_then_relocation,
            daemon=True)
        # relocation Thread 시작
        relocation_thread.start()

    # 정지 후 relocation 명령 송신 함수 선언
    def send_stop_then_relocation(self):
        # relocation 전 현재 active motion 제거
        self.active_motion = None
        # 먼저 정지 명령 송신
        self.send_stop()
        # 짧게 대기
        time.sleep(0.2)
        # relocation 명령 송신
        self.send_relocation()

    # relocation 명령 송신 함수 선언
    def send_relocation(self):
        # SEER_commu 객체가 없으면
        if self.seer is None:
            # 디버그 출력
            print("[UI] AMR이 연결되지 않아 Relocation 명령을 보낼 수 없습니다.")
            # 실패 반환
            return False
        # 에러가 없으면
        try:
            # 제어 Lock 사용
            with self.ctrl_lock:
                # SEER_commu의 relocation 함수 호출
                success = self.seer.relocation()
            # 디버그 출력
            print(f"[UI] Relocation 명령 송신 완료 success={success}")
            # 성공 여부 반환
            return success
        # 예외가 발생하면
        except Exception as error:
            # 에러 출력
            print(f"[UI] Relocation 명령 실패 : {error}")
            # 실패 반환
            return False

    # 층 선택 DO 제어 비동기 실행 함수 선언
    def set_floor_do_async(self, floor_name):
        # 층 선택 DO 제어 Thread 생성
        floor_thread = threading.Thread(
            target=self.set_floor_do,
            args=(floor_name,),
            daemon=True)
        # 층 선택 DO 제어 Thread 시작
        floor_thread.start()

    # 층 선택 DO 제어 함수 선언
    def set_floor_do(self, floor_name):
        # DIO 객체가 없으면
        if self.dio is None:
            # 디버그 출력
            print("[UI] DIO 모듈이 연결되지 않아 층 선택 DO를 제어할 수 없습니다.")
            # 실패 반환
            return False
        # 에러가 없으면
        try:
            # DIO Lock 사용
            with self.dio_lock:
                # 2F 버튼이 눌렸으면
                if floor_name == "FLOOR_2":
                    # 24번 DO High
                    self.dio.set_do_on(FLOOR_2_DO)
                    # 25번 DO Low
                    self.dio.set_do_off(FLOOR_3_DO)
                    # 26번 DO Low
                    self.dio.set_do_off(FLOOR_4_DO)
                    # 디버그 출력
                    print("[UI] 2F 선택 완료 : DO24=High, DO25=Low, DO26=Low")
                # 3F 버튼이 눌렸으면
                elif floor_name == "FLOOR_3":
                    # 24번 DO Low
                    self.dio.set_do_off(FLOOR_2_DO)
                    # 25번 DO High
                    self.dio.set_do_on(FLOOR_3_DO)
                    # 26번 DO Low
                    self.dio.set_do_off(FLOOR_4_DO)
                    # 디버그 출력
                    print("[UI] 3F 선택 완료 : DO24=Low, DO25=High, DO26=Low")
                # 4F 버튼이 눌렸으면
                elif floor_name == "FLOOR_4":
                    # 24번 DO Low
                    self.dio.set_do_off(FLOOR_2_DO)
                    # 25번 DO Low
                    self.dio.set_do_off(FLOOR_3_DO)
                    # 26번 DO High
                    self.dio.set_do_on(FLOOR_4_DO)
                    # 디버그 출력
                    print("[UI] 4F 선택 완료 : DO24=Low, DO25=Low, DO26=High")
                # 알 수 없는 층 이름이면
                else:
                    # 디버그 출력
                    print(f"[UI] 알 수 없는 층 선택 : {floor_name}")
                    # 실패 반환
                    return False
            # 성공 반환
            return True
        # 예외가 발생하면
        except Exception as error:
            # 에러 출력
            print(f"[UI] 층 선택 DO 제어 실패 : {error}")
            # 실패 반환
            return False

    # 종료 처리 함수 선언
    def close(self):
        # 프로그램 실행 상태 False로 변경
        self.running = False
        # 정지 명령 송신
        self.send_stop()
        # SEER_commu 객체가 있으면
        if self.seer is not None:
            # 에러가 없으면
            try:
                # 소켓 종료
                self.seer.socket_close()
                # 종료 출력
                print("[UI] SEER AMR 소켓 종료 완료")
            # 예외가 발생하면
            except Exception as error:
                # 에러 출력
                print(f"[UI] SEER AMR 소켓 종료 실패 : {error}")
        # DIO 객체가 있으면
        if self.dio is not None:
            # 에러가 없으면
            try:
                # DIO 소켓 종료
                self.dio.dio_close()
                # 종료 출력
                print("[UI] DIO 모듈 소켓 종료 완료")
            # 예외가 발생하면
            except Exception as error:
                # 에러 출력
                print(f"[UI] DIO 모듈 소켓 종료 실패 : {error}")

# ----------------------- UI 전체 출력 함수 선언 -----------------------
def draw_ui(screen, fonts, ui_images, amr_controller):
    # 전체 배경 파란색 출력
    screen.fill(BLUE)
    # 메인 흰색 패널 출력
    draw_rounded_rect_with_shadow(
        screen=screen,
        rect=MAIN_PANEL_RECT,
        fill_color=WHITE,
        border_color=None,
        border_width=0,
        radius=28)
    # 로고 이미지 출력
    if ui_images["lab2m_logo"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["lab2m_logo"],
            center_pos=(
                LOGO_POS[0] + LOGO_SIZE[0] // 2,
                LOGO_POS[1] + LOGO_SIZE[1] // 2))
    # 로고 이미지가 없으면 placeholder 출력
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(LOGO_POS, LOGO_SIZE),
            label="lab2m_logo",
            font=fonts["placeholder"])
    # 메인 제목 출력
    draw_text(
        screen=screen,
        text="AMR Control PAD",
        font=fonts["title"],
        color=NAVY,
        center_pos=TITLE_CENTER_POS)
    # 배터리 잔량 문자열 가져오기
    battery_text = amr_controller.get_battery_text()
    # 배터리 위젯 출력
    draw_battery_widget(
        screen=screen,
        battery_text=battery_text,
        percent_font=fonts["battery"])
    # 좌측 패널 출력
    pygame.draw.rect(screen, PURE_WHITE, LEFT_PANEL_RECT, border_radius=20)
    # 좌측 패널 테두리 출력
    pygame.draw.rect(screen, BORDER_BLUE, LEFT_PANEL_RECT, width=2, border_radius=20)
    # 우측 패널 출력
    pygame.draw.rect(screen, PURE_WHITE, RIGHT_PANEL_RECT, border_radius=20)
    # 우측 패널 테두리 출력
    pygame.draw.rect(screen, BORDER_BLUE, RIGHT_PANEL_RECT, width=2, border_radius=20)
    # 컨트롤러 아이콘 출력
    if ui_images["game_controller"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["game_controller"],
            center_pos=(
                GAME_CONTROLLER_POS[0] + GAME_CONTROLLER_SIZE[0] // 2,
                GAME_CONTROLLER_POS[1] + GAME_CONTROLLER_SIZE[1] // 2))
    # 컨트롤러 아이콘이 없으면 placeholder 출력
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(GAME_CONTROLLER_POS, GAME_CONTROLLER_SIZE),
            label="game-controller",
            font=fonts["placeholder"])
    # AMR Control 제목 출력
    draw_text(
        screen=screen,
        text="AMR Control",
        font=fonts["section_title"],
        color=NAVY,
        center_pos=AMR_CONTROL_TITLE_CENTER_POS)
    # 엘리베이터 아이콘 출력
    if ui_images["elevator"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["elevator"],
            center_pos=(
                ELEVATOR_POS[0] + ELEVATOR_SIZE[0] // 2,
                ELEVATOR_POS[1] + ELEVATOR_SIZE[1] // 2,
            ))
    # 엘리베이터 아이콘이 없으면 placeholder 출력
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(ELEVATOR_POS, ELEVATOR_SIZE),
            label="elevator",
            font=fonts["placeholder"])
    # Floor Selection 제목 출력
    draw_text(
        screen=screen,
        text="Floor Selection",
        font=fonts["section_title"],
        color=NAVY,
        center_pos=FLOOR_TITLE_CENTER_POS)
    # 직진 방향키 이미지 출력
    draw_image(screen, ui_images["go_straight"], GO_STRAIGHT_POS)
    # 좌회전 방향키 이미지 출력
    draw_image(screen, ui_images["turn_left"], TURN_LEFT_POS)
    # 우회전 방향키 이미지 출력
    draw_image(screen, ui_images["turn_right"], TURN_RIGHT_POS)
    # 후진 방향키 이미지 출력
    draw_image(screen, ui_images["go_backward"], GO_BACKWARD_POS)
    # Relocation 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=RELOCATION_RECT,
        text="Relocation",
        font=fonts["relocation"])
    # 2층 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=FLOOR_2_RECT,
        text="2F",
        font=fonts["floor"])
    # 3층 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=FLOOR_3_RECT,
        text="3F",
        font=fonts["floor"])
    # 4층 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=FLOOR_4_RECT,
        text="4F",
        font=fonts["floor"])

# ----------------------- 마우스 클릭 처리 함수 선언 -----------------------
def handle_mouse_down(mouse_pos, amr_controller):
    # 직진 버튼 클릭 시
    if BUTTON_RECTS["GO_STRAIGHT"].collidepoint(mouse_pos):
        # 직진 active motion 설정
        amr_controller.set_active_motion("GO_STRAIGHT")
    # 후진 버튼 클릭 시
    elif BUTTON_RECTS["GO_BACKWARD"].collidepoint(mouse_pos):
        # 후진 active motion 설정
        amr_controller.set_active_motion("GO_BACKWARD")
    # 좌측 회전 버튼 클릭 시
    elif BUTTON_RECTS["TURN_LEFT"].collidepoint(mouse_pos):
        # 좌측 회전 active motion 설정
        amr_controller.set_active_motion("TURN_LEFT")
    # 우측 회전 버튼 클릭 시
    elif BUTTON_RECTS["TURN_RIGHT"].collidepoint(mouse_pos):
        # 우측 회전 active motion 설정
        amr_controller.set_active_motion("TURN_RIGHT")
    # Relocation 버튼 클릭 시
    elif BUTTON_RECTS["RELOCATION"].collidepoint(mouse_pos):
        # Relocation 버튼 클릭 로그 출력
        print("[UI] RELOCATION 버튼 클릭 -> Relocation 실행")
        # 현재 active motion 제거
        amr_controller.active_motion = None
        # 정지 후 relocation 명령 비동기 송신
        amr_controller.send_relocation_async()
    # 2층 버튼 클릭 시
    elif BUTTON_RECTS["FLOOR_2"].collidepoint(mouse_pos):
        # 2F 버튼 클릭 로그 출력
        print("[UI] FLOOR_2 버튼 클릭 -> DO24 High, DO25/DO26 Low")
        # 2F DO 제어 비동기 실행
        amr_controller.set_floor_do_async("FLOOR_2")
    # 3층 버튼 클릭 시
    elif BUTTON_RECTS["FLOOR_3"].collidepoint(mouse_pos):
        # 3F 버튼 클릭 로그 출력
        print("[UI] FLOOR_3 버튼 클릭 -> DO25 High, DO24/DO26 Low")
        # 3F DO 제어 비동기 실행
        amr_controller.set_floor_do_async("FLOOR_3")
    # 4층 버튼 클릭 시
    elif BUTTON_RECTS["FLOOR_4"].collidepoint(mouse_pos):
        # 4F 버튼 클릭 로그 출력
        print("[UI] FLOOR_4 버튼 클릭 -> DO26 High, DO24/DO25 Low")
        # 4F DO 제어 비동기 실행
        amr_controller.set_floor_do_async("FLOOR_4")

# ----------------------- 메인 함수 선언 -----------------------
def main():
    # pygame 초기화
    pygame.init()
    # pygame font 초기화
    pygame.font.init()
    # 화면 객체 생성
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    # pygame 창 제목 설정
    pygame.display.set_caption("AMR Control PAD")
    # FPS 제어 객체 생성
    clock = pygame.time.Clock()
    # 폰트 딕셔너리 생성
    fonts = {
        "title": get_korean_font(64, bold=True),
        "section_title": get_korean_font(54, bold=True),
        "floor": get_korean_font(56, bold=True),
        "relocation": get_korean_font(38, bold=True),
        "battery": get_korean_font(26, bold=True),
        "placeholder": get_korean_font(24, bold=False)}
    # 전체 UI 이미지 로드
    ui_images = load_all_ui_images()
    # AMR UI 제어 객체 생성
    amr_controller = AMR_UI_Controller()
    # 프로그램 실행 상태 변수 선언
    running = True
    # 메인 반복문 시작
    while running:
        # FPS 60으로 제한
        clock.tick(60)
        # active motion 처리
        amr_controller.process_active_motion()
        # pygame 이벤트 순회
        for event in pygame.event.get():
            # 창 닫기 버튼을 눌렀을 경우
            if event.type == pygame.QUIT:
                # 프로그램 실행 상태 False로 변경
                running = False
            # 마우스 버튼을 눌렀을 경우
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # 마우스 클릭 좌표 저장
                mouse_pos = pygame.mouse.get_pos()
                # 마우스 클릭 처리
                handle_mouse_down(
                    mouse_pos=mouse_pos,
                    amr_controller=amr_controller)
            # 마우스 버튼을 뗐을 경우
            elif event.type == pygame.MOUSEBUTTONUP:
                # 누르고 있던 motion 명령 해제
                amr_controller.clear_active_motion()
        # UI 전체 출력
        draw_ui(
            screen=screen,
            fonts=fonts,
            ui_images=ui_images,
            amr_controller=amr_controller)
        # 화면 업데이트
        pygame.display.flip()
    # AMR 제어 객체 종료 처리
    amr_controller.close()
    # pygame 종료
    pygame.quit()
    # 프로그램 종료
    sys.exit()

# ----------------------- 메인 함수 실행 -----------------------
if __name__ == "__main__":
    main()