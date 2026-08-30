"""Unit tests for EziMotorClient frame parsing.

Regression: clamp on the real robot reported "no response from EZI motor
(timeout)" for clampOn/clamp/unclamp even though tcpdump showed the drive
replying in ~0.4 ms. Root cause: an ACK reply (6 bytes: header, length, sync,
reserved, frame_type, comm_status) carries the communication status in the
status byte (raw[5] -> resp["status"]) with EMPTY data. servo_enable / move /
alarm_reset / goto_origin / move_to_limit read it from resp["data"][0] and
require len(data) >= 1, so they returned None on every ACK -> a fake timeout ->
the move command was never even sent. The already-correct siblings move_stop /
emergency_stop / clear_position read resp["status"]; these tests pin that
behaviour for the ACK-only commands too.
"""

import asyncio

from utils.ezi_motor import EziMotorClient


class _FakeAckSock:
    """Stand-in UDP socket that returns a 6-byte ACK (status byte, no data)."""

    def __init__(self, status=0):
        self.status = status
        self.sent = []

    def settimeout(self, _t):
        pass

    def sendto(self, frame, _addr):
        self.sent.append(bytes(frame))

    def recvfrom(self, _n):
        sent = self.sent[-1]
        sync_no, frame_type = sent[2], sent[4]
        # header, length=4, sync, reserved, frame_type, comm_status — data empty
        reply = bytes([EziMotorClient.HEADER, 0x04, sync_no, 0x00, frame_type, self.status])
        return reply, ("10.8.8.2", 3002)

    def close(self):
        pass


def _client(status=0):
    c = EziMotorClient("10.8.8.2")
    c.sock.close()
    c.sock = _FakeAckSock(status=status)
    return c


def test_servo_enable_parses_ack_status_byte():
    c = _client(status=0)
    res = asyncio.run(c.servo_enable(True))
    assert res is not None, "ACK reply (status byte, empty data) must not read as no-response"
    assert res["communication_status"] == 0


def test_move_single_axis_abs_pos_parses_ack_status_byte():
    c = _client(status=0)
    res = asyncio.run(c.move_single_axis_abs_pos(16000, 20000))
    assert res is not None, "move ACK must be parsed; otherwise the move is reported as a fake timeout"
    assert res["communication_status"] == 0
    # the move payload is 8 bytes (int32 pos + int32 speed) -> 13-byte frame on the wire
    assert len(c.sock.sent[-1]) == 13


def test_ack_commands_surface_nonzero_status_for_rejection():
    # A real drive rejection (non-zero comm status) must come through so
    # _raise_if_motor_rejected can fail the action instead of silently "finishing".
    for status in (3, 7):
        assert asyncio.run(_client(status=status).servo_enable(True))["communication_status"] == status
        assert asyncio.run(_client(status=status).move_single_axis_abs_pos(1, 1))["communication_status"] == status
