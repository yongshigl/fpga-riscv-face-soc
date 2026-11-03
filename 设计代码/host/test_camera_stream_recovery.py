#!/usr/bin/env python3
"""Check recovery when a UART camera packet loses bytes before another packet."""
import struct
import unittest
import zlib

from camera_stream import CAMERA_MAGIC, RESPONSE_MAGIC, WireDecoder


def packet(header, payload):
    body = header + payload
    return body + struct.pack("<I", zlib.crc32(body))


def camera(sequence):
    payload = struct.pack("<HHI", 3, 3, 0) + bytes(range(9))
    return packet(struct.pack("<4s4BII", CAMERA_MAGIC, 1, 0, 1, 0, sequence, len(payload)), payload)


def response(sequence):
    payload = struct.pack("<II", 0, 123)
    return packet(struct.pack("<4sIII", RESPONSE_MAGIC, 1, sequence, len(payload)), payload)


class CameraRecoveryTests(unittest.TestCase):
    def test_truncated_camera_preserves_following_cpu_response(self):
        decoder = WireDecoder()
        decoder.push(camera(1)[:-8] + response(2))
        received = decoder.pop()
        self.assertIsNotNone(received)
        self.assertEqual((received.magic, received.sequence, received.payload),
                         (RESPONSE_MAGIC, 2, struct.pack("<II", 0, 123)))
        self.assertEqual(decoder.crc_errors, 1)

    def test_truncated_camera_preserves_following_camera(self):
        decoder = WireDecoder()
        decoder.push(camera(1)[:-8] + camera(2))
        received = decoder.pop()
        self.assertIsNotNone(received)
        self.assertEqual((received.magic, received.sequence), (CAMERA_MAGIC, 2))
        self.assertEqual(decoder.crc_errors, 1)

    def test_full_corrupted_camera_preserves_following_camera(self):
        decoder = WireDecoder()
        broken = bytearray(camera(1))
        broken[24] ^= 1
        decoder.push(broken + camera(2))
        received = decoder.pop()
        self.assertEqual((received.magic, received.sequence), (CAMERA_MAGIC, 2))
        self.assertEqual(decoder.crc_errors, 1)

    def test_corrupted_cpu_response_still_raises_and_preserves_next_response(self):
        decoder = WireDecoder()
        broken = bytearray(response(1))
        broken[20] ^= 1
        decoder.push(broken + response(2))
        with self.assertRaisesRegex(ValueError, "RISC-V response CRC mismatch"):
            decoder.pop()
        received = decoder.pop()
        self.assertEqual((received.magic, received.sequence), (RESPONSE_MAGIC, 2))
        self.assertEqual(decoder.crc_errors, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
