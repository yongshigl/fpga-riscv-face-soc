#!/usr/bin/env python3
"""Check executable-RAM coherency with actual caches and all frontend depths."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--cache-ingress-write", action="store_true")
    parser.add_argument("--cache-fast-write-response", action="store_true")
    parser.add_argument("--cache-register-tags", action="store_true")
    parser.add_argument("--cache-tags-lutram", action="store_true")
    parser.add_argument("--disable-cache-statistics", action="store_true")
    parser.add_argument("--regfile-lutram", action="store_true")
    parser.add_argument("--register-bypass", action="store_true")
    parser.add_argument("--early-load-request", action="store_true")
    parser.add_argument("--split-mtime", action="store_true")
    parser.add_argument("--align-mmio-address", action="store_true")
    parser.add_argument("--control-load-guard", type=int, default=1, choices=(0, 1))
    parser.add_argument("--load-use-guard", type=int, default=1, choices=(0, 1))
    args = parser.parse_args()
    build = args.build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    elf, image = build / "cache_fence.elf", build / "cache_fence.hex"
    subprocess.run(["riscv64-unknown-elf-gcc", "-march=rv32i", "-mabi=ilp32",
                    "-nostdlib", "-nostartfiles", "-mno-relax", "-T", str(ROOT / "sw/link.ld"),
                    "-o", str(elf), str(ROOT / "sw/cache_fence_test.S")], check=True)
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                    "--verilog-data-width=4", str(elf), str(image)], check=True)
    sources = [ROOT / "rtl" / name for name in (
        "rv32i_pipeline_core_lutram.s", "uart_tx.s", "uart_rx.v", "cpu_data_port.v",
        "two_way_cache.v", "line_port_arbiter.v", "memory_line_backend.v", "rv32i_cached_soc.v")]
    sources.append(ROOT / "tb/tb_cached_fence.s")
    records = []
    for depth in range(3):
        for registered in (0, 1):
            name = f"pipeline{5+depth}_invalidate{registered}"
            binary = build / f"{name}.vvp"
            command = ["iverilog", "-g2012", "-s", "tb_cached_fence", "-o", str(binary),
                       f"-DCPU_FETCH_PIPE_STAGES={depth}", "-DCPU_DECOUPLE_FETCH=1",
                       "-DCPU_SEPARATE_EXMEM_PAYLOAD=1", "-DCPU_SEPARATE_FRONTEND_PAYLOAD=1",
                       "-DCPU_EARLY_BRANCH_CHECK=1", "-DCPU_FAST_TRAP_PATH=1",
                       f"-DCPU_REGISTER_BYPASS={int(args.register_bypass)}",
                       f"-DCPU_EARLY_LOAD_REQUEST={int(args.early_load_request)}",
                       f"-DCPU_SPLIT_MTIME={int(args.split_mtime)}",
                       f"-DCPU_ALIGN_MMIO_ADDRESS={int(args.align_mmio_address)}",
                       f"-DCPU_CONTROL_LOAD_GUARD={args.control_load_guard}",
                       f"-DCPU_LOAD_USE_GUARD={args.load_use_guard}",
                       "-DCPU_CACHE_FAST_RESPONSE=1", "-DCPU_CACHE_EARLY_RESTART=1",
                       f"-DCPU_CACHE_INGRESS_WRITE={int(args.cache_ingress_write)}",
                       f"-DCPU_CACHE_FAST_WRITE_RESPONSE={int(args.cache_fast_write_response)}",
                       f"-DCPU_CACHE_REGISTER_TAGS={int(args.cache_register_tags)}",
                       f"-DCPU_CACHE_STATISTICS={int(not args.disable_cache_statistics)}",
                       f"-DCPU_REGISTER_ICACHE_INVALIDATE={registered}", *map(str, sources)]
            if args.regfile_lutram:
                command.insert(1, "-DCPU_REGFILE_LUTRAM")
            if args.cache_tags_lutram:
                command.insert(1, "-DCPU_CACHE_TAGS_LUTRAM")
            subprocess.run(command, cwd=ROOT, check=True)
            result = subprocess.run(["vvp", str(binary), f"+image={image.name}"],
                                    cwd=build, capture_output=True, text=True)
            (build / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode or "PASS:" not in result.stdout:
                raise RuntimeError(f"{name} failed:\n{result.stdout}{result.stderr}")
            print(f"{name}: {result.stdout.strip()}", flush=True)
            records.append({"pipeline_stages": 5 + depth, "registered_invalidate": bool(registered),
                            "cache_ingress_write": args.cache_ingress_write,
                            "cache_fast_write_response": args.cache_fast_write_response,
                            "cache_register_tags": args.cache_register_tags,
                            "cache_tags_lutram": args.cache_tags_lutram,
                            "cache_statistics": not args.disable_cache_statistics,
                            "regfile_lutram": args.regfile_lutram,
                            "register_bypass": args.register_bypass,
                            "early_load_request": args.early_load_request,
                            "split_mtime": args.split_mtime,
                            "align_mmio_address": args.align_mmio_address,
                            "control_load_guard": bool(args.control_load_guard),
                            "load_use_guard": bool(args.load_use_guard),
                            "command": command, "output": result.stdout})
    report = {"passed": True, "cases": records, "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                          [*sources, ROOT / "sw/cache_fence_test.S", ROOT / "sw/link.ld"]}}
    (build / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
