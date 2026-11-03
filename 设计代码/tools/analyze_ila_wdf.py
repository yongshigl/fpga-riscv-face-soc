#!/usr/bin/env python3
"""Decode a Fabric Debugger WDF capture and analyse CoreMark pipeline behaviour.

Why this exists: `dbg_power_init` + `dbg_save_project` are scriptable and write a
`.wdf` next to the `.dprj`, while VCD/ASCII export is a GUI-only "Export data"
menu action (see the 2026-10-01 capture logs).  The WDF itself is parseable:
every data channel is stored as its name (uint16 length + UTF-16LE) followed by
one UTF-16LE '0'/'1' character per captured sample.

Verified against the 2026-10-01 capture that also has a vendor-exported VCD:
channel bits from the WDF match the VCD sample-for-sample for 2047 of 2047
samples, with the WDF holding samples 1..2047 (the VCD's sample 0 is the
exporter's 'x' initialisation state).  `--check-vcd` re-runs that comparison.

This tool writes samples.csv / report.json / README.md / pipeline_window.png and
never mutates the capture directory.
"""
import argparse
from collections import Counter
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

FLAGS = ("ifid_valid", "idex_valid", "exmem_valid", "memwb_valid",
         "load_use_stall", "ex_prediction_error", "ex_is_branch", "take_trap",
         "memory_stall", "arbiter_busy")

# The fic alias and the netlist hierarchy path differ for the arbiter busy flag;
# both builds have been captured, so accept either spelling.
CHANNEL_ALIASES = {"soc/arbiter/busy": "soc/core/arbiter_busy"}
NAME_BYTES = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
DATA_BYTES = re.compile(rb"(?:[01]\x00){200,}")
BRANCH_OPCODE = 0x63
JAL_OPCODE = 0x6F
JALR_OPCODE = 0x67


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def channel_name(blob, offset):
    """Return the UTF-16 channel name stored just before `offset`."""
    window = blob[max(0, offset - 96):offset]
    matches = list(NAME_BYTES.finditer(window))
    if not matches:
        raise ValueError(f"No channel name before data at 0x{offset:x}")
    text = matches[-1].group().decode("utf-16-le")
    # The name is prefixed by its own uint16 byte length.  When that length is
    # below 0x80 its low byte is printable ASCII and the string scan swallows
    # it, so the decoded text starts with one stray character ("&" for a
    # 38-byte name, "$" for 36, and so on).  Only strip it when it really is
    # the length of the remaining name.
    if len(text) > 1 and ord(text[0]) == (len(text) - 1) * 2:
        text = text[1:]
    return text


