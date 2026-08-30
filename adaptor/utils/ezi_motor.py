# ---------------------------
# Example Usage: All Functions
# ---------------------------

# LIMIT MINUS : Gripper is opened / encoder value decresing means gripper is oppened ( Tray is unlocked )
# LIMIT PLUS : Gripper is closed / encoder value increasing means gripper is closed ( Tray is locked )

# from utils.ezi_motor import EziMotorClient

# client = EziMotorClient("10.8.8.2")
# await client.get_board_info()
# await client.get_motor_info()

# SET FUNCTION #
# await client.alarm_reset()
# await client.servo_enable(True)                         # True -> Servo ON, False -> Servo OFF
# await client.move_single_axis_abs_pos(-12000, 10000)    # position -12000 pulses, speed 10000 pps
# await client.goto_limit_plus(10000)          # speed 10000 pps, direction +Limit
# await client.goto_limit_minus(10000)          # speed 10000 pps, direction -Limit
# await client.goto_origin()
# await client.move_stop()
# await client.emergency_stop()
# await clent.clear_position()  # set actual encoder position to ZERO
# await client.close()

# GET FUNCTION #
# await client.get_actual_position()
# await client.get_axis_status()
# await client.is_origin_sensor_on()
# await client.is_origin_done()
# await client.is_in_position()
# await client.is_motion_done()
# await client.is_limit_plus_done()
# await client.is_limit_minus_done()
# await client.is_error_all()

# ---------------------------
# Example Usage 1:  Close By Limit Sensor 
# ---------------------------
# client = EziMotorClient("10.8.8.2")
# await client.goto_limit_minus(10000)          # speed 10000 pps, direction +Limit # LIMIT MINUS : Gripper is opened 
# await client.goto_limit_plus(10000)          # speed 10000 pps, direction -Limit # LIMIT PLUS : Gripper is closed


# ---------------------------
# Example Usage 2: Initialize Open Close By Encoder Position 
# ---------------------------
# client = EziMotorClient("10.8.8.2")
# await client.alarm_reset()
# await client.servo_enable(True)
# result = await client.initialized_open_close_encoder_position(origin_encoder_offset = 1000)  # origin_encoder_offset is at config.toml
# print(f"Encoder Position Plus: {result['open_gripper']}")
# print(f"Encoder Position Minus: {result['close_gripper']}")



import socket
import struct
import asyncio

class EziMotorClient:
    HEADER = 0xAA
    RESERVED = 0x00


