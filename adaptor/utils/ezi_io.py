# Example Usage:
# from utils.ezi_io import EZIIOClient

# client = EZIIOClient("10.8.8.87")
# await client.connect()
# await client.get_board_info()

# await client.get_input()
# pin_value = await client.get_input_pin(0)

# await client.clear_all_outputs()
# await client.turn_on_output(0)
# await client.turn_off_output(0)
# output = await client.get_output(1)  # 1 is ON, 0 is OFF

# client.close()

import asyncio
import struct

class EZIIOClient:
    HEADER = 0xAA
    RESERVED = 0x00

    def __init__(self, ip, port=3002, timeout=2):
        self.addr = (ip, port)
        self.timeout = timeout
        self.sync_no = 0
        self.transport = None
        self.protocol = None
        self._send_lock = asyncio.Lock()
        self.last_error = ""

    # ---------------------------
    # Connect
    # ---------------------------
    async def connect(self):
        loop = asyncio.get_running_loop()

        self.protocol = UDPClientProtocol()

        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self.protocol,
            remote_addr=self.addr
        )

    # ---------------------------
    # Frame Builder
    # ---------------------------
    def _build_frame(self, frame_type, data=b""):
        self.sync_no = (self.sync_no + 1) & 0xFF

        length = 3 + len(data)

        frame = bytearray([
            self.HEADER,
            length,
            self.sync_no,
            self.RESERVED,
            frame_type
        ])

        frame.extend(data)
        return bytes(frame)

    
    # ---------------------------
    # Send
    # ---------------------------
    async def _send(self, frame_type, data=b""):
        async with self._send_lock:
            if not self.transport or not self.protocol:
                await self.connect()

            frame = self._build_frame(frame_type, data)

            self.protocol.prepare_response()
            self.transport.sendto(frame)

            try:
                raw = await asyncio.wait_for(
                    self.protocol.wait_response(),
                    timeout=self.timeout
                )
                self.last_error = ""
                return self._parse(raw)

            except asyncio.TimeoutError:
                self.last_error = (
                    f"timeout waiting for frame_type=0x{frame_type:02X} "
                    f"from {self.addr[0]}:{self.addr[1]} after {self.timeout}s"
                )
                self.close()
                return None

    
    # ---------------------------
    # Parse
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

    async def get_board_info(self):
        resp = await self._send(0x01)

        if not resp or len(resp["data"]) < 1:
            return None

        d = resp["data"]

        slave_type = d[0]
        text = d[1:].split(b"\x00", 1)[0].decode(errors="ignore")

        return {
            "status": resp["status"],
            "slave_type": slave_type,
            "description": text
        }

    
    # ---------------------------
    # 0xC0 - FAS_GetInput ( Get All inputs )
    # ---------------------------
    async def get_input(self):
        resp = await self._send(0xC0)

        if not resp:
            if not self.last_error:
                self.last_error = "no response for FAS_GetInput"
            return None

        if len(resp["data"]) != 8:
            self.last_error = (
                f"invalid FAS_GetInput payload length={len(resp['data'])}; expected=8"
            )
            return None

        input_val, latch_val = struct.unpack("<II", resp["data"])

        inputs = self._bits16(input_val)
        latches = self._bits16(latch_val)

        return {
            "comm_status": resp["status"],
            "input_raw": input_val,
            "latch_raw": latch_val,
            "inputs": inputs,
            "latches": latches
        }

    
    # ---------------------------
    # 0xC0 - FAS_GetInput ( Get Single input )
    # ---------------------------
    async def get_input_pin(self, pin):
        resp = await self._send(0xC0)

        if not resp or len(resp["data"]) != 8:
            return None

        input_val, _ = struct.unpack("<II", resp["data"])
        return (input_val >> pin) & 1


    # ---------------------------
    # 0xC5 - FAS_GetOutput
    # Get output / trigger run-stop status
    # ---------------------------
    async def get_output(self):
        resp = await self._send(0xC5)

        if not resp:
            return None

        if len(resp["data"]) < 8:
            print("Invalid get_output data:", resp["data"].hex())
            return None

        output_val, run_stop_val = struct.unpack("<II", resp["data"][:8])

        outputs = [
            (output_val >> (16 + i)) & 1
            for i in range(16)
        ]

        run_stop = [
            (run_stop_val >> i) & 1
            for i in range(16)
        ]

        return {
            "comm_status": resp["status"],
            "output_raw": output_val,
            "run_stop_raw": run_stop_val,
            "outputs": outputs,
            "run_stop": run_stop
        }

    # ---------------------------
    # 0xC5 - FAS_GetOutput
    # Get single output pin
    # ---------------------------
    async def get_output_pin(self, pin):
        if not 0 <= pin <= 15:
            raise ValueError("Output pin must be 0~15")

        resp = await self.get_output()

        if not resp:
            return None

        return resp["outputs"][pin]

    # ---------------------------
    # 0xC5 - FAS_GetOutput
    # Get single trigger Run/Stop pin
    # 0 = OFF / Trigger Stop
    # 1 = ON  / Trigger Run
    # ---------------------------
    async def get_run_stop_pin(self, pin):
        if not 0 <= pin <= 15:
            raise ValueError("Run/Stop pin must be 0~15")

        resp = await self.get_output()

        if not resp:
            return None

        return resp["run_stop"][pin]

    
    # ---------------------------
    # 0xC6 - FAS_SetOutput ( Set and Reset outputs )
    # ---------------------------
    async def set_output(self, set_mask=0, reset_mask=0):
        data = struct.pack("<II", set_mask, reset_mask)

        # print(f"[SET  ] {set_mask:032b}")
        # print(f"[RESET] {reset_mask:032b}")

        resp = await self._send(0xC6, data)

        if not resp:
            return None

        return resp["status"]

    @staticmethod
    def output_bit(n):
        # SetMask/ResetMask bit for output n. FAS_GetOutput reports output n at
        # bit (16 + n), so writes MUST use the same bit — otherwise a write hits
        # the neighbouring output and no read-back ever confirms it
        # (get_output_pin polling loops in elevator.py/airshower.py, and the
        # WebUi out badges, all depend on this symmetry).
        if not 0 <= n <= 15:
            raise ValueError("Output pin must be 0~15")
        return 1 << (16 + n)

    
    # ---------------------------
    # 0xC6 - FAS_SetOutput ( Clear all outputs )
    # ---------------------------
    async def clear_all_outputs(self):
        return await self.set_output(reset_mask=0xFFFF << 16)

    
    # ---------------------------
    # 0xC6 - FAS_SetOutput ( Turn on output )
    # ---------------------------
    async def turn_on_output(self, n):
        return await self.set_output(set_mask=self.output_bit(n))

    
    # ---------------------------
    # 0xC6 - FAS_SetOutput ( Turn off output )
    # ---------------------------
    async def turn_off_output(self, n):
        return await self.set_output(reset_mask=self.output_bit(n))

    def close(self):
        if self.transport:
            self.transport.close()
            self.transport = None
            self.protocol = None


