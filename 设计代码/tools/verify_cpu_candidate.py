#!/usr/bin/env python3
"""Exercise CPU changes with ISA, hazards, traps, interrupts and reset tests."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, default=ROOT / "rtl/rv32i_pipeline_core_lutram.s")
    parser.add_argument("--include-dir", type=Path, action="append", default=[])
    parser.add_argument("--ras-bits", type=int, default=3)
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
    parser.add_argument("--build", type=Path, default=ROOT / "build/cpu_regression")
    args = parser.parse_args()
    core_text = args.core.read_text()
    if args.mul_carry_save_merge and (not args.multiply_pipeline or
            "parameter integer MUL_CARRY_SAVE_MERGE" not in core_text):
        parser.error("--mul-carry-save-merge requires the independent merge candidate and --multiply-pipeline")
    if args.counter_csr_predecode and "parameter integer COUNTER_CSR_PREDECODE" not in core_text:
        parser.error("This core does not implement --counter-csr-predecode")
    if args.csr_address_predecode and "parameter integer CSR_ADDRESS_PREDECODE" not in core_text:
        parser.error("This core does not implement --csr-address-predecode")
    if args.fast_redirect_select and "parameter integer FAST_REDIRECT_SELECT" not in core_text:
        parser.error("This core does not implement --fast-redirect-select")
    args.build.mkdir(parents=True, exist_ok=True)
    programs = [("rv32i_compliance", "rv32i", "rv32i_compliance.hex"),
                ("load_forward_test", "rv32i", "mul/load_forward_test.hex"),
                ("zmmul_test", "rv32im", "mul/zmmul_test.hex"),
                ("trap_test", "rv32i", "trap_test.hex"),
                ("exception_matrix", "rv32i", "exception_matrix.hex"),
                ("external_irq_test", "rv32i", "external_irq_test.hex"),
                ("branch_predictor_test", "rv32i", "branch_predictor_test.hex"),
                ("ras_test", "rv32i", "ras_test.hex"),
                ("memory_wait_test", "rv32i", "memory_wait_test.hex"),
                ("bypass_wait_test", "rv32i", "bypass_wait_test.hex"),
                ("csr_write_intent_test", "rv32i", "csr_write_intent_test.hex"),
                ("machine_csr_test", "rv32i", "machine_csr_test.hex"),
                ("mul_random_test", "rv32im", "mul_random_test.hex")]
    for program, isa, image in programs:
        elf = args.build.resolve() / f"{program}.elf"
        destination = ROOT / "build" / image
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["riscv64-unknown-elf-gcc", f"-march={isa}", "-mabi=ilp32",
                        "-nostdlib", "-nostartfiles", "-mno-relax", "-T", str(ROOT / "sw/link.ld"),
                        "-o", str(elf), str(ROOT / "sw" / f"{program}.S")], cwd=ROOT, check=True)
        subprocess.run(["riscv64-unknown-elf-objcopy", "-j", ".text", "-O", "verilog",
                        "--verilog-data-width=4", str(elf), str(destination)],
                       cwd=ROOT, check=True)
    cases = [("tb_rv32i_pipeline", []), ("tb_rv32i_pipeline", ["-DLOAD_FORWARD_TEST"]),
             ("tb_zmmul", []), ("tb_pipeline_trap", []), ("tb_exception_matrix", []),
             ("tb_external_irq", []), ("tb_branch_predictor", []), ("tb_lutram_reset", []),
             ("tb_ras", []), ("tb_memory_wait", []), ("tb_early_control", []), ("tb_fast_trap_path", []),
             ("tb_fast_branch_compare", []), ("tb_btb_hash_alias", []), ("tb_bypass_wait", []),
             ("tb_early_data_port", []), ("tb_early_load_decode", []),
             ("tb_csr_write_intent", []), ("tb_machine_csr", []),
             ("tb_fast_trap_path", ["-DCOUNTER_CSR_PREDECODE_COMPARE"]),
             ("tb_mul_random", [])]
    if args.csr_address_predecode:
        cases.extend([("tb_fast_trap_path", ["-DCSR_ADDRESS_PREDECODE_COMPARE"]),
                      ("tb_csr_address_space", [])])
    if args.fast_redirect_select:
        cases.append(("tb_fast_trap_path", ["-DFAST_REDIRECT_SELECT_COMPARE"]))
    records = []
    for index, (name, defines) in enumerate(cases):
        binary = args.build.resolve() / f"{index}_{name}.vvp"
        command = ["iverilog", "-g2012", "-s", name, "-o", str(binary),
                   f"-DCPU_RAS_BITS={args.ras_bits}",
                   f"-DCPU_CONTROL_LOAD_GUARD={int(args.control_load_guard)}",
                   f"-DCPU_LOAD_USE_GUARD={int(args.load_use_guard)}", *defines,
                   f"-DCPU_SEPARATE_EXMEM_PAYLOAD={int(args.separate_payload)}",
                   f"-DCPU_SEPARATE_FRONTEND_PAYLOAD={int(args.separate_frontend)}",
                   f"-DCPU_EARLY_BRANCH_CHECK={int(args.early_branch_check)}",
                   f"-DCPU_FAST_TRAP_PATH={int(args.fast_trap_path)}",
                   f"-DCPU_FAST_REDIRECT_SELECT={int(args.fast_redirect_select)}",
                   f"-DCPU_REGISTER_BYPASS={int(args.register_bypass)}",
                   f"-DCPU_COUNTER_CSR_PREDECODE={int(args.counter_csr_predecode)}",
                   f"-DCPU_CSR_ADDRESS_PREDECODE={int(args.csr_address_predecode)}",
                   f"-DCPU_EARLY_LOAD_REQUEST={int(args.early_load_request)}",
                   f"-DCPU_FETCH_PIPE_STAGES={args.fetch_pipe_stages}",
                   f"-DCPU_DECOUPLE_FETCH={int(args.decouple_fetch)}",
                   *[f"-I{path.resolve()}" for path in args.include_dir],
                   str(args.core.resolve()), str(ROOT / "rtl/cpu_data_port.v"),
                   str(ROOT / "tb" / f"{name}.s")]
        if args.multiply_pipeline:
            command.insert(1, "-DCPU_MULTIPLY_PIPELINE=1")
        command.insert(1, f"-DCPU_MUL_CARRY_SAVE_MERGE={int(args.mul_carry_save_merge)}")
        if args.regfile_lutram:
            command.insert(1, "-DCPU_REGFILE_LUTRAM")
        subprocess.run(command, cwd=ROOT, check=True)
        result = subprocess.run(["vvp", str(binary)], cwd=ROOT, capture_output=True, text=True)
        (args.build / f"{index}_{name}.log").write_text(result.stdout + result.stderr)
        if result.returncode or "PASS:" not in result.stdout:
            raise RuntimeError(f"{name} failed:\n{result.stdout}{result.stderr}")
        checks = [line for line in result.stdout.splitlines() if "PASS:" in line]
        records.append({"test": name, "command": command, "checks": checks})
        print("\n".join(checks), flush=True)
    sources = [args.core.resolve(), ROOT / "rtl/cpu_data_port.v", ROOT / "sw/link.ld",
               *[ROOT / "sw" / f"{program}.S" for program, _, _ in programs],
               *[ROOT / "tb" / f"{name}.s" for name, _ in cases]]
    report = {"passed": True, "pipeline_stages": 5 + args.fetch_pipe_stages,
              "regfile_lutram": args.regfile_lutram,
              "register_bypass": args.register_bypass,
              "counter_csr_predecode": args.counter_csr_predecode,
              "csr_address_predecode": args.csr_address_predecode,
              "fast_redirect_select": args.fast_redirect_select,
              "mul_carry_save_merge": args.mul_carry_save_merge,
              "early_load_request": args.early_load_request,
              "fetch_pipe_stages": args.fetch_pipe_stages,
              "decouple_fetch": args.decouple_fetch or args.fetch_pipe_stages != 0,
              "cases": records, "sources": {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (args.build / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