# ---------------------------
# Axis Flags ( For Status Flags )
# ---------------------------
    AXIS_FLAGS = {
        "FFLAG_ERRORALL":        0x00000001,
        "FFLAG_HWPOSILMT":        0x00000002,
        "FFLAG_HWNEGALMT":        0x00000004,
        "FFLAG_SWPOGILMT":        0x00000008,
        "FFLAG_SWNEGALMT":       0x00000010,
        "FFLAG_ERRPOSOVERFLOW":  0x00000080,
        "FFLAG_ERROVERCURRENT":  0x00000100,
        "FFLAG_ERROVERSPEED":    0x00000200,
        "FFLAG_ERRPOSTRACKING":  0x00000400,
        "FFLAG_ERROVERLOAD":     0x00000800,
        "FFLAG_ERROVERHEAT":     0x00001000,
        "FFLAG_ERRBACKEMF":      0x00002000,
        "FFLAG_ERRMOTORPOWER":   0x00004000,
        "FFLAG_ERRINPOSITION":   0x00008000,
        "FFLAG_EMGSTOP":         0x00010000,
        "FFLAG_SLOWSTOP":        0x00020000,
        "FFLAG_ORIGINRETURNING": 0x00040000,
        "FFLAG_INPOSITION":      0x00080000,
        "FFLAG_SERVOON":         0x00100000,
        "FFLAG_ALARMRESET":      0x00200000,
        "FFLAG_PTSTOPED":        0x00400000,
        "FFLAG_ORIGINSENSOR":    0x00800000,
        "FFLAG_ZPULSE":          0x01000000,
        "FFLAG_ORIGINRETOK":     0x02000000,
        "FFLAG_MOTIONDIR":       0x04000000,
        "FFLAG_MOTIONING":       0x08000000,
        "FFLAG_MOTIONPAUSE":     0x10000000,
        "FFLAG_MOTIONACCEL":     0x20000000,
        "FFLAG_MOTIONDECEL":     0x40000000,
        "FFLAG_MOTIONCONST":     0x80000000,
    }


    def __init__(self, ip, port=3002, timeout=2, poll_interval_sec: float = 0.1):
        self.addr = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self.sync_no = 0
        self._poll_interval = poll_interval_sec

    # ---------------------------
    # Frame Builder
    # ---------------------------
    def _build_frame(self, frame_type, data=b''):
        self.sync_no = (self.sync_no + 1) & 0xFF

        length = 3 + len(data)  # sync + reserved + frame_type + data

        frame = bytearray([
            self.HEADER,
            length,
            self.sync_no,
            self.RESERVED,
            frame_type
        ])
        frame.extend(data)

        return frame


    # ---------------------------
    # Send / Receive
    # ---------------------------
    def _send_blocking(self, frame_type, data=b''):
        frame = self._build_frame(frame_type, data)

        # print("[TX]", frame.hex())
        self.sock.sendto(frame, self.addr)

        try:
            resp, _ = self.sock.recvfrom(1024)
            # print("[RX]", resp.hex())
            return self._parse(resp)

        except socket.timeout:
            print("Timeout Motor")
            return None

    async def _send(self, frame_type, data=b''):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._send_blocking, frame_type, data
        )

    # ---------------------------
    # Frame Parser (SAFE)
    # ---------------------------
    def _parse(self, raw):
        if len(raw) < 6:
            raise ValueError("Response too short")

        if raw[0] != self.HEADER:
            raise ValueError("Invalid header")

        length = raw[1]
        frame_type = raw[4]
        status = raw[5]
        data = raw[6:]

        # ✅ Correct length calculation
        expected_len = 4 + len(data)

        if length != expected_len:
            print(f"⚠ Length mismatch: got {length}, expected {expected_len}")

        return {
            "frame_type": frame_type,
            "status": status,
            "data": data
        }

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _bits16(value):
        return [(value >> i) & 1 for i in range(16)]

    
    # ---------------------------
    # 0x01 - Get Board Info
    # ---------------------------
    async def get_board_info(self):
        resp = await self._send(0x01)

        if not resp or len(resp["data"]) < 1:
            return None

        d = resp["data"]

        slave_type = d[0]
        text = d[1:].split(b'\x00', 1)[0].decode(errors='ignore')

        return {
            "status": resp["status"],
            "slave_type": slave_type,
            "description": text
        }

    # ---------------------------
    # 0x05 - Get Motor Info
    # ---------------------------
    async def get_motor_info(self):
        resp = await self._send(0x05)

        # Validate response
        if not resp or "data" not in resp:
            return None

        d = resp["data"]

        # Minimum response:
        # 1 byte = communication status
        # 1 byte = motor number
        if len(d) < 2:
            return None

        communication_status = d[0]
        motor_no = d[1]

        # Remaining bytes:
        # ASCII string terminated with NULL byte
        description = ""
        if len(d) > 2:
            description = d[2:].split(b'\x00', 1)[0].decode(
                "ascii",
                errors="ignore"
            )

        return {
            "status": resp["status"],               # transport/protocol status
            "communication_status": communication_status,
            "motor_no": motor_no,
            "description": description
        }

    # ---------------------------
    # 0x53 - FAS_GetActualPos
    # ---------------------------
    async def get_actual_position(self):
        resp = await self._send(0x53)

        if not resp or "data" not in resp:
            print("No response")
            return None

        if len(resp["data"]) < 4:
            print("Invalid actual position response:", resp)
            return None

        communication_status = resp["status"]

        position = struct.unpack(
            "<i",
            resp["data"][0:4]
        )[0]

        return {
            "communication_status": communication_status,
            "position": position
        }

    # ---------------------------
    # 0x40 - FAS_GetAxisStatus Motor Status
    # ---------------------------
    async def get_axis_status(self):
        resp = await self._send(0x40)

        if not resp:
            print("No response from motor")
            return None

        if "data" not in resp:
            print("No data field:", resp)
            return None

        if len(resp["data"]) < 4:
            print("Invalid data length:", len(resp["data"]), resp)
            return None

        communication_status = resp["status"]

        status_flags = struct.unpack("<I", resp["data"][0:4])[0]

        active_flags = {
            name: bool(status_flags & bit)
            for name, bit in self.AXIS_FLAGS.items()
        }

        return {
            "communication_status": communication_status,
            "status_flags": status_flags,
            "status_flags_hex": f"0x{status_flags:08X}",
            "active_flags": active_flags
        }

    
    # ---------------------------
    # 0x40 - helper functions for FAS_GetAxisStatus 
    # ---------------------------
    async def is_origin_done(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_ORIGINRETOK"]
        )

    async def is_origin_sensor_on(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_ORIGINSENSOR"]
        )

    async def is_in_position(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_INPOSITION"]
        )

    async def is_motion_done(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_MOTIONING"]
        )

    async def is_limit_plus_done(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_HWPOSILMT"]
        )

    async def is_limit_minus_done(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_HWNEGALMT"]
        )

    async def is_error_all(self):
        s = await self.get_axis_status()
        return bool(
            s and
            s["active_flags"]["FFLAG_ERRORALL"]
        )


    # ---------------------------
    # 0x2B - FAS_ServoAlarmReset
    # ---------------------------
    async def alarm_reset(self):
        """
        Reset servo alarm.

        Command:
            0x2B - FAS_ServoAlarmReset

        Returns:
            dict | None
        """

        resp = await self._send(0x2B)

        # ACK frame: communication status is the status byte, data is empty.
        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"]
        }


    # ---------------------------
    # 0x2A - FAS_ServoEnable
    # ---------------------------
    async def servo_enable(self, enable=True):
        """
        Enable or disable servo.

        Args:
            enable (bool): 
                True  -> Servo ON
                False -> Servo OFF

        Returns:
            dict | None
        """

        # 0 = OFF, 1 = ON
        value = b'\x01' if enable else b'\x00'

        resp = await self._send(0x2A, value)

        # ACK frame: the communication status is the status byte (resp["status"]);
        # the data section is empty. Reading it from resp["data"][0] (and requiring
        # len(data) >= 1) made every ACK look like a no-response/timeout, so the
        # caller raised before the move was ever sent. Matches move_stop/clear_position.
        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"],
            "servo_enabled": enable
        }


    # ---------------------------
    # 0x34 - FAS_MoveSingleAxisAbsPos
    # ---------------------------
    async def move_single_axis_abs_pos(self, position, speed):
        # 4 bytes position + 4 bytes speed
        # little-endian signed int32
        payload = struct.pack("<ii", position, speed)

        resp = await self._send(0x34, payload)

        # ACK frame: communication status is the status byte, data is empty.
        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"],
            "position": position,
            "speed": speed
        }


    # ---------------------------
    # 0x36 - FAS_MoveToLimit
    # ---------------------------
    async def move_to_limit(self, speed=10000, direction=1):
        if direction not in (0, 1):
            raise ValueError("direction must be 0 (-Limit) or 1 (+Limit)")

        if speed < 0:
            raise ValueError("speed must be positive")

        # 4 bytes speed + 1 byte direction
        payload = struct.pack("<IB", speed, direction)

        resp = await self._send(0x36, payload)

        # ACK frame: communication status is the status byte, data is empty.
        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"],
            "speed": speed,
            "direction": direction
        }

    async def goto_limit_plus(self, speed=10000):
        return await self.move_to_limit(speed=speed, direction=1)

    async def goto_limit_minus(self, speed=10000):
        return await self.move_to_limit(speed=speed, direction=0)


    # ---------------------------
    # 0x33 - FAS_MoveOriginSingleAxis ( Move to Origin )
    # ---------------------------
    async def goto_origin(self):

        resp = await self._send(0x33)

        # ACK frame: communication status is the status byte, data is empty.
        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"]
        }


    # ---------------------------
    # 0x31 - FAS_MoveStop
    # ---------------------------
    async def move_stop(self):
        resp = await self._send(0x31)

        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"]
        }


    # ---------------------------
    # 0x32 - FAS_EmergencyStop
    # ---------------------------
    async def emergency_stop(self):
        resp = await self._send(0x32)

        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"]
        }

    # ---------------------------
    # 0x56 - FAS_ClearPosition
    # ---------------------------
    async def clear_position(self):
        resp = await self._send(0x56)

        if not resp:
            return None

        return {
            "status": resp["status"],
            "communication_status": resp["status"]
        }


    # ---------------------------
    # Custom Functions - Search Open Close Encoder Position
    # ---------------------------
    async def initialized_open_close_encoder_position(self, origin_encoder_offset = 1000):
            """
            Initialize motor position.
            
            - goto origin
            - wait for origin sensor on
            - moving is stopped
            - clear position
            - get actual position
            - calculate encoder position plus related to origin_encoder_offset
            - calculate encoder position minus related to origin_encoder_offset
            - return encoder position as "close_gripper" and "open_gripper"

            - if error in origin sensor, move to limit + and limit -, get actual position of limit sensors, and return limit + and limit - positions
            """


            encoder_position_plus = None
            encoder_position_minus = None



            await self.goto_origin()
            while not await self.is_origin_sensor_on():
                await asyncio.sleep(self._poll_interval)


            await self.move_stop()
            await self.clear_position()
            pos = await self.get_actual_position()
            print(f"Position after clear: {pos}")

            # if position is found, calculate encoder position plus and minus
            if pos:
                return {
                        "close_gripper": origin_encoder_offset,
                        "open_gripper": pos["position"]- origin_encoder_offset
                }

            else:
                # if no position because of error in origin sensor, return Limit + and Limit - sensors positions
                while not await self.is_limit_plus_done():
                    await asyncio.sleep(self._poll_interval)
                encoder_position_plus = await self.get_actual_position()

                while not await self.is_limit_minus_done():
                    await asyncio.sleep(self._poll_interval)
                encoder_position_minus = await self.get_actual_position()

                return {
                    "close_gripper": encoder_position_plus["position"],
                    "open_gripper": encoder_position_minus["position"]
                }

    

    # ---------------------------
    # Close
    # ---------------------------
    async def close(self):
        self.sock.close()


