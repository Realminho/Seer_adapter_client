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
from custom_package.sam_dio_commu import DIO_Commu

# ----------------------- 화면 크기 설정 -----------------------
# 기존 UI를 설계했던 기준 가로 크기 설정
BASE_WIDTH = 1600
# 기존 UI를 설계했던 기준 세로 크기 설정
BASE_HEIGHT = 900
# Jetson Nano 현재 디스플레이 가로 크기 설정
SCREEN_WIDTH = 1024
# Jetson Nano 현재 디스플레이 세로 크기 설정
SCREEN_HEIGHT = 600
# 가로 방향 스케일 비율 계산
SCALE_X = SCREEN_WIDTH / BASE_WIDTH
# 세로 방향 스케일 비율 계산
SCALE_Y = SCREEN_HEIGHT / BASE_HEIGHT
# 폰트, 테두리, radius 등에 사용할 공통 스케일 비율 설정
SCALE = min(SCALE_X, SCALE_Y)

# ----------------------- 스케일 변환 함수 선언 -----------------------
# x 좌표 또는 가로 크기를 현재 화면 비율에 맞게 변환하는 함수 선언
def sx(value):
    return int(value * SCALE_X)

# y 좌표 또는 세로 크기를 현재 화면 비율에 맞게 변환하는 함수 선언
def sy(value):
    return int(value * SCALE_Y)

# 폰트 크기를 현재 화면 비율에 맞게 변환하는 함수 선언
def sf(value):
    return max(10, int(value * SCALE))

# 선 굵기, 테두리 두께를 현재 화면 비율에 맞게 변환하는 함수 선언
def sw(value):
    return max(1, int(value * SCALE))

# radius 값을 현재 화면 비율에 맞게 변환하는 함수 선언
def sr(value):
    return max(1, int(value * SCALE))

# pygame.Rect를 현재 화면 비율에 맞게 변환하는 함수 선언
def scaled_rect(x, y, w, h):
    return pygame.Rect(sx(x), sy(y), sx(w), sy(h))

# 좌표 tuple을 현재 화면 비율에 맞게 변환하는 함수 선언
def scaled_pos(x, y):
    return (sx(x), sy(y))

# 크기 tuple을 현재 화면 비율에 맞게 변환하는 함수 선언
def scaled_size(w, h):
    return (sx(w), sy(h))

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
# 연한 파란색 구분선 설정
LIGHT_BLUE = (135, 190, 240)
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
LAB2M_LOGO_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_hmi/image/lab2m_logo.png"
# MoMa State 영역 AMR 아이콘 이미지 경로 설정
AMR_ICON_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_hmi/image/amr_icon.png"
# Release Brake 영역 push 아이콘 이미지 경로 설정
PUSH_ICON_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_hmi/image/push_icon.png"
# Battery Info 영역 배터리 아이콘 이미지 경로 설정
BATTERY_2_IMAGE_PATH = "/home/mic-711/Desktop/SamDisplay/sam_hmi/image/battery_2.png"
# ----------------------- UI 영역 위치 설정 -----------------------
# 전체 흰색 메인 패널 영역 설정
MAIN_PANEL_RECT = scaled_rect(25, 25, 1550, 850)
# 좌측 MoMa State / Release Brake 영역 설정
LEFT_PANEL_RECT = scaled_rect(65, 230, 830, 600)
# 우측 Battery Info 영역 설정
RIGHT_PANEL_RECT = scaled_rect(930, 230, 605, 600)
# ----------------------- 이미지 위치 및 크기 설정 -----------------------
# 로고 이미지 출력 위치 및 크기 설정
LOGO_POS = scaled_pos(65, 55)
LOGO_SIZE = scaled_size(190, 150)
# 우측 상단 배터리 위젯 출력 위치 및 크기 설정
BATTERY_POS = scaled_pos(1350, 35)
BATTERY_SIZE = scaled_size(185, 165)
# MoMa State AMR 아이콘 출력 위치 및 크기 설정
AMR_ICON_POS = scaled_pos(115, 280)
AMR_ICON_SIZE = scaled_size(190, 170)
# Push 아이콘 출력 위치 및 크기 설정
PUSH_ICON_POS = scaled_pos(95, 575)
PUSH_ICON_SIZE = scaled_size(210, 165)
# Battery Info 아이콘 출력 위치 및 크기 설정
BATTERY_2_POS = scaled_pos(980, 270)
BATTERY_2_SIZE = scaled_size(130, 110)
# ----------------------- 텍스트 위치 설정 -----------------------
# 메인 제목 중심 좌표 설정
TITLE_CENTER_POS = scaled_pos(800, 125)
# MoMa State 제목 중심 좌표 설정
MOMA_STATE_TITLE_CENTER_POS = scaled_pos(515, 320)
# Release Brake 제목 중심 좌표 설정
RELEASE_BRAKE_TITLE_CENTER_POS = scaled_pos(525, 610)
# Battery Info 제목 중심 좌표 설정
BATTERY_INFO_TITLE_CENTER_POS = scaled_pos(1265, 325)
# ----------------------- 박스 및 버튼 위치 설정 -----------------------
# MoMa State 상태 표시 박스 설정
MOMA_STATE_BOX_RECT = scaled_rect(315, 365, 520, 120)
# 좌측 영역 구분선 시작/끝 좌표 설정
LEFT_DIVIDER_START_POS = scaled_pos(95, 520)
LEFT_DIVIDER_END_POS = scaled_pos(860, 520)
# ON 버튼 위치 및 크기 설정
ON_BUTTON_RECT = scaled_rect(335, 665, 240, 120)
# OFF 버튼 위치 및 크기 설정
OFF_BUTTON_RECT = scaled_rect(615, 665, 240, 120)
# Battery Info 내용 표시 박스 설정
BATTERY_INFO_BOX_RECT = scaled_rect(970, 405, 525, 365)
# 수동 충전 시작 버튼 위치 및 크기 설정
MANUAL_CHARGE_BUTTON_RECT = scaled_rect(970, 750, 525, 45)
# Dobot On 버튼 위치 및 크기 설정
DOBOT_ON_BUTTON_RECT = scaled_rect(1205,62, 200, 145)
# ----------------------- 버튼 클릭 영역 딕셔너리 설정 -----------------------
# 버튼 Rect 딕셔너리 설정
BUTTON_RECTS = {
    "ON_BUTTON": ON_BUTTON_RECT,
    "OFF_BUTTON": OFF_BUTTON_RECT,
    "MANUAL_CHARGE_BUTTON": MANUAL_CHARGE_BUTTON_RECT,
    "DOBOT_ON_BUTTON": DOBOT_ON_BUTTON_RECT}
