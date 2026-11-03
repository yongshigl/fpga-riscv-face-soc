#!/usr/bin/env python3
"""Check the local vision environment and running board without reprogramming it."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))


def serial_ports():
    if os.name == "nt":
        from serial.tools import list_ports
        return [{"device": port.device, "description": port.description,
                 "vid": port.vid, "pid": port.pid, "serial_number": port.serial_number}
                for port in list_ports.comports()]
    code = ("import json; from serial.tools import list_ports; "
            "print(json.dumps([dict(device=p.device,description=p.description,"
            "vid=p.vid,pid=p.pid,serial_number=p.serial_number) for p in list_ports.comports()]))")
    result = subprocess.run(["/mnt/c/python.exe", "-X", "utf8", "-c", code],
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=20)
    return json.loads(result.stdout)


def choose_port(specification, ports):
    if specification.lower() != "auto":
        return specification
    candidates = [port["device"] for port in ports
                  if port["vid"] == 0x10c4 and port["pid"] == 0xea60]
    if len(candidates) != 1:
        raise ValueError(f"Expected one CP210x board UART, found {candidates}; use --port COMx")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--hardware", action="store_true", help="Open UART and check running firmware/sensors")
    parser.add_argument("--camera-seconds", type=float, default=0)
    parser.add_argument("--enable-preview", action="store_true", help="Start preview for the bounded frame check")
    args = parser.parse_args()
    if args.camera_seconds < 0 or (args.camera_seconds and not args.hardware):
        parser.error("Camera observation requires --hardware and a nonnegative duration")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "source_root": str(ROOT), "hardware_requested": args.hardware,
              "hardware_reprogrammed": False, "flash_written": False, "passed": False}
    try:
        import cv2
        import numpy as np
        from hardware_transport import Board
        from save_final_checkpoint import validate_project
        report["environment"] = {"python": sys.version, "opencv": cv2.__version__, "numpy": np.__version__}
        ports = serial_ports()
        report["serial_ports"] = ports
        port = choose_port(args.port, ports) if args.hardware else args.port
        report["selected_port"] = port
        if args.project:
            manifest, bitstream = validate_project(args.project.resolve())
            report["frozen_project"] = {"project": manifest["project"],
                                        "bitstream_sha256": hashlib.sha256(bitstream.read_bytes()).hexdigest(),
                                        "timing_met": True}
        if args.hardware:
            frames = [[], []]

            def accept(packet):
                if packet.command == 2:
                    return
                image, info = packet.image(baud=args.baud)
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                info.update(luma_mean=float(gray.mean()), luma_stddev=float(gray.std()),
                            laplacian_variance=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                            jpeg_bytes=len(packet.payload) - 12 if packet.format == 5 else None)
                frames[packet.camera].append(info)
                if packet.format == 5:
                    (args.output / f"camera{packet.camera}.jpg").write_bytes(packet.payload[12:])

            with Board(port, baud=args.baud, bridge_log=args.output / "bridge.log", camera_callback=accept) as board:
                identity = board.ping(timeout=10)
                report["identity"] = identity
                report["bridge_startup_ms"] = board.bridge_startup_ms
                capabilities = board.capabilities()
                report["capabilities"] = capabilities
                if args.project:
                    for key, expected in (("cpu_hz", manifest["cpu_clock_hz"]),
                                          ("ai_hz", manifest["ai_clock_hz"]),
                                          ("uart_baud", manifest["uart_baud"]),
                                          ("firmware_version", int(manifest["firmware"]["firmware_version"], 16))):
                        if identity[key] != expected:
                            raise ValueError(f"Running identity differs: {key}={identity[key]}, expected {expected}")
                    report["identity_matches_project"] = True
                    report["identity_scope"] = "Firmware version/clocks match; UART identity is not a bitstream readback hash."
                if capabilities["camera_preview"]:
                    diagnostic = board.preview(0)
                    report["preview"] = diagnostic
                    if not all(sensor["verified"] for sensor in diagnostic["sensors"]):
                        raise RuntimeError("Both OV5640 diagnostics must pass")
                    if args.enable_preview:
                        board.preview(1)
                    if args.camera_seconds:
                        if not diagnostic["active"] and not args.enable_preview:
                            raise RuntimeError("Preview is paused; pass --enable-preview for a frame observation")
                        deadline = time.monotonic() + args.camera_seconds
                        while time.monotonic() < deadline:
                            board.read_camera(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
                        if not all(frames):
                            raise RuntimeError("Did not receive images from both cameras")
                        report["frames"] = frames
                        report["frame_counts"] = list(map(len, frames))
                elif args.camera_seconds:
                    raise RuntimeError("Running firmware has no camera-preview capability")
                report["counters"] = board.counters()
                report["decoder"] = {"crc_errors": board.decoder.crc_errors,
                                     "framing_errors": board.decoder.framing_errors}
                if report["counters"]["rx_errors"] or any(report["decoder"].values()):
                    raise RuntimeError("UART error counters are nonzero; inspect the preserved diagnostics")
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        report["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
