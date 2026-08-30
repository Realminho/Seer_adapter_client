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

# ----------------------- 화면 크기 설정 -----------------------
# pygame 화면 가로 크기 설정
SCREEN_WIDTH = 1600
# pygame 화면 세로 크기 설정
SCREEN_HEIGHT = 900

# ----------------------- UI 적용 색상 설정 -----------------------
# 배경 파란색 설정
BLUE = (0, 120, 190)
# 내부 흰색 설정
WHITE = (245, 245, 245)
# 검정색 설정
BLACK = (0, 0, 0)
# 진한 파란색 설정
DARK_BLUE = (0, 80, 150)
# 빨간색 설정
RED = (220, 0, 0)
# 초록색 설정
GREEN = (0, 190, 0)
# 주황색 설정
ORANGE = (255, 140, 0)
# 회색 설정
GRAY = (180, 180, 180)
# 진회색 설정
DARK_GRAY = (70, 70, 70)
# ----------------------- UI 사용 이미지 경로 설정 -----------------------
# LTM 로고 이미지 경로 설정
LOGO_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/lab2m_logo.png"
# 직진 버튼 이미지 경로 설정
GO_STRAIGHT_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/go_straight.png"
# 후진 버튼 이미지 경로 설정
GO_BACKWARD_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/go_backward.png"
# 좌측 회전 버튼 이미지 경로 설정
TURN_LEFT_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/turn_left.png"
# 우측 회전 버튼 이미지 경로 설정
TURN_RIGHT_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/turn_right.png"
# 배터리 아이콘 이미지 경로 설정
BATTERY_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/battery.png"
# 정지 버튼 이미지 경로 설정
STOP_BUTTON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/stop_button.png"
# Relocation 버튼 이미지 경로 설정
RELOCATION_BUTTON_IMAGE_PATH = "/home/fullmoon34213/Desktop/SamDisplay/sam_ui/image/relocation_button.png"

# ----------------------- UI 영역 설정 -----------------------
# 내부 흰색 영역 Rect 설정
INNER_RECT = pygame.Rect(55, 60, 1490, 790)

# ----------------------- 이미지 출력 위치 및 크기 설정 -----------------------
# 로고 출력 위치 및 크기 설정
LOGO_POS = (95, 70)
LOGO_SIZE = (215, 205)

# 제목 출력 중심 좌표 설정
TITLE_CENTER_POS = (780, 160)

# 배터리 위젯 출력 위치 및 크기 설정
BATTERY_POS = (1340, 80)
BATTERY_SIZE = (165, 230)

# 방향키 전체 위로 이동 오프셋 설정
DIRECTION_BUTTON_UP_OFFSET = 30

# 직진 버튼 출력 위치 및 크기 설정
GO_STRAIGHT_POS = (380, 315 - DIRECTION_BUTTON_UP_OFFSET)
GO_STRAIGHT_SIZE = (275, 200)

# 후진 버튼 출력 위치 및 크기 설정
GO_BACKWARD_POS = (375, 635 - DIRECTION_BUTTON_UP_OFFSET)
GO_BACKWARD_SIZE = (275, 200)

# 좌측 회전 버튼 출력 위치 및 크기 설정
TURN_LEFT_POS = (80, 480 - DIRECTION_BUTTON_UP_OFFSET)
TURN_LEFT_SIZE = (275, 200)

# 우측 회전 버튼 출력 위치 및 크기 설정
TURN_RIGHT_POS = (680, 480 - DIRECTION_BUTTON_UP_OFFSET)
TURN_RIGHT_SIZE = (275, 200)

# 정지 버튼 출력 위치 및 크기 설정
STOP_BUTTON_POS = (1060, 445)
STOP_BUTTON_SIZE = (295, 175)

# Relocation 버튼 출력 위치 및 크기 설정
RELOCATION_BUTTON_POS = (1060, 640)
RELOCATION_BUTTON_SIZE = (295, 180)

# ----------------------- AMR 수동 조작 속도 설정 -----------------------
# 직진 속도 설정
FORWARD_VX = 0.3
# 후진 속도 설정
BACKWARD_VX = -0.3
# 좌측 회전 각속도 설정
TURN_LEFT_W = 0.5
# 우측 회전 각속도 설정
TURN_RIGHT_W = -0.5
# motion_control 송신 주기 설정
MOTION_SEND_INTERVAL_SEC = 0.1
# motion_control duration 설정
MOTION_DURATION = 500
# STOP motion_control duration 설정
STOP_DURATION = 100
# 배터리 정보 갱신 주기 설정
BATTERY_UPDATE_INTERVAL_SEC = 2.0

