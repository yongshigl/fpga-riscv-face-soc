#!/usr/bin/env python3
"""Compare optional depthwise traversal/cache configurations with exact references.

The DDR address replay is checked against every historical board layer counter.
RTL tests use randomized DMA backpressure, all 124 exported model layers,
independent float64 integer-exact references, and a single reset across jobs.
Reported cycle improvements are simulation measurements, not board results.
"""
import argparse
import datetime
import hashlib
import itertools
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
from fpga_model import Model, unpack_tensor
from verify_int8 import case, random_case


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LineCache:
    """The RTL fully associative, invalid-first, tree PLRU gather cache."""
    def __init__(self, lines):
        self.tags = [None] * lines
        self.tree = [0] * (lines - 1)
        self.bits = (lines - 1).bit_length()

    def access(self, address):
        hit = address in self.tags
        if hit:
            way = self.tags.index(address)
        elif None in self.tags:
            way = self.tags.index(None)
        else:
            node, way = 0, 0
            for _ in range(self.bits):
                direction = self.tree[node]
                way = way * 2 + direction
                node = node * 2 + 1 + direction
        self.tags[way] = address
        node = 0
        for level in range(self.bits):
            direction = (way >> (self.bits - 1 - level)) & 1
            self.tree[node] = 1 - direction
            node = node * 2 + 1 + direction
        return not hit


