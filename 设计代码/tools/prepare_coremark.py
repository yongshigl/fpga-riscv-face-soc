#!/usr/bin/env python3
"""Build an unchanged official CoreMark image and record its board configuration."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock-hz", type=int, required=True)
    parser.add_argument("--iterations", type=int, required=True)
    parser.add_argument("--isa", choices=["rv32i", "rv32im"], default="rv32im")
    parser.add_argument("--ram-bytes", type=int, choices=[4096, 5120, 6144, 8192], default=4096)
    parser.add_argument("--readonly-in-ram", action="store_true")
    parser.add_argument("--optimize", choices=["O2", "O3", "Os"], default="O3")
    parser.add_argument("--inline-functions", action="store_true")
    parser.add_argument("--lto", action="store_true")
    parser.add_argument("--relax", action="store_true", help="Allow standard RISC-V linker instruction relaxation")
    parser.add_argument("--extra-cflag", action="append", default=[])
    args = parser.parse_args()
    if args.clock_hz <= 0 or args.iterations <= 0:
        parser.error("Clock and iterations must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    ram_end = 0x10000000 + args.ram_bytes
    readonly = "*(.rodata*)" if args.readonly_in_ram else ""
    rom_readonly = "" if args.readonly_in_ram else "*(.rodata*)"
    linker = output / "coremark.ld"
    linker.write_text(f"""ENTRY(_start)
MEMORY {{
    ROM (rx) : ORIGIN = 0x0, LENGTH = 16K
    RAM (rwx) : ORIGIN = 0x10000000, LENGTH = {args.ram_bytes}
}}
SECTIONS {{
    .text : {{ *(.text.start) *(.text*) {rom_readonly} . = ALIGN(4); }} > ROM
    .data : {{ *(.data*) *(.sdata*) {readonly} }} > RAM
    .bss (NOLOAD) : {{ *(.bss*) *(.sbss*) *(COMMON) }} > RAM
    _end = .;
    ASSERT(_end <= 0x{ram_end - 512:x}, "Insufficient CoreMark stack reserve")
}}
""", encoding="ascii")
    coremark = ROOT / "third_party/coremark"
    sources = [coremark / f"core_{name}.c" for name in
               ["main", "list_join", "matrix", "state", "util"]]
    startup = (ROOT / f"sw/coremark_crt0_{args.ram_bytes // 1024}k.S" if args.ram_bytes in (4096, 8192)
               else ROOT / "sw/coremark_crt0_compact.S")
    sources += [ROOT / "coremark_port/core_portme.c", ROOT / "coremark_port/ee_printf.c", startup]
    flags = [f"-march={args.isa}", "-mabi=ilp32", f"-{args.optimize}",
             "-ffreestanding", "-fno-builtin", "-msmall-data-limit=0",
             "-mrelax" if args.relax else "-mno-relax"]
    if not args.inline_functions:
        flags.append("-fno-inline-functions")
    if args.isa == "rv32im":
        flags.append("-mno-div")
    if args.lto:
        flags.append("-flto")
    flags.extend(args.extra_cflag)
    if args.ram_bytes not in (4096, 8192):
        flags.append(f"-DCOREMARK_RAM_BYTES={args.ram_bytes}")
    elf = output / "coremark.elf"
    command = ["riscv64-unknown-elf-gcc", *flags, "-DPERFORMANCE_RUN=1", "-DMEM_METHOD=MEM_STATIC",
               "-DMAIN_HAS_NOARGC=1", "-DHAS_FLOAT=0", f"-DITERATIONS={args.iterations}",
               f"-DCLOCKS_PER_SEC={args.clock_hz}", f"-DFLAGS_STR=\"{' '.join(flags)}\"",
               f"-I{ROOT / 'coremark_port'}", f"-I{coremark}", "-nostdlib", "-nostartfiles",
               "-T", str(linker), "-Wl,--relax" if args.relax else "-Wl,--no-relax",
               "-o", str(elf), *map(str, sources), "-lgcc"]
    subprocess.run(command, check=True)
    disassembly = subprocess.check_output(["riscv64-unknown-elf-objdump", "-d", str(elf)], text=True)
    if re.search(r"\s(?:div|divu|rem|remu)\s", disassembly):
        raise RuntimeError("Image contains unsupported hardware division")
    (output / "coremark.dis").write_text(disassembly, encoding="utf-8")
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                    "--verilog-data-width=4", str(elf), str(output / "coremark_board_text.hex")], check=True)
    if any(not word.startswith("@") and not re.fullmatch(r"[0-9a-fA-F]{8}", word)
           for word in (output / "coremark_board_text.hex").read_text().split()):
        raise RuntimeError("CoreMark ROM image contains a partial 32-bit word")
    data = output / "coremark_board_data.bin"
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".data", "-O", "binary", "--gap-fill=0",
                    f"--pad-to=0x{args.ram_bytes:x}", "--change-addresses=-0x10000000",
                    str(elf), str(data)], check=True)
    binary = data.read_bytes()
    if len(binary) != args.ram_bytes:
        raise RuntimeError("Unexpected data image size")
    (output / "coremark_board_data.hex").write_text(
        "".join(f"{int.from_bytes(binary[i:i + 4], 'little'):08x}\n" for i in range(0, len(binary), 4)),
        encoding="ascii")
    symbols = subprocess.check_output(["riscv64-unknown-elf-nm", "-n", str(elf)], text=True)
    static_end = int(re.search(r"^([0-9a-f]+)\s+\w\s+_end$", symbols, re.MULTILINE)[1], 16)
    manifest = {"clock_hz": args.clock_hz, "iterations": args.iterations, "compiler_flags": flags,
                "isa": args.isa, "hardware_division": False, "linker_relaxation": args.relax,
                "ram_bytes": args.ram_bytes,
                "static_end": static_end, "stack_reserve_bytes": ram_end - static_end,
                "data_rom_enable": not args.readonly_in_ram, "coremark_version": "v1.01",
                "elf_sha256": hashlib.sha256(elf.read_bytes()).hexdigest(),
                "files": {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in
                          ["coremark_board_text.hex", "coremark_board_data.hex"]}}
    (output / "firmware.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="ascii")
    subprocess.run(["riscv64-unknown-elf-size", str(elf)], check=True)
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