# ----------------------- 한글 지원 폰트 반환 함수 선언 -----------------------
def get_korean_font(size, bold=False):
    # 우선 탐색할 한글 지원 폰트 이름 목록 선언
    font_candidates = [
        "malgungothic",
        "맑은고딕",
        "nanumgothic",
        "nanumbarungothic",
        "gulim",
        "dotum",
        "applegothic"
    ]

    # 후보 폰트들을 순서대로 탐색
    for one_font_name in font_candidates:
        # 현재 폰트 이름으로 시스템 폰트 경로 찾기
        font_path = pygame.font.match_font(one_font_name)

        # 폰트 경로를 찾았으면
        if font_path:
            # 폰트 객체 생성
            font = pygame.font.Font(font_path, size)
            # bold 적용
            font.set_bold(bold)
            # 폰트 객체 반환
            return font

    # 후보 폰트를 찾지 못했으면 기본 SysFont 사용
    fallback_font = pygame.font.SysFont("arial", size, bold=bold)
    # 기본 폰트 반환
    return fallback_font


# ----------------------- 텍스트 출력 함수 선언 -----------------------
def draw_text(screen, text, font, color, center_pos):
    # 텍스트 Surface 생성
    text_surface = font.render(text, True, color)
    # 텍스트 위치 Rect 생성
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
    # 배터리 잔량이 70 이상이면
    if 70 <= battery_percent <= 100:
        # 초록색 반환
        return GREEN
    # 배터리 잔량이 50 이상 70 미만이면
    elif 50 <= battery_percent < 70:
        # 주황색 반환
        return ORANGE
    # 배터리 잔량이 50 미만이면
    else:
        # 빨간색 반환
        return RED


# ----------------------- 방향키 안내 문자열 생성 함수 선언 -----------------------
def make_button_info_texts():
    # motion duration을 초 단위로 변환
    motion_duration_sec = MOTION_DURATION / 1000.0
    # 직진 1회 명령 기준 이동 거리 계산
    forward_distance_m = abs(FORWARD_VX) * motion_duration_sec
    # 후진 1회 명령 기준 이동 거리 계산
    backward_distance_m = abs(BACKWARD_VX) * motion_duration_sec
    # 좌회전 1회 명령 기준 회전 각도 계산
    turn_left_deg = abs(TURN_LEFT_W) * motion_duration_sec * 180.0 / 3.141592
    # 우회전 1회 명령 기준 회전 각도 계산
    turn_right_deg = abs(TURN_RIGHT_W) * motion_duration_sec * 180.0 / 3.141592

    # 방향키 안내 문자열 딕셔너리 반환
    return {
        "GO_STRAIGHT": f"{forward_distance_m:.2f}m 이동",
        "GO_BACKWARD": f"{backward_distance_m:.2f}m 이동",
        "TURN_LEFT": f"{turn_left_deg:.1f}도 회전",
        "TURN_RIGHT": f"{turn_right_deg:.1f}도 회전",
    }


# ----------------------- 단일 이미지 로드 함수 선언 -----------------------
def load_ui_image(image_path, image_size):
    # 이미지 파일이 존재하지 않으면
    if not os.path.exists(image_path):
        # 에러 문구 출력
        print(f"[UI] 이미지 파일을 찾을 수 없습니다 : {image_path}")
        # None 반환
        return None
    # 이미지 파일 로드
    image = pygame.image.load(image_path).convert_alpha()
    # 이미지 크기 조절
    image = pygame.transform.smoothscale(image, image_size)
    # 이미지 반환
    return image

