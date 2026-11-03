#!/usr/bin/env python3
"""Measure sustained camera and model readiness through the actual HTTP console."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from console_integrity import INTEGRITY_SCOPE, begin_observation, check_connection, counter_snapshot

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8086")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/console_stability_validation")
    parser.add_argument("--frames-per-camera", type=int, default=1024)
    parser.add_argument("--min-seconds", type=float, default=600)
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args(argv)
    if args.frames_per_camera <= 0 or not 0 <= args.min_seconds <= args.timeout:
        parser.error("Require positive frame counts and a bounded observation time")
    if (args.output / "report.json").exists():
        parser.error("Use a new output directory to preserve an earlier result")
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    source_names = ("hardware_transport.py", "hardware_face_demo.py", "demo_server.py", "serial_bridge.py", "windows_serial_pipeline.py",
                    "camera_stream.py", "verify_console_hardware.py", "console_integrity.py", "verify_console_stability.py")
    started = time.monotonic()
    samples, initial_counts = [], None
    status, project, failure = None, None, None
    initial_status, baseline = None, None
    bridge_log = args.runtime / "bridge.log"
    bridge_offset = bridge_log.stat().st_size if bridge_log.exists() else 0
    next_progress = started
    diagnostic_tail = b""
    try:
        while True:
            with urllib.request.urlopen(args.url + "/api/status", timeout=10) as response:
                status = json.load(response)
            if initial_status is None:
                initial_status = status
            if project is None:
                project = validate_project(args.project, status["board"])
            if (not status["connected"] or not status["models_ready"] or not status["preview_enabled"]
                    or status["mode"] != "vision" or status["error"] or status["active_job"]
                    or status.get("pending_jobs", 0) or status.get("continuous", {}).get("enabled")
                    or status.get("continuous", {}).get("active_job")):
                raise RuntimeError("Sustained console readiness or UART integrity failed")
            if baseline is None:
                baseline = begin_observation(status)
            check_connection(status, baseline)
            cameras = status["cameras"]
            stale_limits = [max(2.0, 2 * (camera["encoded_bytes"] + 32) * 10 / status["baud"] + 1)
                            if camera.get("format") == 3 else 2.0 for camera in cameras]
            if len(cameras) != 2 or any(not camera["sensor"]["verified"] or camera["stale_seconds"] is None
                                        or camera["stale_seconds"] > limit
                                        for camera, limit in zip(cameras, stale_limits)):
                raise RuntimeError("A camera stopped delivering verified frames")
            counts = [camera["frames"] for camera in cameras]
            if initial_counts is None:
                initial_counts = counts
            received = [count - initial_counts[index] for index, count in enumerate(counts)]
            elapsed = time.monotonic() - started
            samples.append({"elapsed_seconds": elapsed, "frames_received": received,
                            "integrity": counter_snapshot(status),
                            "fps": [camera["fps"] for camera in cameras],
                            "stale_seconds": [camera["stale_seconds"] for camera in cameras],
                            "maximum_stale_seconds": stale_limits})
            if bridge_log.exists():
                with bridge_log.open("rb") as stream:
                    stream.seek(bridge_offset)
                    diagnostics = stream.read()
                    bridge_offset = stream.tell()
                combined = diagnostic_tail + diagnostics
                diagnostic_tail = combined[-128:]
                if any(tag in combined for tag in (b"SERIAL_BRIDGE_COMM_ERROR", b"SERIAL_BRIDGE_READ_ERROR",
                                                    b"SERIAL_BRIDGE_WRITE_ERROR")):
                    raise RuntimeError("Windows reported a serial driver or line error")
            if elapsed >= args.min_seconds and min(received) >= args.frames_per_camera:
                break
            if elapsed >= args.timeout:
                raise TimeoutError("Camera frame targets did not complete within the observation budget")
            if time.monotonic() >= next_progress:
                print(f"STABILITY {elapsed:.1f}s camera_frames={received} current_uart_errors=0 "
                      f"new_retries=0 new_decode_errors=0 generation={baseline['connection_generation']}", flush=True)
                next_progress = time.monotonic() + 30
            time.sleep(2)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Continuous real-board dual-camera preview through the PC console",
              "project_evidence": project, "source_hashes": {
                  **{name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest() for name in source_names},
                  **transport_hashes},
              "target_frames_per_camera": args.frames_per_camera, "target_seconds": args.min_seconds,
              "integrity_scope": INTEGRITY_SCOPE, "initial_status": initial_status,
              "initial_integrity": baseline, "final_integrity": counter_snapshot(status) if status else None,
              "elapsed_seconds": time.monotonic() - started,
              "frames_received": samples[-1]["frames_received"] if samples else [0, 0],
              "samples": samples, "final_status": status, "error": failure, "passed": failure is None}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    if failure:
        raise RuntimeError(failure)
    print(f"PASS sustained real console: {report['elapsed_seconds']:.1f}s {report['frames_received']} frames", flush=True)


if __name__ == "__main__":
    main()