def gather_addresses(registers, channel_outer):
    cfg = [0] + registers
    op = cfg[1]
    iw, ih = cfg[7] & 65535, cfg[7] >> 16
    cin, cout = cfg[8] & 65535, cfg[8] >> 16
    ow, oh = cfg[9] & 65535, cfg[9] >> 16
    row = cfg[10] & 65535
    kw, kh = cfg[13] & 15, (cfg[13] >> 4) & 15
    sx, sy = (cfg[13] >> 8) & 15, (cfg[13] >> 12) & 15
    px, py = (cfg[13] >> 16) & 15, (cfg[13] >> 20) & 15
    channels = [0] if op == 1 else range(cout)
    if op == 2 and channel_outer:
        tiles = ((y, x, channel) for channel, y, x in itertools.product(channels, range(oh), range(0, ow, 32)))
    else:
        tiles = itertools.product(range(oh), range(0, ow, 32), channels)
    for tile_y, tile_x, channel in tiles:
        pixels = min(32, ow - tile_x)
        for reduction_channel in (range(cin) if op == 1 else [channel]):
            for kernel_y in (range(kh) if op in (1, 2, 4) else [0]):
                for kernel_x in (range(kw) if op in (1, 2, 4) else [0]):
                    x = tile_x // 2 if op == 5 else tile_x * (1 if op == 3 else sx) + kernel_x - px
                    y = tile_y // 2 if op == 5 else tile_y * (1 if op == 3 else sy) + kernel_y - py
                    last_x = x + ((pixels - 1) // 2 if op == 5 else
                                  (pixels - 1) * 2 if sx == 2 and op != 3 else pixels - 1)
                    if y < 0 or y >= ih or x >= iw or last_x < 0:
                        continue
                    for source in ([cfg[2], cfg[3]] if op == 3 else [cfg[2]]):
                        address = source + reduction_channel * cfg[11] + y * row
                        for line in range(max(x, 0) // 32, min(last_x, iw - 1) // 32 + 1):
                            yield address + line * 32


def replay(registers, configurations):
    op, cout = registers[0], registers[7] >> 16
    reduction = (1 if op == 2 else registers[7] & 65535) * (registers[12] & 15) ** 2
    preload = ((reduction * ((cout + 15) // 16) + 1) // 2 + (cout + 1) // 2) if op in (1, 2) else 0
    result = {}
    for outer in (0, 1):
        selected = [entry for entry in configurations if entry[2] == outer]
        caches = {name: LineCache(lines) for name, lines, _ in selected}
        counts = {name: preload for name in caches}
        for address in gather_addresses(registers, outer):
            for name, cache in caches.items():
                counts[name] += cache.access(address)
        result.update(counts)
    return result


def make_cases(directory, models, historical):
    rng = np.random.default_rng(261013)
    specs = [
        ("pointwise_tail", 3, 3, 37, 17, 1, 1, 0, 1),
        ("spatial_stride2", 3, 9, 67, 17, 3, 2, 1, 1),
        ("spatial_kernel7", 3, 9, 37, 17, 7, 1, 3, 1),
        ("maximum_reduction", 64, 3, 35, 32, 3, 1, 1, 1),
        ("maximum_weights", 512, 1, 1, 128, 1, 1, 0, 1),
        ("depthwise_tail", 17, 7, 65, 17, 3, 1, 1, 2),
        ("depthwise_stride2", 17, 9, 67, 17, 3, 2, 1, 2),
        ("depthwise_global7", 17, 7, 7, 17, 7, 1, 0, 2),
        ("depthwise_kernel7_border", 5, 9, 67, 5, 7, 1, 3, 2),
        ("depthwise_single_pixel", 1, 1, 1, 1, 3, 1, 1, 2),
        ("depthwise_tile_boundary", 17, 3, 33, 17, 3, 1, 1, 2),
        ("depthwise_three_lines", 3, 5, 97, 3, 3, 1, 1, 2),
        ("depthwise_pointwise", 17, 3, 65, 17, 1, 1, 0, 2),
        ("residual_zeros", 17, 5, 67, 17, 1, 1, 0, 3),
        ("pool_odd_width", 3, 7, 67, 3, 2, 2, 0, 4),
        ("resize_tail", 3, 5, 35, 3, 1, 2, 0, 5),
    ]
    entries = [random_case(directory, name, rng, cin, height, width, cout, kernel, stride, padding, op)
               for name, cin, height, width, cout, kernel, stride, padding, op in specs]
    # Distinct data at identical addresses follows other jobs without a reset.
    entries.append(random_case(directory, "depthwise_rewrite_same_addresses", rng, 17, 7, 65, 17, 3, 1, 1, 2))
    entries.append(random_case(directory, "unsupported_kernel2", rng, 3, 5, 35, 17, 2, 1, 0, 1, 1))
    for name, fault in (("ddr_read_fault", 1), ("ddr_write_fault", 2)):
        entries.append(random_case(directory, name, rng, 3, 5, 35, 17, 3, 1, 1, 1, 2, fault))
    for name in ("yunet", "mobilefacenet"):
        model = Model(models, name)
        payload = (models / f"{name}_input.bin").read_bytes()
        reference, _, _ = model.reference(payload, force_float64=True)
        historical_layers = next(item["layers"] for item in historical["models"] if item["name"] == name)
        for layer in model.manifest["layers"]:
            registers = layer["registers"]
            op = layer["op"]
            source = reference[layer["source"]]
            expected = reference[layer["target"]]
            second = weights = parameters = None
            if op in (1, 2):
                cout, kernel = registers[7] >> 16, registers[12] & 15
                reduction = (1 if op == 2 else source.shape[1]) * kernel * kernel
                offset = registers[4] - model.manifest["image_address"]
                values = np.frombuffer(model.image[offset:offset + ((cout + 15) // 16) * reduction * 16], np.int8)
                weights = values.reshape(-1, reduction, 16).transpose(0, 2, 1).reshape(-1, reduction)[:cout]
                weights = weights.reshape(cout, 1 if op == 2 else source.shape[1], kernel, kernel)
                offset = registers[5] - model.manifest["image_address"]
                parameters = [struct.unpack_from("<iiBBBBI", model.image, offset + channel * 16)[:5]
                              for channel in range(cout)]
            elif op == 3:
                second_name = next(tensor["name"] for tensor in model.manifest["tensors"] if tensor["address"] == registers[2])
                second = reference[second_name]
            entry = case(directory, f"{name}_layer_{layer['index']:03d}", registers, source, second, weights, parameters)
            packed_expected = (directory / entry["name"] / "expected.bin").read_bytes()
            if not np.array_equal(unpack_tensor(packed_expected, layer["output"]), expected):
                raise RuntimeError("Two independent exact reference paths disagree")
            measured = historical_layers[layer["index"]]
            if entry["expected_sha256"] != measured["actual_sha256"]:
                raise RuntimeError(f"Independent exact reference differs from historical board layer: {name}/{layer['index']}")
            entry.update(model=name, layer=layer["index"], op=op,
                         model_sha256=model.manifest["model_sha256"], image_sha256=model.manifest["image_sha256"])
            entries.append(entry)
        print(f"REFERENCE_COMPLETE {name} layers={len(model.manifest['layers'])}", flush=True)
    return entries


def build_and_run(name, lines, outer, rtl_directory, build, entries, cases, report_directory, full_lru=False):
    destination = build / name
    destination.mkdir(parents=True, exist_ok=True)
    rtl = [rtl_directory / item for item in ("int8_mac_array.v", "int8_requantize.v", "int8_accelerator.v")]
    command = ["verilator", "--cc", "--exe", "--build", "-j", "2", "-Wall", "--Wno-WIDTH", "--Wno-UNUSED",
               "--Wno-DECLFILENAME", "--Wno-SYNCASYNCNET", "--top-module", "int8_accelerator",
               "--Mdir", str(destination), "-GMAC_PIXELS=16"]
    if name != "original":
        command += [f"-GREAD_CACHE_LINES={lines}", f"-GDEPTHWISE_CHANNEL_OUTER={outer}",
                    f"-GREAD_CACHE_FULL_LRU={int(full_lru)}"]
    command += [*map(str, rtl), str(ROOT / "tb/sim_int8_cache.cpp")]
    print(f"COMPILE {name}", flush=True)
    with (destination / "compile.log").open("w") as output:
        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
    print(f"SIMULATE {name} cases={len(entries)}", flush=True)
    result = subprocess.run([str(destination / "Vint8_accelerator"),
                             *[str((cases / item["name"]).resolve()) for item in entries]], capture_output=True, text=True)
    (report_directory / f"{name}.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name}: {result.stdout[-2000:]}{result.stderr}")
    matches = re.findall(r"PASS case=(\S+) cycles=(\d+) reads=(\d+) writes=(\d+) compute=(\d+) error=(\d+)", result.stdout)
    if len(matches) != len(entries):
        raise RuntimeError("Simulator did not validate every test case")
    metrics = {case_name: dict(zip(("cycles", "reads", "writes", "compute", "error"), map(int, values)))
               for case_name, *values in matches}
    return {"read_cache_lines": lines, "depthwise_channel_outer": outer, "read_cache_full_lru": bool(full_lru),
            "rtl_sha256": {path.name: digest(path) for path in rtl}, "cases": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("/home/yongshi/fpga-sim/project_completion_20261013/ai"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/experiments/project_completion_20261013/ai")
    parser.add_argument("--models", type=Path, default=ROOT / "build/vision/fpga")
    parser.add_argument("--original-rtl", type=Path)
    args = parser.parse_args()
    args.build.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = args.build / "cases"
    cases.mkdir(parents=True, exist_ok=True)
    configurations = [("default", 2, 0), ("cache8_only", 8, 0), ("cache16_only", 16, 0),
                      ("dw_channel_cache2", 2, 1), ("dw_channel_cache8", 8, 1), ("dw_channel_cache16", 16, 1)]
    report = {"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Verilator simulation and DDR address replay; no board programming or measurements",
              "hardware_tested": False, "clock_hz_assumed_for_comparison_only": 63000000,
              "reference": "Independent PyTorch float64 integer-exact convolution; rational ties-to-even quantization",
              "memory": "64 MiB, deterministic randomized backpressure/delays, one reset across all jobs",
              "testbench_sha256": digest(ROOT / "tb/sim_int8_cache.cpp"),
              "verifier_sha256": digest(__file__), "address_replay": [], "configurations": {}}
    historical_path = ROOT / "build/vision/unified_board_validation/report.json"
    report["historical_board_report_sha256"] = digest(historical_path)
    historical = json.loads(historical_path.read_text())
    for historical_model in historical["models"]:
        name = historical_model["name"]
        model = Model(args.models, name)
        for layer, measured in zip(model.manifest["layers"], historical_model["profile"]["layers"]):
            counts = replay(layer["registers"], configurations)
            if counts["default"] != measured["reads"]:
                raise RuntimeError(f"Address replay differs from real board counter: {name}/{layer['index']}")
            report["address_replay"].append({"model": name, "layer": layer["index"], "op": layer["op"],
                                            "historical_board_reads": measured["reads"], "predicted_reads": counts})
        print(f"ADDRESS_REPLAY_VALIDATED {name} layers={len(model.manifest['layers'])}", flush=True)
    entries = make_cases(cases, args.models, historical)
    report["cases"] = entries
    original = args.original_rtl or args.build / "original_rtl"
    if not (original / "int8_accelerator.v").exists():
        raise RuntimeError("Pass --original-rtl with the untouched historical engine for default-equivalence validation")
    report["configurations"]["original"] = build_and_run("original", 2, 0, original, args.build, entries, cases, args.output)
    # Cache-only candidates are analyzed by complete address replay. Their tiny
    # traffic improvement does not warrant extra synthesis/implementation runs.
    for name, lines, outer in [configurations[0], *configurations[3:]]:
        result = build_and_run(name, lines, outer, ROOT / "rtl", args.build, entries, cases, args.output)
        report["configurations"][name] = result
        for entry in entries:
            metrics = result["cases"][entry["name"]]
            if not entry["expected_error"]:
                reads = replay(entry["config"], [(name, lines, outer)])[name]
                if reads != metrics["reads"]:
                    raise RuntimeError(f"RTL traffic differs from independent replay: {name}/{entry['name']}")
            baseline = report["configurations"]["original"]["cases"][entry["name"]]
            if name == "default" and metrics != baseline:
                raise RuntimeError(f"Default metrics differ from historical engine: {entry['name']}")
            if not entry["expected_error"] and (metrics["writes"] != baseline["writes"] or metrics["compute"] != baseline["compute"]):
                raise RuntimeError(f"Traversal changed output count or compute coverage: {name}/{entry['name']}")
        print(f"CONFIGURATION_VERIFIED {name}", flush=True)
        (args.output / "partial.json").write_text(json.dumps(report, indent=2) + "\n")
    report["summary"] = {}
    for name, configuration in report["configurations"].items():
        model_results = {}
        for model in ("yunet", "mobilefacenet"):
            selected = [configuration["cases"][item["name"]] for item in entries if item.get("model") == model]
            sums = {key: sum(item[key] for item in selected) for key in ("cycles", "reads", "writes", "compute")}
            baseline = report["configurations"]["original"]
            original_cycles = sum(baseline["cases"][item["name"]]["cycles"] for item in entries if item.get("model") == model)
            sums["cycle_reduction_fraction"] = 1 - sums["cycles"] / original_cycles
            sums["simulation_ms_at_assumed_63mhz"] = sums["cycles"] / 63000
            model_results[model] = sums
        report["summary"][name] = model_results
    report["all_default_metrics_equal_original"] = True
    report["all_model_layers_byte_identical"] = True
    report["all_rtl_reads_equal_independent_replay"] = True
    report["all_dma_backpressure_and_output_bounds_checks_passed"] = True
    report["complete"] = True
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2), flush=True)
    print(f"PASS_REPORT {args.output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
