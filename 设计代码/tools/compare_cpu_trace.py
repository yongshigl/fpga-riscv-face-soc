#!/usr/bin/env python3
"""Compare a post-retirement RTL register trace with Unicorn RV32 execution."""
import argparse
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UcError, UC_ARCH_RISCV, UC_MODE_RISCV32
from unicorn import riscv_const


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()
    emulator = Uc(UC_ARCH_RISCV, UC_MODE_RISCV32)
    for address, size in [(0, 65536), (0x10000000, 32768),
                          (0x20000000, 65536), (0x21000000, 65536),
                          (0x80000000, 64 * 1024 * 1024)]:
        emulator.mem_map(address, size)
    with args.elf.open("rb") as stream:
        elf = ELFFile(stream)
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                emulator.mem_write(segment["p_vaddr"], segment.data())
    emulator.mem_write(0x10000000, Path(str(args.trace) + ".ram").read_bytes())
    registers = [getattr(riscv_const, f"UC_RISCV_REG_X{index}") for index in range(32)]
    with args.trace.open() as stream:
        initial = [int(value, 16) for value in next(stream).split()]
        for register, value in zip(registers, initial[1:]):
            emulator.reg_write(register, value)
        # The recorded first instruction is the function's linear prologue.
        emulator.reg_write(riscv_const.UC_RISCV_REG_PC, initial[0] + 4)
        previous = initial
        matched = 1
        for index, line in enumerate(stream, 2):
            if index > args.limit:
                break
            values = [int(value, 16) for value in line.split()]
            pc = emulator.reg_read(riscv_const.UC_RISCV_REG_PC)
            if pc != values[0]:
                raise SystemExit(f"PC mismatch at row {index}: RTL=0x{values[0]:08x}, "
                                 f"reference=0x{pc:08x}, previous=0x{previous[0]:08x}")
            instruction = int.from_bytes(emulator.mem_read(pc, 4), "little")
            try:
                emulator.emu_start(pc, 0xffffffff, count=1)
            except UcError as error:
                raise SystemExit(f"Unicorn error at row {index}, PC=0x{pc:08x}: {error}") from error
            differences = []
            for reg, (register, expected) in enumerate(zip(registers, values[1:])):
                actual = emulator.reg_read(register)
                if actual != expected:
                    differences.append(f"x{reg}: RTL=0x{expected:08x}, reference=0x{actual:08x}")
            if differences:
                print(f"Register mismatch at row {index}, PC=0x{pc:08x}, instruction=0x{instruction:08x}")
                print("\n".join(differences))
                print("Before: " + " ".join(f"x{reg}={value:08x}" for reg, value in enumerate(previous[1:])))
                raise SystemExit(1)
            previous = values
            matched = index
        print(f"PASS: {matched} retirement rows match Unicorn RV32")


if __name__ == "__main__":
    main()
