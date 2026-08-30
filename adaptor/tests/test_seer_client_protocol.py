"""Tests for the SEER (Robokit) net-protocol frame codec.

SEER(Robokit) 네트워크 프로토콜 프레임 코덱 테스트.

The wire frame is a 16-byte header (struct ``!BBHLH6s``: sync 0x5A, version,
req_id, msg_len, msg_typ, 6-byte reserved) optionally followed by a JSON body of
``msg_len`` bytes. These tests pin pack/unpack against that layout, faithful to
the vendor SDK (seer/SamDisplay/Robokit_TCP_API_py/netprotocol/rbkNetProtoEnums.py)
but using compact JSON (matching jibot-client) and byte-accurate lengths.
와이어 프레임 = 16바이트 헤더(struct ``!BBHLH6s``) + (선택) ``msg_len`` 바이트 JSON
본문. 이 테스트들은 pack/unpack 을 그 레이아웃에 고정한다. 벤더 SDK 에 충실하되 컴팩트
JSON(jibot-client 일치) + 바이트 정확 길이를 사용.
"""

import json
import struct
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _src in ("amr-client-contract/src", "seer-client/src"):
    _p = str(REPO_ROOT / _src)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from seer_client.protocol import (  # noqa: E402
    HEADER_FMT,
    HEADER_SIZE,
    SYNC_BYTE,
    ApiNumber,
    pack_message,
    unpack_header,
)


class PackMessageTest(unittest.TestCase):
    def test_empty_body_is_16_byte_header_only(self):
        """A request with no body is exactly the 16-byte header, msg_len=0.

        본문 없는 요청은 정확히 16바이트 헤더, msg_len=0.
        """
        frame = pack_message(1, 1000)
        self.assertEqual(len(frame), HEADER_SIZE)
        sync, version, req_id, msg_len, msg_typ, rsv = struct.unpack(HEADER_FMT, frame)
        self.assertEqual(sync, SYNC_BYTE)
        self.assertEqual(version, 1)
        self.assertEqual(req_id, 1)
        self.assertEqual(msg_len, 0)
        self.assertEqual(msg_typ, 1000)
        self.assertEqual(rsv, b"\x00" * 6)

    def test_empty_dict_body_is_header_only(self):
        """An empty dict body is treated as "no body" (vendor parity).

        빈 dict 본문은 "본문 없음"으로 취급(벤더 동작 일치).
        """
        self.assertEqual(len(pack_message(1, 1000, {})), HEADER_SIZE)

    def test_body_appended_as_compact_json_with_matching_length(self):
        """A JSON body is appended compact (no spaces) and msg_len = its bytes.

        JSON 본문은 컴팩트(공백 없음)로 붙고 msg_len = 본문 바이트 길이.
        """
        frame = pack_message(7, 3051, {"id": "LM5"})
        header, body = frame[:HEADER_SIZE], frame[HEADER_SIZE:]
        self.assertEqual(body, b'{"id":"LM5"}')
        _, _, req_id, msg_len, msg_typ, _ = struct.unpack(HEADER_FMT, header)
        self.assertEqual(req_id, 7)
        self.assertEqual(msg_typ, 3051)
        self.assertEqual(msg_len, len(body))
        # Body round-trips back to the original mapping.
        self.assertEqual(json.loads(body), {"id": "LM5"})

    def test_msg_typ_accepts_intenum(self):
        """``msg_typ`` may be an ApiNumber IntEnum, packed as its int value.

        ``msg_typ``은 ApiNumber IntEnum 가능, 정수 값으로 패킹.
        """
        frame = pack_message(1, ApiNumber.STATUS_LOC)
        _, _, _, _, msg_typ, _ = struct.unpack(HEADER_FMT, frame)
        self.assertEqual(msg_typ, 1004)

    def test_multibyte_body_length_is_byte_accurate(self):
        """msg_len counts UTF-8 bytes, not characters (multibyte safe).

        msg_len 은 문자 수가 아니라 UTF-8 바이트 수(멀티바이트 안전).
        """
        frame = pack_message(1, 1300, {"map": "사무실"})
        header, body = frame[:HEADER_SIZE], frame[HEADER_SIZE:]
        _, _, _, msg_len, _, _ = struct.unpack(HEADER_FMT, header)
        self.assertEqual(msg_len, len(body))
        self.assertGreater(len(body), len('{"map":"사무실"}'))  # bytes > chars


class UnpackHeaderTest(unittest.TestCase):
    def test_roundtrips_packed_fields(self):
        """unpack_header returns (msg_len, req_id, msg_typ) as packed.

        unpack_header 는 패킹된 (msg_len, req_id, msg_typ)를 그대로 반환.
        """
        frame = pack_message(42, 1007, {"k": "v"})
        msg_len, req_id, msg_typ = unpack_header(frame)
        self.assertEqual(req_id, 42)
        self.assertEqual(msg_typ, 1007)
        self.assertEqual(msg_len, len(frame) - HEADER_SIZE)

    def test_empty_body_reports_zero_length(self):
        """A header-only frame reports msg_len == 0.

        헤더만 있는 프레임은 msg_len == 0.
        """
        msg_len, req_id, msg_typ = unpack_header(pack_message(3, 1100))
        self.assertEqual((msg_len, req_id, msg_typ), (0, 3, 1100))

    def test_rejects_bad_sync_byte(self):
        """A frame whose first byte is not 0x5A is a desync -> ValueError.

        첫 바이트가 0x5A 가 아니면 desync -> ValueError.
        """
        bad = struct.pack(HEADER_FMT, 0x00, 1, 1, 0, 1000, b"\x00" * 6)
        with self.assertRaises(ValueError):
            unpack_header(bad)

    def test_rejects_short_header(self):
        """Fewer than 16 bytes cannot be a header -> ValueError.

        16바이트 미만은 헤더가 될 수 없음 -> ValueError.
        """
        with self.assertRaises(ValueError):
            unpack_header(b"\x5a\x01\x00")


if __name__ == "__main__":
    unittest.main()