# ----------------------- DIO 모듈 설정 -----------------------
# DIO 모듈 IP 설정
DIO_IP = "192.168.0.13"
# DIO 모듈 Port 설정
DIO_PORT = 2001
# DIO 통신 timeout 설정
DIO_TIMEOUT = 5.0
# Release Brake 제어용 DIO DO 번호 설정
RELEASE_BRAKE_DO = 31
# Dobot On 제어용 DIO DO 번호 설정
DOBOT_ON_DO = 1
# Dobot On 신호 유지 시간 설정
DOBOT_ON_PULSE_SEC = 1.5
# ----------------------- SEER AMR 설정 -----------------------
# SEER AMR IP 설정
SEER_IP = "192.168.0.100"
# 수동 충전 시작 제어용 AMR DO 번호 설정
MANUAL_CHARGE_DO = 2
# ----------------------- 배터리 / 네비게이션 정보 갱신 설정 -----------------------
# 배터리 정보 갱신 주기 설정
BATTERY_UPDATE_INTERVAL_SEC = 2.0
# 네비게이션 상태 갱신 주기 설정
NAV_UPDATE_INTERVAL_SEC = 0.2
# Run Completed 표시 유지 시간 설정
RUN_COMPLETED_DISPLAY_SEC = 0.5


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


# ----------------------- 좌측 정렬 텍스트 출력 함수 선언 -----------------------
def draw_text_left(screen, text, font, color, pos):
    # 텍스트 Surface 생성
    text_surface = font.render(text, True, color)

    # 화면에 텍스트 출력
    screen.blit(text_surface, pos)


# ----------------------- 배터리 잔량 숫자 반환 함수 선언 -----------------------
def get_battery_percent_value(battery_text):
    # 배터리 상태 문자열이 에러 계열이면
    if battery_text in ["ERR", "N/A", "BAT"]:
        return 0.0

    # 에러가 없으면
    try:
        # % 문자를 제거하고 숫자로 변환
        battery_value = float(str(battery_text).replace("%", ""))

        # 배터리 값이 0보다 작으면
        if battery_value < 0:
            return 0.0

        # 배터리 값이 100보다 크면
        if battery_value > 100:
            return 100.0

        # 정상 값 반환
        return battery_value

    # 숫자 변환 실패 시
    except Exception:
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


# ----------------------- 배터리 정보 값 표시 문자열 변환 함수 선언 -----------------------
def format_battery_value(value, unit="", digits=2):
    # 값이 None이면 N/A 반환
    if value is None:
        return "N/A"

    # bool 값이면 True / False 형태로 반환
    if isinstance(value, bool):
        return "True" if value else "False"

    # 숫자면 소수점 자리수 정리
    try:
        number_value = float(value)

        # 정수처럼 보이는 값이면 정수 형태로 표시
        if number_value.is_integer():
            return f"{int(number_value)}{unit}"

        # 실수면 지정한 자리수까지 표시
        return f"{number_value:.{digits}f}{unit}"

    # 숫자 변환 실패 시 문자열로 반환
    except Exception:
        return str(value)


# ----------------------- charging 값 표시 문자열 변환 함수 선언 -----------------------
def format_charging_value(value):
    # 값이 None이면
    if value is None:
        return "N/A"

    # bool 타입이면
    if isinstance(value, bool):
        return "Charging" if value else "Not Charging"

    # 숫자 또는 문자열로 들어온 경우 처리
    value_text = str(value)

    # True 계열이면
    if value_text in ["1", "True", "true", "TRUE"]:
        return "Charging"

    # False 계열이면
    if value_text in ["0", "False", "false", "FALSE"]:
        return "Not Charging"

    # 그 외에는 그대로 반환
    return value_text


# ----------------------- 네비게이션 상태 코드 문자열 변환 함수 선언 -----------------------
def format_nav_state(current_state):
    # 상태값이 None이면
    if current_state is None:
        return "Waiting.."

    # 에러가 없으면
    try:
        # 상태값을 정수로 변환
        state_code = int(current_state)

    # 정수 변환 실패 시
    except Exception:
        return "Unknown"

    # 상태값이 0 또는 1이면
    if state_code in [0, 1]:
        return "Waiting.."

    # 상태값이 2이면
    elif state_code == 2:
        return "Run"

    # 상태값이 3이면
    elif state_code == 3:
        return "Blocked"

    # 상태값이 4이면
    elif state_code == 4:
        return "Run Completed"

    # 상태값이 5이면
    elif state_code == 5:
        return "Error"

    # 상태값이 6이면
    elif state_code == 6:
        return "Run Cancled"

    # 그 외 상태값이면
    else:
        return "Unknown"


# ----------------------- 이미지 비율 유지 로드 함수 선언 -----------------------
def load_ui_image_keep_ratio(image_path, max_size, allow_upscale=True):
    # 이미지 경로가 비어있으면
    if image_path is None or image_path == "":
        return None

    # 이미지 파일이 존재하지 않으면
    if not os.path.exists(image_path):
        print(f"[UI] 이미지 파일을 찾을 수 없습니다 : {image_path}")
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
        scale_ratio = min(scale_ratio, 1.0)

    # 새 크기 계산
    new_w = max(1, int(original_w * scale_ratio))
    new_h = max(1, int(original_h * scale_ratio))

    # 이미지 크기 변경
    image = pygame.transform.smoothscale(image, (new_w, new_h))

    # 이미지 반환
    return image