# ----------------------- 전체 UI 이미지 로드 함수 선언 -----------------------
def load_all_ui_images():
    # UI 이미지 딕셔너리 생성
    ui_images = {
        # 로고 이미지 로드
        "logo": load_ui_image(LOGO_IMAGE_PATH, LOGO_SIZE),
        # 직진 버튼 이미지 로드
        "go_straight": load_ui_image(GO_STRAIGHT_IMAGE_PATH, GO_STRAIGHT_SIZE),
        # 후진 버튼 이미지 로드
        "go_backward": load_ui_image(GO_BACKWARD_IMAGE_PATH, GO_BACKWARD_SIZE),
        # 좌측 회전 버튼 이미지 로드
        "turn_left": load_ui_image(TURN_LEFT_IMAGE_PATH, TURN_LEFT_SIZE),
        # 우측 회전 버튼 이미지 로드
        "turn_right": load_ui_image(TURN_RIGHT_IMAGE_PATH, TURN_RIGHT_SIZE),
        # 정지 버튼 이미지 로드
        "stop": load_ui_image(STOP_BUTTON_IMAGE_PATH, STOP_BUTTON_SIZE),
        # Relocation 버튼 이미지 로드
        "relocation": load_ui_image(RELOCATION_BUTTON_IMAGE_PATH, RELOCATION_BUTTON_SIZE),
    }
    # UI 이미지 딕셔너리 반환
    return ui_images

# ----------------------- 버튼 클릭 영역 생성 함수 선언 -----------------------
def make_button_rects():
    # 버튼 클릭 영역 딕셔너리 생성
    button_rects = {
        # 직진 버튼 클릭 영역
        "GO_STRAIGHT": pygame.Rect(GO_STRAIGHT_POS, GO_STRAIGHT_SIZE),
        # 후진 버튼 클릭 영역
        "GO_BACKWARD": pygame.Rect(GO_BACKWARD_POS, GO_BACKWARD_SIZE),
        # 좌측 회전 버튼 클릭 영역
        "TURN_LEFT": pygame.Rect(TURN_LEFT_POS, TURN_LEFT_SIZE),
        # 우측 회전 버튼 클릭 영역
        "TURN_RIGHT": pygame.Rect(TURN_RIGHT_POS, TURN_RIGHT_SIZE),
        # 정지 버튼 클릭 영역
        "STOP": pygame.Rect(STOP_BUTTON_POS, STOP_BUTTON_SIZE),
        # Relocation 버튼 클릭 영역
        "RELOCATION": pygame.Rect(RELOCATION_BUTTON_POS, RELOCATION_BUTTON_SIZE),
    }
    # 버튼 클릭 영역 딕셔너리 반환
    return button_rects


# ----------------------- 이미지 출력 함수 선언 -----------------------
def draw_image(screen, image, image_pos):
    # 이미지가 정상적으로 로드되었으면
    if image is not None:
        # 화면에 이미지 출력
        screen.blit(image, image_pos)

# ----------------------- 배터리 위젯 출력 함수 선언 -----------------------
def draw_battery_widget(screen, battery_text, percent_font):
    # 배터리 잔량 숫자 계산
    battery_percent = get_battery_percent_value(battery_text)
    # 배터리 채움 색상 계산
    fill_color = get_battery_color(battery_percent)
    # 배터리 바디 크기 계산
    body_x = BATTERY_POS[0] + 25
    body_y = BATTERY_POS[1] + 20
    body_w = 110
    body_h = 195
    # 배터리 머리 부분 크기 계산
    cap_w = 38
    cap_h = 16
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
        fill_h
    )
    # 배터리 내부 바탕 출력
    pygame.draw.rect(
        screen,
        WHITE,
        pygame.Rect(inner_x, inner_y, inner_w, inner_h),
        border_radius=10
    )
    # 잔량이 0보다 크면 채움 출력
    if fill_h > 0:
        pygame.draw.rect(
            screen,
            fill_color,
            fill_rect,
            border_radius=10
        )
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
        center_pos=(body_x + body_w // 2, body_y + body_h // 2)
    )


