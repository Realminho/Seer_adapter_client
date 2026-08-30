# ACS 프로토콜 관련 클래스 선언
class ACS_Protocol:
    # 클래스 초기화 함수 선언
    def __init__(self,amr_id="444"):
        # 인자로 받은 id를 3자리 문자열로 저장
        # ex) 7 → 007
        self.amr_id = str(amr_id).zfill(3)
        # STX 설정(02)
        self.stx = b"\x02"
        # ETX 설정(03)
        self.etx = b"\x03"
    # ------------------------------------------------ 프레임 구성 요소 관련 함수 -------------------------------------------------------
    # bytes 문자열을 HEX 문자열로 변환하는 함수 선언
    def byte_to_hex_string(self,data):
        # 인자로 받은 data를 HEX 문자열로 변환 후 return
        return " ".join(f"{one_byte:02X}" for one_byte in data)
    # CRC-16/Modbus 계산 함수 선언
    def cal_crc16(self,data):
        # CRC-16 초기값 선언
        crc16_value=0xFFFF
        # 입력 데이터의 각 byte에 대해
        for one_byte in data:
            # 현재 byte를 CRC값과 XOR 연산 처리
            crc16_value ^= one_byte
            # 각 byte마다 8비트 반복 계산
            for _ in range(8):
                # 최하위 비트가 1이면 다항식 적용
                if crc16_value & 0x0001:
                    crc16_value = (crc16_value >>1) ^  0xA001
                # 최하위 비트가 나머지 값이면
                else:
                    crc16_value >>=1
        # CRC값을 16비트 범위로 제한
        crc16_value &= 0xFFFF
        # 계산된 CRC16 값 return
        return crc16_value
    # CRC-16 계산 결과 중 마지막 1자리를 Checksum 값으로 변환하는 함수 선언
    def cal_checksum(self,data):
        # CRC_16 정수값 계산
        crc_value = self.cal_crc16(data)
        # CRC_16 4자리를 대문자 HEX 문자열로 변환
        crc_hex = f"{crc_value:04X}"
        # 마지막 1글자만 체크섬으로 사용
        checksum_char = crc_hex[-1]
        # ASCII 1글자로 bytes 변환하여 return
        return checksum_char.encode("ascii")
    # body bytes를 인자로 받아 전체 프레임을 구성하는 함수 선언
    def make_frame(self,body_bytes):
        # body 기준으로 checksum 1글자 계산
        checksum_byte = self.cal_checksum(body_bytes)
        # STX + Body + Checksum + ETX 순서로 프레임 생성
        return self.stx + body_bytes + checksum_byte + self.etx
    # 수신한 프레임을 Body와 checksum으로 분리하는 함수 선언
    def split_frame(self,frame_data):
        # 인자로 받은 프레임 길이가 너무 짧으면
        if len(frame_data) < 4:
            # 디버그 문구 print
            print("[C Command] 프레임 길이가 너무 짧음")
        # 첫 바이트가 STX가 아니면
        if frame_data[:1] != self.stx:
            # 디버그 문구 print
            print("[C Command] STX 불일치")
        # 마지막 바이트가 ETX가 아니면
        if frame_data[-1:] != self.etx:
            # 디버그 문구 print
            print("[C Command] ETX 불일치")
        # STX와 ETX를 제외한 데이터 추출
        inner_data = frame_data[1:-1]
        # 추출한 데이터가 너무 짧으면
        if len(inner_data) < 2:
            # 디버그 문구 print
            print("[C Command] ETX 불일치")
        # 마지막 1byte를 checksum으로 분리
        recv_checksum = inner_data[-1:]
        # 그 앞부분을 Body로 분리
        recv_body = inner_data[:-1]
        # 분리한 body와 checksum return
        return recv_body, recv_checksum
    # checksum 검증 함수 선언
    def check_checksum(self,frame_data):
        # 인자로 받은 frame에서 Body와 Checksum으로 분리
        recv_body, recv_checksum = self.split_frame(frame_data)
        # 수신한 body 기준으로 checksum 계산
        cal_checksum = self.cal_checksum(recv_body)
        # 수신한 checksum과 계산한 checksum을 비교
        checksum_ok = (recv_checksum == cal_checksum)
        # 검증 결과 및 수신한 데이터, 계산한 checksum return
        return checksum_ok, recv_body, recv_checksum, cal_checksum
    # ------------------------------------------------ ACS 프레임 명령 관련 함수 -------------------------------------------------------
    # C명령 파싱 함수 선언
    def parse_c_command(self,frame_data):
        # 인자로 받은 frame checksum 검증 수행
        checksum_ok, recv_body, recv_checksum, cal_checksum = self.check_checksum(frame_data)
        # checksum 검증 실패 시 
        if not checksum_ok:
            # 디버그 문구 print
            print("[C Command] checksum 불일치")
            # 수신 checksum print
            print(f"[C Command] 수신한 checksum : {recv_checksum.decode('ascii', errors='ignore')}")
            # 계산한 checksum print
            print(f"[C Command] 계산한 checksum : {cal_checksum.decode('ascii', errors='ignore')}")
            # None return
            return None
        # 에러가 없으면
        try:
            # 수신한 body를 ascii 문자열로 변환
            body_frame = recv_body.decode("ascii")
        # 에러 발생 시 
        except Exception:
            # 디버그 문구 Print
            print("[C Command] Body frame ASCII 변환 중 에러 발생")
            # None return
            return None
        # body 프레임 4번째 글자가 A가 아니면
        if body_frame[3] != "A":
            # 디버그 문구 print
            print(f"[C Command] A 위치 오류 : {body_frame}")
            # None return
            return None
        # 5번째 문자가 C가 아니면
        if body_frame[4] != "C":
            # 디버그 문구 print
            print(f"[C Command] C 명령 위치 오류 : {body_frame}")
            # None return
            return None
        # AMR_ID 저장
        amr_id = body_frame[0:3]
        # 명령 코드 저장
        command_code = body_frame[4]
        # 목적지 번호 저장
        target_node = body_frame[5:9]
        # worktype 저장
        worktype = body_frame[9:11]
        # 파싱 결과 dict type으로 정리
        parse_data = {
            "amr_id": amr_id,
            "command_code": command_code,
            "target_node": target_node,
            "work_type":worktype}
        # 파싱 데이터 return
        return parse_data
   # C 명령 응답 프레임 구성 함수 선언
    def build_c_command_response_frame(self,target_node,work_type="00"):
        # 타겟 노드 4자리 문자열로 변환
        target_node = str(target_node).strip().zfill(4)
        # work_type 2자리 문자열로 변환
        work_type = str(work_type).strip().zfill(2)
        # 응답 Body frame 생성
        c_command_response_body_frame = f"A{self.amr_id}C{target_node}{work_type}"
        # 응답 Body frame ASCII로 변환
        c_command_response_body_bytes = c_command_response_body_frame.encode("ascii")
        # 전체 응답 프레임 구성
        c_command_response_frame = self.make_frame(c_command_response_body_bytes)
        # 전체 응답 프레임 return
        return c_command_response_frame
    # S 명령 프레임 구성 함수 선언
    def build_s_command_frame(self,target_node,carry_flag,move_flag):
        # 타겟 노드 4자리 문자열로 변환
        target_node = str(target_node).strip().zfill(4)
        # carry_flag 문자열로 변환
        carry_flag = str(carry_flag).strip()
        # move_flag 문자열로 변환
        move_flag = str(move_flag).strip()
        # S코드 Body frame 생성
        s_command_body_frame = f"A{self.amr_id}S{target_node}{carry_flag}{move_flag}"
        # S코드 Body frame ASCII로 변환
        s_command_body_frame = s_command_body_frame.encode("ascii")
        # 전체 S 명령 구성
        s_command_frame = self.make_frame(s_command_body_frame)
        # 전체 응답 프레임 return
        return s_command_frame
    # T 코드 프레임 구성 함수 선언
    def build_t_command_frame(self,location_node):
        # location_node 4자리 문자열로 변환
        location_node = str(location_node).strip().zfill(4)
        # T코드 Body frame 생성
        t_command_body_frame = f"A{self.amr_id}T00{location_node}"
        # T코드 Body frame ASCII로 변환
        t_command_body_frame = t_command_body_frame.encode("ascii")
        # 전체 T 명령 구성
        t_command_frame = self.make_frame(t_command_body_frame)
        # 전체 응답 프레임 return
        return t_command_frame
    # L 코드 프레임 구성 함수 선언
    def build_l_command_frame(self,loading_node):
        # loading_node 4자리 문자열로 변환
        loading_node = str(loading_node).strip().zfill(4)
        # L코드 Body frame 생성
        l_command_body_frame = f"A{self.amr_id}L{loading_node}00"
        # l코드 Body frame ASCII로 변환
        l_command_body_frame = l_command_body_frame.encode("ascii")
        # 전체 L 명령 구성
        l_command_frame = self.make_frame(l_command_body_frame)
        # 전체 응답 프레임 return
        return l_command_frame
    # U 코드 프레임 구성 함수 선언
    def build_u_command_frame(self,unloading_node):
        # unloading_node 4자리 문자열로 변환
        unloading_node = str(unloading_node).strip().zfill(4)
        # U코드 Body frame 생성
        u_command_body_frame = f"A{self.amr_id}U{unloading_node}00"
        # U코드 Body frame ASCII로 변환
        u_command_body_frame = u_command_body_frame.encode("ascii")
        # 전체 U 명령 구성
        u_command_frame = self.make_frame(u_command_body_frame)
        # 전체 응답 프레임 return
        return u_command_frame