# ----------------------- 전체 이미지 로드 함수 선언 -----------------------
def load_all_ui_images():
    # UI 이미지 딕셔너리 생성
    ui_images = {
        "lab2m_logo": load_ui_image_keep_ratio(
            LAB2M_LOGO_IMAGE_PATH,
            LOGO_SIZE,
            allow_upscale=True
        ),
        "amr_icon": load_ui_image_keep_ratio(
            AMR_ICON_IMAGE_PATH,
            AMR_ICON_SIZE,
            allow_upscale=True
        ),
        "push_icon": load_ui_image_keep_ratio(
            PUSH_ICON_IMAGE_PATH,
            PUSH_ICON_SIZE,
            allow_upscale=True
        ),
        "battery_2": load_ui_image_keep_ratio(
            BATTERY_2_IMAGE_PATH,
            BATTERY_2_SIZE,
            allow_upscale=True
        )
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


# ----------------------- 이미지 없을 때 placeholder 출력 함수 선언 -----------------------
def draw_image_placeholder(screen, rect, label, font):
    # placeholder 배경 출력
    pygame.draw.rect(screen, (235, 240, 245), rect, border_radius=sr(18))

    # placeholder 테두리 출력
    pygame.draw.rect(screen, GRAY, rect, width=sw(2), border_radius=sr(18))

    # placeholder 텍스트 출력
    draw_text(
        screen=screen,
        text=label,
        font=font,
        color=DARK_GRAY,
        center_pos=rect.center
    )


# ----------------------- 그림자 있는 둥근 사각형 출력 함수 선언 -----------------------
def draw_rounded_rect_with_shadow(screen, rect, fill_color, border_color=None, border_width=0, radius=25):
    # 그림자 영역 생성
    shadow_rect = pygame.Rect(rect.x + sw(4), rect.y + sw(5), rect.w, rect.h)

    # 그림자 출력
    pygame.draw.rect(screen, SHADOW, shadow_rect, border_radius=sr(radius))

    # 실제 사각형 출력
    pygame.draw.rect(screen, fill_color, rect, border_radius=sr(radius))

    # 테두리가 필요한 경우
    if border_color is not None and border_width > 0:
        pygame.draw.rect(
            screen,
            border_color,
            rect,
            width=sw(border_width),
            border_radius=sr(radius)
        )


# ----------------------- 세로 그라데이션 버튼 출력 함수 선언 -----------------------
def draw_gradient_button(screen, rect, text, font):
    # 버튼 상단 색상 설정
    top_color = (0, 135, 225)

    # 버튼 하단 색상 설정
    bottom_color = (0, 90, 180)

    # 그림자 영역 생성
    shadow_rect = pygame.Rect(rect.x + sw(5), rect.y + sw(6), rect.w, rect.h)

    # 그림자 출력
    pygame.draw.rect(screen, SHADOW, shadow_rect, border_radius=sr(22))

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
        border_radius=sr(22)
    )

    # 마스크 적용
    gradient_surface.blit(mask_surface, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    # 그라데이션 버튼 출력
    screen.blit(gradient_surface, rect.topleft)

    # 버튼 테두리 출력
    pygame.draw.rect(screen, (70, 170, 245), rect, width=sw(3), border_radius=sr(22))

    # 텍스트 출력
    draw_text(
        screen=screen,
        text=text,
        font=font,
        color=PURE_WHITE,
        center_pos=rect.center
    )


# ----------------------- 빈 정보 박스 출력 함수 선언 -----------------------
def draw_empty_info_box(screen, rect, radius=18):
    # 흰색 박스 출력
    pygame.draw.rect(screen, PURE_WHITE, rect, border_radius=sr(radius))

    # 파란색 테두리 출력
    pygame.draw.rect(screen, BORDER_BLUE, rect, width=sw(2), border_radius=sr(radius))



# ----------------------- Dobot On 원형 버튼 출력 함수 선언 -----------------------
def draw_dobot_on_button(screen, rect, font):
    # 버튼 중심 좌표 계산
    center_pos = rect.center

    # 버튼 반지름 계산
    radius = min(rect.w, rect.h) // 2

    # 그림자 중심 좌표 계산
    shadow_center_pos = (center_pos[0] + sw(5), center_pos[1] + sw(6))

    # 버튼 그림자 출력
    pygame.draw.circle(screen, SHADOW, shadow_center_pos, radius)

    # 버튼 원형 배경 출력
    pygame.draw.circle(screen, (0, 115, 195), center_pos, radius)

    # 버튼 외곽선 출력
    pygame.draw.circle(screen, NAVY, center_pos, radius, width=sw(4))

    # 첫 번째 줄 텍스트 Surface 생성
    text_surface_1 = font.render("Dobot", True, PURE_WHITE)

    # 두 번째 줄 텍스트 Surface 생성
    text_surface_2 = font.render("On", True, PURE_WHITE)

    # 첫 번째 줄 텍스트 Rect 생성
    text_rect_1 = text_surface_1.get_rect(center=(center_pos[0], center_pos[1] - sy(18)))

    # 두 번째 줄 텍스트 Rect 생성
    text_rect_2 = text_surface_2.get_rect(center=(center_pos[0], center_pos[1] + sy(18)))

    # 첫 번째 줄 텍스트 출력
    screen.blit(text_surface_1, text_rect_1)

    # 두 번째 줄 텍스트 출력
    screen.blit(text_surface_2, text_rect_2)

# ----------------------- 우측 상단 배터리 아이콘 출력 함수 선언 -----------------------
def draw_top_battery_widget(screen, battery_text, percent_font):
    # 배터리 잔량 숫자 계산
    battery_percent = get_battery_percent_value(battery_text)

    # 배터리 잔량 색상 계산
    fill_color = get_battery_color(battery_percent)

    # 배터리 전체 기준 좌표 가져오기
    widget_x, widget_y = BATTERY_POS

    # 배터리 전체 기준 크기 가져오기
    widget_w, widget_h = BATTERY_SIZE

    # 배터리 몸통 크기 계산
    body_w = int(widget_w * 0.42)
    body_h = int(widget_h * 0.78)

    # 배터리 몸통 위치 계산
    body_x = widget_x + int(widget_w * 0.30)
    body_y = widget_y + int(widget_h * 0.18)

    # 배터리 머리 부분 크기 계산
    cap_w = int(body_w * 0.42)
    cap_h = int(widget_h * 0.08)

    # 배터리 머리 부분 위치 계산
    cap_x = body_x + (body_w - cap_w) // 2
    cap_y = body_y - cap_h + 2

    # 배터리 몸통 Rect 생성
    body_rect = pygame.Rect(body_x, body_y, body_w, body_h)

    # 배터리 머리 Rect 생성
    cap_rect = pygame.Rect(cap_x, cap_y, cap_w, cap_h)

    # 배터리 내부 여백 설정
    inner_padding = sw(8)

    # 배터리 내부 영역 계산
    inner_x = body_x + inner_padding
    inner_y = body_y + inner_padding
    inner_w = body_w - inner_padding * 2
    inner_h = body_h - inner_padding * 2

    # 배터리 잔량에 따른 채움 높이 계산
    fill_h = int(inner_h * (battery_percent / 100.0))

    # 배터리 채움 Rect 생성
    fill_rect = pygame.Rect(
        inner_x,
        inner_y + inner_h - fill_h,
        inner_w,
        fill_h
    )

    # 배터리 내부 배경 출력
    pygame.draw.rect(
        screen,
        PURE_WHITE,
        pygame.Rect(inner_x, inner_y, inner_w, inner_h),
        border_radius=sr(8)
    )

    # 배터리 잔량이 0보다 크면 색상 채움 출력
    if fill_h > 0:
        pygame.draw.rect(
            screen,
            fill_color,
            fill_rect,
            border_radius=sr(8)
        )

    # 배터리 외곽선 출력
    pygame.draw.rect(
        screen,
        BLACK,
        body_rect,
        width=sw(6),
        border_radius=sr(14)
    )

    # 배터리 머리 부분 출력
    pygame.draw.rect(
        screen,
        BLACK,
        cap_rect,
        border_radius=sr(4)
    )

    # 배터리 표시 문자열 결정
    if battery_text in ["ERR", "N/A", "BAT"]:
        display_text = battery_text
    else:
        display_text = f"{battery_percent:.0f}%"

    # 배터리 내부 중앙에 퍼센트 텍스트 출력
    draw_text(
        screen=screen,
        text=display_text,
        font=percent_font,
        color=BLACK,
        center_pos=(body_x + body_w // 2, body_y + body_h // 2)
    )


# ----------------------- MoMa State 상태 박스 출력 함수 선언 -----------------------
def draw_moma_state_box(screen, rect, nav_state_text, state_font):
    # 상태 박스 기본 출력
    draw_empty_info_box(
        screen=screen,
        rect=rect,
        radius=18
    )

    # 상태 텍스트 색상 기본값 설정
    state_color = NAVY

    # Error 상태면 빨간색으로 표시
    if nav_state_text == "Error":
        state_color = RED

    # Blocked 상태면 주황색으로 표시
    elif nav_state_text == "Blocked":
        state_color = ORANGE

    # Run 또는 Run Completed 상태면 초록색으로 표시
    elif nav_state_text in ["Run", "Run Completed"]:
        state_color = GREEN

    # 상태 텍스트 출력
    draw_text(
        screen=screen,
        text=nav_state_text,
        font=state_font,
        color=state_color,
        center_pos=rect.center
    )


# ----------------------- Battery Info 상세 정보 박스 출력 함수 선언 -----------------------
def draw_battery_detail_box(screen, rect, battery_detail, title_font, row_font):
    # Battery Info 박스 기본 출력
    draw_empty_info_box(
        screen=screen,
        rect=rect,
        radius=20
    )

    # 배터리 정보가 아직 없으면
    if battery_detail is None:
        draw_text(
            screen=screen,
            text="Loading Battery Info...",
            font=title_font,
            color=NAVY,
            center_pos=rect.center
        )
        return

    # 표시할 배터리 정보 리스트 생성
    info_rows = [
        ("SOC", format_battery_value(battery_detail.get("level"), "%", digits=0)),
        ("Temp", format_battery_value(battery_detail.get("temp"), " °C", digits=1)),
        ("Charging", format_charging_value(battery_detail.get("charging"))),
        ("Voltage", format_battery_value(battery_detail.get("voltage"), " V", digits=2)),
        ("Current", format_battery_value(battery_detail.get("current"), " A", digits=2)),
        ("Max Charge V", format_battery_value(battery_detail.get("max_charge_voltage"), " V", digits=2)),
        ("Max Charge A", format_battery_value(battery_detail.get("max_charge_current"), " A", digits=2)),
        ("Cycle", format_battery_value(battery_detail.get("cycle"), "", digits=0)),
        ("Error", format_battery_value(battery_detail.get("error"), "", digits=0))
    ]

    # 시작 좌표 설정
    start_x = rect.x + sx(50)
    start_y = rect.y + sy(35)

    # 행 간격 설정
    row_gap = sy(34)

    # label x 좌표 설정
    label_x = start_x

    # value x 좌표 설정
    value_x = rect.x + sx(275)

    # 각 배터리 정보 출력
    for row_index, (label, value) in enumerate(info_rows):
        # 현재 y 좌표 계산
        current_y = start_y + row_index * row_gap

        # 라벨 텍스트 출력
        draw_text_left(
            screen=screen,
            text=f"{label}",
            font=row_font,
            color=NAVY,
            pos=(label_x, current_y)
        )

        # 콜론 출력
        draw_text_left(
            screen=screen,
            text=":",
            font=row_font,
            color=NAVY,
            pos=(value_x - sx(25), current_y)
        )

        # 값 텍스트 출력
        draw_text_left(
            screen=screen,
            text=f"{value}",
            font=row_font,
            color=BLACK,
            pos=(value_x, current_y)
        )


# ----------------------- HMI 제어 클래스 선언 -----------------------
class L2M_HMI_Controller:
    # 클래스 초기화 함수 선언
    def __init__(self):
        # SEER_commu 객체 저장 변수 선언
        self.seer = None

        # DIO는 상시 연결하지 않기 때문에 기본값만 None으로 둠
        self.dio = None

        # SEER 통신 충돌 방지를 위한 Lock 선언
        self.seer_lock = threading.Lock()

        # 배터리 값 보호를 위한 Lock 선언
        self.battery_lock = threading.Lock()

        # 네비게이션 상태 보호를 위한 Lock 선언
        self.nav_lock = threading.Lock()

        # DIO 제어 명령 충돌 방지를 위한 Lock 선언
        self.dio_lock = threading.Lock()

        # 수동 충전 상태 보호를 위한 Lock 선언
        self.manual_charge_lock = threading.Lock()

        # 프로그램 실행 상태 변수 선언
        self.running = True

        # 현재 배터리 상태 문자열 저장 변수 선언
        self.battery_status_text = "BAT"

        # 현재 배터리 상세 정보 저장 변수 선언
        self.battery_detail = None

        # 현재 네비게이션 상태 표시 문자열 저장 변수 선언
        self.nav_state_text = "Waiting.."

        # 현재 타겟 노드 저장 변수 선언
        self.current_target_node = None

        # 남은 노드 저장 변수 선언
        self.remaining_node = None

        # Run Completed가 처음 표시된 시간 저장 변수 선언
        self.run_completed_start_time = None

        # 수동 충전 AMR DO 출력 상태 저장 변수 선언
        self.manual_charge_on = False

        # SEER AMR 연결 시도
        self.connect_amr()

        # 중요:
        # DIO 모듈은 여기서 상시 연결하지 않음
        # ON/OFF 버튼을 누를 때만 임시 연결해서 사용함
        # self.connect_dio()

        # 배터리 정보 갱신 Thread 시작
        self.start_battery_thread()

        # 네비게이션 상태 갱신 Thread 시작
        self.start_nav_thread()

    # SEER AMR 연결 함수 선언
    def connect_amr(self):
        try:
            # SEER_commu 객체 생성
            self.seer = SEER_commu(ip=SEER_IP)

            # 연결 완료 출력
            print("[HMI] SEER AMR 연결 완료")

        except Exception as error:
            # SEER_commu 객체 None 처리
            self.seer = None

            # 에러 출력
            print(f"[HMI] SEER AMR 연결 실패 : {error}")

    # DIO 모듈 연결 함수 선언
    # 현재 구조에서는 직접 사용하지 않지만, 필요 시 다시 상시 연결 구조로 되돌릴 수 있도록 남겨둠
    def connect_dio(self):
        try:
            # DIO_Commu 객체 생성
            self.dio = DIO_Commu(
                ip=DIO_IP,
                port=DIO_PORT,
                timeout=DIO_TIMEOUT
            )

            # DIO 모듈 연결
            self.dio.dio_connect()

            # 연결 완료 출력
            print("[HMI] DIO 모듈 연결 완료")

        except Exception as error:
            # DIO 객체 None 처리
            self.dio = None

            # 에러 출력
            print(f"[HMI] DIO 모듈 연결 실패 : {error}")

    # 배터리 Thread 시작 함수 선언
    def start_battery_thread(self):
        battery_thread = threading.Thread(
            target=self.battery_update_loop,
            daemon=True
        )

        battery_thread.start()

    # 네비게이션 Thread 시작 함수 선언
    def start_nav_thread(self):
        nav_thread = threading.Thread(
            target=self.nav_update_loop,
            daemon=True
        )

        nav_thread.start()

    # 배터리 정보 갱신 반복 함수 선언
    def battery_update_loop(self):
        while self.running:
            if self.seer is not None:
                try:
                    with self.seer_lock:
                        battery_info = self.seer.get_battery_info()

                    if isinstance(battery_info, tuple) and len(battery_info) >= 9:
                        current_battery_level = battery_info[0]
                        current_battery_temp = battery_info[1]
                        current_battery_charging = battery_info[2]
                        current_battery_voltage = battery_info[3]
                        current_battery_current = battery_info[4]
                        battery_max_charge_voltage = battery_info[5]
                        battery_max_charge_current = battery_info[6]
                        current_battery_cycle = battery_info[7]
                        current_error = battery_info[8]

                        print(f"[HMI] 현재 배터리 원본 값 : {current_battery_level}")

                        if current_battery_level is not None:
                            current_battery_level_float = float(current_battery_level)

                            if 0 <= current_battery_level_float <= 1:
                                display_battery_level = current_battery_level_float * 100
                            else:
                                display_battery_level = current_battery_level_float

                            with self.battery_lock:
                                self.battery_status_text = f"{display_battery_level:.0f}%"
                                self.battery_detail = {
                                    "level": display_battery_level,
                                    "temp": current_battery_temp,
                                    "charging": current_battery_charging,
                                    "voltage": current_battery_voltage,
                                    "current": current_battery_current,
                                    "max_charge_voltage": battery_max_charge_voltage,
                                    "max_charge_current": battery_max_charge_current,
                                    "cycle": current_battery_cycle,
                                    "error": current_error
                                }

                        else:
                            with self.battery_lock:
                                self.battery_status_text = "N/A"
                                self.battery_detail = {
                                    "level": None,
                                    "temp": current_battery_temp,
                                    "charging": current_battery_charging,
                                    "voltage": current_battery_voltage,
                                    "current": current_battery_current,
                                    "max_charge_voltage": battery_max_charge_voltage,
                                    "max_charge_current": battery_max_charge_current,
                                    "cycle": current_battery_cycle,
                                    "error": current_error
                                }

                    else:
                        with self.battery_lock:
                            self.battery_status_text = "ERR"
                            self.battery_detail = None

                        print(f"[HMI] 배터리 정보 형식 이상 : {battery_info}")

                except Exception as error:
                    print(f"[HMI] 배터리 정보 수신 실패 : {error}")

                    with self.battery_lock:
                        self.battery_status_text = "ERR"
                        self.battery_detail = None

            time.sleep(BATTERY_UPDATE_INTERVAL_SEC)

    # 네비게이션 상태 갱신 반복 함수 선언
    def nav_update_loop(self):
        while self.running:
            if self.seer is not None:
                try:
                    with self.seer_lock:
                        nav_info = self.seer.get_nav_info()

                    if isinstance(nav_info, tuple) and len(nav_info) >= 3:
                        current_state = nav_info[0]
                        current_target_node = nav_info[1]
                        remaining_node = nav_info[2]
                        now_time = time.time()

                        try:
                            current_state_code = int(current_state)
                        except Exception:
                            current_state_code = None

                        if current_state_code == 4:
                            if self.run_completed_start_time is None:
                                self.run_completed_start_time = now_time
                                nav_state_text = "Run Completed"
                            else:
                                if now_time - self.run_completed_start_time >= RUN_COMPLETED_DISPLAY_SEC:
                                    nav_state_text = "Waiting.."
                                else:
                                    nav_state_text = "Run Completed"

                        else:
                            self.run_completed_start_time = None
                            nav_state_text = format_nav_state(current_state)

                        with self.nav_lock:
                            self.nav_state_text = nav_state_text
                            self.current_target_node = current_target_node
                            self.remaining_node = remaining_node

                        print(f"[HMI] Navigation State : {current_state} -> {nav_state_text}")

                    else:
                        with self.nav_lock:
                            self.nav_state_text = "Waiting.."
                            self.current_target_node = None
                            self.remaining_node = None
                            self.run_completed_start_time = None

                        print(f"[HMI] 네비게이션 정보 형식 이상 : {nav_info}")

                except Exception as error:
                    print(f"[HMI] 네비게이션 정보 수신 실패 : {error}")

                    with self.nav_lock:
                        self.nav_state_text = "Waiting.."
                        self.current_target_node = None
                        self.remaining_node = None
                        self.run_completed_start_time = None

            time.sleep(NAV_UPDATE_INTERVAL_SEC)

    # 현재 배터리 표시 문자열 반환 함수 선언
    def get_battery_text(self):
        with self.battery_lock:
            return self.battery_status_text

    # 현재 배터리 상세 정보 반환 함수 선언
    def get_battery_detail(self):
        with self.battery_lock:
            if self.battery_detail is None:
                return None

            return dict(self.battery_detail)

    # 현재 네비게이션 상태 문자열 반환 함수 선언
    def get_nav_state_text(self):
        with self.nav_lock:
            nav_state_text = self.nav_state_text
            run_completed_start_time = self.run_completed_start_time

        if nav_state_text == "Run Completed" and run_completed_start_time is not None:
            if time.time() - run_completed_start_time >= RUN_COMPLETED_DISPLAY_SEC:
                return "Waiting.."

        return nav_state_text

    # 현재 수동 충전 상태 반환 함수 선언
    def get_manual_charge_state(self):
        with self.manual_charge_lock:
            return self.manual_charge_on

    # Release Brake DO 제어 비동기 실행 함수 선언
    def set_release_brake_async(self, flag):
        brake_thread = threading.Thread(
            target=self.set_release_brake,
            args=(flag,),
            daemon=True
        )

        brake_thread.start()

    # Release Brake DO 제어 함수 선언
    def set_release_brake(self, flag):
        # 임시 DIO 객체 변수 선언
        temp_dio = None

        try:
            # DIO 제어 명령 충돌 방지를 위한 Lock 사용
            with self.dio_lock:
                # DIO_Commu 객체 임시 생성
                temp_dio = DIO_Commu(
                    ip=DIO_IP,
                    port=DIO_PORT,
                    timeout=DIO_TIMEOUT
                )

                # DIO 모듈과 TCP 연결
                temp_dio.dio_connect()

                # flag가 True이면 Release Brake DO High
                if flag:
                    temp_dio.set_do_on(RELEASE_BRAKE_DO)
                    print("[HMI] Release Brake ON : DO31=High")

                # flag가 False이면 Release Brake DO Low
                else:
                    temp_dio.set_do_off(RELEASE_BRAKE_DO)
                    print("[HMI] Release Brake OFF : DO31=Low")

                # 정상 완료
                return True

        except Exception as error:
            # 에러 출력
            print(f"[HMI] Release Brake DO 제어 실패 : {error}")
            return False

        finally:
            # DIO 연결 종료
            if temp_dio is not None:
                try:
                    temp_dio.dio_close()
                    print("[HMI] Release Brake 제어 후 DIO 연결 종료")
                except Exception as error:
                    print(f"[HMI] DIO 연결 종료 실패 : {error}")

    # 수동 충전 AMR DO 토글 비동기 실행 함수 선언
    def toggle_manual_charge_async(self):
        manual_charge_thread = threading.Thread(
            target=self.toggle_manual_charge,
            daemon=True
        )

        manual_charge_thread.start()

    # 수동 충전 AMR DO 토글 함수 선언
    def toggle_manual_charge(self):
        if self.seer is None:
            print("[HMI] SEER AMR이 연결되지 않아 수동 충전을 제어할 수 없습니다.")
            return False

        try:
            with self.manual_charge_lock:
                next_manual_charge_state = not self.manual_charge_on

            with self.seer_lock:
                self.seer.setDO(MANUAL_CHARGE_DO, next_manual_charge_state)

            with self.manual_charge_lock:
                self.manual_charge_on = next_manual_charge_state

            if next_manual_charge_state:
                print("[HMI] 수동 충전 시작 : AMR DO2=High")
            else:
                print("[HMI] 수동 충전 정지 : AMR DO2=Low")

            return True

        except Exception as error:
            print(f"[HMI] 수동 충전 AMR DO 제어 실패 : {error}")
            return False

    # Dobot On DO Pulse 비동기 실행 함수 선언
    def pulse_dobot_on_async(self):
        dobot_thread = threading.Thread(
            target=self.pulse_dobot_on,
            daemon=True
        )

        dobot_thread.start()

    # Dobot On DO Pulse 제어 함수 선언
    def pulse_dobot_on(self):
        # 임시 DIO 객체 변수 선언
        temp_dio = None

        # Dobot On 출력 High 성공 여부 변수 선언
        do_high_done = False

        try:
            # DIO 제어 명령 충돌 방지를 위한 Lock 사용
            with self.dio_lock:
                # DIO_Commu 객체 임시 생성
                temp_dio = DIO_Commu(
                    ip=DIO_IP,
                    port=DIO_PORT,
                    timeout=DIO_TIMEOUT
                )

                # DIO 모듈과 TCP 연결
                temp_dio.dio_connect()

                # Dobot On DO High 출력
                temp_dio.set_do_on(DOBOT_ON_DO)

                # High 성공 여부 저장
                do_high_done = True

                # 로그 출력
                print(f"[HMI] Dobot On : DO{DOBOT_ON_DO}=High")

                # 지정한 시간 동안 High 유지
                time.sleep(DOBOT_ON_PULSE_SEC)

                # Dobot On DO Low 출력
                temp_dio.set_do_off(DOBOT_ON_DO)

                # High 상태 해제 완료 처리
                do_high_done = False

                # 로그 출력
                print(f"[HMI] Dobot On : DO{DOBOT_ON_DO}=Low")

                # 정상 완료
                return True

        except Exception as error:
            # 에러 출력
            print(f"[HMI] Dobot On DO Pulse 제어 실패 : {error}")
            return False

        finally:
            # High 출력 후 에러가 발생한 경우 Low 복구 시도
            if temp_dio is not None and do_high_done:
                try:
                    temp_dio.set_do_off(DOBOT_ON_DO)
                    print(f"[HMI] Dobot On 예외 복구 : DO{DOBOT_ON_DO}=Low")
                except Exception as error:
                    print(f"[HMI] Dobot On 예외 복구 실패 : {error}")

            # DIO 연결 종료
            if temp_dio is not None:
                try:
                    temp_dio.dio_close()
                    print("[HMI] Dobot On 제어 후 DIO 연결 종료")
                except Exception as error:
                    print(f"[HMI] DIO 연결 종료 실패 : {error}")

    # 종료 처리 함수 선언
    def close(self):
        self.running = False

        if self.seer is not None:
            try:
                self.seer.setDO(MANUAL_CHARGE_DO, False)
                print("[HMI] 종료 처리 : 수동 충전 AMR DO2=Low")
            except Exception as error:
                print(f"[HMI] 종료 처리 중 수동 충전 AMR DO2 Low 실패 : {error}")

        # DIO는 상시 연결하지 않으므로 일반적으로 종료할 소켓이 없음
        if self.dio is not None:
            try:
                self.dio.dio_close()
                print("[HMI] DIO 모듈 소켓 종료 완료")
            except Exception as error:
                print(f"[HMI] DIO 모듈 소켓 종료 실패 : {error}")

        if self.seer is not None:
            try:
                self.seer.socket_close()
                print("[HMI] SEER AMR 소켓 종료 완료")
            except Exception as error:
                print(f"[HMI] SEER AMR 소켓 종료 실패 : {error}")


# ----------------------- UI 전체 출력 함수 선언 -----------------------
def draw_ui(screen, fonts, ui_images, hmi_controller):
    # 전체 배경 파란색 출력
    screen.fill(BLUE)

    # 메인 흰색 패널 출력
    draw_rounded_rect_with_shadow(
        screen=screen,
        rect=MAIN_PANEL_RECT,
        fill_color=WHITE,
        border_color=None,
        border_width=0,
        radius=28
    )

    # 로고 이미지 출력
    if ui_images["lab2m_logo"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["lab2m_logo"],
            center_pos=(
                LOGO_POS[0] + LOGO_SIZE[0] // 2,
                LOGO_POS[1] + LOGO_SIZE[1] // 2
            )
        )
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(LOGO_POS, LOGO_SIZE),
            label="lab2m_logo",
            font=fonts["placeholder"]
        )

    # 메인 제목 출력
    draw_text(
        screen=screen,
        text="L2M Mobile Manipulator",
        font=fonts["title"],
        color=NAVY,
        center_pos=TITLE_CENTER_POS
    )

    # Dobot On 버튼 출력
    draw_dobot_on_button(
        screen=screen,
        rect=DOBOT_ON_BUTTON_RECT,
        font=fonts["dobot_button"]
    )

    # 배터리 표시 문자열 가져오기
    battery_text = hmi_controller.get_battery_text()

    # 우측 상단 배터리 아이콘 출력
    draw_top_battery_widget(
        screen=screen,
        battery_text=battery_text,
        percent_font=fonts["top_battery"]
    )

    # 좌측 패널 출력
    pygame.draw.rect(screen, PURE_WHITE, LEFT_PANEL_RECT, border_radius=sr(20))

    # 좌측 패널 테두리 출력
    pygame.draw.rect(screen, BORDER_BLUE, LEFT_PANEL_RECT, width=sw(2), border_radius=sr(20))

    # 우측 패널 출력
    pygame.draw.rect(screen, PURE_WHITE, RIGHT_PANEL_RECT, border_radius=sr(20))

    # 우측 패널 테두리 출력
    pygame.draw.rect(screen, BORDER_BLUE, RIGHT_PANEL_RECT, width=sw(2), border_radius=sr(20))

    # MoMa State AMR 아이콘 출력
    if ui_images["amr_icon"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["amr_icon"],
            center_pos=(
                AMR_ICON_POS[0] + AMR_ICON_SIZE[0] // 2,
                AMR_ICON_POS[1] + AMR_ICON_SIZE[1] // 2
            )
        )
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(AMR_ICON_POS, AMR_ICON_SIZE),
            label="amr_icon",
            font=fonts["placeholder"]
        )

    # MoMa State 제목 출력
    draw_text(
        screen=screen,
        text="MoMa State",
        font=fonts["section_title"],
        color=NAVY,
        center_pos=MOMA_STATE_TITLE_CENTER_POS
    )

    # 네비게이션 상태 문자열 가져오기
    nav_state_text = hmi_controller.get_nav_state_text()

    # MoMa State 상태 표시 박스 출력
    draw_moma_state_box(
        screen=screen,
        rect=MOMA_STATE_BOX_RECT,
        nav_state_text=nav_state_text,
        state_font=fonts["moma_state"]
    )

    # 좌측 구분선 출력
    pygame.draw.line(
        screen,
        LIGHT_BLUE,
        LEFT_DIVIDER_START_POS,
        LEFT_DIVIDER_END_POS,
        width=sw(2)
    )

    # Push 아이콘 출력
    if ui_images["push_icon"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["push_icon"],
            center_pos=(
                PUSH_ICON_POS[0] + PUSH_ICON_SIZE[0] // 2,
                PUSH_ICON_POS[1] + PUSH_ICON_SIZE[1] // 2
            )
        )
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(PUSH_ICON_POS, PUSH_ICON_SIZE),
            label="push_icon",
            font=fonts["placeholder"]
        )

    # Release Brake 제목 출력
    draw_text(
        screen=screen,
        text="Release Brake",
        font=fonts["section_title"],
        color=NAVY,
        center_pos=RELEASE_BRAKE_TITLE_CENTER_POS
    )

    # ON 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=ON_BUTTON_RECT,
        text="ON",
        font=fonts["button"]
    )

    # OFF 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=OFF_BUTTON_RECT,
        text="OFF",
        font=fonts["button"]
    )

    # Battery Info 아이콘 출력
    if ui_images["battery_2"] is not None:
        draw_image_centered(
            screen=screen,
            image=ui_images["battery_2"],
            center_pos=(
                BATTERY_2_POS[0] + BATTERY_2_SIZE[0] // 2,
                BATTERY_2_POS[1] + BATTERY_2_SIZE[1] // 2
            )
        )
    else:
        draw_image_placeholder(
            screen=screen,
            rect=pygame.Rect(BATTERY_2_POS, BATTERY_2_SIZE),
            label="battery_2",
            font=fonts["placeholder"]
        )

    # Battery Info 제목 출력
    draw_text(
        screen=screen,
        text="Battery Info",
        font=fonts["section_title"],
        color=NAVY,
        center_pos=BATTERY_INFO_TITLE_CENTER_POS
    )

    # 배터리 상세 정보 가져오기
    battery_detail = hmi_controller.get_battery_detail()

    # Battery Info 상세 정보 박스 출력
    draw_battery_detail_box(
        screen=screen,
        rect=BATTERY_INFO_BOX_RECT,
        battery_detail=battery_detail,
        title_font=fonts["battery_info_title"],
        row_font=fonts["battery_info_row"]
    )

    # 수동 충전 상태 가져오기
    manual_charge_on = hmi_controller.get_manual_charge_state()

    # 수동 충전 버튼 문구 결정
    if manual_charge_on:
        manual_charge_button_text = "Manual Charging OFF"
    else:
        manual_charge_button_text = "Manual Charging ON"

    # 수동 충전 버튼 출력
    draw_gradient_button(
        screen=screen,
        rect=MANUAL_CHARGE_BUTTON_RECT,
        text=manual_charge_button_text,
        font=fonts["charge_button"]
    )


