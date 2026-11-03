#!/usr/bin/env python3
"""Summarize recorded camera transport measurements with explicit timing limits."""
import argparse
import json
import statistics
from pathlib import Path

from dateutil.parser import isoparse


def summarize(path):
    capture = json.loads(path.read_text(encoding="utf-8-sig"))
    sensors = []
    for camera in range(2):
        frames = [frame for frame in capture["frames"] if frame["camera"] == camera]
        times = [isoparse(frame["utc"]) for frame in frames]
        elapsed = (times[-1] - times[0]).total_seconds() if len(times) > 1 else 0
        record = {"camera": camera, "frames": len(frames),
                  "fps": (len(frames) - 1) / elapsed if elapsed else None}
        for name in ["encoded_bytes", "compression_ratio", "pre_transmit_ms", "receive_ms",
                     "estimated_frame_age_ms", "host_processing_ms"]:
            values = [frame[name] for frame in frames if frame.get(name) is not None]
            if values:
                record[name] = {"mean": statistics.mean(values), "minimum": min(values), "maximum": max(values)}
        sensors.append(record)
    return {"source": str(path), "width": capture["preview_width"], "height": capture["preview_height"],
            "bitstream_sha256": capture["bitstream_sha256"], "crc_errors": capture["crc_errors"],
            "framing_errors": capture["framing_errors"], "cameras": sensors,
            "latency_scope": "Estimated capture-complete to UART-complete; excludes sensor exposure and USB/host scheduling."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.capture)
    if args.baseline:
        baseline = summarize(args.baseline)
        result["baseline"] = baseline
        result["pixel_count_ratio"] = result["width"] * result["height"] / (baseline["width"] * baseline["height"])
        result["fps_ratio"] = [new["fps"] / old["fps"] for new, old in zip(result["cameras"], baseline["cameras"])]
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
