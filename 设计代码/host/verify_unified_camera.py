#!/usr/bin/env python3
"""Verify real sensors, compressed CPU writes and shared UART handoffs on FPGA."""
import argparse
import datetime
import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from hardware_transport import Board

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/unified_camera_validation")
    parser.add_argument("--port", default="COM7")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    frames = [[], []]
    sensors = [None, None]
    latest = [None, None]

    def accept(packet):
        if packet.command == 2:
            sensors[packet.camera] = packet.sensor()
            if not sensors[packet.camera]["verified"]:
                raise RuntimeError("Physical OV5640 diagnostic did not pass")
            return
        image, info = packet.image()
        if packet.format != 5 or image.shape != (360, 640, 3):
            raise RuntimeError("Unexpected combined-bitstream camera format")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        info.update(luma_mean=float(gray.mean()), luma_stddev=float(gray.std()),
                    laplacian_variance=float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        if gray.std() <= 5 or int(gray.max()) - int(gray.min()) <= 40:
            raise RuntimeError("Camera image is blank or too dark for this validation")
        frames[packet.camera].append(info)
        latest[packet.camera] = packet.payload[12:]

    with Board(args.port, bridge_log=args.output / "bridge.log", camera_callback=accept) as board:
        identity = board.ping()
        project = validate_project(args.project, identity)
        capabilities = board.capabilities()
        if not capabilities["camera_preview"] or not capabilities["compressed_write"]:
            raise RuntimeError("This validation requires combined camera and compressed-upload hardware")
        diagnostic = board.preview(2)
        deadline = time.monotonic() + 15
        while not all(sensor["verified"] for sensor in diagnostic["sensors"]):
            if time.monotonic() >= deadline:
                raise RuntimeError("Physical camera initialization did not complete")
            time.sleep(0.25)
            diagnostic = board.preview(0)
        uploads = []
        rng = np.random.default_rng(261002)
        patterns = (bytes(8191), bytes(range(256)) * 33,
                    rng.integers(0, 256, 8191, dtype=np.uint8).tobytes())
        for index, payload in enumerate(patterns):
            address = 0x83f00001 + index * 0x10000
            board.upload(address, payload)
            record = dict(board.last_upload_stats)
            if board.download(address, len(payload)) != payload:
                raise RuntimeError("Compressed/raw CPU DDR upload differs from the input")
            if index == 0 and not record["compressed_blocks"]:
                raise RuntimeError("Compressible data did not use the CPU decompressor")
            if index == 2 and not record["raw_blocks"]:
                raise RuntimeError("Random data did not exercise raw upload fallback")
            record["sha256"] = hashlib.sha256(payload).hexdigest()
            uploads.append(record)
        print("PASS actual CPU compressed/raw writes and unaligned DDR readback", flush=True)
        handoffs = []
        for target in (32, 64):
            board.preview(1)
            deadline = time.monotonic() + 90
            while any(len(channel) < target for channel in frames):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Both real cameras did not produce enough valid frames")
                board.read_camera(0.25)
            stopped = board.preview(2)
            if stopped["active"] or board.ping() != identity:
                raise RuntimeError("Camera-to-CPU UART handoff failed")
            handoffs.append({"frames": [len(channel) for channel in frames], "preview": stopped})
            print(f"PASS shared UART stop/ping/resume: {[len(channel) for channel in frames]}", flush=True)
        counters = board.counters()
        if counters["rx_errors"] or board.decoder.crc_errors or board.decoder.framing_errors:
            raise RuntimeError("UART errors occurred during the combined-system validation")
        summaries = []
        for camera, channel in enumerate(frames):
            if not sensors[camera] or not sensors[camera]["verified"]:
                raise RuntimeError("Missing physical sensor readback in the camera stream")
            times = [frame["received_monotonic"] for frame in channel]
            summaries.append({"camera": camera, "frames": len(channel),
                              "fps_including_control_handoff": (len(times) - 1) / (times[-1] - times[0]),
                              "mean_estimated_frame_age_ms": float(np.mean([
                                  frame["estimated_frame_age_ms"] for frame in channel])),
                              "p95_estimated_frame_age_ms": float(np.percentile([
                                  frame["estimated_frame_age_ms"] for frame in channel], 95))})
            (args.output / f"camera{camera}.jpg").write_bytes(latest[camera])
        report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "execution": "Physical dual OV5640, FPGA native JPEG, RISC-V compressed DDR upload",
                  "board": identity, "project_evidence": project, "sensors": sensors,
                  "source_hashes": transport_hashes,
                  "capabilities": capabilities, "uploads": uploads, "handoffs": handoffs,
                  "frames": frames, "camera_summary": summaries, "counters": counters,
                  "crc_errors": board.decoder.crc_errors, "framing_errors": board.decoder.framing_errors,
                  "timing_scope": "Estimated capture-complete to UART-complete age; excludes exposure, USB and browser rendering",
                  "complete": True}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print("PASS combined sensors, 128+ JPEG frames, compressed uploads and shared UART", flush=True)


if __name__ == "__main__":
    main()
