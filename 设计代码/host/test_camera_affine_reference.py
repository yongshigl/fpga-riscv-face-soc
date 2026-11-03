#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

from fpga_model import embedding_input, pack_tensor


ROOT = Path(__file__).resolve().parents[1]
CAMERA = ROOT / "board/pg2l200h_mini/camera_uart"
TRANSFORMS = [
    ("identity", (1, 0, 8, 0, 1, 4)),
    ("vertical_fraction", (1, 0, 8, 0, 1, 4.5)),
    ("two_fractions", (1, 0, 8.25, 0, 1, 4.75)),
    ("line_crossing", (0.5, 0, 15.5, 0, 0.5, 5.5)),
    ("rotation", (0.875, -0.25, 18.125, 0.25, 0.875, 5.625)),
    ("negative_border", (0.5, 0, -2.75, 0, 0.5, -1.5)),
    ("last_pixel", (0, 0, 63, 0, 0, 47)),
    ("positive_border", (0.5, 0.25, 61.75, -0.125, 0.5, 46.75)),
    ("reverse", (-1.125, 0.125, 40.375, 0.25, -0.875, 30.625)),
]


def reference(image, coefficients):
    destination_y, destination_x = np.indices((12, 20), dtype=np.int64)
    source_x = coefficients[0] * destination_x + coefficients[1] * destination_y + coefficients[2]
    source_y = coefficients[3] * destination_x + coefficients[4] * destination_y + coefficients[5]
    source_x = np.clip(source_x, 0, (image.shape[1] - 1) * 65536)
    source_y = np.clip(source_y, 0, (image.shape[0] - 1) * 65536)
    left, top = source_x // 65536, source_y // 65536
    right = np.minimum(left + 1, image.shape[1] - 1)
    bottom = np.minimum(top + 1, image.shape[0] - 1)
    fraction_x, fraction_y = (source_x % 65536)[..., None], (source_y % 65536)[..., None]
    upper = image[top, left] * (65536 - fraction_x) + image[top, right] * fraction_x
    lower = image[bottom, left] * (65536 - fraction_x) + image[bottom, right] * fraction_x
    result = (upper * (65536 - fraction_y) + lower * fraction_y + (1 << 31)) >> 32
    model_input = json.loads((ROOT / "build/vision/fpga/mobilefacenet.json").read_text())["input"]
    tensor = {**model_input, "shape": [1, 3, 12, 20], "row_stride": 32,
              "plane_stride": 384, "bytes": 1152}
    return pack_tensor(embedding_input(result.astype(np.uint8)), tensor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=CAMERA / "camera_affine_resampler.v")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source_y, source_x = np.indices((48, 64), dtype=np.int64)
    red = (source_x * 5 + source_y * 3) % 32
    green = (source_x * 11 + source_y * 7) % 64
    blue = (source_x * 13 + source_y * 17) % 32
    pixels = (red << 11) | (green << 5) | blue
    image = np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                      (red << 3) | (red >> 2)], axis=2)
    memory = bytearray(65536)
    memory[0x8000:0x9800] = pixels.astype(">u2").tobytes()
    (output / "memory.hex").write_text("".join(f"{value:02x}\n" for value in memory))
    transforms = [(name, [round(value * 65536) for value in values]) for name, values in TRANSFORMS]
    expected = b"".join(reference(image, coefficients) for _, coefficients in transforms)
    (output / "expected.hex").write_text("".join(f"{value:02x}\n" for value in expected))
    (output / "coefficients.hex").write_text("".join(
        f"{value & 0xffffffff:08x}\n" for _, coefficients in transforms for value in coefficients))
    sources = [args.source.resolve(), CAMERA / "tb_camera_affine_reference.v"]
    subprocess.run(["iverilog", "-g2012", "-s", "tb_camera_affine_reference", "-o",
                    str(output / "test.vvp"), *map(str, sources)], check=True)
    result = subprocess.run(["vvp", "test.vvp"], cwd=output, capture_output=True, text=True, timeout=120)
    (output / "simulation.log").write_text(result.stdout + result.stderr)
    report = {"passed": result.returncode == 0, "cases": transforms,
              "reference": "Independent Q16.16 bilinear and replicated borders, then actual BGR MobileFaceNet normalization, quantization and padding",
              "checked_output_bytes": len(expected), "expected_sha256": hashlib.sha256(expected).hexdigest(),
              "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(result.stdout, end="")
    if result.returncode:
        raise RuntimeError(f"Affine reference regression failed; see {output / 'simulation.log'}")


if __name__ == "__main__":
    main()