# ---------------------------
# Example Usage
# ---------------------------
async def main():

    # this is for testing purpose , to import config.config from config folder
    import sys
    from pathlib import Path
    import time

    adaptor_root = Path(__file__).resolve().parents[1]
    if str(adaptor_root) not in sys.path:
        sys.path.insert(0, str(adaptor_root))

    from config.config import get_config

    # Config data
    config_data = get_config()
    ezi_motor = config_data.ezi_config.ezi_motor


    client = EziMotorClient(ezi_motor)
    try:
        info = await client.get_board_info()
        if info:
            print("\n=== BOARD INFO 1 ===")
            print("Status      :", info["status"])
            print("Slave Type  :", info["slave_type"])
            print("Description :", info["description"])

        motor_info = await client.get_motor_info()
        if motor_info:
            print("\n=== MOTOR INFO ===")
            print("Status      :", motor_info["status"])
            print("Comm Status :", motor_info["communication_status"])
            print("Motor No    :", motor_info["motor_no"])
            print("Description :", motor_info["description"])

        await client.alarm_reset()
        await client.servo_enable(True)

        pos = await client.get_actual_position()
        print(f"position: {pos}")
        time.sleep(2)
        
        # Motor goes to limit +
        # await client.goto_limit_plus(speed=config_data.ezi_config.motor_speed)
        # while not await client.is_limit_plus_done():
        #     await asyncio.sleep(0.1)

        # pos = await client.get_actual_position()
        # print(f"Limit plus position: {pos}")

        # # Motor goes to limit +
        # await client.goto_limit_minus(speed=config_data.ezi_config.motor_speed)
        # while not await client.is_limit_minus_done():
        #     await asyncio.sleep(0.1)

        # pos = await client.get_actual_position()
        # print(f"Limit minus position: {pos}")
        # time.sleep(2)


        # Motor is opened and closed by origin sensor
        print(f"Start Searching origin")
        result = await client.initialized_open_close_encoder_position(origin_encoder_offset=config_data.ezi_config.origin_encoder_offset)
        print(f"Open Gripper: {result['open_gripper']}")
        print(f"Close Gripper: {result['close_gripper']}")
        time.sleep(1)

        await client.servo_enable(False)
        time.sleep(2)

        while True:

            await client.alarm_reset()
            await client.servo_enable(True)
            time.sleep(2)

            await client.move_single_axis_abs_pos(position=result["open_gripper"], speed=config_data.ezi_config.motor_speed)
            while not await client.is_in_position():
                await asyncio.sleep(0.1)
            print(f"Gripper is openned by origin")
            time.sleep(2)

            await client.move_single_axis_abs_pos(position=result["close_gripper"], speed=config_data.ezi_config.motor_speed)
            while not await client.is_in_position():
                await asyncio.sleep(0.1)
            print(f"Position Gripper is closed by origin")
            time.sleep(2)

            await client.servo_enable(False)
            time.sleep(2)


        
        # get axis status
        # while True:
        #     st = await client.get_axis_status()
        #     print(st)
        #     await asyncio.sleep(0.2)

    except KeyboardInterrupt:
        await client.emergency_stop()
        await client.close()
        print("\nStopped")

    finally:
        await client.emergency_stop()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
