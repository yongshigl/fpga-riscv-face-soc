#!/usr/bin/env python3
"""Score CoreMark on the vision bitstream itself (op 22).

This is the same-bitstream evidence run: the bitstream that runs face recognition is
asked to run stock CoreMark in place, so the score and the application share one
configuration file. Formal numbers need >= 10 s of runtime, the four iteration-
independent CRCs and post-route resources from the same build.
"""
import argparse
import json
import re
import time
from pathlib import Path

from hardware_transport import Board

EXPECTED_CRC = {"seedcrc": 0xe9F5, "crclist": 0xE714, "crcmatrix": 0x1FD7, "crcstate": 0x8E3A}
FIELD = re.compile(r"^(seedcrc|\[0\]crclist|\[0\]crcmatrix|\[0\]crcstate|\[0\]crcfinal)\s*:\s*(0x[0-9a-fA-F]+)", re.MULTILINE)


def parse(text):
    values = {}
    for name, value in re.findall(r"([A-Za-z][A-Za-z /]*?)\s*:\s*([0-9.]+)", text):
        values.setdefault(name.strip().lower(), float(value))
    total = values.get("total ticks")
    iterations = values.get("iterations")
    crcs = {name.replace("[0]", ""): int(value, 16) for name, value in FIELD.findall(text)}
    return {"total_ticks": int(total) if total else None,
            "iterations": int(iterations) if iterations else None,
            "crc": crcs,
            "validated": "Correct operation validated" in text,
            "banner": "CoreMark" in text}


def choose_iterations(cpu_hz, ticks_per_iter, requested):
    """Pick the longest run that still fits CoreMark's 32-bit cycle counter.

    CoreMark reads `cycle` as 32 bits, so the run must stay under 2**32 ticks and
    above the 10 s minimum. The vision SoC is much slower per iteration than the
    CPU-only CoreMark SoC, which makes the old fixed ladder able to fall through
    to a wrapping count; search the whole range instead.
    """
    if requested:
        return requested
    if not ticks_per_iter or ticks_per_iter <= 0:
        raise SystemExit(f"Warm-up reported no usable tick count: {ticks_per_iter!r}")
    least = int(-(-10.2 * cpu_hz // ticks_per_iter))          # 10.2 s, 2% margin
    most = int(3.95e9 // ticks_per_iter)                      # 8% headroom under 2**32
    if most < least:
        raise SystemExit(f"{ticks_per_iter:.0f} ticks/iteration cannot run 10 s inside a "
                         f"32-bit counter at {cpu_hz/1e6:.3f} MHz")
    # Long enough to be a formal run, short enough to keep the UART wait sane.
    return min(most, max(least, int(20.0 * cpu_hz // ticks_per_iter)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--iterations", type=int, default=0,
                        help="Formal iteration count; 0 picks the largest value that keeps the 32-bit cycle counter from wrapping")
    parser.add_argument("--output", type=Path, default=Path("build/benchmark_board"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with Board(port=args.port, bridge_log=args.output / "bridge.log") as board:
        identity = board.ping()
        capabilities = board.capabilities()
        if not capabilities.get("benchmark"):
            raise SystemExit("This firmware has no op 22; flash the --benchmark vision image first")
        cpu_hz = identity["cpu_hz"]
        print(f"firmware 0x{identity['firmware_version']:08x} cpu {cpu_hz/1e6:.3f} MHz "
              f"accelerator {identity['ai_hz']/1e6:.3f} MHz gallery {identity['gallery_size']}")

        warm = board.benchmark(1, timeout=120)
        sample = parse(warm["text"])
        # One iteration cannot reach CoreMark's 10 s minimum, so upstream prints
        # "ERROR! Must execute for at least 10 secs" and skips the validated line.
        # The CRCs decide whether the port computes; 10 s is asserted on the real run.
        for name, expected in EXPECTED_CRC.items():
            if sample["crc"].get(name) != expected:
                raise SystemExit(f"warm-up {name} mismatch: got {sample['crc'].get(name)!r}, "
                                 f"expected {expected:#x}\n{warm['text']}")
        ticks_per_iter = sample["total_ticks"] / sample["iterations"]
        print(f"warm-up: {sample['total_ticks']} ticks for 1 iteration")

        iterations = choose_iterations(cpu_hz, ticks_per_iter, args.iterations)
        started = time.monotonic()
        run = board.benchmark(iterations, timeout=600)
        wall = time.monotonic() - started
        result = parse(run["text"])
        (args.output / "coremark_uart.txt").write_text(run["text"], encoding="utf-8")

        total = result["total_ticks"]
        count = result["iterations"]
        if not result["validated"] or count != iterations:
            raise SystemExit("CoreMark run did not validate:\n" + run["text"])
        for name, expected in EXPECTED_CRC.items():
            if result["crc"].get(name) != expected:
                raise SystemExit(f"{name} mismatch: got {result['crc'].get(name)!r}, expected {expected:#x}")
        per_iteration = total / count
        seconds = total / cpu_hz
        if seconds < 10.0:
            raise SystemExit(f"Runtime {seconds:.2f} s is below the 10 s requirement; raise --iterations")
        if total >= 2**32:
            raise SystemExit("32-bit cycle counter wrapped; CoreMark's total ticks are not comparable")

        identity = board.ping()
        counters = board.counters()
        report = {"port": args.port, "firmware_version": f"{identity['firmware_version']:08x}",
                  "cpu_hz": cpu_hz, "ai_hz": identity["ai_hz"],
                  "iterations": count, "total_ticks": total, "ticks_per_iteration": per_iteration,
                  "seconds": seconds, "wall_seconds": wall,
                  "coremark": cpu_hz / per_iteration, "coremark_per_mhz": 1e6 / per_iteration,
                  "crc": result["crc"], "validated": result["validated"],
                  "end_cycles": run["end_cycles"], "counters": counters,
                  "uart": run["text"]}
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"ticks/iteration {per_iteration:.4f}  CoreMark/MHz {report['coremark_per_mhz']:.6f}  "
              f"CoreMark {report['coremark']:.3f}  runtime {seconds:.2f} s")
        print(f"protocol survived: firmware 0x{identity['firmware_version']:08x} "
              f"icache {counters['icache_hits']}/{counters['icache_misses']} "
              f"dcache {counters['dcache_hits']}/{counters['dcache_misses']} rx_errors {counters['rx_errors']}")


if __name__ == "__main__":
    main()
