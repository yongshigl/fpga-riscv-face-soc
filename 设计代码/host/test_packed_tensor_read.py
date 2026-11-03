#!/usr/bin/env python3
"""Check packed-read negotiation/chunking and exact legacy fallback bytes."""
import struct
import unittest

from hardware_transport import Board


class MemoryBoard(Board):
    def __init__(self, enabled, compact, raw):
        self.enabled, self.compact, self.raw = enabled, compact, raw
        self.calls = []

    def capabilities(self):
        return {"packed_tensor_read": self.enabled}

    def command(self, op, payload):
        self.calls.append((op, payload))
        assert op == 14 and len(payload) == 32
        *_, offset, count = struct.unpack("<8I", payload)
        assert 0 < count <= 4092
        return self.compact[offset:offset+count]

    def download(self, address, length):
        self.calls.append((3, address, length))
        assert length == len(self.raw)
        return self.raw


class PackedReadTests(unittest.TestCase):
    def test_capability_and_legacy_have_identical_bytes(self):
        channels, height, width, row, plane = 5, 7, 123, 128, 1024
        raw = bytearray([239] * (channels * plane))
        compact = bytearray()
        for c in range(channels):
            for y in range(height):
                values = bytes((c*29+y*43+x*17)&255 for x in range(width))
                raw[c*plane+y*row:c*plane+y*row+width] = values
                compact.extend(values)
        tensor = {"shape": [1, channels, height, width], "address": 0x80000001,
                  "row_stride": row, "plane_stride": plane, "bytes": len(raw)}
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                board = MemoryBoard(enabled, bytes(compact), bytes(raw))
                self.assertEqual(board.download_tensor(tensor), bytes(compact))
                if enabled:
                    self.assertEqual([struct.unpack("<8I", call[1])[-2:] for call in board.calls],
                                     [(0, 4092), (4092, len(compact)-4092)])
                self.assertEqual(board.last_download_stats["logical_bytes"], len(compact))

    def test_bad_response_is_rejected(self):
        board = MemoryBoard(True, b"", b"\0"*32)
        tensor = {"shape": [1,1,1,1], "address": 0x80000000,
                  "row_stride": 32, "plane_stride": 32, "bytes": 32}
        with self.assertRaises(RuntimeError):
            board.download_tensor(tensor)

    def test_invalid_layout_does_not_send_a_request(self):
        tensor = {"shape": [1,1,1,2], "address": 0x80000000,
                  "row_stride": 32, "plane_stride": 32, "bytes": 32}
        for changes in ({"row_stride":1}, {"plane_stride":1}, {"bytes":1},
                        {"address":0xbfffffff}, {"shape":[2,1,1,2]}, {"shape":[1,0,1,2]}):
            board = MemoryBoard(True, b"xx", b"\0"*32)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                board.download_tensor({**tensor, **changes})
            self.assertEqual(board.calls, [])


if __name__ == "__main__":
    unittest.main()
