#!/usr/bin/env python3
"""Verify compiled RISC-V firmware through real UART, caches, CDC and INT8 RTL."""
import argparse
import hashlib
import json
import re
import subprocess
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=ROOT / "build/vision_soc")
    parser.add_argument("--firmware", type=Path, default=ROOT / "build/firmware")
    parser.add_argument("--case", type=Path, default=None,
                        help="INT8 test program built from tb/sim_int8.cpp; omit to only compile and check hashes")
    parser.add_argument("--rtl-directory", type=Path, default=ROOT / "rtl")
    parser.add_argument("--ai-read-cache-lines", type=int,
                        choices=(2, 4, 8, 16, 32, 64, 128, 256), default=2)
    parser.add_argument("--ai-read-cache-two-way", action="store_true")
    parser.add_argument("--ai-read-cache-set-shift", type=int, choices=(5, 6), default=5)
    parser.add_argument("--ai-read-cache-set-hash", action="store_true")
    parser.add_argument("--ai-depthwise-channel-outer", action="store_true")
    parser.add_argument("--ai-read-cache-full-lru", action="store_true")
    parser.add_argument("--ai-depthwise-window-reuse", action="store_true")
    parser.add_argument("--ai-dw-row-prefetch", action="store_true")
    parser.add_argument("--ai-quant-channel-pipeline", action="store_true")
    parser.add_argument("--ai-write-queue-depth", type=int, choices=(4, 8, 16, 32), default=32)
    parser.add_argument("--ai-gather-mac-fusion", action="store_true")
    parser.add_argument("--counter-csr-predecode", action="store_true")
    parser.add_argument("--split-mtime", action="store_true")
    parser.add_argument("--align-mmio-address", action="store_true")
    parser.add_argument("--optimize-cached-cpu", action="store_true")
    parser.add_argument("--fetch-pipe-stages", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument("--camera-overlap", action="store_true")
    parser.add_argument("--cache-ingress-write", action="store_true")
    parser.add_argument("--cache-register-tags", action="store_true")
    parser.add_argument("--cache-fast-write-response", action="store_true")
    parser.add_argument("--register-bypass", action="store_true")
    parser.add_argument("--early-load-request", action="store_true")
    parser.add_argument("--precompute-control-target", action="store_true")
    parser.add_argument("--v64-cpu", action="store_true")
    parser.add_argument("--cpu-core", help="Override the core filename inside --rtl-directory")
    parser.add_argument("--cpu-soc", help="Override the cached-SoC filename inside --rtl-directory")
    parser.add_argument("--icache-set-bits", type=int, choices=range(1, 10), default=6)
    parser.add_argument("--dcache-set-bits", type=int, choices=range(1, 10), default=5)
    parser.add_argument("--memory-clock-hz", type=int, default=125000000)
    args = parser.parse_args()
    args.build = args.build.resolve()
    args.firmware = args.firmware.resolve()
    args.rtl_directory = args.rtl_directory.resolve()
    args.build.mkdir(parents=True, exist_ok=True)
    firmware = json.loads((args.firmware / "firmware.json").read_text())
    camera_enable = bool(firmware.get("features", {}).get("camera_preview", False))
    if args.camera_overlap and not camera_enable:
        parser.error("--camera-overlap requires camera-enabled firmware")
    cpu_hz, ai_hz = firmware["cpu_clock_hz"], firmware["ai_clock_hz"]
    if min(cpu_hz, ai_hz, args.memory_clock_hz) <= 0 or firmware["uart_baud"] != 921600:
        parser.error("Positive clock frequencies and the supported 921600-baud UART are required")
    for name, expected in firmware["files"].items():
        if hashlib.sha256((args.firmware / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Firmware hash mismatch: {name}")
    defines = {"CPU_FETCH_PIPE_STAGES": args.fetch_pipe_stages}
    defines["CPU_COUNTER_CSR_PREDECODE"] = int(args.counter_csr_predecode)
    defines["CPU_SPLIT_MTIME"] = int(args.split_mtime)
    defines["CPU_ALIGN_MMIO_ADDRESS"] = int(args.align_mmio_address)
    defines["CPU_REGISTER_BYPASS"] = int(args.register_bypass)
    defines["CPU_EARLY_LOAD_REQUEST"] = int(args.early_load_request)
    defines["CPU_PRECOMPUTE_CONTROL_TARGET"] = int(args.precompute_control_target)
    defines["CPU_FAST_REDIRECT_SELECT"] = int(args.optimize_cached_cpu)
    defines["CPU_CSR_ADDRESS_PREDECODE"] = int(args.counter_csr_predecode)
    defines["CPU_CACHE_STATISTICS"] = 0
    defines["CPU_CACHE_REGISTER_TAGS"] = int(args.cache_register_tags)
    if args.v64_cpu:
        defines["CPU_REGFILE_LUTRAM"] = 1
    defines["CPU_CACHE_INGRESS_WRITE"] = int(args.cache_ingress_write)
    defines["CPU_CACHE_FAST_WRITE_RESPONSE"] = int(args.cache_fast_write_response)
    for name in ("CPU_DECOUPLE_FETCH", "CPU_SEPARATE_EXMEM_PAYLOAD", "CPU_SEPARATE_FRONTEND_PAYLOAD",
                 "CPU_EARLY_BRANCH_CHECK", "CPU_FAST_TRAP_PATH", "CPU_CACHE_FAST_RESPONSE",
                 "CPU_CACHE_EARLY_RESTART", "CPU_REGISTER_ICACHE_INVALIDATE"):
        defines[name] = int(args.optimize_cached_cpu)
    camera_build_id = 0x26100401 if args.camera_overlap else 0x26100106
    cflags = (f"-DVISION_SIM_CPU_HZ={cpu_hz} -DVISION_SIM_AI_HZ={ai_hz} "
              f"-DVISION_SIM_MEMORY_HZ={args.memory_clock_hz} -DVISION_SIM_CAMERA_BUILD_ID={camera_build_id}")
    cases = [(bytes((index * 7 + index // 31) & 255 for index in range(4096)), 6, zlib.Z_DEFAULT_STRATEGY),
             (bytes(range(256)) * 8, 1, zlib.Z_FIXED), (bytes(range(256)) * 4, 0, zlib.Z_DEFAULT_STRATEGY)]
    packed = bytearray(struct.pack("<I", len(cases)))
    for expected, level, strategy in cases:
        encoder = zlib.compressobj(level, zlib.DEFLATED, zlib.MAX_WBITS, strategy=strategy)
        compressed = encoder.compress(expected) + encoder.flush()
        packed.extend(struct.pack("<II", len(expected), len(compressed)) + expected + compressed)
    (args.build / "compressed_cases.bin").write_bytes(packed)
    delta_cases = [bytes(range(256)) * 16, bytes([255, 0, 1, 0, 255]) * 819,
                   bytes((index * 7 + index // 31) & 255 for index in range(4096)),
                   bytes(4096), bytes([255]), bytes(range(256)) * 5]
    delta_packed = bytearray(struct.pack("<I", len(delta_cases)))
    for expected in delta_cases:
        differences = bytes([expected[0]]) + bytes((current - previous) & 255
                          for current, previous in zip(expected[1:], expected[:-1]))
        compressed = zlib.compress(differences, 1)
        delta_packed.extend(struct.pack("<II", len(expected), len(compressed)) + expected + compressed)
    (args.build / "delta_compressed_cases.bin").write_bytes(delta_packed)
    core_name = args.cpu_core or (
        "rv32i_pipeline_core_trap_value.s" if args.v64_cpu else "rv32i_pipeline_core_lutram.s"
    )
    soc_name = args.cpu_soc or (
        "rv32i_cached_soc_device_address.v" if args.v64_cpu else "rv32i_cached_soc.v"
    )
    uart_tx_name = "uart_tx.s" if (args.rtl_directory / "uart_tx.s").exists() else "uart_tx.v"
    names = [core_name, uart_tx_name, "uart_rx.v", "cpu_data_port.v", "two_way_cache.v",
             "line_port_arbiter.v", "memory_line_backend.v", "rv32i_cached_soc.v", "transaction_cdc.v",
             "int8_mac_array.v", "int8_requantize.v"]
    if (args.rtl_directory / "row_cache_256.v").exists():
        names.append("row_cache_256.v")
    names.extend(("int8_accelerator.v", "vision_soc.v"))
    rtl = [args.rtl_directory / name for name in names]
    if soc_name != "rv32i_cached_soc.v":
        rtl[names.index("rv32i_cached_soc.v")] = args.rtl_directory / soc_name
    source_paths = [*rtl, ROOT / "tb/sim_vision.cpp", Path(__file__).resolve()]
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    ai_parameters = {"AI_READ_CACHE_LINES": args.ai_read_cache_lines,
                     "AI_READ_CACHE_TWO_WAY": int(args.ai_read_cache_two_way),
                     "AI_READ_CACHE_SET_SHIFT": args.ai_read_cache_set_shift,
                     "AI_READ_CACHE_SET_HASH": int(args.ai_read_cache_set_hash),
                     "AI_DEPTHWISE_CHANNEL_OUTER": int(args.ai_depthwise_channel_outer),
                     "AI_READ_CACHE_FULL_LRU": int(args.ai_read_cache_full_lru),
                     "AI_DEPTHWISE_WINDOW_REUSE": int(args.ai_depthwise_window_reuse),
                     "AI_DW_ROW_PREFETCH": int(args.ai_dw_row_prefetch),
                     "AI_QUANT_CHANNEL_PIPELINE": int(args.ai_quant_channel_pipeline),
                     "AI_WRITE_QUEUE_DEPTH": args.ai_write_queue_depth,
                     "AI_GATHER_MAC_FUSION": int(args.ai_gather_mac_fusion)}
    available_parameters = set(re.findall(
        r"\bparameter\s+(?:integer\s+)?(?:\[[^\]]+\]\s+)?(\w+)\s*=",
        (args.rtl_directory / "vision_soc.v").read_text()))
    optional_defaults = {"AI_READ_CACHE_TWO_WAY": 0, "AI_READ_CACHE_SET_SHIFT": 5,
                         "AI_READ_CACHE_SET_HASH": 0, "AI_DEPTHWISE_WINDOW_REUSE": 0,
                         "AI_DW_ROW_PREFETCH": 0, "AI_QUANT_CHANNEL_PIPELINE": 0,
                         "AI_WRITE_QUEUE_DEPTH": 32, "AI_GATHER_MAC_FUSION": 0}
    for name in list(ai_parameters):
        if name not in available_parameters:
            if name not in optional_defaults or ai_parameters[name] != optional_defaults[name]:
                raise ValueError(f"The selected vision_soc RTL does not provide {name}")
            del ai_parameters[name]
    command = ["verilator", "--cc", "--exe", "--build", "-j", "2", "-Wall", "--Wno-WIDTH", "--Wno-UNUSED",
               "--Wno-DECLFILENAME", "--Wno-SYNCASYNCNET", "--Wno-UNOPTFLAT", "--Wno-TIMESCALEMOD",
               "--Wno-REDEFMACRO",
               "-I" + str(args.rtl_directory.parent),
               "--top-module", "vision_soc",
               "--Mdir", str(args.build), f'-GIMEM_INIT_FILE="{(args.firmware / "vision_text.hex").resolve()}"',
               f'-GDMEM_INIT_FILE="{(args.firmware / "vision_data.hex").resolve()}"',
               f'-GCPU_CLOCK_HZ={cpu_hz}', f'-GAI_CLOCK_HZ={ai_hz}', f'-GCAMERA_ENABLE={int(camera_enable)}',
               f'-GICACHE_SET_BITS={args.icache_set_bits}', f'-GDCACHE_SET_BITS={args.dcache_set_bits}',
               *[f"-G{name}={value}" for name, value in ai_parameters.items()],
               *[f"-D{name}={value}" for name, value in defines.items()], "-CFLAGS", cflags,
               *map(str, rtl), str(ROOT / "tb/sim_vision.cpp")]
    (args.build / "command.json").write_text(json.dumps(command, indent=2) + "\n", encoding="ascii")
    print(f"Compiling vision SoC: pipeline={5 + args.fetch_pipe_stages} clocks={cpu_hz}/{ai_hz}/{args.memory_clock_hz}",
          flush=True)
    if args.case is None:
        print("note: --case not given, the SoC is compiled but the INT8 simulation is skipped", flush=True)
    with (args.build / "compile.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    if args.case is None:
        for path in source_paths:
            if hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[path.name]:
                raise RuntimeError(f"Source changed during verification: {path}")
        print("compiled and source hashes verified; no simulation output", flush=True)
        return
    result = subprocess.run([str(args.build / "Vvision_soc"), str(args.case.resolve()),
                             str((args.build / "compressed_cases.bin").resolve()),
                             str((args.build / "delta_compressed_cases.bin").resolve())], capture_output=True, text=True)
    (args.build / "simulation.log").write_text(result.stdout + result.stderr, encoding="ascii")
    print(result.stdout + result.stderr, end="", flush=True)
    if result.returncode or "PASS: complete vision SoC" not in result.stdout:
        raise RuntimeError("Vision SoC regression failed")
    for path in source_paths:
        if hashlib.sha256(path.read_bytes()).hexdigest() != source_hashes[path.name]:
            raise RuntimeError(f"Source changed during verification: {path}")
    for name, expected in firmware["files"].items():
        if hashlib.sha256((args.firmware / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Firmware changed during verification: {name}")
    evidence = {"reference": "Independent PyTorch exact integer convolution; UART CRC32 and byte comparisons",
                "clock_hz": {"cpu": cpu_hz, "accelerator": ai_hz, "memory": args.memory_clock_hz},
                "clock_half_period_ps": {name: (500000000000 + value // 2) // value
                                         for name, value in (("cpu", cpu_hz), ("accelerator", ai_hz),
                                                             ("memory", args.memory_clock_hz))},
                "pipeline_stages": 5 + args.fetch_pipe_stages, "cpu_defines": defines,
                "icache_set_bits": args.icache_set_bits, "dcache_set_bits": args.dcache_set_bits,
                "icache_bytes": 64 * (1 << args.icache_set_bits),
                "dcache_bytes": 64 * (1 << args.dcache_set_bits),
                "cpu_core": core_name, "cpu_soc": soc_name,
                "ai_parameters": ai_parameters,
                "memory": "64 MiB with randomized ready/response delays",
                "firmware": firmware,
                "camera_control": "Protocol peripheral model; physical JPEG UART handoff verified separately",
                "camera_model_build_id": f"{camera_build_id:08x}",
                "sources": source_hashes, "source_hashes_verified_unchanged": True,
                "passed": True}
    (args.build / "result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
