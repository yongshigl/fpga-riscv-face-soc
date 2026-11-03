#!/usr/bin/env python3
"""Build the bare-metal RISC-V model scheduler and binary UART service."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/vision/firmware")
    parser.add_argument("--cpu-hz", type=int, default=81000000)
    parser.add_argument("--ai-hz", type=int, default=54000000)
    parser.add_argument("--optimization", choices=("-O2", "-O3", "-Os"), default="-O2")
    parser.add_argument("--cameras", action="store_true")
    parser.add_argument("--camera-tensor", action="store_true")
    parser.add_argument("--delta-compressed-write", action="store_true")
    parser.add_argument("--reciprocal-normalization", action="store_true")
    parser.add_argument("--fast-crc32", action="store_true")
    parser.add_argument("--face-decode", dest="face_decode", action="store_true", default=None,
                        help="Decode YuNet outputs and NMS on the RISC-V core (default with --camera-tensor)")
    parser.add_argument("--no-face-decode", dest="face_decode", action="store_false")
    parser.add_argument("--benchmark", action="store_true",
                        help="Link stock CoreMark into the image so one bitstream scores and runs the application")
    parser.add_argument("--coremark-optimization", choices=("-O3", "-O2", "-Os"), default="-O3",
                        help="Optimisation level for the CoreMark translation units only. -O3 is the "
                             "flag set the standalone CPU firmware uses; -Os is for images that must stay "
                             "inside a 32 KiB ROM. Whatever is chosen is recorded in firmware.json, so a "
                             "score can never be attributed to the wrong flags.")
    parser.add_argument("--rom-bytes", type=int, default=None,
                        help="Instruction ROM size recorded in firmware.json (default: 65536 with "
                             "--benchmark, else 32768). Pass the size the RTL actually declares.")
    parser.add_argument("--linker-script", type=Path, default=None,
                        help="Override the linker script (default: sw/vision_bench.ld with --benchmark).")
    args = parser.parse_args()
    if args.face_decode is None:
        args.face_decode = args.camera_tensor
    if args.face_decode and not args.camera_tensor:
        parser.error("--face-decode requires --camera-tensor")
    if args.camera_tensor and not args.cameras:
        parser.error("--camera-tensor requires --cameras")
    if args.reciprocal_normalization and not args.camera_tensor:
        parser.error("--reciprocal-normalization requires --camera-tensor for its firmware identity")
    if args.fast_crc32 and not args.camera_tensor:
        parser.error("--fast-crc32 requires --camera-tensor for its firmware identity")
    args.output.mkdir(parents=True, exist_ok=True)
    elf = args.output / "vision.elf"
    flags = ["-march=rv32im", "-mabi=ilp32", args.optimization, "-mno-div", "-mno-relax",
             "-msmall-data-limit=0", "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fdata-sections",
             "-DMINIZ_NO_MALLOC", "-DMINIZ_NO_ARCHIVE_APIS", "-DMINIZ_NO_DEFLATE_APIS",
             "-DMINIZ_NO_ZLIB_APIS", "-DMINIZ_NO_STDIO", "-DMINIZ_NO_TIME", "-DNDEBUG",
             "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "sw/baremetal_include"),
             "-I" + str(ROOT / "third_party/miniz")]
    linker_script = args.linker_script or ROOT / ("sw/vision_bench.ld" if args.benchmark else "sw/vision.ld")
    if not linker_script.is_absolute():
        linker_script = (ROOT / linker_script).resolve()
    if not linker_script.is_file():
        parser.error(f"linker script not found: {linker_script}")
    sources = [ROOT / "sw/vision_crt0.S", ROOT / "sw/vision_firmware.c", ROOT / "sw/soft_div64.c", linker_script]
    objects = []
    if args.benchmark:
        # CoreMark keeps the exact flag set the standalone board firmware uses, so the
        # ticks measured here stay comparable with the CPU-only build. It is C89 and
        # trips -Werror, hence -w.
        coremark_tuning = [args.coremark_optimization, "-ffreestanding", "-fno-builtin", "-msmall-data-limit=0",
                           "-mno-relax", "-fno-inline-functions", "-mno-div", "-falign-functions=16",
                           "-fsched2-use-superblocks", "-fsched-pressure", "-funroll-loops",
                           "--param=max-unroll-times=3", "--param=max-unrolled-insns=40",
                           "-fno-tree-dominator-opts", "-fno-code-hoisting", "-fno-tree-pre",
                           "-fno-if-conversion", "-fno-if-conversion2", "-fno-tree-forwprop",
                           "-fno-tree-ccp", "-fno-rename-registers"]
        coremark_flags = ["-march=rv32im", "-mabi=ilp32", *coremark_tuning,
                          "-UFLAGS_STR",
                          f"-DFLAGS_STR=\"{args.coremark_optimization} --param=max-unroll-times=3 -march=rv32im -mno-div\"",
                          "-Dmain=coremark_entry", "-DITERATIONS=1", f"-DCLOCKS_PER_SEC={args.cpu_hz}u",
                          "-DMAIN_HAS_NOARGC=1", "-DHAS_FLOAT=0", "-ffunction-sections", "-fdata-sections",
                          "-I" + str(ROOT / "coremark_port"), "-I" + str(ROOT / "third_party/coremark"), "-w"]
        coremark_sources = [ROOT / "third_party/coremark" / name for name in
                            ("core_main.c", "core_list_join.c", "core_matrix.c", "core_state.c", "core_util.c")]
        coremark_sources += [ROOT / "coremark_port" / "core_portme.c", ROOT / "coremark_port" / "ee_printf.c"]
        object_dir = args.output / "coremark"
        object_dir.mkdir(parents=True, exist_ok=True)
        for source in coremark_sources:
            obj = object_dir / (source.stem + ".o")
            subprocess.run(["riscv64-unknown-elf-gcc", *coremark_flags, "-c", str(source), "-o", str(obj)],
                           check=True)
            objects.append(obj)
        sources += [ROOT / "sw/vision_bench.c", ROOT / "sw/vision_bench.h", *coremark_sources,
                    ROOT / "coremark_port/core_portme.h"]
    subprocess.run(["riscv64-unknown-elf-gcc", *flags, f"-DCPU_CLOCK_HZ={args.cpu_hz}u",
                    f"-DAI_CLOCK_HZ={args.ai_hz}u", f"-DCAMERA_ENABLE={int(args.cameras)}",
                    f"-DCAMERA_TENSOR={int(args.camera_tensor)}",
                    f"-DDELTA_WRITE_ENABLE={int(args.delta_compressed_write)}",
                    f"-DVISION_RECIPROCAL_NORMALIZATION={int(args.reciprocal_normalization)}",
                    f"-DVISION_FAST_CRC32={int(args.fast_crc32)}",
                    f"-DVISION_FACE_DECODE={int(args.face_decode)}",
                    f"-DVISION_BENCHMARK={int(args.benchmark)}",
                    "-nostdlib", "-nostartfiles", "-T", str(sources[3]),
                    "-Wl,--no-relax,--gc-sections", "-o", str(elf), *map(str, sources[:3]),
                    str(ROOT / "sw/vision_memory.c"), str(ROOT / "sw/vision_yunet.c"),
                    *([str(ROOT / "sw/vision_bench.c")] if args.benchmark else []),
                    *map(str, objects),
                    str(ROOT / "third_party/miniz/miniz_tinfl.c"), "-lgcc"], check=True)
    disassembly = subprocess.check_output(["riscv64-unknown-elf-objdump", "-d", str(elf)], text=True)
    if re.search(r"\s(?:div|divu|rem|remu)\s", disassembly):
        raise RuntimeError("Firmware contains unsupported hardware division")
    (args.output / "vision.dis").write_text(disassembly, encoding="utf-8")
    text_hex = args.output / "vision_text.hex"
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                    "--verilog-data-width=4", str(elf), str(text_hex)], check=True)
    if any(not word.startswith("@") and not re.fullmatch(r"[0-9a-fA-F]{8}", word)
           for word in text_hex.read_text().split()):
        raise RuntimeError("ROM image contains a partial 32-bit word; align .text in the linker script")
    data_bin = args.output / "vision_data.bin"
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".data", "-O", "binary", "--gap-fill=0",
                    "--pad-to=0x8000", "--change-addresses=-0x10000000", str(elf), str(data_bin)], check=True)
    data = data_bin.read_bytes()
    if not data:
        data = bytes(32768)
        data_bin.write_bytes(data)
    if len(data) != 32768:
        raise RuntimeError(f"Unexpected firmware data image size: {len(data)}")
    data_hex = args.output / "vision_data.hex"
    data_hex.write_text("".join(f"{int.from_bytes(data[i:i+4], 'little'):08x}\n"
                              for i in range(0, len(data), 4)), encoding="ascii")
    symbols = subprocess.check_output(["riscv64-unknown-elf-nm", "-n", str(elf)], text=True)
    static_end = int(re.search(r"^([0-9a-f]+)\s+\w\s+_end$", symbols, re.MULTILINE)[1], 16)
    sources += [ROOT / "sw/vision_memory.c", *sorted((ROOT / "third_party/miniz").glob("*.h")),
                ROOT / "third_party/miniz/miniz_tinfl.c", *sorted((ROOT / "sw/baremetal_include").glob("*.h"))]
    if args.reciprocal_normalization:
        sources.append(ROOT / "sw/vision_normalize.h")
    if args.fast_crc32:
        sources.append(ROOT / "sw/vision_crc32.h")
    if args.face_decode:
        sources.extend([ROOT / "sw/vision_yunet.c", ROOT / "sw/vision_yunet.h"])
    manifest = {"cpu_clock_hz": args.cpu_hz, "ai_clock_hz": args.ai_hz, "uart_baud": 921600,
                "firmware_version": "26102A01" if args.face_decode and args.benchmark else
                                    "26102A02" if args.benchmark else
                                    "26102901" if args.face_decode else
                                    "26102503" if args.fast_crc32 and args.reciprocal_normalization else
                                    "26102504" if args.fast_crc32 else
                                    "26102502" if args.reciprocal_normalization else
                                    "26102401" if args.camera_tensor else
                                    "26101401" if args.delta_compressed_write else "26101301",
                "features": {"camera_preview": args.cameras, "camera_tensor": args.camera_tensor, "compressed_write": True,
                             "packed_tensor_read": True, "max_decoded_bytes": 4096,
                             "camera_coherent_capture": args.camera_tensor,
                             "delta_compressed_write": args.delta_compressed_write,
                             "camera_face_decode": args.face_decode,
                             "benchmark": args.benchmark},
                "compiler_flags": flags, "isa": "RV32I plus MUL/MULH/MULHSU/MULHU; software division",
                "rom_bytes": args.rom_bytes or (65536 if args.benchmark else 32768),
                "ram_bytes": 32768, "static_end": static_end,
                "stack_reserve_bytes": 0x10008000 - static_end, "protocol": "PGR1/PGS1 CRC32",
                "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in [elf, text_hex, data_hex]},
                "sources": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    if args.benchmark:
        # CoreMark is compiled with its own flag set, so a score can only be
        # attributed to the right build if these flags travel with the image.
        manifest["coremark_compiler_flags"] = coremark_flags
        manifest["coremark_optimization"] = args.coremark_optimization
    if args.fast_crc32:
        manifest["features"]["fast_crc32"] = True
    if args.reciprocal_normalization:
        manifest["features"]["reciprocal_normalization"] = True
    (args.output / "firmware.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
    subprocess.run(["riscv64-unknown-elf-size", str(elf)], check=True)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
