#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np

from face_pipeline import letterbox


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    camera = ROOT / "board/pg2l200h_mini/camera_uart"
    sources = [camera / name for name in ("camera_capture_gate.v", "camera_frame_writer.v",
                                         "camera_tensor_writer.v", "tb_camera_coherent_capture.v")]
    subprocess.run(["iverilog", "-g2012", "-s", "tb_camera_coherent_capture", "-o", str(output / "test.vvp"),
                    *map(str, sources)], check=True)
    result = subprocess.run(["vvp", "test.vvp"], cwd=output, capture_output=True, text=True, timeout=120)
    (output / "simulation.log").write_text(result.stdout + result.stderr)
    print(result.stdout, end="")
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    captures = re.findall(r"CAPTURE job=(\d+) seed=(\d+) bank=(\d+)", result.stdout)
    assert len(captures) == 3
    records = []
    for job, seed, bank in captures:
        job, seed, bank = int(job), int(seed), int(bank)
        memory = (output / f"capture_{job}.bin").read_bytes()
        source_y, source_x = np.indices((16, 128), dtype=np.uint16)
        red = np.full((16, 128), seed % 32, dtype=np.uint16)
        green = (source_x * 3 + source_y * 5 + seed) % 64
        blue = (source_x * 7 + source_y + seed) % 32
        pixels = (red << 11) | (green << 5) | blue
        raw = pixels.astype(">u2").tobytes()
        base = (0x4000, 0x6000)[bank]
        assert memory[base:base + len(raw)] == raw, "Raw frame differs from the captured sensor frame"
        image = np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                          (red << 3) | (red >> 2)], axis=2).astype(np.uint8)
        padded, _ = letterbox(image, (32, 6))
        expected = padded.transpose(2, 0, 1).tobytes()
        assert memory[0x1000:0x1000 + len(expected)] == expected, "Detector input and raw frame are not coherent"
        records.append({"job": job, "source_frame": seed, "bank": bank,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "tensor_sha256": hashlib.sha256(expected).hexdigest()})
    report = {"passed": True, "scope": "Actual tensor and raw-frame RTL, real host BGR letterbox/INTER_AREA reference; both frozen across later frames",
              "captures": records, "sources": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                                 for path in sources}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS exact sensor frame, detector tensor, padding, alternating banks and no inference-time overwrite")


if __name__ == "__main__":
    main()
