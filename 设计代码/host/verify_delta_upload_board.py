#!/usr/bin/env python3
"""Measure reversible input compression against legacy uploads on the same FPGA."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import struct
import zlib

import numpy as np

from board_evidence import validate_project
from face_pipeline import read_image
from fpga_model import Model, align_face, detector_input, embedding_input
from hardware_transport import Board, BoardError
from transport_evidence import snapshot_transport

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--demo-report", type=Path, required=True)
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--port", default="COM7")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = snapshot_transport(args.output)
    hashes[Path(__file__).name] = sha(Path(__file__).read_bytes())
    (args.output / "driver_sources" / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    detector, embedding = [Model(args.models, name) for name in ("yunet", "mobilefacenet")]
    inputs = [(f"fixed_{model.name}", (args.models / f"{model.name}_input.bin").read_bytes())
              for model in (detector, embedding)]
    for record in json.loads(args.demo_report.read_text())["images"]:
        path = ROOT / "vision/test_images" / Path(record["path"]).name
        if sha(path.read_bytes()) != record["image_sha256"]:
            raise ValueError("Public source image changed")
        image = read_image(path)
        data = detector.input_bytes(detector_input(image))
        if sha(data) != record["detection"]["input_sha256"]:
            raise ValueError("Detector input differs from the validated public demonstration")
        inputs.append((f"{path.stem}_yunet", data))
        for index, face in enumerate(record["faces"]):
            detection = np.asarray([*face["box"], *np.asarray(face["landmarks"]).reshape(-1),
                                    face["confidence"]], np.float32)
            data = embedding.input_bytes(embedding_input(align_face(image, detection)))
            if sha(data) != face["input_sha256"]:
                raise ValueError("Embedding input differs from the validated public demonstration")
            inputs.append((f"{path.stem}_mobilefacenet_{index}", data))
    records, rejected = [], []
    # Scratch DDR keeps weights, tensors and gallery out of this comparison.
    address = 0x83e00001
    with Board(args.port, bridge_log=args.output / "bridge.log") as board:
        identity = board.ping()
        project = validate_project(args.project, identity)
        if not board.capabilities().get("delta_compressed_write"):
            raise RuntimeError("The running firmware does not advertise delta uploads")
        board.preview(2)
        for name, data in inputs:
            trials = {"legacy": [], "adaptive_delta": []}
            for repeat in range(3):
                order = (False, True) if repeat % 2 == 0 else (True, False)
                for delta in order:
                    board.upload(address, data, delta=delta)
                    stats = dict(board.last_upload_stats)
                    if board.download(address, len(data)) != data:
                        raise RuntimeError(f"Exact DDR readback failed: {name}, delta={delta}")
                    trials["adaptive_delta" if delta else "legacy"].append(stats)
            medians = {mode: statistics.median(item["milliseconds"] for item in measurements)
                       for mode, measurements in trials.items()}
            records.append({"name": name, "input_bytes": len(data), "input_sha256": sha(data),
                            "trials": trials, "median_ms": medians,
                            "elapsed_reduction_percent": 100 * (1 - medians["adaptive_delta"] / medians["legacy"]),
                            "exact_bytes": True})
            print(f"UPLOAD {name}: legacy={medians['legacy']:.2f}ms delta={medians['adaptive_delta']:.2f}ms", flush=True)
        sentinel = bytes((index * 37 + 9) & 255 for index in range(8191))
        board.upload(address, sentinel, compress=False)
        expected = bytes(range(256)) * 5
        differences = bytes([expected[0]]) + bytes((a - b) & 255 for a, b in zip(expected[1:], expected[:-1]))
        valid = struct.pack("<II", address, len(expected)) + zlib.compress(differences, 1)
        damaged = [valid[:-1], valid + b"\0", valid[:-1] + bytes([valid[-1] ^ 1]),
                   struct.pack("<II", address, 1) + valid[8:],
                   struct.pack("<II", address, 4097) + valid[8:], b""]
        for index, payload in enumerate(damaged):
            try:
                board.command(15, payload)
                raise RuntimeError("Invalid delta stream was accepted")
            except BoardError as error:
                if error.status != (3 if index >= 4 else 9):
                    raise RuntimeError("Unexpected invalid-stream response") from error
                rejected.append({"case": index, "payload_sha256": sha(payload), "status": error.status})
            if board.download(address, len(sentinel)) != sentinel:
                raise RuntimeError("Rejected delta stream changed DDR")
        counters = board.counters()
        if counters["rx_errors"] or board.decoder.crc_errors or board.decoder.framing_errors:
            raise RuntimeError("UART integrity failed during delta comparison")
    driver_errors = [line for line in (args.output / "bridge.log").read_text(errors="replace").splitlines()
                     if any(tag in line for tag in ("COMM_ERROR", "READ_ERROR", "WRITE_ERROR"))]
    result = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "project_evidence": project, "board": identity, "source_hashes": hashes,
              "inputs": records, "invalid_requests": rejected, "counters": counters,
              "driver_errors": driver_errors, "passed": not driver_errors,
              "scope": "Alternating same-board legacy/adaptive uploads, three trials each; exact unaligned DDR readback. Network execution is excluded."}
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    if driver_errors:
        raise RuntimeError("Serial driver reported errors during the comparison")
    print("PASS same-board delta upload comparison and invalid-stream DDR guards", flush=True)


if __name__ == "__main__":
    main()
