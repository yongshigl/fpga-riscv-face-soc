#!/usr/bin/env python3
"""Install a freshly built firmware image into this candidate's mem/ and manifest.

The candidate builds whatever mem/vision_text.hex holds (vision_soc.v defaults), so
the image has to be swapped in before the PDS flow runs and recorded in manifest.json

Usage: update_firmware.py <firmware build directory>
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
source = Path(sys.argv[1]).resolve()
build = json.loads((source / "firmware.json").read_text())

for name in ("vision_text.hex", "vision_data.hex"):
    shutil.copy2(source / name, PROJECT / "mem" / name)
manifest_path = PROJECT / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
manifest["firmware"] = build
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

for name, digest in build["files"].items():
    if not name.endswith(".hex"):
        continue
    actual = hashlib.sha256((PROJECT / "mem" / name).read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit(f"mismatched {name}")
print(json.dumps({"firmware_version": build["firmware_version"],
                  "features": build["features"],
                  "static_end": build["static_end"],
                  "stack_reserve_bytes": build["stack_reserve_bytes"]}, indent=2))
