#!/usr/bin/env python3
"""Validate an independent timer candidate against the frozen original cached SoC."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--soc", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=ROOT / "rtl/rv32i_cached_soc.v")
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    candidate, baseline = args.soc.resolve(), args.baseline.resolve()
    if "parameter integer SPLIT_MTIME" not in candidate.read_text():
        parser.error("--soc must implement the independent split timer candidate")
    renamed = build / "rv32i_cached_soc_baseline.v"
    renamed.write_text(baseline.read_text().replace("module rv32i_cached_soc #(",
                       "module rv32i_cached_soc_baseline #(", 1))
    elf, image = build / "mtime_soc.elf", build / "mtime_soc.hex"
    subprocess.run(["riscv64-unknown-elf-gcc", "-march=rv32i", "-mabi=ilp32", "-nostdlib",
                    "-nostartfiles", "-mno-relax", "-T", str(ROOT / "sw/link.ld"),
                    "-o", str(elf), str(ROOT / "sw/mtime_soc_test.S")], check=True)
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                    "--verilog-data-width=4", str(elf), str(image)], check=True)
    mmio_elf, mmio_image = build / "mmio_word.elf", build / "mmio_word.hex"
    subprocess.run(["riscv64-unknown-elf-gcc", "-march=rv32i", "-mabi=ilp32", "-nostdlib",
                    "-nostartfiles", "-mno-relax", "-T", str(ROOT / "sw/link.ld"),
                    "-o", str(mmio_elf), str(ROOT / "sw/mmio_word_test.S")], check=True)
    subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                    "--verilog-data-width=4", str(mmio_elf), str(mmio_image)], check=True)
    common = [ROOT / "rtl" / name for name in ("rv32i_pipeline_core_lutram.s", "uart_tx.s",
              "uart_rx.v", "cpu_data_port.v", "two_way_cache.v", "line_port_arbiter.v",
              "memory_line_backend.v")]
    flags = ["-DCPU_REGFILE_LUTRAM", "-DCPU_REGISTER_BYPASS=1", "-DCPU_MULTIPLY_PIPELINE=1",
             "-DCPU_COUNTER_CSR_PREDECODE=1", "-DCPU_DECOUPLE_FETCH=1",
             "-DCPU_CONTROL_LOAD_GUARD=1", "-DCPU_LOAD_USE_GUARD=1",
             "-DCPU_SEPARATE_EXMEM_PAYLOAD=1", "-DCPU_SEPARATE_FRONTEND_PAYLOAD=1",
             "-DCPU_EARLY_BRANCH_CHECK=1", "-DCPU_CACHE_FAST_RESPONSE=1", "-DCPU_CACHE_EARLY_RESTART=1",
             "-DCPU_CACHE_INGRESS_WRITE=1", "-DCPU_CACHE_FAST_WRITE_RESPONSE=1",
             "-DCPU_CACHE_STATISTICS=0", "-DCPU_REGISTER_ICACHE_INVALIDATE=1"]
    cases = []
    for fast, early in ((1, 1), (0, 1), (1, 0)):
        for tb in ("tb_mtime_soc", "tb_mtime_bus"):
            name = f"{tb}_fast{fast}_early{early}"
            binary = build / f"{name}.vvp"
            command = ["iverilog", "-g2012", "-s", tb, "-o", str(binary), *flags,
                       f"-DCPU_FAST_TRAP_PATH={fast}", f"-DCPU_EARLY_LOAD_REQUEST={early}",
                       *map(str, common), str(renamed), str(candidate), str(ROOT / f"tb/{tb}.s")]
            subprocess.run(command, check=True, cwd=build)
            result = subprocess.run(["vvp", str(binary), f"+image={image.name}"], cwd=build,
                                    text=True, capture_output=True)
            (build / f"{name}.log").write_text(result.stdout + result.stderr)
            print(name + ": " + result.stdout.strip(), flush=True)
            if result.returncode or "PASS:" not in result.stdout:
                raise RuntimeError(f"{name} failed: {result.stderr}")
            cases.append({"name": name, "passed": True, "command": command, "output": result.stdout})
    # Aligned decode must work for byte/halfword accesses from an actual CPU,
    # while retaining precise faults and exactly-once device side effects.
    for fast, early in ((1, 1), (0, 1), (1, 0)):
        name = f"tb_mmio_word_fast{fast}_early{early}"
        binary = build / f"{name}.vvp"
        command = ["iverilog", "-g2012", "-s", "tb_mmio_word", "-o", str(binary),
                   *[f for f in flags if f != "-DCPU_CACHE_STATISTICS=0"], "-DCPU_CACHE_STATISTICS=1",
                   f"-DCPU_FAST_TRAP_PATH={fast}", f"-DCPU_EARLY_LOAD_REQUEST={early}",
                   *map(str, common), str(candidate), str(ROOT / "tb/tb_mmio_word.s")]
        subprocess.run(command, check=True, cwd=build)
        result = subprocess.run(["vvp", str(binary), f"+image={mmio_image.name}"], cwd=build,
                                text=True, capture_output=True)
        (build / f"{name}.log").write_text(result.stdout + result.stderr)
        print(name + ": " + result.stdout.strip(), flush=True)
        if result.returncode or "PASS:" not in result.stdout:
            raise RuntimeError(f"{name} failed: {result.stderr}")
        cases.append({"name": name, "passed": True, "command": command, "output": result.stdout})
    failure_binary = build / "mmio_alignment_disabled.vvp"
    failure_command = command.copy()
    failure_command[failure_command.index("-o") + 1] = str(failure_binary)
    failure_command.insert(1, "-Ptb_mmio_word.ALIGN_MMIO_ADDRESS=0")
    subprocess.run(failure_command, check=True, cwd=build)
    failure = subprocess.run(["vvp", str(failure_binary), f"+image={mmio_image.name}"], cwd=build,
                             text=True, capture_output=True)
    (build / "mmio_alignment_disabled_expected_failure.log").write_text(failure.stdout + failure.stderr)
    if failure.returncode == 0 or "Software MMIO failed" not in failure.stdout or "cause=00000007" not in failure.stdout:
        raise RuntimeError("Alignment-disabled SoC did not reproduce the original nonzero-lane store fault")
    print("PASS: disabled MMIO fix reproduces original nonzero-lane store access fault", flush=True)
    sources = [*common, baseline, candidate, ROOT / "tb/tb_mtime_soc.s", ROOT / "tb/tb_mtime_bus.s",
               ROOT / "tb/tb_mmio_word.s", ROOT / "sw/mtime_soc_test.S", ROOT / "sw/mmio_word_test.S",
               ROOT / "sw/link.ld", Path(__file__).resolve()]
    snapshot = build / "source_snapshot"
    for path in sources:
        relative = path.relative_to(ROOT) if path.is_relative_to(ROOT) else Path("candidate") / path.name
        dest = snapshot / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    report = {"passed": True, "functional_only": True, "cases": cases,
              "original_bug_reproduced": {"expected_failure": True, "command": failure_command,
                                          "output": failure.stdout},
              "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (build / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
