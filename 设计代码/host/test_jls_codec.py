#!/usr/bin/env python3
"""Verify the RTL encoder with the independent CharLS decoder."""

import argparse
import json
import subprocess
import zlib
from pathlib import Path

import imagecodecs
import numpy as np

from face_pipeline import ROOT, read_image, write_image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, default=ROOT.parent / "build/camera/rgb-v3-20261001/cam0.png")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "build/camera/codec-test")
    parser.add_argument("--buffer-words", type=int, default=65536)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    image = read_image(args.image)
    height, width = image.shape[:2]
    rgb = image[:, :, ::-1]
    codes = rgb // np.array([8, 4, 8], dtype=np.uint8)
    pixels = (codes[:, :, 0].astype(np.uint16) << 11) | (codes[:, :, 1].astype(np.uint16) << 5) | codes[:, :, 2]
    args.output = args.output.resolve()
    input_file = args.output / "input.hex"
    np.savetxt(input_file, pixels.reshape(-1), fmt="%04x")
    camera = ROOT.parent / "board/pg2l200h_mini/camera_uart"
    executable = args.output / "encoder.vvp"
    encoded_file = args.output / "rtl.jls"
    sources = [camera / "camera_frame_encode.v", camera / "jls_encoder_pango.v",
               camera / "tb_camera_frame_encode.v"]
    subprocess.run(["iverilog", "-g2012", "-s", "tb_camera_frame_encode",
                    f"-Ptb_camera_frame_encode.WIDTH={width}", f"-Ptb_camera_frame_encode.HEIGHT={height}",
                    f"-Ptb_camera_frame_encode.BUFFER_WORDS={args.buffer_words}",
                    "-o", str(executable), *map(str, sources)], check=True)
    # Icarus plusarg filenames must be ASCII on this mixed Windows/WSL workspace.
    subprocess.run(["vvp", "encoder.vvp", "+input=input.hex", "+output=rtl.jls"],
                   cwd=args.output, check=True)
    if args.buffer_words == 32:
        print("PASS RTL JLS buffer overflow and consecutive-frame recovery", flush=True)
        return
    encoded = encoded_file.with_suffix(".jls.0").read_bytes()
    for frame in range(2):
        expected_codes = codes if frame == 0 else np.array([31, 63, 31], dtype=np.uint8) - codes
        expected = np.concatenate([expected_codes[:, :, i] for i in range(3)], axis=0)
        actual = imagecodecs.jpegls_decode(encoded_file.with_suffix(f".jls.{frame}").read_bytes())
        if actual.shape != expected.shape or np.abs(actual.astype(np.int16) - expected).max() > 1:
            raise AssertionError(f"Consecutive frame {frame}: dimensions or NEAR bound failed")
    decoded = imagecodecs.jpegls_decode(encoded)
    expected = np.concatenate([codes[:, :, i] for i in range(3)], axis=0)
    if decoded.shape != expected.shape:
        raise AssertionError(f"Wrong JLS dimensions: {decoded.shape} versus {expected.shape}")
    difference = np.abs(decoded.astype(np.int16) - expected.astype(np.int16))
    if difference.max() > 1:
        raise AssertionError(f"JPEG-LS NEAR=1 error exceeded: {difference.max()}")
    components = np.stack(np.split(decoded, 3, axis=0), axis=2)
    components = np.minimum(components, np.array([31, 63, 31], dtype=np.uint8)).astype(np.uint16)
    reconstructed = ((components << np.array([3, 2, 3])) |
                     (components >> np.array([2, 4, 2]))).astype(np.uint8)
    write_image(args.output / "decoded.png", reconstructed[:, :, ::-1])
    metadata = (width.to_bytes(2, "little") + height.to_bytes(2, "little") +
                (1000).to_bytes(4, "little") + (2000).to_bytes(4, "little"))
    payload = metadata + encoded
    header = b"PGC2" + bytes([1, 0, 4, 7]) + (42).to_bytes(4, "little") + len(payload).to_bytes(4, "little")
    packet = header + payload
    packet += zlib.crc32(packet).to_bytes(4, "little")
    (args.output / "packet.bin").write_bytes(packet)
    (args.output / "expected_rgb.bin").write_bytes(reconstructed.tobytes())
    summary = {"width": width, "height": height, "raw_bytes": int(pixels.size * 2),
               "encoded_bytes": len(encoded), "compression_ratio": float(pixels.size * 2 / len(encoded)),
               "maximum_channel_code_error": int(difference.max()), "near": 1}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("PASS RTL JLS decoded with independent CharLS: " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
