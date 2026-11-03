#!/usr/bin/env python3
"""Derive published metrics from a frozen PDS manifest and captured UART run."""
import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.project / "manifest.json").read_text(encoding="utf-8-sig"))
    uart = (args.project / "coremark_uart.txt").read_text(encoding="ascii")
    for pattern in (r"crclist\s*:\s*0xe714", r"crcmatrix\s*:\s*0x1fd7", r"crcstate\s*:\s*0x8e3a", r"Correct operation validated"):
        if not re.search(pattern, uart):
            raise RuntimeError(f"Missing validated CoreMark evidence: {pattern}")
    ticks = int(re.search(r"Total ticks\s*:\s*(\d+)", uart)[1])
    iterations = int(re.search(r"Iterations\s*:\s*(\d+)", uart)[1])
    clock = manifest["clock_hz"]
    bitstream = args.project / "generate_bitstream/pg2l200h_mini_coremark_top.sbit"
    if (not manifest["timing_met"] or ticks < 10 * clock or
            iterations != manifest["firmware"]["iterations"] or
            hashlib.sha256(bitstream.read_bytes()).hexdigest().lower() != manifest["bitstream_sha256"].lower()):
        raise RuntimeError("Timing, run duration, firmware or bitstream validation failed")
    score = iterations * clock / ticks
    result = {"measured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "project": manifest["project"], "ticks": ticks, "iterations": iterations,
              "seconds": ticks / clock, "clock_hz": clock,
              "coremark_per_mhz": iterations * 1000000 / ticks, "coremark_per_second": score,
              "lut_synthesis": manifest["lut_synthesis"], "lut_post_route": manifest["lut_post_route"],
              "coremark_per_lut_synthesis": score / manifest["lut_synthesis"],
              "coremark_per_lut_post_route": score / manifest["lut_post_route"],
              "crc_validated": True, "bitstream_sha256": manifest["bitstream_sha256"],
              "scope": "Complete benchmark SoC; cameras and CNN excluded",
              "synchronous_memory": manifest.get("synchronous_memory", False),
              "icache_bytes": manifest.get("icache_bytes", 0), "dcache_bytes": manifest.get("dcache_bytes", 0),
              "pipeline_stages": manifest.get("pipeline_stages", 5),
              "regfile_lutram": manifest.get("regfile_lutram", False),
              "decouple_fetch": manifest.get("decouple_fetch", False),
              "cache_fast_response": manifest.get("cache_fast_response", False),
              "cache_early_restart": manifest.get("cache_early_restart", False),
              "cache_ingress_write": manifest.get("cache_ingress_write", False),
              "cache_fast_write_response": manifest.get("cache_fast_write_response", False),
              "counter_csr_predecode": manifest.get("counter_csr_predecode", False),
              "csr_address_predecode": manifest.get("csr_address_predecode", False),
              "fast_redirect_select": manifest.get("fast_redirect_select", False),
              "split_mtime": manifest.get("split_mtime", False),
              "align_mmio_address": manifest.get("align_mmio_address", False),
              "register_bypass": manifest.get("register_bypass", False),
              "early_load_request": manifest.get("early_load_request", False),
              "cache_register_tags": manifest.get("cache_register_tags", False),
              "cache_tags_lutram": manifest.get("cache_tags_lutram", False),
              "cache_statistics": manifest.get("cache_statistics", manifest.get("synchronous_memory", False)),
              "register_icache_invalidate": manifest.get("register_icache_invalidate", False),
              "coremark_version": manifest["firmware"]["coremark_version"],
              "clock_independently_measured": manifest.get("clock_independently_measured", False)}
    (args.project / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="ascii")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
