#!/usr/bin/env python3
"""Rebuild manifest.json for this candidate after the PDS flow, mirroring build_vision.ps1.

The project was cloned from the routed dual-mode candidate with src/ unchanged; only the
firmware image differs (-Os, CoreMark linked in, 8,053 words in a 32 KiB ROM). Every
hardware-derived field is recomputed from this run's reports, and the preview geometry is
read back from the top-level RTL rather than assumed.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
NAME = PROJECT.name
TOP = "pg2l200h_mini_vision_top"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


manifest = json.loads((PROJECT / "manifest.json").read_text(encoding="utf-8-sig"))
route = (PROJECT / "place_route" / f"{TOP}.prr").read_text(encoding="utf-8", errors="replace")
timing = (PROJECT / "report_timing" / f"{TOP}.rtr").read_text(encoding="utf-8", errors="replace")
match = re.search(rf"(?m)^\|\s+{TOP}\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+([\d.]+)", route)
if not match:
    raise SystemExit("Missing PDS resource report")
lut, ff, dram, apm, drm = match.groups()
bitstream = PROJECT / "generate_bitstream" / f"{TOP}.sbit"

# Preview geometry must come from the RTL that was actually built, not from a constant
# left over by whichever candidate this one was cloned from: the cam0 preview width is
# 1280 >> RGB_PREVIEW_DOWNSAMPLE_BITS, and getting it wrong would put a false
# resolution in the evidence.
top_source = (PROJECT / "src" / f"{TOP}.v").read_text(encoding="utf-8", errors="replace")
preview_bits = re.search(r"\.RGB_PREVIEW_DOWNSAMPLE_BITS\s*\(\s*(\d+)\s*\)", top_source)
if not preview_bits:
    raise SystemExit("Missing RGB_PREVIEW_DOWNSAMPLE_BITS in the top level")
preview_bits = int(preview_bits.group(1))

manifest.update({
    "project": NAME,
    "built_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "timing_met": "Design Summary : All Constraints Met." in timing,
    "lut_post_route": int(lut),
    "ff_post_route": int(ff),
    "distributed_ram_luts": int(dram),
    "apm": int(apm),
    "drm": float(drm),
    "bitstream_sha256": digest(bitstream),
    "timing_report_sha256": digest(PROJECT / "report_timing" / f"{TOP}.rtr"),
    "camera_rgb_preview": [1280 >> preview_bits, 720 >> preview_bits],
    "camera_preview_downsample_bits": preview_bits,
})

# cam1 runs the sensor's own JPEG engine in this bitstream (innovation 1). Record only the
# switch, read from the shipped config; the actual frame geometry is measured from the
# JPEG headers on board (证据 preview/stills.json), never copied from a config constant.
vision_config = (PROJECT / "src" / "vision_config.vh").read_text(encoding="utf-8", errors="replace")
manifest["camera1_native_jpeg"] = bool(
    re.search(r"^\s*`define\s+VISION_CAM1_JPEG\b", vision_config, re.MULTILINE))
manifest["sources"] = [
    {"name": str(path.relative_to(PROJECT)).replace("/", "\\"), "sha256": digest(path)}
    for path in sorted((PROJECT / "src").rglob("*")) if path.is_file()
]
manifest["constraints"] = [{"name": name, "sha256": digest(PROJECT / name)}
                           for name in ("board_pins.fdc", "vision_clocks.fdc")]
# The ROM/RAM images live in mem/ (that is what $readmemh loads); the ELF that produced
# them stays in build/firmware/. Resolve every name the template recorded against both,
# and drop names that are genuinely absent instead of crashing after a 80-minute P&R run.
firmware_files = {}
for name in manifest["firmware"]["files"]:
    for base in (PROJECT / "mem", PROJECT / "build" / "firmware"):
        if (base / name).is_file():
            firmware_files[name] = digest(base / name)
            break
    else:
        print(f"  note: firmware file {name} not found in mem/ or build/firmware/ — omitted")
for required in ("vision_text.hex", "vision_data.hex"):
    if required not in firmware_files:
        raise SystemExit(f"Missing installed firmware image mem/{required}")
manifest["firmware"]["files"] = firmware_files

(PROJECT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"timing_met={manifest['timing_met']} LUT={lut} FF={ff} DRAM={dram} APM={apm} DRM={drm}")
print(f"bitstream sha256 {manifest['bitstream_sha256']}")
print(f"preview {manifest['camera_rgb_preview']}")
