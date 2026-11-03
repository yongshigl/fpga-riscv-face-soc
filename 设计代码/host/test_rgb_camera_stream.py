#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

import numpy as np

from camera_stream import CAMERA_MAGIC, WireDecoder


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overlap-capture", action="store_true")
    parser.add_argument("--downsample-bits", type=int, choices=(2, 3), default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    camera = ROOT / "board/pg2l200h_mini/camera_uart"
    sources = [camera / name for name in ("dvp_rgb565.v", "frame_capture.v", "camera_native_stream.v",
                                         "camera_packet_tx.v", "tb_rgb_camera_stream.v")]
    sources.append(ROOT / "rtl/uart_tx.s")
    subprocess.run(["iverilog", "-g2012", "-s", "tb_rgb_camera_stream", "-o", str(output / "test.vvp"),
                    f"-Ptb_rgb_camera_stream.OVERLAP_CAPTURE={int(args.overlap_capture)}",
                    f"-Ptb_rgb_camera_stream.DOWNSAMPLE_BITS={args.downsample_bits}",
                    *map(str, sources)], check=True)
    result = subprocess.run(["vvp", "test.vvp"], cwd=output, capture_output=True, text=True, timeout=120)
    (output / "simulation.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    data = (output / "packets.bin").read_bytes()
    offset, images, diagnostics = 0, [], []
    counts, sequences = [0, 0], [0, 0]
    preview_width, preview_height = 64 >> args.downsample_bits, 32 >> args.downsample_bits
    while offset < len(data):
        magic, kind, sensor, pixel_format, flags, sequence, length = struct.unpack_from("<4s4BII", data, offset)
        assert magic == CAMERA_MAGIC and sensor in (0, 1) and pixel_format == 3
        end = offset + 16 + length
        assert zlib.crc32(data[offset:end]) == struct.unpack_from("<I", data, end)[0]
        payload = data[offset + 16:end]
        if kind == 2:
            assert length == 40 and payload[23] == 0xa3
            diagnostics.append(sensor)
        else:
            assert kind == 1 and flags == 7 and sensor == len(images) % 2
            width, height, capture, send = struct.unpack_from("<HHII", payload)
            assert (width, height) == (preview_width, preview_height) and (send - capture) & 0xffffffff < 27000
            assert sequence > sequences[sensor]
            seed = ((3, 5) if counts[sensor] == 0 else (11, 13))[sensor]
            source_y, source_x = np.indices((preview_height, preview_width), dtype=np.uint16) * (1 << args.downsample_bits)
            red = np.full((preview_height, preview_width), seed, dtype=np.uint16)
            green = (source_x * 3 + source_y * 5) % 64
            blue = (source_x + source_y + seed) % 32
            expected = ((red << 11) | (green << 5) | blue).astype("<u2").tobytes()
            assert payload[12:] == expected, f"Camera {sensor} frame {counts[sensor]} differs from independent RGB565 reference"
            counts[sensor] += 1
            sequences[sensor] = sequence
            images.append({"camera": sensor, "sequence": sequence, "sha256": hashlib.sha256(expected).hexdigest()})
        offset = end + 4
    assert offset == len(data) and counts == [2, 2] and diagnostics == [0, 1, 0, 1]
    for chunk_bytes in (1, 7, 1024, len(data)):
        decoder, packets = WireDecoder(), []
        for offset in range(0, len(data), chunk_bytes):
            decoder.push(data[offset:offset + chunk_bytes])
            while (packet := decoder.pop()) is not None:
                packets.append(packet)
        assert decoder.crc_errors == decoder.framing_errors == 0 and len(packets) == 8
        for packet in packets:
            if packet.command == 2:
                assert packet.sensor()["verified"]
            else:
                image, metadata = packet.image()
                assert image.shape == (preview_height, preview_width, 3) and metadata["format"] == 3
    report = {"passed": True, "scope": "Real DVP bytes -> RTL RGB capture -> UART bits -> independent CRC/pixels -> browser decoder",
              "overlap_capture": args.overlap_capture, "downsample_bits": args.downsample_bits, "images": images,
              "sources": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(result.stdout, end="")
    print("PASS independent RGB pixels, diagnostics, CRC, freshness and shared-stream decoding")


if __name__ == "__main__":
    main()
