# json 활용을 위한 json 라이브러리 import
import json
# 패킷 구성을 위한 sturct 라이브러리 import
import struct

# ------------------------------ AMR 기능별 통신 포트 선언 -------------------
# AMR API 기본 포트 번호 선언
API_PORT_ROBOD = 19200
# AMR 상태 조회용 포트 번호 선언
API_PORT_STATE = 19204
# AMR 제어용 포트 번호 선언
API_PORT_CTRL = 19205
# AMR 작업 할당용 포트 번호 선언
API_PORT_TASK = 19206
# AMR 설정 포트 번호 선언
API_PORT_CONFIG = 19207
# AMR 커널 관련 포트 번호 선언
API_PORT_KERNEL = 19208
# 기타 기능 포트 번호 선언s
API_PORT_OTHER = 19210
# ---------------------------- AMR 상태 관련 메시지 타입 정의 -------------------
# 로봇 전체 상태 정보 요청(Request) 메시지 타입/번호 정의
robot_status_info_req = 1000
# # 로봇 실행 상태(run) 요청 메시지 타입/번호 정의
robot_status_run_req = 1002
# # 로봇 모드(mode) 요청 메시지 타입/번호 정의
robot_status_mode_req = 1003
# 로봇 위치(localization) 요청 메시지 타입/번호 정의
robot_status_loc_req = 1004
# 로봇 속도(speed) 요청 메시지 타입/번호 정의
robot_status_speed_req = 1005
# 로봇 구역(area) 요청 메시지 타입/번호 정의
robot_status_area_req = 1011
# 로봇 IO 상태 요청 메시지 타입/번호 정의
robot_status_io_req = 1013
# 로봇 작업(task) 상태 요청 메시지 타입/번호 정의
robot_status_task_req = 1020
# 로봇 상태 전체(all1) 요청 메시지 타입/번호 정의
robot_status_all1_req = 1100
# 로봇 알람(alarm) 응답(Response) 메시지 타입/번호 정의
robot_status_alarm_res = 1050
# 로봇 맵 상태 확인 요청 메시지 타입/번호 정의
robot_status_map_req = 1300
# 로봇 맵 스위칭 요청 메시지 타입/번호 정의
robot_control_loadmap_req = 2022
# 로봇 배터리 상태 요청 메시지 타입/번호 정의
robot_status_battery_req = 1007
# 로봇 block 상태 요청 메시지 타입/번호 정의
robot_status_block_req = 1006
# ----------------------------- AMR 제어 관련 메시지 타입 정의 -------------------
# 로봇 재로컬라이제이션 메시지 타입/번호 정의
robot_control_reloc_req = 2002
# 로봇 모션 제어 요청 메시지 타입/번호 정의
robot_control_motion_req = 2010
# ----------------------------- AMR 목표점 이동 관련 메시지 타입 정의 -----------------
robot_task_gotarget_req = 3051
# ----------------------------- AMR 데몬 파일 관련 메시지 타입 정의 -------------------
# 로봇 데몬 파일 목록(ls) 요청 메시지 타입/번호 정의
robot_daemon_ls_req = 5100
# 로봇 데몬 파일 복사(scp) 요청 메시지 타입/번호 정의
robot_daemon_scp_req = 5101
# 로봇 데몬 파일 삭제(rm) 요청 메시지 타입/번호 정의
robot_daemon_rm_req = 5102
# ----------------------------- AMR DO 관련 메시지 타입 정의 -------------------
# 로봇 기타 기능 중 DO 출력 설정(set do) 요청 메시지 타입/번호 정의
robot_other_setdo_req = 6001

# 패킷 헤더 설정
# 0x5A + Version + serierNum + jsonLen + reqNum + rsv
PACK_HEAD_FMT_STR = '!BBHLH6s'
# 6s에 들어갈 데이터 설정
PACK_RSV_DATA = b'\x00\x00\x00\x00\x00\x00'

# 요청 ID(reqId), 메시지 타입(msgTyp), JSON 메시지(msg)를 인자로 받아 전송용 바이너리 패킷으로 포장하는 함수 선언
def packMsg(reqId, msgTyp, msg={}):
    # Json 본문 길이를 0으로 초기화
    msgLen = 0
    # 인자로 받은 msg를 json 문자열로 변환
    jsonStr = json.dumps(msg)
    # msg가 빈 문자열이 아니라면
    if(msg != {}):
        # Json 본문 길이 업데이트
        msgLen = len(jsonStr)
    # 패킷 헤더를 포멧에 따라 rawMsg에 저장
    rawMsg = struct.pack(PACK_HEAD_FMT_STR, 0x5A, 1, reqId,
                         msgLen, msgTyp, PACK_RSV_DATA)
    # 메시지가 빈 문자열이 아니면
    if(msg != {}):
        # 패킷 헤더 뒤에 json 문자열을 ascii로 변환하여 붙이기
        rawMsg += bytearray(json.dumps(msg), 'ascii')
    # 제작한 패킷 return
    return rawMsg

# 수신한 데이터에서 헤더를 해석하여 JSON 길이와 요청 메시지 타입을 추출하는 함수 선언
def unpackHead(data):
    # PACK_HEAD_FMT_STR 패킷 헤더 양식을 기준으로 수신한 데이터를 unpack하여 result에 저장
    result = struct.unpack(PACK_HEAD_FMT_STR, data)
    # data 3번째 값을 jsonLen 변수에 저장
    jsonLen = result[3]
    # data 4번째 값을 reqNum 변수에 저장
    reqNum = result[4]
    # jsonLen. reqNum return
    return (jsonLen, reqNum)
