#!/usr/bin/env python3
"""Check cache response policies against the stalled backing-memory reference."""
import argparse
import hashlib
import itertools
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--disable-statistics", action="store_true")
    parser.add_argument("--cache-tags-lutram", action="store_true")
    parser.add_argument("--fast-write-response", action="store_true")
    args = parser.parse_args()
    build = args.build.resolve()
    build.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / "rtl/two_way_cache.v", ROOT / "tb/tb_two_way_cache.s",
               ROOT / "rtl/memory_line_backend.v", ROOT / "tb/tb_memory_line_backend.v"]
    records = []
    backend_binary = build / "backend.vvp"
    backend_command = ["iverilog", "-g2012", "-s", "tb_memory_line_backend",
                       f"-Ptb_memory_line_backend.FAST_WRITE_RESPONSE={int(args.fast_write_response)}",
                       "-o", str(backend_binary), *map(str, sources[2:])]
    subprocess.run(backend_command, cwd=ROOT, check=True)
    backend = subprocess.run(["vvp", str(backend_binary)], cwd=ROOT, capture_output=True, text=True)
    (build / "backend.log").write_text(backend.stdout + backend.stderr, encoding="ascii")
    if backend.returncode or "PASS:" not in backend.stdout:
        raise RuntimeError(f"Line backend failed:\n{backend.stdout}{backend.stderr}")
    print(backend.stdout, end="", flush=True)
    for fast, early, ingress, tags in itertools.product((0, 1), repeat=4):
        name = f"fast{fast}_early{early}_ingress{ingress}_tags{tags}"
        binary = build / f"{name}.vvp"
        command = ["iverilog", "-g2012", "-s", "tb_two_way_cache",
                   f"-Ptb_two_way_cache.FAST_RESPONSE={fast}",
                   f"-Ptb_two_way_cache.EARLY_RESTART={early}",
                   f"-Ptb_two_way_cache.INGRESS_WRITE_REQUEST={ingress}",
                   f"-Ptb_two_way_cache.FAST_WRITE_RESPONSE={int(args.fast_write_response)}",
                   f"-Ptb_two_way_cache.REGISTER_TAG_LOOKUP={tags}",
                   f"-Ptb_two_way_cache.STATISTICS_ENABLE={int(not args.disable_statistics)}",
                   "-o", str(binary), *map(str, sources[:2])]
        if args.cache_tags_lutram:
            command.insert(1, "-DCPU_CACHE_TAGS_LUTRAM")
        subprocess.run(command, cwd=ROOT, check=True)
        result = subprocess.run(["vvp", str(binary)], cwd=ROOT, capture_output=True, text=True)
        (build / f"{name}.log").write_text(result.stdout + result.stderr, encoding="ascii")
        if result.returncode or "PASS:" not in result.stdout:
            raise RuntimeError(f"{name} failed:\n{result.stdout}{result.stderr}")
        print(result.stdout, end="", flush=True)
        records.append({"fast_response": bool(fast), "early_restart": bool(early),
                        "ingress_write_request": bool(ingress),
                        "fast_write_response": args.fast_write_response,
                        "registered_tag_lookup": bool(tags),
                        "statistics_enabled": not args.disable_statistics,
                        "cache_tags_lutram": args.cache_tags_lutram,
                        "command": command, "output": result.stdout})
    report = {"passed": True, "cases": records,
              "backend": {"command": backend_command, "output": backend.stdout}, "sources": {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}
    (build / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
