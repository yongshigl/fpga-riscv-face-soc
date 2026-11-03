#!/usr/bin/env python3
"""Measure short real RISC-V replies without camera or model-transfer overhead."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import time

from board_evidence import validate_project
from transport_evidence import snapshot_transport
from hardware_transport import Board

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=100)
    args = parser.parse_args()
    if args.rounds <= 0:
        parser.error("Reply measurement requires positive repetitions")
    args.output.mkdir(parents=True, exist_ok=True)
    transport_hashes = snapshot_transport(args.output)
    samples = []
    with Board(bridge_log=args.output / "bridge.log") as board:
        identity = board.ping(timeout=10)
        board.preview(2)
        for _ in range(args.rounds):
            start = time.perf_counter()
            if board.ping() != identity:
                raise RuntimeError("The real board identity changed during reply measurements")
            samples.append((time.perf_counter() - start) * 1000)
        errors = {"crc": board.decoder.crc_errors, "framing": board.decoder.framing_errors}
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Real RISC-V ping round trips through the Windows serial bridge; camera preview stopped",
              "project_evidence": validate_project(args.project, identity),
              "source_hashes": {**transport_hashes, **{name: hashlib.sha256((ROOT / "vision" / name).read_bytes()).hexdigest()
                                for name in ("hardware_transport.py", "serial_bridge.py", "verify_bridge_latency.py")}},
              "samples_ms": samples, "mean_ms": statistics.mean(samples),
              "median_ms": statistics.median(samples), "maximum_ms": max(samples), "errors": errors,
              "passed": not any(errors.values())}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    if not report["passed"]:
        raise RuntimeError("The real reply measurement contained UART errors")
    print(f"BRIDGE_LATENCY rounds={len(samples)} median_ms={report['median_ms']:.3f} "
          f"mean_ms={report['mean_ms']:.3f} max_ms={report['maximum_ms']:.3f}", flush=True)


if __name__ == "__main__":
    main()
