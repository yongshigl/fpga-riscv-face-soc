#!/usr/bin/env python3
"""Pause WSL consumption of real camera bytes to exercise the Windows bridge."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import time

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from camera_stream import CAMERA_MAGIC
from hardware_transport import Board

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause-seconds", type=float, default=5)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--frames-per-camera", type=int, default=8)
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    if os.name == "nt" or not 0 < args.pause_seconds <= 5 or min(args.cycles, args.frames_per_camera) <= 0:
        parser.error("Use WSL with a positive pause of at most five seconds and positive observation targets")
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    bridge_log = args.output / "bridge.log"
    results = []
    with Board(bridge_log=bridge_log) as board:
        identity = board.ping(timeout=10)
        board.preview(1)
        deadline = time.monotonic() + 15
        while True:
            packet = board.read_camera(0.5)
            if packet and packet.magic == CAMERA_MAGIC and packet.command == 1:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("No camera frame arrived before the injected output pause")
        log_offset = bridge_log.stat().st_size
        previous_crc = board.decoder.crc_errors
        for cycle in range(args.cycles):
            started = time.monotonic()
            time.sleep(args.pause_seconds)
            counts = [0, 0]
            deadline = time.monotonic() + 30
            while min(counts) < args.frames_per_camera:
                packet = board.read_camera(0.5)
                if packet and packet.magic == CAMERA_MAGIC and packet.command == 1:
                    counts[packet.camera] += 1
                if time.monotonic() >= deadline:
                    raise TimeoutError("Camera delivery did not recover after the WSL output pause")
            errors = board.decoder.crc_errors - previous_crc
            previous_crc = board.decoder.crc_errors
            results.append({"cycle": cycle + 1, "read_pause_seconds": args.pause_seconds,
                            "elapsed_seconds": time.monotonic() - started,
                            "frames_received": counts, "new_crc_errors": errors})
            print(f"BRIDGE_BACKPRESSURE cycle={cycle + 1} frames={counts} new_crc_errors={errors}", flush=True)
        board.preview(2)
        crc_errors, framing_errors = board.decoder.crc_errors, board.decoder.framing_errors
        last_crc_error = board.decoder.last_crc_error
        startup_ms = board.bridge_startup_ms
    diagnostics = bridge_log.read_bytes()[log_offset:].decode("utf-8", errors="replace")
    driver_errors = [line for line in diagnostics.splitlines()
                     if any(tag in line for tag in ("SERIAL_BRIDGE_COMM_ERROR", "SERIAL_BRIDGE_READ_ERROR",
                                                    "SERIAL_BRIDGE_WRITE_ERROR"))]
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Real dual-camera UART bytes through the Windows bridge with deliberate WSL read pauses",
              "project_evidence": validate_project(args.project, identity),
              "source_hashes": {**transport_hashes, **{name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest()
                                for name in ("hardware_transport.py", "serial_bridge.py", "camera_stream.py",
                                             "verify_bridge_backpressure.py")}},
              "bridge_startup_ms": startup_ms, "results": results,
              "crc_errors": crc_errors, "framing_errors": framing_errors, "last_crc_error": last_crc_error,
              "new_driver_errors": driver_errors,
              "bridge_statistics": [line for line in diagnostics.splitlines() if "SERIAL_BRIDGE_STATS" in line],
              "passed": not (crc_errors or framing_errors or driver_errors)}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(f"BRIDGE_BACKPRESSURE_COMPLETE passed={report['passed']} crc={crc_errors} driver_errors={len(driver_errors)}",
          flush=True)
    if args.require_clean and not report["passed"]:
        raise RuntimeError("The Windows bridge lost camera bytes under output backpressure")


if __name__ == "__main__":
    main()