# ----------------------- 마우스 클릭 확인 함수 선언 -----------------------
def handle_mouse_down(mouse_pos, hmi_controller):
    # ON 버튼 클릭 시
    if BUTTON_RECTS["ON_BUTTON"].collidepoint(mouse_pos):
        print("[HMI] ON 버튼 클릭 -> DO31 High")
        hmi_controller.set_release_brake_async(True)

    # OFF 버튼 클릭 시
    elif BUTTON_RECTS["OFF_BUTTON"].collidepoint(mouse_pos):
        print("[HMI] OFF 버튼 클릭 -> DO31 Low")
        hmi_controller.set_release_brake_async(False)

    # Dobot On 버튼 클릭 시
    elif BUTTON_RECTS["DOBOT_ON_BUTTON"].collidepoint(mouse_pos):
        print(f"[HMI] Dobot On 버튼 클릭 -> DO{DOBOT_ON_DO} 1.5초 High 후 Low")
        hmi_controller.pulse_dobot_on_async()

    # 수동 충전 버튼 클릭 시
    elif BUTTON_RECTS["MANUAL_CHARGE_BUTTON"].collidepoint(mouse_pos):
        print("[HMI] 수동 충전 버튼 클릭 -> AMR DO2 Toggle")
        hmi_controller.toggle_manual_charge_async()


