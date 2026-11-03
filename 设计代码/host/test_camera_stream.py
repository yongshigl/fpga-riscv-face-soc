#!/usr/bin/env python3
"""Check the complete camera RTL transport against independent pixel and CRC references."""
import json
import struct
import subprocess
import zlib
from pathlib import Path

import imagecodecs
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "build/camera/stream-test"
    output.mkdir(parents=True, exist_ok=True)
    camera = ROOT / "board/pg2l200h_mini/camera_uart"
    sources = [camera / name for name in ("camera_uart_top.v", "camera_packet_tx.v", "camera_frame_encode.v",
                "dvp_rgb565.v", "frame_capture.v", "tb_camera_stream.v")]
    sources.extend([camera / "jls_encoder_pango.v", ROOT / "rtl/uart_tx.s"])
    subprocess.run(["iverilog", "-g2012", "-s", "tb_camera_stream", "-o", str(output / "stream.vvp"),
                    *map(str, sources)], check=True)
    subprocess.run(["vvp", "stream.vvp", "+output=packets.bin"], cwd=output, check=True)
    data = (output / "packets.bin").read_bytes()
    offset, counts = 0, [0, 0]
    frames = []
    while offset < len(data):
        magic, kind, sensor, fmt, flags, sequence, length = struct.unpack_from("<4s4BII", data, offset)
        assert magic == b"PGC2", "Framing error"
        end = offset + 16 + length
        assert zlib.crc32(data[offset:end]) == struct.unpack_from("<I", data, end)[0], "CRC error"
        payload = data[offset + 16:end]
        offset = end + 4
        if kind == 2:
            assert length == 40 and int.from_bytes(payload[32:36], "little") == 512
            continue
        assert kind == 1 and flags & 3 == 3
        width, height, capture, send = struct.unpack_from("<HHII", payload)
        assert (width, height) == (32, 16) and (send - capture) & 0xffffffff < 25000
        seed = (3 if counts[0] == 0 else 11) if sensor == 0 else (1 if counts[1] == 0 else 9)
        yy, xx = np.indices((32, 64))
        if sensor == 0:
            codes = np.full((32, 64, 3), [seed, seed + 10, seed + 14], dtype=np.int32)
        else:
            codes = np.stack([(yy + seed) & 31, (xx * 3 + yy * 5 + seed) & 63, (xx + seed) & 31], axis=2)
        expected = (codes.reshape(16, 2, 32, 2, 3).sum(axis=(1, 3)) + 2) // 4
        if fmt == 4:
            assert sensor == 0 and flags & 4, "Flat scene did not use compression"
            planes = imagecodecs.jpegls_decode(payload[12:])
            actual = np.stack(np.split(planes, 3, axis=0), axis=2)
            assert np.abs(actual.astype(np.int32) - expected).max() <= 1
        else:
            assert fmt == 3 and sensor == 1 and flags & 8, "Overflow did not use raw fallback"
            pixels = np.frombuffer(payload[12:], dtype="<u2").reshape(height, width)
            actual = np.stack([(pixels >> 11) & 31, (pixels >> 5) & 63, pixels & 31], axis=2)
            np.testing.assert_array_equal(actual, expected)
        counts[sensor] += 1
        frames.append({"camera": sensor, "sequence": sequence, "format": fmt, "bytes": length,
                       "capture_ticks": capture, "send_ticks": send})
    assert counts == [2, 2], counts
    (output / "summary.json").write_text(json.dumps(frames, indent=2) + "\n")
    print("PASS independent full-camera transport: CRC32, pixels, dimensions, timestamps, consecutive frames, raw fallback")


if __name__ == "__main__":
    main()
