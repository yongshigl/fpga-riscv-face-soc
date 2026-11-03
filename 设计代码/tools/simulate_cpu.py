#!/usr/bin/env python3
"""Run a CPU candidate against the official single-iteration CoreMark CRC checks."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, default=ROOT / "rtl/rv32i_pipeline_core_lutram.s")
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--btb-bits", type=int, default=5, choices=range(1, 8))
    parser.add_argument("--ras-bits", type=int, default=0, choices=range(0, 5))
    parser.add_argument("--control-load-guard", action="store_true")
    parser.add_argument("--load-use-guard", action="store_true")
    parser.add_argument("--multiply-pipeline", action="store_true")
    parser.add_argument("--mul-carry-save-merge", action="store_true")
    parser.add_argument("--separate-payload", action="store_true")
    parser.add_argument("--separate-frontend", action="store_true")
    parser.add_argument("--early-branch-check", action="store_true")
    parser.add_argument("--fast-trap-path", action="store_true")
    parser.add_argument("--fast-redirect-select", action="store_true")
    parser.add_argument("--regfile-lutram", action="store_true")
    parser.add_argument("--register-bypass", action="store_true")
    parser.add_argument("--counter-csr-predecode", action="store_true")
    parser.add_argument("--csr-address-predecode", action="store_true")
    parser.add_argument("--early-load-request", action="store_true")
    parser.add_argument("--fetch-pipe-stages", type=int, default=0, choices=range(3))
    parser.add_argument("--decouple-fetch", action="store_true")
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--soc", type=Path, help="Independent cached SoC source; requires --cached")
    parser.add_argument("--split-mtime", action="store_true")
    parser.add_argument("--align-mmio-address", action="store_true")
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
    parser.add_argument("--no-cache", action="store_true", help="Use the synchronous backing memory without cached hits")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="Reuse only an identical compiled RTL configuration")
    args = parser.parse_args()
    if args.soc and not args.cached:
        parser.error("--soc requires --cached")
    if args.no_cache and not args.cached:
        parser.error("--no-cache requires --cached")
    if args.early_load_request and not args.cached:
        parser.error("--early-load-request requires --cached")
    if (args.cache_fast_response or args.cache_early_restart or args.cache_ingress_write or args.cache_fast_write_response or args.cache_register_tags or args.cache_tags_lutram or args.disable_cache_statistics or
            args.register_icache_invalidate) and not args.cached:
        parser.error("Cache response options require --cached")
    core, firmware, build = args.core.resolve(), args.firmware.resolve(), args.build.resolve()
    soc = args.soc.resolve() if args.soc else ROOT / "rtl/rv32i_cached_soc.v"
    core_text = core.read_text()
    if args.split_mtime and (not args.cached or "parameter integer SPLIT_MTIME" not in
            soc.read_text()):
        parser.error("--split-mtime requires a cached SoC implementing the independent timer candidate")
    if args.align_mmio_address and (not args.cached or "parameter integer ALIGN_MMIO_ADDRESS" not in
            soc.read_text()):
        parser.error("--align-mmio-address requires the independent cached SoC candidate")
    if args.mul_carry_save_merge and (not args.multiply_pipeline or
            "parameter integer MUL_CARRY_SAVE_MERGE" not in core_text):
        parser.error("--mul-carry-save-merge requires the independent merge candidate and --multiply-pipeline")
    if args.counter_csr_predecode and "parameter integer COUNTER_CSR_PREDECODE" not in core_text:
        parser.error("This core does not implement --counter-csr-predecode")
    if args.csr_address_predecode and "parameter integer CSR_ADDRESS_PREDECODE" not in core_text:
        parser.error("This core does not implement --csr-address-predecode")
    if args.fast_redirect_select and "parameter integer FAST_REDIRECT_SELECT" not in core_text:
        parser.error("This core does not implement --fast-redirect-select")
    manifest = json.loads((firmware / "firmware.json").read_text())
    if manifest["iterations"] != 1 or manifest["clock_hz"] != 10000:
        parser.error("Use a one-iteration, 10000-Hz functional image; simulation is not a published score")
    build.mkdir(parents=True, exist_ok=True)
    images = build / "images"
    images.mkdir(exist_ok=True)
    for name in ("coremark_board_text.hex", "coremark_board_data.hex"):
        shutil.copyfile(firmware / name, images / name)
    cflags = f"-DCOREMARK_RAM_BYTES={manifest['ram_bytes']}" + (" -DCOREMARK_PROFILE" if args.profile else "")
    if args.cached:
        cflags += " -DCOREMARK_CACHE"
    command = ["verilator", "-Wall", "--Wno-UNUSED", "--Wno-WIDTH", "--Wno-UNOPTFLAT",
               "--Wno-DECLFILENAME", "--Wno-SYNCASYNCNET", "--cc", "--exe", "--build", "-j", "2",
               f"-DCPU_BTB_INDEX_BITS={args.btb_bits}", f"-DCPU_RAS_BITS={args.ras_bits}",
               f"-DCPU_CONTROL_LOAD_GUARD={int(args.control_load_guard)}",
               f"-DCPU_LOAD_USE_GUARD={int(args.load_use_guard)}", "-CFLAGS", cflags,
               "--top-module", "coremark_soc_top", "--Mdir", str(build),
               f"-GDMEM_WORDS={manifest['ram_bytes'] // 4}",
               f"-GDATA_ROM_ENABLE={int(manifest['data_rom_enable'])}",
               '-GIMEM_INIT_FILE="images/coremark_board_text.hex"',
               '-GDMEM_INIT_FILE="images/coremark_board_data.hex"',
               str(core), str(ROOT / "rtl/uart_tx.s"), str(ROOT / "rtl/rv32i_pipeline_soc.s"),
               str(ROOT / "tb/coremark_candidate_top.s"), str(ROOT / "tb/sim_coremark.cpp")]
    command.insert(1, f"-DCPU_MULTIPLY_PIPELINE={int(args.multiply_pipeline)}")
    command.insert(1, f"-DCPU_MUL_CARRY_SAVE_MERGE={int(args.mul_carry_save_merge)}")
    command.insert(1, f"-DCPU_SEPARATE_EXMEM_PAYLOAD={int(args.separate_payload)}")
    command.insert(1, f"-DCPU_SEPARATE_FRONTEND_PAYLOAD={int(args.separate_frontend)}")
    command.insert(1, f"-DCPU_EARLY_BRANCH_CHECK={int(args.early_branch_check)}")
    command.insert(1, f"-DCPU_FAST_TRAP_PATH={int(args.fast_trap_path)}")
    command.insert(1, f"-DCPU_FAST_REDIRECT_SELECT={int(args.fast_redirect_select)}")
    command.insert(1, f"-DCPU_REGISTER_BYPASS={int(args.register_bypass)}")
    command.insert(1, f"-DCPU_COUNTER_CSR_PREDECODE={int(args.counter_csr_predecode)}")
    command.insert(1, f"-DCPU_CSR_ADDRESS_PREDECODE={int(args.csr_address_predecode)}")
    command.insert(1, f"-DCPU_SPLIT_MTIME={int(args.split_mtime)}")
    command.insert(1, f"-DCPU_ALIGN_MMIO_ADDRESS={int(args.align_mmio_address)}")
    command.insert(1, f"-DCPU_EARLY_LOAD_REQUEST={int(args.early_load_request)}")
    if args.regfile_lutram:
        command.insert(1, "-DCPU_REGFILE_LUTRAM")
    command.insert(1, f"-DCPU_FETCH_PIPE_STAGES={args.fetch_pipe_stages}")
    command.insert(1, f"-DCPU_DECOUPLE_FETCH={int(args.decouple_fetch)}")
    command.insert(1, f"-DCPU_CACHE_FAST_RESPONSE={int(args.cache_fast_response)}")
    command.insert(1, f"-DCPU_CACHE_EARLY_RESTART={int(args.cache_early_restart)}")
    command.insert(1, f"-DCPU_CACHE_INGRESS_WRITE={int(args.cache_ingress_write)}")
    command.insert(1, f"-DCPU_CACHE_FAST_WRITE_RESPONSE={int(args.cache_fast_write_response)}")
    command.insert(1, f"-DCPU_CACHE_REGISTER_TAGS={int(args.cache_register_tags)}")
    if args.cache_tags_lutram:
        command.insert(1, "-DCPU_CACHE_TAGS_LUTRAM")
    command.insert(1, f"-DCPU_CACHE_STATISTICS={int(not args.disable_cache_statistics)}")
    command.insert(1, f"-DCPU_REGISTER_ICACHE_INVALIDATE={int(args.register_icache_invalidate)}")
    rtl_paths = [core, ROOT / "rtl/uart_tx.s", ROOT / "rtl/rv32i_pipeline_soc.s",
                 ROOT / "tb/coremark_candidate_top.s", ROOT / "tb/sim_coremark.cpp"]
    if args.cached:
        command[1:1] = ["-DCPU_CACHED_SOC", f"-GCACHE_ENABLE={int(not args.no_cache)}",
                        f"-GICACHE_SET_BITS={args.icache_set_bits}",
                        f"-GDCACHE_SET_BITS={args.dcache_set_bits}"]
        for name in ("uart_rx.v", "cpu_data_port.v", "two_way_cache.v", "line_port_arbiter.v",
                     "memory_line_backend.v", "rv32i_cached_soc.v"):
            path = soc if name == "rv32i_cached_soc.v" else ROOT / "rtl" / name
            command.append(str(path))
            rtl_paths.append(path)
    compile_key = {"command": command, "sources": {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in rtl_paths}}
    saved_key = build / "compile_key.json"
    identical = saved_key.exists() and json.loads(saved_key.read_text()) == compile_key
    if not (args.reuse and identical and (build / "Vcoremark_soc_top").exists()):
        with (build / "compile.log").open("w") as log:
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        saved_key.write_text(json.dumps(compile_key, indent=2) + "\n", encoding="ascii")
    simulation = subprocess.run([str(build / "Vcoremark_soc_top")], cwd=build,
                                capture_output=True, text=True)
    report = simulation.stdout + simulation.stderr
    (build / "simulation.log").write_text(report, encoding="utf-8")
    print(report, end="")
    if simulation.returncode:
        raise RuntimeError(f"CPU simulation failed: {simulation.returncode}")
    required = [r"crclist\s*:\s*0xe714", r"crcmatrix\s*:\s*0x1fd7", r"crcstate\s*:\s*0x8e3a",
                r"PASS: CoreMark CRC validated", r"PASS: compact RAM stack minimum"]
    if not all(re.search(pattern, report) for pattern in required):
        raise RuntimeError("Missing independent CoreMark or stack validation")
    ticks = int(re.search(r"Total ticks\s*:\s*(\d+)", report)[1])
    stack = int(re.search(r"stack minimum=0x([0-9a-f]+)", report)[1], 16)
    if stack < manifest["static_end"] + 128:
        raise RuntimeError("Insufficient measured stack margin")
    result = {"core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(), "firmware": manifest,
              "btb_entries": 1 << args.btb_bits, "ras_entries": (1 << args.ras_bits) if args.ras_bits else 0,
              "control_load_guard": args.control_load_guard, "functional_only": True, "timed_cycles": ticks,
              "load_use_guard": args.load_use_guard,
              "multiply_pipeline": args.multiply_pipeline,
              "separate_exmem_payload": args.separate_payload,
              "separate_frontend_payload": args.separate_frontend,
              "early_branch_check": args.early_branch_check,
              "fast_trap_path": args.fast_trap_path,
              "fast_redirect_select": args.fast_redirect_select,
              "regfile_lutram": args.regfile_lutram,
              "register_bypass": args.register_bypass,
              "counter_csr_predecode": args.counter_csr_predecode,
              "csr_address_predecode": args.csr_address_predecode,
              "split_mtime": args.split_mtime,
              "align_mmio_address": args.align_mmio_address,
              "mul_carry_save_merge": args.mul_carry_save_merge,
              "early_load_request": args.early_load_request,
              "fetch_pipe_stages": args.fetch_pipe_stages,
              "pipeline_stages": 5 + args.fetch_pipe_stages,
              "decouple_fetch": args.decouple_fetch or args.fetch_pipe_stages != 0,
              "synchronous_memory": args.cached, "cache_enabled": args.cached and not args.no_cache,
              "cache_fast_response": args.cache_fast_response,
              "cache_early_write_request": args.cache_fast_response,
              "cache_early_restart": args.cache_early_restart,
              "cache_ingress_write": args.cache_ingress_write,
              "cache_fast_write_response": args.cache_fast_write_response,
              "cache_register_tags": args.cache_register_tags,
              "cache_tags_lutram": args.cache_tags_lutram,
              "cache_statistics": args.cached and not args.disable_cache_statistics,
              "register_icache_invalidate": args.register_icache_invalidate,
              "icache_bytes": 64 * (1 << args.icache_set_bits) if args.cached else 0,
              "dcache_bytes": 64 * (1 << args.dcache_set_bits) if args.cached else 0,
              "minimum_stack": stack, "measured_stack_margin_bytes": stack - manifest["static_end"]}
    (build / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
