#!/usr/bin/env python3
"""Verify captured JPEG bytes and real UART packets with independent codecs."""
import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/camera/native-stream-test")
    parser.add_argument("--overlap-capture", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    yy, xx = np.indices((16, 32))
    sources = [np.stack([(xx * 7) % 256, (yy * 15) % 256, ((xx + yy) * 11) % 256], axis=2).astype(np.uint8),
               cv2.resize(cv2.imread(str(ROOT / "vision/test_images/obama.jpg")), (32, 16))]
    encoded = []
    for index, source in enumerate(sources):
        success, image = cv2.imencode(".jpg", source, [cv2.IMWRITE_JPEG_QUALITY, 90])
        assert success
        data = image.tobytes()
        encoded.append(data)
        (output / f"jpeg{index}.jpg").write_bytes(data)
        (output / f"jpeg{index}.hex").write_text("".join(f"{value:02x}\n" for value in data), encoding="ascii")
    camera = ROOT / "board/pg2l200h_mini/camera_uart"
    rtl = [camera / name for name in ("jpeg_frame_capture.v", "camera_packet_tx.v", "camera_native_stream.v",
                                     "tb_native_camera_stream.v")]
    rtl.append(ROOT / "rtl/uart_tx.s")
    subprocess.run(["iverilog", "-g2012", "-s", "tb_native_camera_stream", "-o", str(output / "stream.vvp"),
                    f"-Ptb_native_camera_stream.OVERLAP_CAPTURE={int(args.overlap_capture)}",
                    *map(str, rtl)], check=True)
    subprocess.run(["vvp", "stream.vvp", "+output=packets.bin",
                    *(f"+jpeg{i}=jpeg{i}.hex" for i in range(2)),
                    *(f"+length{i}={len(encoded[i])}" for i in range(2))], cwd=output, check=True)
    data = (output / "packets.bin").read_bytes()
    offset, counts, frames, last_sequence = 0, [0, 0], [], [None, None]
    while offset < len(data):
        magic, kind, sensor, fmt, flags, sequence, length = struct.unpack_from("<4s4BII", data, offset)
        assert magic == b"PGC2" and fmt == 5 and sensor <= 1
        end = offset + 16 + length
        assert zlib.crc32(data[offset:end]) == struct.unpack_from("<I", data, end)[0]
        payload = data[offset+16:end]
        offset = end+4
        if kind == 2:
            assert length == 40 and payload[23] == 0xA4
            assert int.from_bytes(payload[32:36], "little") == len(encoded[sensor])
            continue
        assert kind == 1 and flags == 7 and sensor == len(frames) % 2
        width, height, capture, send = struct.unpack_from("<HHII", payload)
        assert (width, height) == (32, 16) and (send-capture) & 0xFFFFFFFF < 27000
        assert last_sequence[sensor] is None or sequence > last_sequence[sensor]
        assert payload[12:] == encoded[sensor], "JPEG bytes were altered by CDC or UART transport"
        image = cv2.imdecode(np.frombuffer(payload[12:], np.uint8), cv2.IMREAD_COLOR)
        reference = cv2.imdecode(np.frombuffer(encoded[sensor], np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(image, reference)
        last_sequence[sensor] = sequence
        counts[sensor] += 1
        frames.append({"camera": sensor, "sequence": sequence, "bytes": length,
                       "capture_ticks": capture, "send_ticks": send,
                       "jpeg_sha256": hashlib.sha256(payload[12:]).hexdigest()})
    assert counts == [2, 2] and offset == len(data)
    report = {"passed": True, "execution": "Icarus RTL with actual UART wire decoding; OpenCV JPEG and zlib CRC32",
              "baud": 921600, "frames": frames,
              "overlap_capture": args.overlap_capture,
              "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in rtl}}
    (output / "report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="ascii")
    print("PASS independent native JPEG transport: exact bytes, CRC32, dimensions, timestamps, fresh frames, pause/resume")


if __name__ == "__main__":
    main()
