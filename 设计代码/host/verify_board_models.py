#!/usr/bin/env python3
"""Compare every hardware layer of both face networks with exact integer sums."""
import argparse
import datetime
import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np

from fpga_model import Model, pack_tensor
from hardware_transport import Board
from board_evidence import validate_project
from transport_evidence import snapshot_transport

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def driver_sources(output):
    paths = [Path(__file__), *(ROOT / "vision" / name for name in
              ("board_evidence.py", "fpga_model.py", "hardware_transport.py", "serial_bridge.py", "windows_serial_pipeline.py", "camera_stream.py"))]
    directory = output / "driver_sources"
    directory.mkdir(parents=True, exist_ok=True)
    result = snapshot_transport(output)
    for path in paths:
        digest = file_hash(path)
        shutil.copy2(path, directory / path.name)
        if file_hash(directory / path.name) != digest:
            raise RuntimeError("Driver source changed while being archived")
        result[path.name] = digest
    return result


def profile_summary(profile, identity):
    cycles = sum(layer["cycles"] for layer in profile["layers"])
    reads = sum(layer["reads"] for layer in profile["layers"])
    writes = sum(layer["writes"] for layer in profile["layers"])
    return {"accelerator_cycles": cycles, "accelerator_ms": cycles * 1000 / identity["ai_hz"],
            "cpu_schedule_cycles": profile["cpu_cycles"],
            "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / identity["cpu_hz"],
            "ddr_read_transactions": reads, "ddr_write_transactions": writes,
            "ddr_read_bytes": reads * 32, "ddr_write_bytes": writes * 32,
            "compute_cycles": sum(layer["compute_cycles"] for layer in profile["layers"])}


def compare_baseline(result, identity, baseline):
    previous = next((entry for entry in baseline["models"] if entry["name"] == result["name"]), None)
    if previous is None or not previous.get("all_bytes_identical"):
        raise ValueError("Baseline does not contain a fully verified matching model")
    for key in ("model_sha256", "image_sha256", "input_sha256"):
        if result[key] != previous[key]:
            raise ValueError(f"A fair performance comparison requires the same {key}: {result['name']}")
    if len(previous["layers"]) != len(result["layers"]):
        raise ValueError("Baseline and candidate layer counts differ")
    old_layers = {layer["index"]: layer for layer in previous["layers"]}
    for layer in result["layers"]:
        old = old_layers[layer["index"]]
        if layer["bytes"] != old["bytes"] or layer["actual_sha256"] != old["actual_sha256"]:
            raise ValueError("Candidate layer bytes differ from the verified historical baseline")
    current = profile_summary(result["profile"], identity)
    old = profile_summary(previous["profile"], baseline["board"])
    improvement = {key: 1 - current[key] / old[key] for key in
                   ("accelerator_cycles", "accelerator_ms", "cpu_schedule_cycles", "cpu_schedule_ms",
                    "ddr_read_transactions", "ddr_write_transactions") if old[key]}
    old_profile = {layer["index"]: layer for layer in previous["profile"]["layers"]}
    layer_metrics = []
    for layer in result["profile"]["layers"]:
        before = old_profile[layer["index"]]
        layer_metrics.append({"index": layer["index"],
                              "candidate": layer, "baseline": before,
                              "cycle_reduction_fraction": 1 - layer["cycles"] / before["cycles"],
                              "read_reduction_fraction": 1 - layer["reads"] / before["reads"] if before["reads"] else 0})
    return {"same_model_image_input_and_layer_bytes": True, "candidate": current, "baseline": old,
            "candidate_ai_hz": identity["ai_hz"], "baseline_ai_hz": baseline["board"]["ai_hz"],
            "candidate_cpu_hz": identity["cpu_hz"], "baseline_cpu_hz": baseline["board"]["cpu_hz"],
            "reduction_fraction": improvement, "layers": layer_metrics,
            "scope": "Internal accelerator counters; UART/readback timing excluded. Cycles and actual-clock milliseconds are reported separately."}


