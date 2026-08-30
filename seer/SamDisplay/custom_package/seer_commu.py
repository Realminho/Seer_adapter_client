# netprotocol에 정의된 상수 import
from Robokit_TCP_API_py.netprotocol.rbkNetProtoEnums import *
# json 활용을 위한 json 라이브러리 import
import json
# TCP 통신을 위한 socket 라이브러리 import
import socket
# os 핸들링을 위한 os 라이브러리 import
import os
#  패킷 생성을 위한 struct 라이브러리 import
import struct
# 시간 핸들링을 위한 time 라이브러리 import
import time
# 시스템 핸들링을 위한 sys 라이브러리 import
import sys
# 수학적 연산을 위한 math 라이브러리 import
import math 

# SEER AMR 제어를 위한 함수를 선언할 SEER_commu 클래스 import
class SEER_commu(object):
    # 클래스 초기화 함수 init 선언
    def __init__(self):
        # AMR IP 선언
        self.seer_ip = '192.168.192.5'
        # SEER 보드와 통신할 때 사용하는 헤더 패킹 포맷 문자열(네트워크 바이트 오더)
        self.seer_header = '!BBHLH6s'
        # ------------------------ 상태 확인 소켓 객체 선언 ----------------------
        # 상태 확인을 위한 통신 객체 선언
        self.state_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 상태 확인 소켓 통신 타임아웃을 3초로 설정
        self.state_socket.settimeout(3)
        # 상태 확인 소켓 통신 연결 진행
        self.state_socket.connect((self.seer_ip, API_PORT_STATE))
        # ------------------------ 작업 명령 소켓 객체 선언 ----------------------
        # 작업 명령 송신을 위한 통신 객체 선언
        self.task_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 작업 명령 송신을 위한 소켓 통신 타임아웃을 3초로 설정
        self.task_socket.settimeout(3)
        # 작업 명령 송신을 위한 소켓 통신 연결 진행
        self.task_socket.connect((self.seer_ip, API_PORT_TASK))
        # ------------------------- 제어 명령 소켓 객체 선언 ---------------------
        # 제어 명령 송신을 위한 통신 객체 선언
        self.ctrl_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 제어 명령 송신을 위한 소켓 통신 타임아웃을 3초로 설정
        self.ctrl_socket.settimeout(3)
        # 제어 명령 송신을 위한 소켓 통신 연결 진행
        self.ctrl_socket.connect((self.seer_ip, API_PORT_CTRL))
        # ------------------------ DO 관련 소켓 객체 선언 -----------------------
        # DO 관련 명령 송신을 위한 통신 객체 선언
        self.do_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # DO 관련 명령 송신을 위한 소켓 통신 타임아웃을 3초로 설정
        self.do_socket.settimeout(3)
        # DO 관련 명령 송신을 위한 소켓 통신 연결 진행
        self.do_socket.connect((self.seer_ip, API_PORT_OTHER))

    # 인자로 받은 landmark로 이동 명령을 보내는 함수 정의
    def gotarget(self, landmark):
        # 작업 명령 송신 소켓으로 인자로 받은 랜드마크로 이동 명령 전송
        self.task_socket.send(packMsg(1, robot_task_gotarget_req, {"id": landmark}))

    # 현재 task_status를 return 
    def get_task_status(self):
        # 상태 소켓으로 현재 task 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_task_req, {}))
        try:
            # 응답 헤더 16바이트 수신
            data = self.state_socket.recv(16)
        except socket.timeout:
            # timeout이면 None 반환
            return None
        # 헤더 길이가 16보다 짧으면 오류
        if len(data) < 16:
            print("pack head error")
            return None
        # 헤더 파싱하여 json body 길이 추출
        tmp = unpackHead(data)
        jsonDataLen = tmp[0] if isinstance(tmp, (list, tuple)) else tmp
        # 실제 JSON 바디 수신
        body = b''
        readSize = 1024
        while jsonDataLen > 0:
            if jsonDataLen < readSize:
                readSize = jsonDataLen
            chunk = self.state_socket.recv(readSize)
            if not chunk:
                return None
            body += chunk
            jsonDataLen -= len(chunk)
        # JSON 파싱
        ret = json.loads(body)
        # 현재 task_status 반환
        return ret.get("task_status", None)

    # 현재 작업(task)이 완료되었는지 확인하는 함수 정의
    def task_end(self):
        # task_status 값에서 완료를 의미하는 상수 선언
        COMPLETED = 4
        # 상태 소켓으로 "robot_status_task_req" 요청을 전송(현재 task 상태 요청)
        self.state_socket.send(packMsg(1, robot_status_task_req, {}))
        try:
            # 응답 헤더 16바이트를 먼저 수신(프로토콜 헤더 크기로 가정)
            data = self.state_socket.recv(16)
        except socket.timeout:
            # 타임아웃이면 아직 응답이 없다고 보고 False 반환
            return False
        # JSON 데이터 길이 저장용 변수 초기화
        jsonDataLen = 0
        # 헤더 길이가 16바이트보다 작으면 프로토콜 오류로 판단
        if(len(data) < 16):
            # 헤더 오류 메시지 출력
            print('pack head error')
            # Windows 환경에서 pause로 콘솔 멈추기
            os.system('pause')
            # 상태 소켓 닫기
            self.state_socket.close()
            # 프로그램 종료
            quit()
        # 헤더길이가 정상이라면
        else:
            # 헤더를 unpackHead로 파싱하여 jsonDataLen 추출
            tmp = unpackHead(data)
            jsonDataLen = tmp[0] if isinstance(tmp, (list, tuple)) else tmp
        # 실제 JSON 바디를 jsonDataLen만큼 정확히 수신
        body = b''
        readSize = 1024
        while jsonDataLen > 0:
            if jsonDataLen < readSize:
                readSize = jsonDataLen
            chunk = self.state_socket.recv(readSize)
            if not chunk:
                return False
            body += chunk
            jsonDataLen -= len(chunk)
        # 수신한 JSON 문자열을 dict로 파싱
        ret = json.loads(body)
        # task_status가 COMPLETED인지 확인
        if ret.get('task_status') == COMPLETED:
            # 완료 상태면 True return
            return True
        # task_status가 COMPLETED가 아니면
        else:
            # False return
            return False

    def get_amr_blocked(self):
        # amr이 blocked 상태인지 확인 요청
        self.state_socket.send(packMsg(1, robot_status_block_req , {}))
        # 에러가 없으면
        try:
            # 응답 헤더 16바이트를 먼저 수신(프로토콜 헤더 크기로 가정)
            data = self.state_socket.recv(16)
        except socket.timeout:
            # 타임아웃이면 아직 응답이 없다고 보고 False 반환
            return False
        # JSON 데이터 길이 저장용 변수 초기화
        jsonDataLen = 0
        # 헤더 길이가 16바이트보다 작으면 프로토콜 오류로 판단
        if(len(data) < 16):
            # 헤더 오류 메시지 출력
            print('pack head error')
            # Windows 환경에서 pause로 콘솔 멈추기
            os.system('pause')
            # 상태 소켓 닫기
            self.state_socket.close()
            # 프로그램 종료
            quit()
        # 헤더길이가 정상이라면
        else:
            # 헤더를 unpackHead로 파싱하여 jsonDataLen 추출
            tmp = unpackHead(data)
            jsonDataLen = tmp[0] if isinstance(tmp, (list, tuple)) else tmp
        # 실제 JSON 바디를 jsonDataLen만큼 정확히 수신
        body = b''
        readSize = 1024
        while jsonDataLen > 0:
            if jsonDataLen < readSize:
                readSize = jsonDataLen
            chunk = self.state_socket.recv(readSize)
            if not chunk:
                return False
            body += chunk
            jsonDataLen -= len(chunk)
        # 수신한 JSON 문자열을 dict로 파싱
        ret = json.loads(body)
        # blocked 상태인지 저장
        blocked_flag = ret.get('blocked',None)
        # blocked 이유 저장
        blocked_reason = ret.get('block_reason',None)
        # 디버그 문구 print
        print(f"[BLOCK] blocked : {blocked_flag}, block_reason : {blocked_reason}")
        # block_flag, block_reason return
        return blocked_flag, blocked_reason

    # 목표 지점까지 이동 후, 도착할 때까지 블로킹(대기)하는 함수 정의
    def gotargetblock(self, point):
        # gotarget 호출(클래스 내부 self 사용 아님)
        self.gotarget(point)
        print(f"[Nav info] {point}로 주행을 시작합니다.")
        # 명령 전송 후 잠깐 대기
        time.sleep(0.5)
        # task_end()가 True가 될 때까지 반복 대기
        while self.task_end() is not True:
            # 0.5초 대기 반복
            time.sleep(0.5)

    # x,y 월드 좌표로 주행 요청을 송신하는 함수 정의
    def gopath(self,x,y,theta,back_mode=0):
        # 인자로 받은 theta radain으로 변경
        theta = math.radians(theta)
        # 내부코드에 전달할 인자 payload 설정
        script_args = {
            "x": float(x),
            "y": float(y),
            "theta":float(theta),
            "coordinate": "world",
            "backMode":int(back_mode)} 
        # Script 작업 payload 선언
        go_path_payload = {
            "script_name": "syspy/goPath.py",
            "script_args": script_args,
            "operation": "Script",
            "id": "SELF_POSITION",
            "source_id": "SELF_POSITION"}
        # 작업 명령 소켓으로 goPath.py 실행 요청 전송
        self.task_socket.send(packMsg(1, robot_task_gotarget_req, go_path_payload))

    # x,y 월드 좌표로 주행 요청을 송신하는 함수 정의
    def gopathblock(self,x,y,theta,back_mode=0):
        # 작업 명령 소켓으로 goPath.py 실행 요청 전송
        self.gopath(x,y,theta,back_mode)
        # 주행 정보 print
        print(f"[Nav info] x: {x},y: {y} 로 주행을 시작합니다.")
        # 명령 전송 후 잠깐 대기
        time.sleep(0.5)
        # task_end()가 True가 될 때까지 반복 대기
        while self.task_end() is not True:
            # 0.5초 대기 반복
            time.sleep(0.5)
        # 주행 정보 print
        print(f"[Nav info] x: {x},y: {y} 로 주행 완료.")

    # freenav로 주행하는 함수 선언
    def freenav(self,x,y,theta):
        # 인자로 받은 theta를 radian 값으로 전달
        theta = math.radians(theta)
        # free Nav payload 설정
        free_nav_payload = {
            "freeGo": {
                "theta": float(theta),
                "x": float(x),
                "y": float(y)
            },
            "id": "SELF_POSITION"}
        # 작업 명령 소켓으로 Free Navigation 주행 요청 전송
        self.task_socket.send(packMsg(1, robot_task_gotarget_req, free_nav_payload))

    # freenav로 주행하고 완료까지 대기하는 함수 선언
    def freenavblock(self,x,y,theta):
        # Freenav 함수를 사용해서 인자로 받은 x,y,theta로 주행
        self.freenav(x,y,theta)
        # 주행 정보 print
        print(f"[FreeNav] x: {x}, y: {y}, theta: {theta} 로 주행을 시작합니다.")
        # 명령 전송 후 0.5초 대기
        time.sleep(0.5)
        # task_end()가 True가 될 때까지 반복 대기
        while self.task_end() is not True:
            # 0.5초 대기 반복
            time.sleep(0.5)
        # 주행 완료 정보 print
        print(f"[FreeNav] x: {x}, y: {y}, theta: {theta} 로 주행 완료.")

    # DO 출력(디지털 출력)을 설정하는 함수 정의
    def setDO(self, do_id, flag):
        # DO 명령 송신 소켓으로 해당 DO id를 status로 ON/OFF
        self.do_socket.send(packMsg(1, robot_other_setdo_req, {"id": do_id, "status": flag}))

    # data에서 target_id에 해당하는 DI status를 찾아 반환하는 함수 정의
    def get_status_by_DI_id(self, data, target_id):
        # data에서 "DI" 리스트를 가져오고, 없으면 빈 리스트로 처리
        for entry in data.get("DI", []):
            # 각 entry의 "id"가 target_id와 같으면
            if entry.get("id") == target_id:
                # 해당 entry의 "status" 값 return
                return entry.get("status") 
        # target_id가 없으면 None return
        return None
    
    # data에서 target_id에 해당하는 DO status를 찾아 반환하는 함수 정의
    def get_status_by_DO_id(self, data, target_id):
        # data에서 "DO" 리스트를 가져오고, 없으면 빈 리스트로 처리
        for entry in data.get("DO", []):
            # 각 entry의 "id"가 target_id와 같으면
            if entry.get("id") == target_id:
                # 해당 entry의 "status" 값 return
                return entry.get("status") 
        # target_id가 없으면 None return
        return None
    
    # DI 값 읽어오는 함수 선언
    def read_DI(self, di_id):
        # 상태 소켓으로 DIO 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_io_req, {}))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            # False retrun
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024)
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON으로 데이터 변환
        payload = json.loads(data)
        # DI id에 해당하는 status 추출
        di_status = self.get_status_by_DI_id(payload, di_id)
        # 출력
        print(f"DI id={di_id}, status={di_status}")
        # 필요하면 호출 측에서 쓰게 반환도 해줌
        return di_status
    
    # DO 값 읽어오는 함수 선언
    def read_DO(self, do_id):
        # 상태 소켓으로 DIO 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_io_req, {}))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024)
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON 파싱
        payload = json.loads(data)
        # DO id에 해당하는 status 추출
        do_status = self.get_status_by_DO_id(payload, do_id)
        # 출력
        print(f"DO id={do_id}, status={do_status}")
        # 필요하면 호출 측에서 쓰게 반환도 해줌
        return do_status
    
    # 현재 네이버게이션 상태 확인 함수 선언
    def get_nav_info(self):
        # 상태 수신 소켓으로 네비게이션 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_task_req , {"simple": False}))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024)
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON으로 변환
        payload = json.loads(data)
        # 현재 목적지 저장
        current_target_node = payload.get("target_id", None)
        # 남은 목적지 저장
        remaining_node = payload.get("unfinished_path", None)
        # 현재 목적지 출력
        print(f"[AMR_INFO] 현재 타겟 노드 : {current_target_node}")
        # 남은 목적지 출력
        print(f"[AMR_INFO] 남은 노드 : {remaining_node}")
        return current_target_node, remaining_node
    
    # 현재 지도 정보 수신 후 출력하는 함수 선언
    def get_map_info(self):
        # 상태 요청 소켓으로 지도 정보 요청 전송
        self.state_socket.send(packMsg(1, robot_status_map_req ,{}))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024) 
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON으로 변환 (bytes → dict)
        payload = json.loads(data)
        # 현재 로드된 맵 이름 저장(없으면 None)
        current_map = payload.get("current_map", None)
        # map_files_info 저장(없으면 빈 리스트)
        map_files_info = payload.get("maps", [])
        # 현재 맵 이름 출력
        print(f"[AMR_INFO] 현재 맵 이름 : {current_map}")
        # 현재 맵 목록 출력
        print(f"[AMR_INFO] AMR에 저장된 맵 목록 : {map_files_info}")
        # 현재 맵 이름 및 맵 파일 정보 반환
        return current_map
    
    # 지도 스위칭 함수 선언
    def map_switch(self,map_name):
        # 제어 요청 소켓으로 지도 정보 요청 전송
        self.ctrl_socket.send(packMsg(1, robot_control_loadmap_req ,{"map_name": map_name}))

    # relocation 요청 함수 선언
    def relocation(self):
        # 제어 요청 소켓으로 relocation 요청 전송(현재 위치로 자동 진행))
        self.ctrl_socket.send(packMsg(1, robot_control_reloc_req ,{"isAuto": True,"x":0,"y":0,"angle":0}))
        # 응답 수신
        try:
            header = self.ctrl_socket.recv(16)
        except socket.timeout:
            print("[MAP_SWITCH] timeout occured!!")
            return False
        if len(header) < 16:
            print("[MAP_SWITCH] pack head error")
            return False
        rx_header = struct.unpack(self.seer_header, header)
        jsonDataLen = rx_header[3]
        data = b''
        while jsonDataLen > 0:
            chunk = self.ctrl_socket.recv(1024)
            if not chunk:
                print("[MAP_SWITCH] socket closed while receiving body")
                return False
            data += chunk
            jsonDataLen -= len(chunk)
        payload = json.loads(data)
        # 결과 출력
        ret_code = payload.get("ret_code", None)
        err_msg = payload.get("err_msg", "")
        print(f"[MAP_SWITCH] ret_code: {ret_code}, err_msg: {err_msg}")
        # 성공/실패 반환
        return (ret_code == 0)
    
    # motion관련 제어 요청 응답을 수신하는 함수 선언
    def ctrl_response_recv(self):
        # 에러가 없으면
        try:
            # 16바이트 응답 수신
            header = self.ctrl_socket.recv(16)
        # timeout이 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("[Motion] timeout occured!!")
            # retrun False, None
            return False, None
        # 헤더가 16byte보다 짧으면
        if len(header) < 16:
            # 디버그 문구 출력
            print("[Motion] pack head error")
            # retrun False, None
            return False, None
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.ctrl_socket.recv(1024) 
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("[Motion] socket closed while receiving body")
                # retrun False, None
                return False, None
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # json body 데이터가 있으면
        if len(data) > 0:
            # JSON으로 변환
            payload = json.loads(data)
        # body 데이터가 없으면
        else:
            # 빈 dict 생성
            payload = {}
        # 반환 코드 추출
        ret_code = payload.get("ret_code", 0)
        # 에러 메시지 추출
        err_msg = payload.get("err_msg", "")
        # motion 응답 결과 출력
        print(f"[MOTION] ret_code: {ret_code}, err_msg: {err_msg}")
        # 성공 여부와 payload return
        return (ret_code == 0), payload
    
    # 인자로 받은 설정값으로 AMR이 이동하거나 회전하도록 제어하는 함수 선언
    def motion_control(self,vx=0.0,vy=0.0,w=0.0,duration=0.1,steer=0.0,real_steer=0.0):
        # motion ctrl payload 구성
        motion_ctrl_payload = {
            "vx": float(vx),
            "vy": float(vy),
            "w": float(w),
            "steer": float(steer),
            "real_steer": float(real_steer),
            "duration": int(duration)}
        # 제어소켓으로 명령 전송
        self.ctrl_socket.send(packMsg(1, robot_control_motion_req ,motion_ctrl_payload))
        # AMR 응답 확인
        success,_ = self.ctrl_response_recv()
        # 성공 여부 return
        return success

    # 현재 배터리 상태 확인 함수 선언
    def get_battery_info(self):
        # 상태 수신 소켓으로 배터리 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_battery_req , {"simple": False}))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024)
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON으로 변환
        payload = json.loads(data)
        # 현재 배터리 레벨 저장
        current_battery_level = payload.get("battery_level",None)
        # 현재 배터리 온도 저장
        current_battery_temp = payload.get("battery_temp",None)
        # 현재 충전 상태 여부 저장
        current_battery_charging = payload.get("charging",None)
        # 현재 배터리 전압 저장
        current_battery_voltage = payload.get("voltage", None)
        # 현재 배터리 전류 저장
        current_battery_current = payload.get("current", None)
        # 배터리 최대 충전 전압 저장
        battery_max_charge_voltage = payload.get("max_charge_voltage", None)
        # 베터리 최대 충전 전류 저장
        battery_max_charge_current = payload.get("max_charge_voltage", None)
        # 현재 배터리 cycle 저장
        current_battery_cycle = payload.get("battery_cycle",None)
        # 현재 발생 에러 저장
        current_error = payload.get("err_msg",None)
        # 데이터 return
        return current_battery_level, current_battery_temp, current_battery_charging, current_battery_voltage, current_battery_current, battery_max_charge_voltage,  battery_max_charge_current , current_battery_cycle, current_error

    # 현재 amr 위치 수신 함수 선언
    def get_amr_cord(self):
        # 상태 수신 소켓으로 배터리 상태 요청 전송
        self.state_socket.send(packMsg(1, robot_status_loc_req))
        # 에러가 없으면
        try:
            # 응답에서 헤더(16바이트) 수신
            header = self.state_socket.recv(16)
        # timeout 에러가 발생하면
        except socket.timeout:
            # 디버그 문구 print
            print("timeout occured!!")
            # False retrun
            return False
        # 헤더 길이가 16bit보다 짧으면
        if len(header) < 16:
            # 디버그 문구 print
            print("pack head error")
            return False
        # 헤더 양식을 기준으로 헤더 추출
        rx_header = struct.unpack(self.seer_header, header)
        # json body 길이만 추출
        jsonDataLen = rx_header[3]
        # json body 저장 변수 선언
        data = b''
        # json body 길이까지
        while jsonDataLen > 0:
            # json body 수신하여 저장
            chunk = self.state_socket.recv(1024)
            # 데이터가 없으면
            if not chunk:
                # 디버그 문구 출력
                print("socket closed while receiving body")
                # False return
                return False
            # 읽은 데이터를 data에 저장
            data += chunk
            # 데이터 길이 -
            jsonDataLen -= len(chunk)
        # JSON으로 변환
        payload = json.loads(data)
        # 현재 AMR X 좌표 저장(World 좌표 기준)
        amr_x_cord = payload.get("x",None)
        # 현재 AMR Y 좌표 저장(World 좌표 기준)
        amr_y_cord = payload.get("y",None)
        # 현재 AMR theta 좌표 저장
        amr_theta = payload.get("angle",None)
        # AMR 좌표 Degree로 변환
        if amr_theta is not None:
            amr_theta = round(math.degrees(amr_theta), 2)
        # 데이터 return
        return amr_x_cord,amr_y_cord,amr_theta

    # 소켓 통신 종료 함수 선언
    def socket_close(self):
        # state_socket 닫기
        try:
            self.state_socket.close()
        except Exception:
            pass
        # task_socket 닫기
        try:
            self.task_socket.close()
        except Exception:
            pass
        # do_socket 닫기
        try:
            self.do_socket.close()
        except Exception:
            pass
        # ctrl_socekt 닫기
        try:
            self.ctrl_socket.close()
        except Exception:
            pass


    





