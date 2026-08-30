#  ----------- 범용 라이브러리 import ---------------------
# 시간 핸들링을 위해 time 라이브러리 import
import time
# ----------- custom 라이브러리 import ---------------------
# DIO모듈 제어를 위한 DIO_Commu 클래스 import
from custom_package.dio_commu import DIO_Commu
# -------------------- Ezi-IO Ethernet DIO 활용 PIO 프로토콜 클래스 선언 --------------------
class PIO_Commu:
    # 클래스 초기화 함수 선언
    def __init__(self,dio_ip="192.168.192.2",dio_port=2001):
        # dio 모듈 IP 설정
        self.dio_ip = dio_ip
        # dio 모듈 Port 설정
        self.dio_port = dio_port
        # dio 모듈 통신 timeout 설정(300초)
        self.dio_timeout = 300
        # di 확인 주기 설정(0.05초)
        self.dio_check_time = 0.05
        # pio 통신 응답 대기 최대 시간 설정(6000초)
        self.pio_max_wait_time = 6000
        # 제품 적재 대기 최대 시간 설정(6000초)
        self.transfer_max_wait_time = 6000
        # PIO 신호에 연결된 DIO 번호 선언
        # REQUEST → PIO 1번핀, DIO 16번핀이랑 연결
        self.request_pin = 0
        # Connect → PIO 2번핀, DIO 17번핀이랑 연결
        self.connect_pin = 1
        # RUN → PIO 3번핀, DIO 18번핀이랑 연결
        self.run_pin = 2
        # DIO_commu 객체 생성
        self.dio = DIO_Commu(ip=self.dio_ip,port=self.dio_port,timeout=self.dio_timeout)
        # DIO 모듈 연결 Falg 변수 선언
        self.dio_is_connected = False
    # --------------------- PIO와 연결된 DIO 연결 함수 선언 ---------------------
    def pio_connect(self):
        # DIO 모듈 TCP 연결
        self.dio.dio_connect()
        # DIO 연결 Flag True로 변경
        self.dio_is_connected = True
        # DIO 모듈 정보 확인
        dio_type, version = self.dio.get_dio_module_info()
        # 디버그 문구 print
        print(f"[PIO] PIO와 연결된 DIO 모듈 연결 완료 : {dio_type}{version} ")
    # --------------------- PIO와 연결된 DIO 연결 종료 함수 선언 -------------------
    def pio_close(self):
        # pio와 연결이 되어있으면
        if self.dio_is_connected:
            # 에러가 없으면
            try:
                # 모든 dio pin Low 처리
                self.all_low()
            # 에러가 발생하면
            except Exception as e:
                # 디버그 문구 print
                print(f"[PIO] 모든 DIO핀 LOW 상태로 제어 중 에러 발생 : {e}")
            # pio와 연결된 dio 모듈 연결 종료
            self.dio.dio_close()
            # DIO 연결 Flag, False로 변경
            self.dio_is_connected = False
    # --------------------- PIO와 연결된 DIO 연결 상태 확인 함수 선언 -------------------
    def check_dio_connected(self):
        # DIO 모듈과 연결되어있지 않으면
        if self.dio_is_connected is False:
            # 에러 발생
            raise ConnectionError("[PIO] DIO 모듈 연결 필요")
    # ------------- PIO와 연결된 DIO 모듈 DO핀 High 상태로 제어 함수 선언 ----------------
    def set_do_high(self,do_no,name=""):
        # dio 모듈 연결 상태 확인
        self.check_dio_connected()
        # 인자로 받은 do High 상태로 제어
        self.dio.set_do_on(do_no)
        # 디버그 문구 print
        print(f"[PIO] DIO 모듈 {do_no}핀 High로 제어 : {name}")
    # ------------- PIO와 연결된 DIO 모듈 DO핀 LOW 상태로 제어 함수 선언 ----------------
    def set_do_low(self,do_no,name=""):
        # dio 모듈 연결 상태 확인
        self.check_dio_connected()
        # 인자로 받은 do High 상태로 제어
        self.dio.set_do_off(do_no)
        # 디버그 문구 print
        print(f"[PIO] DIO 모듈 {do_no}핀 Low로 제어 : {name}")
    # ------------------- PIO와 연결된 DIO 모듈 DI핀 상태 읽기 함수 선언 -----------------
    def read_di(self,di_no,name=""):
        # dio 모듈 연결 상태 확인
        self.check_dio_connected()
        # di값 읽기
        di_state = self.dio.read_di(di_no)
        # 디버그 문구 print
        # print(f"[PIO] DIO 모듈 {di_no}핀 상태 : {di_state}")
        # 인자로 받은 di 상태 return
        return di_state
    # ------------------- 설비쪽과 통신 후 특정 DI가 HIGH가 될때까지 대기하는 함수 선언 -----------------
    def wait_di_high(self,di_no,name=""):
        # 함수 시작 시간 저장
        start_time = time.time()
        # 최대 대기 시간 동안 반복
        while time.time() - start_time < self.pio_max_wait_time:
            # DI 정보를 읽고 High이면
            if self.read_di(di_no) is True:
                # 디버그 문구 print
                print(f"[PIO] {di_no} High 상태 확인 : {name}")
                # True return
                return True
            # 확인 주기 만큼 대기
            time.sleep(self.dio_check_time)
        # 최대 대기 시간 동안 True가 return되지 않았다면 타임아웃 문구 출력
        print("[PIO] DI High 대기 시간 초과")
        # 모든 dio pin Low 처리
        self.all_low()
        # False return
        return False
    # ---------------------- 제품 loading 동작이 완료될때까지 대기하는 함수 선언 ----------------------------------
    def wait_loading_done(self,job_name=""):
        # 함수 시작 시간 저장
        start_time = time.time()
        # 최대 대기 시간 동안 반복
        while time.time() - start_time < self.transfer_max_wait_time:
            # ------------------------
            # 제품 loading 코드 작성 필요(모터 제어 등)
            # ------------------------
            # return True
        # 최대 대기 시간 동안 True가 return되지 않았다면 타임아웃 문구 출력
        print("[PIO] 제품 loading 동작 대기 시간 초과")
        # 모든 dio pin Low 처리
        self.all_low()
        # False return
        return False
    # ---------------------- 제품이 적재 될때까지 대기하는 함수 선언 ----------------------------------
    def wait_unloading_done(self,job_name=""):
        # 함수 시작 시간 저장
        start_time = time.time()
        # 최대 대기 시간 동안 반복
        while time.time() - start_time < self.transfer_max_wait_time:
            # ------------------------
            # 제품 unloading 코드 작성 필요(모터 제어 등)
            # ------------------------
            # return True
        # 최대 대기 시간 동안 True가 return되지 않았다면 타임아웃 문구 출력
        print("[PIO] 제품 적재 대기 시간 초과")
        # 모든 dio pin Low 처리
        self.all_low()
        # False return
        return False
    # ------------------- 전체 PIO와 연결된 모든 DO핀 Low로 설정 -----------------
    def all_low(self):
        # dio 모듈 연결 상태 확인
        self.check_dio_connected()
        # RUN DO Low로 제어 문구 print
        print("[PIO] RUN DO LOW")
        # PIO RUN DO Low로 제어
        self.dio.set_do_off(self.run_pin)
        # 0.1초 대기
        time.sleep(0.1)
        # Request DO Low로 제어 문구 print
        print("[PIO] REQUEST DO LOW")
        # PIO Request DO Low로 제어
        self.dio.set_do_off(self.request_pin)
        # 0.1초 대기
        time.sleep(0.1)
        # Connect DO Low로 제어 문구 print
        print("[PIO] Connect DO LOW")
        # PIO Connect DO Low로 제어
        self.dio.set_do_off(self.connect_pin)
        # 0.1초 대기
        time.sleep(0.1)
        # 시퀀스 종료 문구 print
        print("[PIO] PIO 통신에 사용되는 모든 DIO핀 LOW 처리 완료")
    # ----------------------- 상품 Loading 시 PIO 시퀀스 함수 선언 --------------------
    def pio_loading_sequence(self):
        # Loading sequence 시작 문구 print
        print("======== [PIO] Loading Sequence 시작 ===========")
        # 모든 dio 모듈 Low로 설정
        self.all_low()
        # 0.2초 대기
        time.sleep(0.2)
        # 1. Connect를 위해서 PIO 2번핀 High로 제어
        self.set_do_high(self.connect_pin,"설비 Connect")
        # 2. 설비쪽에서 PIO 2번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.connect_pin,"설비 쪽 Connect 확인"):
            # False return
            return False
        # 3. Request를 위해서 PIO 1번핀 High로 제어
        self.set_do_high(self.request_pin,"Request 요청")
        # 4. 설비쪽에서 PIO 1번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.request_pin,"설비 쪽 Request 승인 확인"):
            # False return
            return False
        # 5. 제품 이동(RUN)을 위해서 PIO 3번핀 High로 제어
        self.set_do_high(self.run_pin,"RUN 시작 송신")
        # 6. 설비쪽에서 PIO 3번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.run_pin,"설비 쪽 RUN 준비 완료 확인"):
            # False return
            return False
        # 7. Loading 동작 실행 후 실패 시 
        if not self.wait_loading_done("Loading 작업 실행"):
            # False return
            return False
        # 8. RUN 종료
        self.set_do_low(self.run_pin,"RUN 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # 9. Request 종료
        self.set_do_low(self.request_pin,"Request 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # 10. Connect 종료
        self.set_do_low(self.connect_pin,"Connect 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # Loading 시퀀스 종료 문구 print
        print("======== [PIO] Loading Sequence 종료 ===========")
        # True return
        return True
    # ----------------------- 상품 UnLoading 시 PIO 시퀀스 함수 선언 --------------------
    def pio_unloading_sequence(self):
        # UnLoading sequence 시작 문구 print
        print("======== [PIO] UnLoading Sequence 시작 ===========")
        # 모든 dio 모듈 Low로 설정
        self.all_low()
        # 0.2초 대기
        time.sleep(0.2)
        # 1. Connect를 위해서 PIO 2번핀 High로 제어
        self.set_do_high(self.connect_pin,"설비 Connect")
        # 2. 설비쪽에서 PIO 2번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.connect_pin,"설비 쪽 Connect 확인"):
            # False return
            return False
        # 3. 설비쪽에서 PIO 1번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.request_pin,"설비 쪽 Request 요청 확인"):
            # False return
            return False
        # 4. Request 응답을 위해서 PIO 1번핀 High로 제어
        self.set_do_high(self.request_pin,"Request 요청 응답")
        # 5. 설비쪽에서 PIO 3번핀을 High로 될때까지 대기 후 실패시
        if not self.wait_di_high(self.run_pin,"설비 쪽 RUN 준비 완료 확인"):
            # False return
            return False
        # 6. 제품 이동(RUN)을 위해서 PIO 3번핀 High로 제어
        self.set_do_high(self.run_pin,"RUN 시작 송신")
        # 7. UnLoading 동작 실행 후 실패 시 
        if not self.wait_unloading_done("UnLoading 작업 실행"):
            # False return
            return False
        # 8. RUN 종료
        self.set_do_low(self.run_pin,"RUN 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # 9. Request 종료
        self.set_do_low(self.request_pin,"Request 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # 10. Connect 종료
        self.set_do_low(self.connect_pin,"Connect 종료")
        # 0.1초 대기
        time.sleep(0.1)
        # Loading 시퀀스 종료 문구 print
        print("======== [PIO] UnLoading Sequence 종료 ===========")
        # True return
        return True        
    
        