# ----------------------- AMR UI 제어 클래스 선언 -----------------------
class AMR_UI_Controller:
    # 클래스 초기화 함수 선언
    def __init__(self):
        # SEER_commu 객체 저장 변수 선언
        self.seer = None
        # AMR 제어 명령 충돌 방지를 위한 Lock 선언
        self.ctrl_lock = threading.Lock()
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
        # 배터리 갱신 thread 시작
        self.start_battery_thread()

    # AMR 연결 함수 선언
    def connect_amr(self):
        # 에러가 없으면
        try:
            # SEER_commu 객체 생성
            self.seer = SEER_commu()
            # 연결 완료 출력
            print("[UI] SEER AMR 연결 완료")
        # 예외가 발생하면
        except Exception as e:
            # SEER_commu 객체 None 처리
            self.seer = None
            # 에러 출력
            print(f"[UI] SEER AMR 연결 실패 : {e}")

    # 배터리 thread 시작 함수 선언
    def start_battery_thread(self):
        # 배터리 갱신 thread 생성
        battery_thread = threading.Thread(target=self.battery_update_loop, daemon=True)
        # 배터리 갱신 thread 시작
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
                except Exception as e:
                    # 에러 출력
                    print(f"[UI] 배터리 정보 수신 실패 : {e}")
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
        # motion thread 생성
        motion_thread = threading.Thread(
            target=self.send_motion,
            args=(vx, w),
            daemon=True
        )
        # motion thread 시작
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
                    duration=MOTION_DURATION
                )
            # 디버그 출력
            print(f"[UI] motion_control vx={vx}, w={w}, duration={MOTION_DURATION}, success={success}")
        # 예외가 발생하면
        except Exception as e:
            # 에러 출력
            print(f"[UI] motion_control 실패 : {e}")

    # 정지 명령 비동기 송신 함수 선언
    def send_stop_async(self):
        # stop thread 생성
        stop_thread = threading.Thread(target=self.send_stop, daemon=True)
        # stop thread 시작
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
                    duration=STOP_DURATION
                )
            # 디버그 출력
            print(f"[UI] STOP 명령 송신 완료 duration={STOP_DURATION}, success={success}")
        # 예외가 발생하면
        except Exception as e:
            # 에러 출력
            print(f"[UI] STOP 명령 실패 : {e}")

    # relocation 명령 비동기 송신 함수 선언
    def send_relocation_async(self):
        # relocation thread 생성
        relocation_thread = threading.Thread(target=self.send_relocation, daemon=True)
        # relocation thread 시작
        relocation_thread.start()

    # relocation 명령 송신 함수 선언
    def send_relocation(self):
        # SEER_commu 객체가 없으면
        if self.seer is None:
            # 디버그 출력
            print("[UI] AMR이 연결되지 않아 Relocation 명령을 보낼 수 없습니다.")
            # 함수 종료
            return
        # 에러가 없으면
        try:
            # 제어 Lock 사용
            with self.ctrl_lock:
                # relocation 명령 송신
                success = self.seer.relocation()
            # 디버그 출력
            print(f"[UI] Relocation 명령 송신 완료 success={success}")
        # 예외가 발생하면
        except Exception as e:
            # 에러 출력
            print(f"[UI] Relocation 명령 실패 : {e}")

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
            except Exception as e:
                # 에러 출력
                print(f"[UI] SEER AMR 소켓 종료 실패 : {e}")


# ----------------------- 전체 UI 출력 함수 선언 -----------------------
def draw_ui(screen, title_font, battery_font, button_info_font, ui_images, amr_controller):
    # 전체 배경 파란색 출력
    screen.fill(BLUE)
    # 내부 흰색 영역 출력
    pygame.draw.rect(screen, WHITE, INNER_RECT)
    # 로고 이미지 출력
    draw_image(screen, ui_images["logo"], LOGO_POS)
    # 제목 출력
    draw_text(
        screen=screen,
        text="AMR Control PAD",
        font=title_font,
        color=BLACK,
        center_pos=TITLE_CENTER_POS
    )
    # 배터리 잔량 문자열 가져오기
    battery_text = amr_controller.get_battery_text()
    # 배터리 위젯 출력
    draw_battery_widget(screen, battery_text, battery_font)
    # 방향키 안내 문자열 딕셔너리 생성
    button_info_texts = make_button_info_texts()
    # 좌측 회전 버튼 출력
    draw_image(screen, ui_images["turn_left"], TURN_LEFT_POS)
    # 좌측 회전 안내 텍스트 출력
    draw_text(
        screen=screen,
        text=button_info_texts["TURN_LEFT"],
        font=button_info_font,
        color=BLACK,
        center_pos=(
            TURN_LEFT_POS[0] + TURN_LEFT_SIZE[0] // 2,
            TURN_LEFT_POS[1] + TURN_LEFT_SIZE[1] + 28
        )
    )
    # 직진 버튼 출력
    draw_image(screen, ui_images["go_straight"], GO_STRAIGHT_POS)
    # 직진 안내 텍스트 출력
    draw_text(
        screen=screen,
        text=button_info_texts["GO_STRAIGHT"],
        font=button_info_font,
        color=BLACK,
        center_pos=(
            GO_STRAIGHT_POS[0] + GO_STRAIGHT_SIZE[0] // 2,
            GO_STRAIGHT_POS[1] + GO_STRAIGHT_SIZE[1] + 28
        )
    )
    # 우측 회전 버튼 출력
    draw_image(screen, ui_images["turn_right"], TURN_RIGHT_POS)
    # 우측 회전 안내 텍스트 출력
    draw_text(
        screen=screen,
        text=button_info_texts["TURN_RIGHT"],
        font=button_info_font,
        color=BLACK,
        center_pos=(
            TURN_RIGHT_POS[0] + TURN_RIGHT_SIZE[0] // 2,
            TURN_RIGHT_POS[1] + TURN_RIGHT_SIZE[1] + 28
        )
    )
    # 후진 버튼 출력
    draw_image(screen, ui_images["go_backward"], GO_BACKWARD_POS)
    # 후진 안내 텍스트 출력
    draw_text(
        screen=screen,
        text=button_info_texts["GO_BACKWARD"],
        font=button_info_font,
        color=BLACK,
        center_pos=(
            GO_BACKWARD_POS[0] + GO_BACKWARD_SIZE[0] // 2,
            GO_BACKWARD_POS[1] + GO_BACKWARD_SIZE[1] + 28
        )
    )
    # 정지 버튼 출력
    draw_image(screen, ui_images["stop"], STOP_BUTTON_POS)
    # Relocation 버튼 출력
    draw_image(screen, ui_images["relocation"], RELOCATION_BUTTON_POS)


