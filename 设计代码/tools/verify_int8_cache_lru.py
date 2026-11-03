#!/usr/bin/env python3
"""Extend the completed stencil-cache experiments with exact LRU candidates."""
import argparse
import datetime
import json
from pathlib import Path

import verify_int8_cache as verification

ROOT = verification.ROOT


class ExactLruCache:
    def __init__(self, lines):
        self.lines = lines
        self.recent = []

    def access(self, address):
        miss = address not in self.recent
        if not miss:
            self.recent.remove(address)
        self.recent.append(address)
        if len(self.recent) > self.lines:
            self.recent.pop(0)
        return miss


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("/home/yongshi/fpga-sim/project_completion_20261013/ai"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/experiments/project_completion_20261013/ai")
    args = parser.parse_args()
    base_path = args.output / "report.json"
    base = json.loads(base_path.read_text())
    if not base.get("complete") or not base.get("all_default_metrics_equal_original"):
        raise RuntimeError("Complete the original/default/PLRU validation first")
    cases = args.build / "cases"
    entries = base["cases"]
    for entry in entries:
        if verification.digest(cases / entry["name"] / "expected.bin") != entry["expected_sha256"]:
            raise RuntimeError("The independent reference input changed since the first experiment")
    report = {"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Verilator simulation only; no hardware or PDS runs", "hardware_tested": False,
              "previous_report_sha256": verification.digest(base_path),
              "historical_board_report_sha256": base["historical_board_report_sha256"],
              "verifier_sha256": verification.digest(__file__),
              "shared_verifier_sha256": verification.digest(verification.__file__),
              "testbench_sha256": verification.digest(ROOT / "tb/sim_int8_cache.cpp"),
              "reference": base["reference"], "memory": base["memory"], "cases": entries,
              "configurations": {}, "summary": {}}
    original_cache = verification.LineCache
    for name, lines, outer, full_lru in [("default_after_lru", 2, 0, False),
                                       ("dw_channel_cache8_lru", 8, 1, True),
                                       ("dw_channel_cache16_lru", 16, 1, True)]:
        result = verification.build_and_run(name, lines, outer, ROOT / "rtl", args.build,
                                            entries, cases, args.output, full_lru=full_lru)
        report["configurations"][name] = result
        verification.LineCache = ExactLruCache if full_lru else original_cache
        for entry in entries:
            measured = result["cases"][entry["name"]]
            original = base["configurations"]["original"]["cases"][entry["name"]]
            if name == "default_after_lru" and measured != original:
                raise RuntimeError("The optional exact-LRU branch changed default behavior")
            if not entry["expected_error"]:
                expected_reads = verification.replay(entry["config"], [(name, lines, outer)])[name]
                if measured["reads"] != expected_reads:
                    raise RuntimeError(f"Exact-LRU RTL traffic differs from replay: {entry['name']}")
                if measured["writes"] != original["writes"] or measured["compute"] != original["compute"]:
                    raise RuntimeError("Exact-LRU candidate changed output or compute coverage")
        model_summary = {}
        for model in ("yunet", "mobilefacenet"):
            selected = [entry["name"] for entry in entries if entry.get("model") == model]
            total = {key: sum(result["cases"][item][key] for item in selected)
                     for key in ("cycles", "reads", "writes", "compute")}
            original_cycles = sum(base["configurations"]["original"]["cases"][item]["cycles"] for item in selected)
            original_reads = sum(base["configurations"]["original"]["cases"][item]["reads"] for item in selected)
            total["cycle_reduction_fraction"] = 1 - total["cycles"] / original_cycles
            total["read_reduction_fraction"] = 1 - total["reads"] / original_reads
            model_summary[model] = total
        report["summary"][name] = model_summary
        report["configurations"][name]["declared_cache_storage_bits"] = (
            lines * (256 + 32 + 1) + (lines * (lines - 1).bit_length() if full_lru else lines - 1))
        print(f"CONFIGURATION_VERIFIED {name} {json.dumps(model_summary)}", flush=True)
        (args.output / "lru_partial.json").write_text(json.dumps(report, indent=2) + "\n")
    verification.LineCache = original_cache
    report.update(complete=True, all_default_metrics_equal_original=True,
                  all_model_layers_byte_identical=True, all_rtl_reads_equal_independent_replay=True,
                  all_dma_backpressure_and_output_bounds_checks_passed=True)
    (args.output / "lru_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS_REPORT {args.output / 'lru_report.json'}", flush=True)


if __name__ == "__main__":
    main()
