#!/usr/bin/env python3
"""Replay actual camera RTL packets and exercise shared-stream boundaries."""
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np

from camera_stream import CAMERA_MAGIC, RESPONSE_MAGIC, WireDecoder
from hardware_transport import Board

ROOT = Path(__file__).resolve().parents[1]


def frame(header, payload):
    data = header + payload
    return data + struct.pack("<I", zlib.crc32(data))


def collect(data, chunk):
    decoder, packets = WireDecoder(), []
    for offset in range(0, len(data), chunk):
        decoder.push(data[offset:offset + chunk])
        while (packet := decoder.pop()) is not None:
            packets.append(packet)
    return decoder, packets


def main():
    for name in ("native-stream-921k", "stream-test"):
        data = (ROOT / "build/camera" / name / "packets.bin").read_bytes()
        for chunk in (1, 7, 1024, len(data)):
            decoder, packets = collect(data, chunk)
            images = [packet for packet in packets if packet.command == 1]
            assert len(images) == 4 and decoder.crc_errors == decoder.framing_errors == 0
            assert [packet.camera for packet in images] == [0, 1, 0, 1]
            for packet in images:
                image, info = packet.image()
                assert image.shape == (16, 32, 3) and info["estimated_frame_age_ms"] > 0
                if packet.format == 5:
                    reference = cv2.imread(str(ROOT / "build/camera" / name / f"jpeg{packet.camera}.jpg"))
                    np.testing.assert_array_equal(image, reference)
    # JPEG comments can contain the complete magic of a CPU response.
    _, jpeg = cv2.imencode(".jpg", np.full((16, 32, 3), 77, np.uint8))
    encoded = jpeg.tobytes()
    fake = frame(struct.pack("<4sIII", RESPONSE_MAGIC, 1, 900, 4), struct.pack("<I", 0))
    encoded = encoded[:2] + b"\xff\xfe" + struct.pack(">H", len(fake) + 2) + fake + encoded[2:]
    payload = struct.pack("<HHII", 32, 16, 0xfffffff0, 0x100) + encoded
    camera = frame(struct.pack("<4s4BII", CAMERA_MAGIC, 1, 0, 5, 7, 11, len(payload)), payload)
    reply = frame(struct.pack("<4sIII", RESPONSE_MAGIC, 3, 901, 8), struct.pack("<II", 0, 1234))
    decoder, packets = collect(b"noisePG" + camera + reply, 13)
    assert [packet.magic for packet in packets] == [CAMERA_MAGIC, RESPONSE_MAGIC]
    assert packets[0].image()[1]["estimated_frame_age_ms"] < 1000
    bad = bytearray(camera)
    bad[-1] ^= 64
    decoder, packets = collect(bytes(bad) + camera + reply, 37)
    assert decoder.crc_errors == 1 and len(packets) == 2
    oversized = struct.pack("<4s4BII", CAMERA_MAGIC, 1, 0, 5, 7, 12, 0x100001)
    decoder, packets = collect(oversized + reply, 5)
    assert decoder.framing_errors == 1 and len(packets) == 1
    board = object.__new__(Board)
    board.decoder = WireDecoder()
    board.decoder.push(camera + reply)
    seen = []
    board.camera_callback = seen.append
    assert board._response(3, 901, 1) == struct.pack("<I", 1234) and len(seen) == 1
    print("PASS shared UART: actual RTL JPEG/JPEG-LS/raw frames, arbitrary chunks, embedded reply magic, "
          "CRC recovery, payload limits, tick wrap and command demultiplexing")


if __name__ == "__main__":
    main()
