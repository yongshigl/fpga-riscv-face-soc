#!/usr/bin/env python3
"""Compare compiler choices using unchanged CoreMark and an identical RTL build."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--simulation-build", type=Path, required=True)
    parser.add_argument("--ras-bits", type=int, default=3)
    parser.add_argument("--btb-bits", type=int, default=5)
    parser.add_argument("--ram-bytes", type=int, choices=(4096, 5120, 6144, 8192), default=8192)
    parser.add_argument("--control-load-guard", action="store_true")
    parser.add_argument("--load-use-guard", action="store_true")
    parser.add_argument("--multiply-pipeline", action="store_true")
    parser.add_argument("--separate-payload", action="store_true")
    parser.add_argument("--separate-frontend", action="store_true")
    parser.add_argument("--early-branch-check", action="store_true")
    parser.add_argument("--fast-trap-path", action="store_true")
    parser.add_argument("--regfile-lutram", action="store_true")
    parser.add_argument("--register-bypass", action="store_true")
    parser.add_argument("--early-load-request", action="store_true")
    parser.add_argument("--fetch-pipe-stages", type=int, default=0, choices=range(3))
    parser.add_argument("--decouple-fetch", action="store_true")
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--cache-fast-response", action="store_true")
    parser.add_argument("--cache-early-restart", action="store_true")
    parser.add_argument("--cache-ingress-write", action="store_true")
    parser.add_argument("--cache-fast-write-response", action="store_true")
    parser.add_argument("--cache-register-tags", action="store_true")
    parser.add_argument("--cache-tags-lutram", action="store_true")
    parser.add_argument("--disable-cache-statistics", action="store_true")
    parser.add_argument("--register-icache-invalidate", action="store_true")
    parser.add_argument("--icache-set-bits", type=int, default=7, choices=range(2, 10))
    parser.add_argument("--dcache-set-bits", type=int, default=6, choices=range(2, 10))
    parser.add_argument("--profiles", nargs="+", default=["o3", "o2", "o3_lto", "o2_lto", "o3_lto_inline", "o3_unroll"])
    args = parser.parse_args()
    if (args.cache_fast_response or args.cache_early_restart or args.cache_ingress_write or args.cache_fast_write_response or args.cache_register_tags or args.cache_tags_lutram or args.disable_cache_statistics or
            args.register_icache_invalidate) and not args.cached:
        parser.error("Cache response options require --cached")
    if args.early_load_request and not args.cached:
        parser.error("--early-load-request requires --cached")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    profiles = {
        "o3": [],
        "o2": ["--optimize", "O2"],
        "o3_lto": ["--lto"],
        "o2_lto": ["--optimize", "O2", "--lto"],
        "o3_lto_inline": ["--lto", "--inline-functions"],
        "o3_unroll": ["--extra-cflag=-funroll-loops"],
        "o3_align16": ["--extra-cflag=-falign-functions=16"],
        "o3_align32": ["--extra-cflag=-falign-functions=32"],
        "o3_align64": ["--extra-cflag=-falign-functions=64"],
        "o3_align_loops16": ["--extra-cflag=-falign-loops=16"],
        "o3_align16_loops8": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-falign-loops=8"],
        "o3_align16_loops16": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-falign-loops=16"],
        "o3_align16_loops32": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-falign-loops=32"],
        "o3_align16_jumps8": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-falign-jumps=8"],
        "o3_align16_no_peel": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fno-peel-loops"],
        "o3_no_reorder_blocks": ["--extra-cflag=-fno-reorder-blocks"],
        "o3_no_guess_branch": ["--extra-cflag=-fno-guess-branch-probability"],
        "o3_no_if_conversion": ["--extra-cflag=-fno-if-conversion", "--extra-cflag=-fno-if-conversion2"],
        "o3_align16_inline": ["--extra-cflag=-falign-functions=16", "--inline-functions"],
        "o3_align16_sifive7": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-mtune=sifive-7-series"],
        "o3_align16_size_tune": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-mtune=size"],
        "o3_align16_branch2": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-mbranch-cost=2"],
        "o3_align16_branch4": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-mbranch-cost=4"],
        "o3_align16_no_sched1": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fno-schedule-insns"],
        "o3_align16_no_sched2": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fno-schedule-insns2"],
        "o3_align16_sched_pressure": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched-pressure"],
        "o3_align16_pressure_branch2": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched-pressure", "--extra-cflag=-mbranch-cost=2"],
        "o3_align16_pressure_branch4": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched-pressure", "--extra-cflag=-mbranch-cost=4"],
        "o3_align16_sched_superblock": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks"],
        "o3_align16_superblock_pressure": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks", "--extra-cflag=-fsched-pressure"],
        "o3_align16_superblock_branch2": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks", "--extra-cflag=-mbranch-cost=2"],
        "o3_align16_superblock_b2_pressure": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks", "--extra-cflag=-mbranch-cost=2", "--extra-cflag=-fsched-pressure"],
        "o3_align16_modulo_sched": ["--extra-cflag=-falign-functions=16", "--extra-cflag=-fmodulo-sched"],
        "o3_relax": ["--relax"],
        "o3_align16_relax": ["--relax", "--extra-cflag=-falign-functions=16"],
        "o3_superblock_relax": ["--relax", "--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks"],
        "o3_superblock_b2_relax": ["--relax", "--extra-cflag=-falign-functions=16", "--extra-cflag=-fsched2-use-superblocks", "--extra-cflag=-mbranch-cost=2"],
        "o3_no_peel": ["--extra-cflag=-fno-peel-loops"],
        "os": ["--optimize", "Os"],
    }
    if any(name not in profiles for name in args.profiles):
        parser.error("Unknown compiler profile")
    results = []
    for name in args.profiles:
        flags = profiles[name]
        directory = output / name
        directory.mkdir(exist_ok=True)
        prepare = [sys.executable, str(ROOT / "tools/prepare_coremark.py"),
                   "--output", str(directory), "--clock-hz", "10000", "--iterations", "1",
                   "--ram-bytes", str(args.ram_bytes), "--readonly-in-ram", *flags]
        simulate = [sys.executable, str(ROOT / "tools/simulate_cpu.py"),
                    "--firmware", str(directory), "--build", str(args.simulation_build.resolve()),
                    "--ras-bits", str(args.ras_bits), "--btb-bits", str(args.btb_bits),
                    "--fetch-pipe-stages", str(args.fetch_pipe_stages),
                    "--icache-set-bits", str(args.icache_set_bits),
                    "--dcache-set-bits", str(args.dcache_set_bits),
                    "--profile", "--reuse"]
        for option in ("control_load_guard", "load_use_guard", "multiply_pipeline", "separate_payload", "separate_frontend",
                       "early_branch_check", "fast_trap_path", "regfile_lutram", "register_bypass", "early_load_request", "decouple_fetch", "cached", "cache_fast_response",
                       "cache_early_restart", "cache_ingress_write", "cache_fast_write_response", "cache_register_tags", "cache_tags_lutram", "disable_cache_statistics", "register_icache_invalidate"):
            if getattr(args, option):
                simulate.append("--" + option.replace("_", "-"))
        result = {"profile": name, "prepare_options": flags, "functional_only": True}
        with (directory / "experiment.log").open("w") as log:
            status = subprocess.run(prepare, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
            if status == 0:
                status = subprocess.run(simulate, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
                if status == 0:
                    result.update(json.loads((args.simulation_build / "result.json").read_text()))
                    shutil.copyfile(args.simulation_build / "simulation.log", directory / "simulation.log")
            result["crc_validated"] = status == 0
            result["exit_code"] = status
        results.append(result)
        (output / "sweep.json").write_text(json.dumps(results, indent=2) + "\n", encoding="ascii")
        print(f"{name}: CRC={result['crc_validated']} cycles={result.get('timed_cycles', '-')}", flush=True)


if __name__ == "__main__":
    main()
