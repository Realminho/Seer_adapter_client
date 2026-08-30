# netprotocol에 정의된 함수/상수 import
from Robokit_TCP_API_py.netprotocol.rbkNetProtoEnums import *
# JSON 데이터 처리를 위한 json 라이브러리 import
import json
# TCP 통신을 위한 socket 라이브러리 import
import socket
# 패킷 헤더 처리를 위한 struct 라이브러리 import
import struct
# 시간 핸들링을 위한 time 라이브러리 import
import time
# ----------------------- 충전기 상태 API 설정 -----------------------
# 충전기 상태 조회 API 포트 선언
CHARGER_STATUS_PORT = 20204
# 충전기 상태 조회 Message Type 선언
CHARGER_STATUS_REQ = 1001
# ----------------------- 충전기 상태 수신 클래스 선언 -----------------------
class Charger_commu(object):
    # 클래스 초기화 함수 선언
    def __init__(self, ip="192.168.0.106", status_port=CHARGER_STATUS_PORT, timeout=5.0):
        # 충전기 IP 주소 저장
        self.charger_ip = ip
        # 충전기 상태 API 포트 저장
        self.status_port = status_port
        # 소켓 timeout 시간 저장
        self.timeout = timeout
        # 충전기 상태 소켓 객체 초기화
        self.status_socket = None
        # SEER/Robokit 계열 TCP API 헤더 포맷 저장
        self.header_format = "!BBHLH6s"
        # 마지막으로 수신한 충전기 상태 데이터 저장
        self.last_status_data = None

    # ----------------------- 충전기 상태 소켓 연결 함수 선언 -----------------------
    def charger_connect(self):
        # 이미 소켓이 연결되어 있으면
        if self.status_socket is not None:
            # True return
            return True
        # 에러가 없으면
        try:
            # TCP 소켓 생성
            self.status_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # 소켓 timeout 설정
            self.status_socket.settimeout(self.timeout)

            # 충전기 상태 API 포트 연결
            self.status_socket.connect((self.charger_ip, self.status_port))

            # 연결 성공 로그 출력
            print(f"[Charger] 연결 성공 : {self.charger_ip}:{self.status_port}")

            # True return
            return True

        # 에러 발생 시
        except Exception as error:
            # 연결 실패 로그 출력
            print(f"[Charger] 연결 실패 : {error}")

            # 소켓 종료
            self.socket_close()

            # False return
            return False

    # ----------------------- 지정 바이트 수만큼 수신하는 함수 선언 -----------------------
    def recv_exact(self, recv_size):
        # 수신 데이터 저장 변수 초기화
        recv_data = b""

        # 원하는 크기만큼 받을 때까지 반복
        while len(recv_data) < recv_size:
            # 남은 크기 계산
            remain_size = recv_size - len(recv_data)

            # 소켓에서 데이터 수신
            chunk = self.status_socket.recv(remain_size)

            # 수신 데이터가 없으면 연결 종료로 판단
            if not chunk:
                # None return
                return None

            # 수신 데이터 누적
            recv_data += chunk

        # 수신 데이터 return
        return recv_data

    # ----------------------- 충전기 상태 원본 JSON 수신 함수 선언 -----------------------
    def get_charger_status_raw(self):
        # 상태 소켓이 연결되어 있지 않으면
        if self.status_socket is None:
            # 충전기 상태 소켓 연결 시도
            if self.charger_connect() is not True:
                # 연결 실패 시 False return
                return False

        # 에러가 없으면
        try:
            # 충전기 상태 조회 요청 패킷 생성
            request_packet = packMsg(1, CHARGER_STATUS_REQ, {"simple": False})

            # 충전기 상태 조회 요청 송신
            self.status_socket.send(request_packet)

            # 응답 헤더 16바이트 수신
            header = self.recv_exact(16)

            # 헤더 수신 실패 시
            if header is None:
                # 로그 출력
                print("[Charger] header 수신 실패")

                # 소켓 종료
                self.socket_close()

                # False return
                return False

            # 헤더 길이가 16바이트보다 짧으면
            if len(header) < 16:
                # 로그 출력
                print("[Charger] pack head error")

                # False return
                return False

            # unpackHead 함수로 JSON body 길이 추출
            tmp = unpackHead(header)

            # unpackHead 결과가 tuple/list이면 첫 번째 값을 body 길이로 사용
            json_data_len = tmp[0] if isinstance(tmp, (list, tuple)) else tmp

            # body 길이가 0이면 빈 dict 처리
            if json_data_len <= 0:
                # 빈 dict 저장
                status_data = {}

                # 마지막 상태 데이터 저장
                self.last_status_data = status_data

                # 빈 dict return
                return status_data

            # JSON body 수신
            body = self.recv_exact(json_data_len)

            # body 수신 실패 시
            if body is None:
                # 로그 출력
                print("[Charger] body 수신 실패")

                # 소켓 종료
                self.socket_close()

                # False return
                return False

            # JSON body 파싱
            status_data = json.loads(body.decode("utf-8", errors="ignore"))

            # 마지막 상태 데이터 저장
            self.last_status_data = status_data

            # 원본 상태 데이터 return
            return status_data

        # timeout 발생 시
        except socket.timeout:
            # timeout 로그 출력
            print("[Charger] timeout occured!!")

            # None return
            return None

        # JSON 파싱 에러 발생 시
        except json.JSONDecodeError as error:
            # JSON 파싱 실패 로그 출력
            print(f"[Charger] JSON 파싱 실패 : {error}")

            # False return
            return False

        # 그 외 에러 발생 시
        except Exception as error:
            # 에러 로그 출력
            print(f"[Charger] 상태 조회 에러 : {error}")

            # 소켓 종료
            self.socket_close()

            # False return
            return False

    # ----------------------- 충전기 상태 정보 추출 함수 선언 -----------------------
    def get_charger_info(self):
        # 충전기 원본 상태 데이터 조회
        status_data = self.get_charger_status_raw()

        # 상태 데이터가 dict가 아니면
        if not isinstance(status_data, dict):
            # 그대로 return
            return status_data

        # 운전 상태 저장
        state = status_data.get("state", None)

        # output 데이터 저장
        output = status_data.get("output", {})

        # status 데이터 저장
        status = status_data.get("status", {})

        # alarms 데이터 저장
        alarms = status_data.get("alarms", {})

        # 출력 전류 저장
        output_current = output.get("current", None)

        # 출력 전압 저장
        output_voltage = output.get("voltage", None)

        # 최대 충전 전류 저장
        max_current = output.get("max_current", None)

        # 최대 충전 전압 저장
        max_voltage = output.get("max_voltage", None)

        # 충전 threshold 저장
        auto_full = output.get("auto_full", None)

        # 수동 모드 여부 저장
        is_manual = status.get("is_manual", None)

        # 자동 모드 여부 저장
        is_auto = status.get("is_auto", None)

        # 충전 시간 저장
        charging_time_min = status.get("time_t", None)

        # 충전기 온도 저장
        charger_temp = status.get("temp", None)

        # 충전 상태 저장
        charging_state = status.get("charging", None)

        # 전극 위치 저장
        sheet_position = status.get("sheet_position", None)

        # 전극 전진 DI 감지 여부 저장
        is_push_di_triggered = status.get("is_push_di_triggered", None)

        # 전극 후진 DI 감지 여부 저장
        is_back_di_triggered = status.get("is_back_di_triggered", None)

        # 비상정지 여부 저장
        is_emc = status.get("is_emc", None)

        # 충전기 내부 상태 머신 번호 저장
        state_machine = status.get("state_machine", None)

        # 전극 감지 전압 저장
        adc_battery_voltage = status.get("adc_battery_voltage", None)

        # 에러 리스트 저장
        errors = alarms.get("errors", [])

        # 충전기 정보 출력
        print("--------------- Charger Info ---------------")
        print(f"state                  : {state}")
        print(f"output_current[A]      : {output_current}")
        print(f"output_voltage[V]      : {output_voltage}")
        print(f"max_current[A]         : {max_current}")
        print(f"max_voltage[V]         : {max_voltage}")
        print(f"auto_full              : {auto_full}")
        print(f"is_manual              : {is_manual}")
        print(f"is_auto                : {is_auto}")
        print(f"charging_time[min]     : {charging_time_min}")
        print(f"charger_temp           : {charger_temp}")
        print(f"charging_state         : {charging_state}")
        print(f"sheet_position[mm]     : {sheet_position}")
        print(f"is_push_di_triggered   : {is_push_di_triggered}")
        print(f"is_back_di_triggered   : {is_back_di_triggered}")
        print(f"is_emc                 : {is_emc}")
        print(f"state_machine          : {state_machine}")
        print(f"adc_battery_voltage[V] : {adc_battery_voltage}")
        print(f"errors                 : {errors}")
        print("--------------------------------------------")

        # 필요한 정보 return
        return (
            state,
            output_current,
            output_voltage,
            max_current,
            max_voltage,
            auto_full,
            is_manual,
            is_auto,
            charging_time_min,
            charger_temp,
            charging_state,
            sheet_position,
            is_push_di_triggered,
            is_back_di_triggered,
            is_emc,
            state_machine,
            adc_battery_voltage,
            errors
        )

    # ----------------------- 마지막 수신 데이터 return 함수 선언 -----------------------
    def get_last_status_data(self):
        # 마지막 수신 데이터 return
        return self.last_status_data

    # ----------------------- 소켓 종료 함수 선언 -----------------------
    def socket_close(self):
        # 상태 소켓이 있으면
        if self.status_socket is not None:
            # 에러가 발생해도 종료되도록 try
            try:
                # 상태 소켓 종료
                self.status_socket.close()
            except Exception:
                pass

        # 상태 소켓 None 처리
        self.status_socket = None

        # 종료 로그 출력
        print("[Charger] 소켓 종료 완료")