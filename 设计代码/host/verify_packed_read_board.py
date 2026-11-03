#!/usr/bin/env python3
"""Exercise packed tensor readback and its guards on the actual RISC-V service."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from board_evidence import validate_project
from fpga_model import Model
from hardware_transport import Board, BoardError

ROOT = Path(__file__).resolve().parents[1]


def logical_bytes(raw, tensor):
    _, channels, height, width = tensor["shape"]
    return b"".join(raw[c * tensor["plane_stride"] + y * tensor["row_stride"]:
                         c * tensor["plane_stride"] + y * tensor["row_stride"] + width]
                    for c in range(channels) for y in range(height))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--model-outputs", action="store_true",
                        help="Compare the current final model outputs without rerunning the networks")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records, invalid = [], []
    with Board(args.port, bridge_log=args.output / "bridge.log") as board:
        identity = board.ping()
        project = validate_project(args.project, identity)
        if not board.capabilities().get("packed_tensor_read"):
            raise RuntimeError("Running firmware has no packed-read capability")
        before = board.counters()

        def compare(tensor, name):
            begin = time.monotonic()
            raw = board.download(tensor["address"], tensor["bytes"])
            raw_ms = (time.monotonic() - begin) * 1000
            expected = logical_bytes(raw, tensor)
            actual = board.download_tensor(tensor)
            if actual != expected:
                raise RuntimeError(f"Packed/physical tensor bytes differ: {name}")
            record = {"name": name, "tensor": tensor, "sha256": hashlib.sha256(actual).hexdigest(),
                      "raw_read_ms": raw_ms, "packed": dict(board.last_download_stats),
                      "identical": True}
            records.append(record)
            return expected

        # This reserved test area is beyond all model images/activations in the manifests.
        layouts = [(37, 3, 4, 64, 224), (123, 7, 5, 128, 1024), (1, 1, 128, 32, 32)]
        models = [Model(ROOT / "build/vision/fpga", name) for name in ("yunet", "mobilefacenet")]
        address = 0x83000001
        for model in models:
            for layer in model.manifest["layers"]:
                tensor = layer["output"]
                if tensor["address"] + tensor["bytes"] >= address:
                    raise RuntimeError("Scratch test area overlaps model activations")
            if model.manifest["image_address"] + len(model.image) >= address:
                raise RuntimeError("Scratch test area overlaps model weights")
        for width, height, channels, row, plane in layouts:
            raw = bytearray([239] * (channels * plane))
            for c in range(channels):
                for y in range(height):
                    begin = c * plane + y * row
                    raw[begin:begin + width] = bytes((c * 29 + y * 43 + x * 17) & 255 for x in range(width))
            tensor = {"shape": [1, channels, height, width], "address": address,
                      "row_stride": row, "plane_stride": plane, "bytes": len(raw)}
            board.upload(address, raw)
            expected = compare(tensor, f"scratch_{channels}x{height}x{width}")
            for offset, count in ((1, min(123, len(expected)-1)), (len(expected)-1, 1)):
                reply = board.command(14, struct.pack("<8I", address, width, height, channels,
                                                    row, plane, offset, count))
                if reply != expected[offset:offset + count]:
                    raise RuntimeError("Packed partial read differs")
            # Layout, bounds and overflow validation happens before any memory access.
            base = [address, width, height, channels, row, plane, 0, 1]
            changes = [{1: 0}, {2: 0}, {3: 0}, {4: width-1}, {5: height*row-1},
                       {6: len(expected)}, {6: len(expected)-1, 7: 2}, {7: 0}, {7: 4093},
                       {0: 0x7fffffff}, {0: 0xbfffffff, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2},
                       {1: 0xffffffff, 2: 0xffffffff, 3: 0xffffffff, 4: 0xffffffff, 5: 0xffffffff}]
            payloads = [b"", b"\0" * 31]
            for change in changes:
                values = list(base)
                for index, value in change.items():
                    values[index] = value
                payloads.append(struct.pack("<8I", *values))
            for payload in payloads:
                try:
                    board.command(14, payload)
                except BoardError as error:
                    if error.status != 3:
                        raise RuntimeError("Invalid packed read returned unexpected status") from error
                    invalid.append({"payload_hex": payload.hex(), "status": error.status})
                else:
                    raise RuntimeError("Invalid packed read was accepted")
            if board.download(address, len(raw)) != bytes(raw):
                raise RuntimeError("Read-only packed tests changed scratch memory")
        if args.model_outputs:
            for model in models:
                seen = set()
                for output in model.manifest["outputs"]:
                    tensor = output["tensor"]
                    if tensor["name"] not in seen:
                        compare(tensor, f"{model.name}:{tensor['name']}")
                        seen.add(tensor["name"])
        after = board.counters()
        if after["rx_errors"] != before["rx_errors"] or board.decoder.crc_errors or board.decoder.framing_errors:
            raise RuntimeError("Packed-read validation introduced UART errors")
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "board": identity, "project_evidence": project, "reads": records,
              "invalid_reads": invalid, "before": before, "after": after,
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "passed": True}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print(f"PASS: {len(records)} packed/physical tensors and {len(invalid)} rejected invalid requests", flush=True)


if __name__ == "__main__":
    main()