# ----------------------- 마우스 클릭 처리 함수 선언 -----------------------
def handle_mouse_down(mouse_pos, button_rects, amr_controller):
    # 직진 버튼 클릭 시
    if button_rects["GO_STRAIGHT"].collidepoint(mouse_pos):
        # 직진 active motion 설정
        amr_controller.set_active_motion("GO_STRAIGHT")

    # 후진 버튼 클릭 시
    elif button_rects["GO_BACKWARD"].collidepoint(mouse_pos):
        # 후진 active motion 설정
        amr_controller.set_active_motion("GO_BACKWARD")

    # 좌측 회전 버튼 클릭 시
    elif button_rects["TURN_LEFT"].collidepoint(mouse_pos):
        # 좌측 회전 active motion 설정
        amr_controller.set_active_motion("TURN_LEFT")

    # 우측 회전 버튼 클릭 시
    elif button_rects["TURN_RIGHT"].collidepoint(mouse_pos):
        # 우측 회전 active motion 설정
        amr_controller.set_active_motion("TURN_RIGHT")

    # 정지 버튼 클릭 시
    elif button_rects["STOP"].collidepoint(mouse_pos):
        # active motion 해제
        amr_controller.active_motion = None
        # 즉시 정지 명령 송신
        amr_controller.send_stop_async()

    # Relocation 버튼 클릭 시
    elif button_rects["RELOCATION"].collidepoint(mouse_pos):
        # active motion 해제
        amr_controller.active_motion = None
        # 정지 명령 송신
        amr_controller.send_stop_async()
        # relocation 명령 송신
        amr_controller.send_relocation_async()


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
    # 제목 폰트 설정
    title_font = get_korean_font(62, bold=True)
    # 배터리 폰트 설정
    battery_font = get_korean_font(34, bold=True)
    # 방향키 안내 텍스트 폰트 설정
    button_info_font = get_korean_font(28, bold=True)
    # 전체 UI 이미지 로드
    ui_images = load_all_ui_images()
    # 버튼 클릭 영역 생성
    button_rects = make_button_rects()
    # AMR UI 제어 객체 생성
    amr_controller = AMR_UI_Controller()
    # 프로그램 실행 상태 변수 선언
    running = True
    # 메인 반복문 시작
    while running:
        # FPS 설정
        clock.tick(60)
        # active motion 처리
        amr_controller.process_active_motion()
        # ----------------------- 이벤트 처리 -----------------------
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
                handle_mouse_down(mouse_pos, button_rects, amr_controller)
            # 마우스 버튼을 뗐을 경우
            elif event.type == pygame.MOUSEBUTTONUP:
                # 누르고 있던 motion 명령 해제
                amr_controller.clear_active_motion()
        # ----------------------- 화면 그리기 -----------------------
        # 전체 UI 출력
        draw_ui(screen, title_font, battery_font, button_info_font, ui_images, amr_controller)
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