class UDPClientProtocol(asyncio.DatagramProtocol):
    # ---------------------------
    # Init
    # ---------------------------
    def __init__(self):
        self.response_future = None

    def prepare_response(self):
        loop = asyncio.get_running_loop()
        self.response_future = loop.create_future()

    async def wait_response(self):
        return await self.response_future

    def datagram_received(self, data, addr):
        if self.response_future and not self.response_future.done():
            self.response_future.set_result(data)

    def error_received(self, exc):
        if self.response_future and not self.response_future.done():
            self.response_future.set_exception(exc)


def show_bits(label, bits):
    s = " ".join(str(b) for b in bits[:8])
    print(f"{label:<12}: {s}")

# ---------------------------
# Example Usage
# ---------------------------
async def main():
    client = EZIIOClient("10.8.8.87", port=3002, timeout=2)

    try:
        await client.connect()

        info = await client.get_board_info()

        if info:
            print("\n=== BOARD INFO ===")
            print("Status      :", info["status"])
            print("Slave Type  :", info["slave_type"])
            print("Description :", info["description"])

        await client.clear_all_outputs()

        await client.turn_on_output(0)

        await asyncio.sleep(3)

        await client.set_output(
            set_mask=client.output_bit(3)
            | client.output_bit(4)
            | client.output_bit(5),
            reset_mask=client.output_bit(0)
        )

        # while True:
        #     io = await client.get_input_pin(3)
        #     if io:
        #         print("Input pin 3 is on")
        #     else:
        #         print("Input pin 3 is off")
        #     await asyncio.sleep(0.5)

        # while True:
        #     io = await client.get_input()

        #     if io:
        #         print("\n=== INPUT STATUS ===")
        #         print("Raw Input :", hex(io["input_raw"]))
        #         print("Raw Latch :", hex(io["latch_raw"]))

        #         for i in range(16):
        #             print(
        #                 f"IN[{i:02}] : {io['inputs'][i]} "
        #                 f"| LATCH: {io['latches'][i]}"
        #             )

        #     await asyncio.sleep(0.5)

        while True:
            # output = await client.get_output()

            # if output:
            #     print("\n=== OUTPUT STATUS ===")
            #     print("Raw Output:", hex(output["output_raw"]))

            #     for i in range(16):
            #         print(
            #             f"OUT[{i:02}] : {output['outputs'][i]} "
            #             f"| RUN/STOP: {output['run_stop'][i]}"
            #         )
            
            # await asyncio.sleep(0.5)

            out0 = await client.get_output_pin(3)
            print(f"Output0: {out0}")
            await asyncio.sleep(0.5)




    except KeyboardInterrupt:
        print("\nStopped")

    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
