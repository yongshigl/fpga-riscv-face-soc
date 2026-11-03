#!/usr/bin/env python3
"""Profile zlib levels using the actual vision upload packet selection rules.

No board or serial connection is opened. Fixed model inputs and archived public
demo inputs are reconstructed and checked against their historical hashes.
"""
import argparse
import datetime
import hashlib
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
from face_pipeline import read_image
from fpga_model import Model, align_face, detector_input, embedding_input


def digest(data):
    return hashlib.sha256(data).hexdigest()


def upload_plan(data, level, verify=False):
    offset = wire_bytes = compressed_blocks = raw_blocks = 0
    compression_ms = 0.0
    payload_sizes = []
    while offset < len(data):
        block = data[offset:offset + 4096]
        begin = time.perf_counter()
        encoded = zlib.compress(block, level)
        compression_ms += (time.perf_counter() - begin) * 1000
        compressed = len(encoded) + 8 <= 2048 and len(encoded) + 4 < len(block) * 0.9
        if not compressed:
            block = block[:2044]
            begin = time.perf_counter()
            encoded = zlib.compress(block, level)
            compression_ms += (time.perf_counter() - begin) * 1000
            compressed = len(encoded) + 8 <= 2048 and len(encoded) + 4 < len(block) * 0.9
        if compressed:
            if verify and zlib.decompress(encoded) != block:
                raise RuntimeError("Compressed upload does not reconstruct the original tensor")
            size = 8 + len(encoded)
            compressed_blocks += 1
        else:
            size = 4 + len(block)
            raw_blocks += 1
        wire_bytes += 20 + size + 28
        payload_sizes.append(size)
        offset += len(block)
    return {"input_bytes": len(data), "wire_bytes": wire_bytes,
            "commands": len(payload_sizes), "compressed_blocks": compressed_blocks,
            "raw_blocks": raw_blocks, "maximum_payload_bytes": max(payload_sizes),
            "compression_ms": compression_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--demo-report", type=Path, default=ROOT / "build/vision/unified_hardware_demo/report.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/experiments/project_completion_20261013/ai/upload_compression.json")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--baud", type=int, default=921600)
    args = parser.parse_args()
    if args.repeats < 3 or args.baud <= 0:
        parser.error("At least three timing repetitions and a positive baud rate are required")
    detector, embedding = Model(args.models, "yunet"), Model(args.models, "mobilefacenet")
    inputs = []
    for model in (detector, embedding):
        payload = (args.models / f"{model.name}_input.bin").read_bytes()
        inputs.append({"name": f"fixed_{model.name}", "model": model.name, "payload": payload,
                       "input_sha256": digest(payload), "source": "Frozen hardware-equivalence model input"})
    demo = json.loads(args.demo_report.read_text())
    for image_record in demo["images"]:
        path = ROOT / "vision/test_images" / Path(image_record["path"]).name
        if digest(path.read_bytes()) != image_record["image_sha256"]:
            raise RuntimeError("Public demonstration image hash changed")
        image = read_image(path)
        payload = detector.input_bytes(detector_input(image))
        if digest(payload) != image_record["detection"]["input_sha256"]:
            raise RuntimeError("Reconstructed detection tensor differs from the actual board demo input")
        inputs.append({"name": f"{path.stem}_yunet", "model": "yunet", "payload": payload,
                       "input_sha256": digest(payload), "source": "Historical FPGA demo input", "image_sha256": image_record["image_sha256"]})
        for face_index, face in enumerate(image_record["faces"]):
            detection = np.asarray([*face["box"], *np.asarray(face["landmarks"]).reshape(-1), face["confidence"]], np.float32)
            payload = embedding.input_bytes(embedding_input(align_face(image, detection)))
            if digest(payload) != face["input_sha256"]:
                raise RuntimeError("Reconstructed embedding tensor differs from the actual board demo input")
            inputs.append({"name": f"{path.stem}_mobilefacenet_{face_index}", "model": "mobilefacenet", "payload": payload,
                           "input_sha256": digest(payload), "source": "Historical FPGA demo input", "image_sha256": image_record["image_sha256"]})
    records = []
    for entry in inputs:
        payload = entry.pop("payload")
        entry["levels"] = {}
        for level in (1, 3, 6, 9):
            measured = upload_plan(payload, level, verify=True)
            samples = [upload_plan(payload, level)["compression_ms"] for _ in range(args.repeats)]
            measured["compression_ms_samples"] = samples
            measured["compression_ms_median"] = float(np.median(samples))
            measured["compression_ms_p95"] = float(np.percentile(samples, 95))
            measured["theoretical_serial_ms"] = measured["wire_bytes"] * 10 * 1000 / args.baud
            measured["estimated_compression_plus_serial_ms"] = measured["compression_ms_median"] + measured["theoretical_serial_ms"]
            entry["levels"][str(level)] = measured
        entry["preferred_level_by_estimate"] = min(entry["levels"], key=lambda key: entry["levels"][key]["estimated_compression_plus_serial_ms"])
        records.append(entry)
        print(entry["name"], {level: {key: values[key] for key in ("wire_bytes", "commands", "compression_ms_median")}
                              for level, values in entry["levels"].items()}, flush=True)
    totals = {}
    for level in (1, 3, 6, 9):
        totals[str(level)] = {key: sum(entry["levels"][str(level)][key] for entry in records)
                              for key in ("wire_bytes", "commands", "compression_ms_median", "theoretical_serial_ms", "estimated_compression_plus_serial_ms")}
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Host compression measurements and upload packet simulation; no hardware or serial access",
              "hardware_tested": False, "repeats": args.repeats, "baud_assumed": args.baud,
              "zlib_build_version": zlib.ZLIB_VERSION, "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
              "packet_policy": "Current transport: try 4096 decoded bytes, fall back to 2044; compressed payload <=2048; at least 10% shrink; request/response framing included",
              "scope": "Serial estimates exclude board decompression, DDR writes, command latency and host scheduling",
              "script_sha256": digest(Path(__file__).read_bytes()),
              "demo_report_sha256": digest(args.demo_report.read_bytes()), "inputs": records, "totals": totals,
              "preferred_level_by_estimate": min(totals, key=lambda key: totals[key]["estimated_compression_plus_serial_ms"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("TOTALS", json.dumps(totals), flush=True)
    print("REPORT", args.output, flush=True)


if __name__ == "__main__":
    main()