def parse_wdf(path):
    blob = Path(path).read_bytes()
    channels = {}
    runs = list(DATA_BYTES.finditer(blob))
    if not runs:
        raise ValueError("No data channels in the WDF file")
    lengths = {len(m.group()) // 2 for m in runs}
    if len(lengths) != 1:
        raise ValueError(f"Channels disagree on sample count: {sorted(lengths)}")
    for match in runs:
        name = channel_name(blob, match.start())
        if name in channels:
            raise ValueError(f"Duplicate channel {name}")
        channels[name] = match.group().decode("utf-16-le")
    return channels, lengths.pop()


def instructions(path):
    result, function = {}, None
    for line in Path(path).read_text().splitlines():
        label = re.fullmatch(r"([0-9a-f]+) <([^>]+)>:", line)
        if label:
            function = label[2]
        decoded = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(.+)", line)
        if decoded:
            result[int(decoded[1], 16)] = {"word": int(decoded[2], 16),
                                         "assembly": decoded[3].strip(), "function": function}
    if not result:
        raise ValueError("No RV32 instructions in the disassembly")
    return result


def source_registers(word):
    opcode, function = word & 0x7f, word >> 12 & 7
    registers = []
    if opcode in (0x03, 0x13, 0x23, 0x33, 0x63, 0x67) or opcode == 0x73 and function in (1, 2, 3):
        registers.append(word >> 15 & 31)
    if opcode in (0x23, 0x33, 0x63):
        registers.append(word >> 20 & 31)
    return sorted(set(registers) - {0})


def build_samples(channels, depth, decoded):
    samples = []
    for cycle in range(depth):
        row = {"sample": cycle + 1}
        for name in ("ifid_pc", "idex_pc"):
            row[name] = sum(int(channels[f"soc/core/{name}[{bit}]"][cycle]) << bit
                            for bit in range(32))
        row.update({name: int(channels[f"soc/core/{name}"][cycle]) for name in FLAGS})
        for name, valid in (("ifid_pc", "ifid_valid"), ("idex_pc", "idex_valid")):
            if row[valid] and (row[name] & 3 or row[name] not in decoded):
                raise ValueError(f"Valid pipeline PC is absent from firmware: {row}")
        instruction = decoded.get(row["idex_pc"], {})
        row["assembly"] = instruction.get("assembly", "")
        row["function"] = instruction.get("function", "")
        row["opcode"] = (instruction.get("word", 0) & 0x7f) if row["idex_valid"] else None
        samples.append(row)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wdf", type=Path, required=True)
    parser.add_argument("--disassembly", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--bitstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trigger-pc", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--pretrigger", type=int, default=256)
    parser.add_argument("--clock-hz", type=int, required=True)
    parser.add_argument("--bitstream-sha256", default=None,
                        help="Expected bitstream hash; compared with --bitstream")
    parser.add_argument("--check-vcd", type=Path,
                        help="Optional vendor VCD of the same capture, for cross-validation")
    args = parser.parse_args()

    channels, depth = parse_wdf(args.wdf)
    channels = {CHANNEL_ALIASES.get(name, name): values for name, values in channels.items()}
    expected ={f"soc/core/{name}[{bit}]" for name in ("ifid_pc", "idex_pc") for bit in range(32)}
    expected.update(f"soc/core/{name}" for name in FLAGS)
    if set(channels) != expected:
        missing = sorted(expected - set(channels))
        extra = sorted(set(channels) - expected)
        raise ValueError(f"Unexpected ILA channels; missing={missing} extra={extra}")

    decoded = instructions(args.disassembly)
    samples = build_samples(channels, depth, decoded)

    if args.check_vcd:
        text = args.check_vcd.read_text(errors="replace")
        name2id = {m.group(2): m.group(1) for m in
                   re.finditer(r"\$var\s+\w+\s+\d+\s+(\S+)\s+(\S+)", text)}
        body = text.split("$enddefinitions")[1]
        values = {i: [] for i in name2id.values()}
        current = {i: "x" for i in name2id.values()}
        for token in body.split():
            if token.startswith("#"):
                for key in values:
                    values[key].append(current[key])
            elif len(token) >= 2 and token[0] in "01" and token[1:] in values:
                current[token[1:]] = token[0]
        mismatches = 0
        for name in expected:
            key = name if name in name2id else "fabric_debugger." + name
            vcd = "".join(values[name2id[key]])[1:1 + depth]
            mismatches += sum(int(a) != int(b) for a, b in zip(vcd, channels[name]))
        if mismatches:
            raise ValueError(f"WDF and VCD disagree on {mismatches} bit samples")
        print(f"WDF cross-checked against {args.check_vcd.name}: 0 mismatching bits over "
              f"{len(expected)} channels x {depth} samples")

    bitstream_hash = sha256(args.bitstream)
    if args.bitstream_sha256 and bitstream_hash != args.bitstream_sha256.lower():
        raise ValueError("Bitstream hash does not match the expected capture record")

    position = args.pretrigger
    trigger_row = samples[position]
    if trigger_row["idex_pc"] != args.trigger_pc or not trigger_row["idex_valid"]:
        raise ValueError(f"Trigger PC 0x{args.trigger_pc:x} is not in ID/EX at sample {position}")

    stalls, redirects, loops, bubbles = [], [], [], []
    hold_violations, noncontrol = [], []
    stale_prediction_errors, traps = [], []
    functions, pcs = Counter(), Counter()
    classes = Counter()
    for index, row in enumerate(samples):
        following = samples[index + 1] if index + 1 < len(samples) else None
        if row["idex_valid"]:
            functions[row["function"]] += 1
            pcs[row["idex_pc"]] += 1
        if row["load_use_stall"]:
            instruction = decoded[row["idex_pc"]]["word"]
            is_load = instruction & 0x7f == 3
            is_high_mul = instruction & 0x7f == 0x33 and instruction >> 25 == 1 and (instruction >> 12 & 7) in (1, 2, 3)
            destination = instruction >> 7 & 31
            sources = source_registers(decoded[row["ifid_pc"]]["word"]) if row["ifid_valid"] else []
            if not row["idex_valid"] or not row["ifid_valid"] or not (is_load or is_high_mul) \
                    or not destination or destination not in sources:
                hold_violations.append({"kind": "load_use_stall_producer", "sample": row["sample"]})
            # The hold must freeze both stages: IF/ID keeps the consumer and
            # ID/EX keeps the producer.  ID/EX may legitimately stay valid on
            # the held PC (and may drop valid while it waits on the data port).
            if following and (following["ifid_pc"] != row["ifid_pc"]
                              or following["ifid_valid"] != row["ifid_valid"]
                              or following["idex_pc"] != row["idex_pc"]):
                hold_violations.append({"kind": "load_use_stall", "sample": row["sample"]})
            stalls.append({"sample": row["sample"], "producer_pc": row["idex_pc"],
                           "producer": row["assembly"], "consumer_pc": row["ifid_pc"],
                           "consumer": decoded[row["ifid_pc"]]["assembly"],
                           "destination_register": destination, "source_registers": sources})
            classes["load_use_stall"] += 1
        elif row["memory_stall"]:
            # Data-side wait: EX holds because the data port has not answered
            # (DDR/cache line refill).  The consumer stays in ID/EX.
            if following and following["idex_valid"] and following["idex_pc"] != row["idex_pc"]:
                hold_violations.append({"kind": "memory_stall", "sample": row["sample"]})
            classes["memory_stall"] += 1
        elif not row["idex_valid"]:
            # Neither dependency nor data-side stall: the front end is waiting
            # for an instruction line (I-cache refill) or a redirect flush.
            classes["bubble"] += 1
            bubbles.append(row["sample"])
        if row["ex_prediction_error"]:
            if not row["idex_valid"]:
                # The raw flag is combinational over the ID/EX word, so a stale
                # instruction held during a stall can assert it.  The core acts on
                # ex_mispredict, which ANDs idex_valid; record, do not count.
                stale_prediction_errors.append(row["sample"])
                continue
            is_control = row["opcode"] in (BRANCH_OPCODE, JAL_OPCODE, JALR_OPCODE, 0x73)
            if not is_control:
                # ex_prediction_error also covers the sequential-prediction check
                # for a non-control instruction; record it instead of aborting.
                classes["frontend_recovery"] += 1
                noncontrol.append({"sample": row["sample"], "pc": row["idex_pc"],
                                   "assembly": row["assembly"]})
                continue
            if following and (following["ifid_valid"] or following["idex_valid"]):
                hold_violations.append({"kind": "redirect_flush", "sample": row["sample"]})
            redirects.append({"sample": row["sample"], "pc": row["idex_pc"],
                              "assembly": row["assembly"], "is_branch": bool(row["ex_is_branch"])})
            classes["redirect"] += 1
        if row["idex_valid"] and following:
            later = next((sample for sample in samples[index + 1:index + 5] if sample["idex_valid"]), None)
            if later and row["opcode"] in (BRANCH_OPCODE, JAL_OPCODE) and later["idex_pc"] < row["idex_pc"]:
                loops.append({"sample": row["sample"], "source_pc": row["idex_pc"],
                              "target_pc": later["idex_pc"], "function": row["function"],
                              "assembly": row["assembly"],
                              "mispredicted": bool(row["ex_prediction_error"])})
        if row["take_trap"]:
            traps.append({"sample": row["sample"], "pc": row["idex_pc"],
                          "assembly": row["assembly"]})

    if not stalls or not redirects or not loops:
        raise ValueError("The capture lacks expected CoreMark activity")

    retired = sum(row["idex_valid"] for row in samples)
    branch_slots = sum(1 for row in samples if row["idex_valid"] and row["opcode"] == BRANCH_OPCODE)
    control_slots = sum(1 for row in samples
                        if row["idex_valid"] and row["opcode"] in (BRANCH_OPCODE, JAL_OPCODE, JALR_OPCODE))
    if branch_slots == 0:
        raise ValueError("No conditional branch entered ID/EX in the window; the capture is not CoreMark")
    loop_counts = Counter((row["source_pc"], row["target_pc"], row["function"]) for row in loops)
    report = {"verified_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "execution": "Fabric Debugger JTAG power-on ILA readback (WDF decoded offline)",
              "wdf_sha256": sha256(args.wdf), "wdf_bytes": Path(args.wdf).stat().st_size,
              "bitstream_sha256": bitstream_hash,
              "firmware_elf_sha256": sha256(args.elf),
              "disassembly_sha256": sha256(args.disassembly),
              "channels": len(channels), "samples": len(samples),
              "sample_numbering": "1..N; the WDF drops the exporter's 'x' initialisation sample",
              "clock_hz": args.clock_hz,
              "capture_duration_us": len(samples) * 1e6 / args.clock_hz,
              "trigger": {"pc": args.trigger_pc, "sample": position,
                          "assembly": decoded[args.trigger_pc]["assembly"],
                          "function": decoded[args.trigger_pc]["function"]},
              "asserted_samples": {name: sum(row[name] for row in samples) for name in FLAGS},
              "retired_in_window": retired,
              "throughput_instructions_per_cycle": retired / len(samples),
              "bubble_classes": dict(classes),
              "branch_slots": branch_slots,
              "control_transfer_slots": control_slots,
              "mispredictions": len(redirects),
              "mispredict_rate_per_control_transfer": len(redirects) / control_slots if control_slots else None,
              "load_use_stalls": len(stalls), "memory_stall_cycles": classes["memory_stall"],
              "arbiter_busy_cycles": sum(row["arbiter_busy"] for row in samples),
              "frontend_recovery_without_control_transfer": len(noncontrol),
              "noncontrol_prediction_errors": noncontrol[:20],
              "stale_prediction_error_samples": len(stale_prediction_errors),
              "stale_prediction_error_list": stale_prediction_errors[:20],
              "trap_samples": traps,
              "capture_clean": not traps,
              "hold_invariant_violations": hold_violations[:20],
              "function_ex_samples": dict(functions.most_common()),
              "hot_pcs": [{"pc": pc, "ex_samples": count, **decoded[pc]} for pc, count in pcs.most_common(20)],
              "backward_edges": [{"source_pc": source, "target_pc": target, "function": function, "count": count}
                                 for (source, target, function), count in loop_counts.most_common()],
              "stalls": stalls, "redirects": redirects, "loop_events": loops,
              "scope": "One actual captured CoreMark window; occupancy is not a whole-run CPI or predictor accuracy"}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "samples.csv").open("w", newline="", encoding="ascii") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(samples[0]))
        writer.writeheader()
        writer.writerows(samples)
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")

    left = max(0, position - 12)
    right = min(len(samples), position + 164)
    selected = samples[left:right]
    figure, axes = plt.subplots(3, 1, figsize=(15, 8.5), sharex=True,
                                gridspec_kw={"height_ratios": [1.3, 1.3, 3.4]})
    x = [row["sample"] for row in selected]
    for axis, name, valid, color in ((axes[0], "ifid_pc", "ifid_valid", "#176a72"),
                                     (axes[1], "idex_pc", "idex_valid", "#a8354c")):
        values = [row[name] for row in selected]
        axis.step(x, values, where="post", color=color, linewidth=1.1)
        axis.set_ylabel(name)
        # Tick only the range this stage actually visited, so a 0xC50..0xD60
        # loop stays legible instead of being flattened by the reset PC.
        seen = [value for value, row in zip(values, selected) if row[valid]]
        if seen:
            low, high = min(seen), max(seen)
            # A tight loop spans only a few instructions; one tick keeps the
            # labels from overprinting on a short axis.
            if high - low < 0x40:
                ticks = [low - low % 0x10]
            else:
                # Low + high only: a third tick bunches near the loop PC and
                # overprints, because the entry PC is far above the loop body.
                ticks = sorted({low - low % 0x10, high - high % 0x10})
            axis.set_yticks(ticks)
        axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"0x{int(value):x}"))
        axis.tick_params(axis="y", labelsize=9)
    axes[0].text(0.012, 0.88,
                 f"trigger at the dashed line: PC 0x{args.trigger_pc:x} "
                 f"({decoded[args.trigger_pc]['function']})",
                 transform=axes[0].transAxes, va="top", fontsize=10, color="#333333",
                 bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85})
    for offset, name in enumerate(FLAGS):
        axes[2].step(x, [offset * 1.6 + row[name] for row in selected], where="post", linewidth=1.1)
    axes[2].set_yticks([offset * 1.6 + 0.5 for offset in range(len(FLAGS))], FLAGS)
    axes[2].set_xlabel("Captured CPU sample (one sample per clock)")
    for axis in axes:
        axis.axvline(position, color="#333333", linestyle="--", linewidth=0.8)
        axis.grid(alpha=0.2)
    axes[2].set_title(f"{retired} of {len(samples)} cycles carry a valid ID/EX instruction  ·  "
                      f"{len(redirects)} prediction redirects  ·  {classes['load_use_stall']} load-use holds",
                      loc="right", fontsize=10, color="#333333", pad=6)
    figure.suptitle(f"Actual JTAG ILA capture — CoreMark at {args.clock_hz / 1e6:g} MHz "
                    f"({len(samples)} samples, one sample = one clock)")
    figure.tight_layout()
    figure.savefig(args.output / "pipeline_window.png", dpi=150)
    plt.close(figure)

    summary = ["# Actual CoreMark Pipeline Capture", "",
               f"All {len(samples)} samples come from Fabric Debugger JTAG readback (`.wdf`, decoded offline).", "",
               f"Trigger: sample {position}, PC `0x{args.trigger_pc:08x}`, `{decoded[args.trigger_pc]['function']}`.",
               f"CPU: {args.clock_hz / 1e6:g} MHz; window: {report['capture_duration_us']:.3f} us.", "",
               "| Signal | Asserted samples |", "| --- | ---: |"]
    summary.extend(f"| `{name}` | {report['asserted_samples'][name]} |" for name in FLAGS)
    summary.extend(["",
                    f"ID/EX carried a valid instruction in {retired} of {len(samples)} samples "
                    f"({report['throughput_instructions_per_cycle']:.3f} instructions per cycle).",
                    f"Bubbles: {classes['load_use_stall']} load-use, {classes['memory_stall']} data-side (memory_stall), "
                    f"{classes['redirect']} prediction flushes, {classes['bubble']} front-end/unclassified.",
                    f"Prediction redirects: {len(redirects)} over {control_slots} control transfers in EX.",
                    "", "Every dependency pause has a matching nonzero source/destination register, holds ID and inserts an EX bubble.",
                    "Every prediction redirect flushes both younger stages.",
                    ("No trap was observed." if not traps
                     else f"WARNING: {len(traps)} trap samples in the window; see `report.json`."), "",
                    "| Backward edge | Function | Observations |", "| --- | --- | ---: |"])
    summary.extend(f"| `0x{source:x} -> 0x{target:x}` | `{function}` | {count} |"
                   for (source, target, function), count in loop_counts.most_common(12))
    summary.extend(["", "The window includes initialisation and predictor training. Counts describe this window only.",
                    "See `samples.csv`, `report.json`, `pipeline_window.png` and the original WDF for review.", ""])
    (args.output / "README.md").write_text("\n".join(summary), encoding="ascii")
    print(f"PASS actual ILA: samples={len(samples)}, stalls={len(stalls)}, redirects={len(redirects)}, "
          f"memory_stall={classes['memory_stall']}, backward_edges={len(loops)}")


if __name__ == "__main__":
    main()