# ----------------------- 메인 함수 선언 -----------------------
def main():
    # pygame 초기화
    pygame.init()

    # pygame font 초기화
    pygame.font.init()

    # 화면 객체 생성
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

    # pygame 창 제목 설정
    pygame.display.set_caption("L2M Mobile Manipulator")

    # FPS 제어 객체 생성
    clock = pygame.time.Clock()

    # 폰트 딕셔너리 생성
    fonts = {
        "title": get_korean_font(sf(64), bold=True),
        "section_title": get_korean_font(sf(48), bold=True),
        "button": get_korean_font(sf(48), bold=True),
        "charge_button": get_korean_font(sf(38), bold=True),
        "top_battery": get_korean_font(sf(24), bold=True),
        "dobot_button": get_korean_font(sf(28), bold=True),
        "moma_state": get_korean_font(sf(44), bold=True),
        "battery_info_title": get_korean_font(sf(30), bold=True),
        "battery_info_row": get_korean_font(sf(24), bold=True),
        "placeholder": get_korean_font(sf(24), bold=False)
    }

    # 전체 UI 이미지 로드
    ui_images = load_all_ui_images()

    # HMI 제어 객체 생성
    hmi_controller = L2M_HMI_Controller()

    # 프로그램 실행 상태 변수 선언
    running = True

    # 메인 반복문 시작
    while running:
        # FPS 60으로 제한
        clock.tick(60)

        # pygame 이벤트 순회
        for event in pygame.event.get():
            # 창 닫기 버튼을 눌렀을 경우
            if event.type == pygame.QUIT:
                running = False

            # 마우스 버튼을 눌렀을 경우
            elif event.type == pygame.MOUSEBUTTONDOWN:
                mouse_pos = pygame.mouse.get_pos()

                handle_mouse_down(
                    mouse_pos=mouse_pos,
                    hmi_controller=hmi_controller
                )

        # UI 전체 출력
        draw_ui(
            screen=screen,
            fonts=fonts,
            ui_images=ui_images,
            hmi_controller=hmi_controller
        )

        # 화면 업데이트
        pygame.display.flip()

    # HMI 제어 객체 종료 처리
    hmi_controller.close()

    # pygame 종료
    pygame.quit()

    # 프로그램 종료
    sys.exit()


# ----------------------- 메인 함수 실행 -----------------------
if __name__ == "__main__":
    main()