def verify_layer(board, layer, values, check_packed=False, output=None):
    tensor = layer["output"]
    expected = pack_tensor(values, tensor, quantized=True)
    begin = time.monotonic()
    actual = board.download(tensor["address"], tensor["bytes"])
    elapsed = (time.monotonic() - begin) * 1000
    if len(actual) != len(expected):
        raise RuntimeError("Full padded layer readback length differs")
    difference = np.frombuffer(actual, np.uint8).astype(np.int16) - np.frombuffer(expected, np.uint8).astype(np.int16)
    mismatch = int(np.count_nonzero(difference))
    descriptor = np.asarray(layer["registers"], dtype="<u4").tobytes()
    record = {"index": layer["index"], "op": layer["op"], "onnx_node": layer["onnx_node"],
              "source_tensor": layer["source"], "target_tensor": layer["target"],
              "tensor": tensor, "descriptor_sha256": hashlib.sha256(descriptor).hexdigest(),
              "bytes": len(actual), "mismatched_bytes": mismatch, "full_padding_verified": True,
              "padded_readback_ms": elapsed,
              "maximum_absolute_error": int(np.abs(difference).max(initial=0)),
              "actual_sha256": hashlib.sha256(actual).hexdigest(),
              "expected_sha256": hashlib.sha256(expected).hexdigest()}
    if mismatch:
        if output:
            (output / f"layer_{layer['index']}_actual.bin").write_bytes(actual)
            (output / f"layer_{layer['index']}_expected.bin").write_bytes(expected)
        raise RuntimeError(f"FPGA layer {layer['index']} differs from the exact reference")
    if check_packed:
        actual_logical = board.download_tensor(tensor)
        expected_logical = np.asarray(values, dtype=np.uint8).tobytes()
        if len(actual_logical) != len(expected_logical) or actual_logical != expected_logical:
            if output:
                (output / f"layer_{layer['index']}_packed_actual.bin").write_bytes(actual_logical)
                (output / f"layer_{layer['index']}_packed_expected.bin").write_bytes(expected_logical)
            raise RuntimeError(f"FPGA layer {layer['index']} packed readback differs")
        record["packed_readback"] = {"logical_bytes": len(actual_logical), "all_bytes_identical": True,
                                     "actual_sha256": hashlib.sha256(actual_logical).hexdigest(),
                                     "expected_sha256": hashlib.sha256(expected_logical).hexdigest(),
                                     "transport": dict(board.last_download_stats)}
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/board_validation")
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--names", nargs="+", default=["yunet", "mobilefacenet"])
    parser.add_argument("--project", type=Path, help="Frozen PDS project with its successful JTAG program record")
    parser.add_argument("--baseline", type=Path, help="Verified fixed-input board report for fair counter comparison")
    parser.add_argument("--check-packed-readback", action="store_true", help="Also validate every layer through command 14; full padded reads are retained")
    parser.add_argument("--skip-gallery-test", action="store_true", help="Preserve a nonempty gallery; still check CPU feature normalization")
    parser.add_argument("--prepare-only", action="store_true", help="Compute and bind exact references without opening a serial connection")
    args = parser.parse_args()
    if args.baseline and not args.project and not args.prepare_only:
        parser.error("A hardware performance comparison requires --project")
    if args.baseline and (args.output / "report.json").resolve() == args.baseline.resolve():
        parser.error("The output report must not overwrite its historical baseline")
    args.output.mkdir(parents=True, exist_ok=True)
    sources = driver_sources(args.output)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    prepared = []
    for name in args.names:
        model = Model(args.models, name)
        input_bytes = (args.models / f"{name}_input.bin").read_bytes()
        print(f"{name}: computing the independent float64 integer reference", flush=True)
        tensors, outputs, reference_ms = model.reference(input_bytes, force_float64=True)
        np.savez_compressed(args.output / f"{name}_integer_reference.npz", **outputs)
        provenance = {"name": name, "model_sha256": model.manifest["model_sha256"],
                      "image_sha256": model.manifest["image_sha256"],
                      "manifest_sha256": file_hash(args.models / f"{name}.json"),
                      "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
                      "reference_ms": reference_ms, "layers": []}
        for layer in model.manifest["layers"]:
            expected = pack_tensor(tensors[layer["target"]], layer["output"], quantized=True)
            provenance["layers"].append({"index": layer["index"], "bytes": len(expected),
                                         "expected_sha256": hashlib.sha256(expected).hexdigest()})
        if baseline:
            previous = next((entry for entry in baseline["models"] if entry["name"] == name), None)
            if previous is None or not previous.get("all_bytes_identical"):
                raise ValueError("Baseline is missing a fully verified model")
            for key in ("model_sha256", "image_sha256", "input_sha256"):
                if provenance[key] != previous[key]:
                    raise ValueError(f"Fixed reference {key} differs from historical baseline")
            if (any(layer["expected_sha256"] != layer["actual_sha256"] for layer in previous["layers"]) or
                    provenance["layers"] != [{key: layer[key] for key in ("index", "bytes", "expected_sha256")}
                                          for layer in previous["layers"]]):
                raise ValueError("Exact reference layer bytes differ from historical baseline")
        prepared.append((model, input_bytes, tensors, outputs, provenance))
    preparation = {"prepared_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "execution": "Host exact-reference preparation; no serial or FPGA access", "hardware_tested": False,
                   "source_hashes": sources, "baseline_path": str(args.baseline) if args.baseline else None,
                   "baseline_sha256": file_hash(args.baseline) if args.baseline else None,
                   "reference": "PyTorch float64 integer-exact convolution and rational ties-to-even rounding",
                   "models": [item[4] for item in prepared],
                   "total_layers": sum(len(item[0].manifest["layers"]) for item in prepared),
                   "total_padded_output_bytes": sum(layer["bytes"] for item in prepared for layer in item[4]["layers"])}
    (args.output / "preparation.json").write_text(json.dumps(preparation, indent=2) + "\n")
    if args.prepare_only:
        print("PASS: exact references prepared; no hardware was accessed", flush=True)
        return
    results = []
    with Board(args.port, bridge_log=args.output / "bridge.log") as board:
        identity = board.ping()
        project = validate_project(args.project, identity)
        if args.project:
            shutil.copy2(args.project / "manifest.json", args.output / "project_manifest.json")
        capabilities = board.capabilities()
        if args.check_packed_readback and not capabilities.get("packed_tensor_read", False):
            raise RuntimeError("Packed layer validation requires firmware capability packed_tensor_read")
        print("Board:", json.dumps(identity), flush=True)
        if identity["rx_errors"]:
            raise RuntimeError("UART receive errors before verification")
        pattern = np.random.default_rng(261001).integers(0, 256, 8191, dtype=np.uint8).tobytes()
        board.upload(0x83f00001, pattern)
        if board.download(0x83f00001, len(pattern)) != pattern:
            raise RuntimeError("Integrated CPU/DDR unaligned readback failed")
        print("PASS: RISC-V DDR byte masks and 8191-byte unaligned readback", flush=True)
        for model, input_bytes, tensors, outputs, provenance in prepared:
            name = model.name
            if not args.skip_upload:
                print(f"{name}: uploading and checking {len(model.image)} model bytes", flush=True)
                model.upload(board, progress=lambda done, total: print(f"  upload {done}/{total}", flush=True))
            board.upload(model.manifest["input"]["address"], input_bytes)
            print(f"{name}: RISC-V scheduling {len(model.manifest['layers'])} hardware layers", flush=True)
            profile = board.run(model.manifest)
            layer_results = []
            layer_output = args.output / name
            layer_output.mkdir(exist_ok=True)
            for layer in model.manifest["layers"]:
                record = verify_layer(board, layer, tensors[layer["target"]], args.check_packed_readback, layer_output)
                layer_results.append(record)
                print(f"{name} layer {layer['index']:02d}: {record['bytes']} padded bytes, mismatches=0", flush=True)
            result = {"name": name, "model_sha256": model.manifest["model_sha256"],
                      "image_sha256": model.manifest["image_sha256"],
                      "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
                      "calibration_input_used_for_hardware_equivalence_only": True,
                      "manifest_sha256": provenance["manifest_sha256"],
                      "reference_ms": provenance["reference_ms"], "profile": profile, "layers": layer_results,
                      "profile_totals": profile_summary(profile, identity),
                      "accelerator_ms": sum(layer["cycles"] for layer in profile["layers"]) * 1000 / identity["ai_hz"],
                      "cpu_schedule_ms": profile["cpu_cycles"] * 1000 / identity["cpu_hz"],
                      "all_bytes_identical": True}
            if baseline:
                result["baseline_comparison"] = compare_baseline(result, identity, baseline)
            if name == "mobilefacenet":
                match = board.match(model.manifest)
                actual = np.asarray(match["embedding_q15"], np.float64) / 32768
                reference = next(iter(outputs.values())).reshape(-1).astype(np.float64)
                reference /= np.linalg.norm(reference)
                cosine = float(actual @ reference / np.linalg.norm(actual))
                if cosine < 0.999999:
                    raise RuntimeError("RISC-V normalized embedding differs from hardware model output")
                result["cpu_embedding_cosine_vs_reference"] = cosine
                if args.skip_gallery_test:
                    result["cpu_match"] = match
                    result["gallery_test"] = {"executed": False, "reason": "--skip-gallery-test"}
                else:
                    if match["gallery_valid"]:
                        raise RuntimeError("Gallery is nonempty; rerun with --skip-gallery-test or on a fresh JTAG configuration")
                    board.enroll(0)
                    enrolled = board.match(model.manifest)
                    if enrolled["identity_index"] != 0 or enrolled["cosine"] < 0.999:
                        raise RuntimeError("RISC-V gallery enrollment/matching failed")
                    result["cpu_match"] = enrolled
                    result["gallery_test"] = {"executed": True, "initial_gallery_valid": 0}
                    board.command(8)
            results.append(result)
            (args.output / f"{name}_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")
        counters = board.counters()
        if counters["rx_errors"]:
            raise RuntimeError("UART overflow or framing errors during model verification")
    report = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "FPGA INT8 CNN and RISC-V CPU model scheduling/normalization/gallery comparison",
              "reference": preparation["reference"],
              "board": identity, "project_evidence": project, "models": results, "counters": counters,
              "source_hashes": sources, "capabilities": capabilities,
              "baseline_sha256": preparation["baseline_sha256"], "baseline_path": preparation["baseline_path"],
              "packed_readback_checked": args.check_packed_readback,
              "total_layers": sum(len(result["layers"]) for result in results),
              "total_padded_output_bytes": sum(layer["bytes"] for result in results for layer in result["layers"]),
              "all_full_padded_bytes_identical": True, "complete": True,
              "scope": "Hardware equivalence on fixed test inputs; this is not recognition dataset accuracy",
              "timing_scope": "Accelerator elapsed time includes its DMA; CPU schedule time includes waiting for the accelerator"}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
    print("PASS: all FPGA model layers and CPU matching verified", flush=True)


if __name__ == "__main__":
    main()
