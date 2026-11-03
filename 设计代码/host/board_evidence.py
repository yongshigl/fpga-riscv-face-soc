"""Bind board measurements to a frozen PDS project and successful JTAG download."""
import hashlib
import json
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_model_report(report, expected_layers):
    if (report.get("complete") is not True
            or report.get("all_full_padded_bytes_identical") is not True
            or report.get("packed_readback_checked") is not True
            or report.get("total_layers") != sum(expected_layers.values())):
        raise ValueError("Model report lacks complete padded and packed verification")
    models = report.get("models", [])
    if len(models) != len(expected_layers) or {model.get("name") for model in models} != set(expected_layers):
        raise ValueError("Model report contains missing or duplicate models")
    for model in models:
        layers = model.get("layers", [])
        if len(layers) != expected_layers[model["name"]]:
            raise ValueError("Model report is missing layer evidence")
        for index, layer in enumerate(layers):
            packed = layer.get("packed_readback", {})
            if (layer.get("index") != index or layer.get("mismatched_bytes") != 0
                    or layer.get("maximum_absolute_error") != 0
                    or layer.get("full_padding_verified") is not True
                    or not layer.get("actual_sha256")
                    or layer["actual_sha256"] != layer.get("expected_sha256")
                    or packed.get("all_bytes_identical") is not True
                    or not packed.get("actual_sha256")
                    or packed["actual_sha256"] != packed.get("expected_sha256")):
                raise ValueError("Model layer padded or packed evidence differs")


def validate_project(project, identity):
    if project is None:
        return None
    project = Path(project).resolve()
    manifest_path = project / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    bitstreams = list((project / "generate_bitstream").glob("*.sbit"))
    expected_hash = manifest["bitstream_sha256"].lower()
    if (len(bitstreams) != 1 or not manifest["timing_met"] or not manifest["volatile_only"]
            or file_hash(bitstreams[0]) != expected_hash):
        raise ValueError("Board evidence requires one matching PDS bitstream with timing closure")
    record = json.loads((project / "program.json").read_text(encoding="utf-8-sig"))
    if (not record["volatile_jtag"] or record.get("flash_written", False)
            or record["bitstream_sha256"].lower() != expected_hash):
        raise ValueError("Successful volatile JTAG record does not match the PDS project")
    if (identity["cpu_hz"] != manifest["cpu_clock_hz"]
            or identity["ai_hz"] != manifest["ai_clock_hz"]
            or identity["uart_baud"] != manifest["uart_baud"]):
        raise ValueError("The running board clocks or UART do not match the PDS project")
    version = manifest["firmware"].get("firmware_version")
    if version is not None and identity["firmware_version"] != int(version, 16):
        raise ValueError("The running firmware version does not match the PDS project")
    for source in manifest["sources"]:
        path = project / source["name"].replace("\\", "/")
        if not path.is_file():
            path = project / "src" / source["name"]
        if file_hash(path) != source["sha256"].lower():
            raise ValueError(f"Frozen source hash differs: {source['name']}")
    for constraint in manifest.get("constraints", []):
        if file_hash(project / constraint["name"]) != constraint["sha256"].lower():
            raise ValueError(f"Constraint hash differs: {constraint['name']}")
    for name, digest in manifest["firmware"]["files"].items():
        if name.endswith(".hex") and file_hash(project / "mem" / name) != digest.lower():
            raise ValueError(f"Compiled firmware image hash differs: {name}")
    return {"project": manifest["project"], "manifest_sha256": file_hash(manifest_path),
            "bitstream_sha256": expected_hash, "program": record,
            "cpu_clock_hz": manifest["cpu_clock_hz"], "ai_clock_hz": manifest["ai_clock_hz"],
            "timing_met": True, "lut_post_route": manifest["lut_post_route"],
            "ff_post_route": manifest["ff_post_route"], "apm": manifest["apm"], "drm": manifest["drm"],
            "camera_preview": manifest.get("camera_preview", False), "mac_pixels": manifest["mac_pixels"],
            "ai_read_cache_lines": manifest.get("ai_read_cache_lines", 2),
            "ai_depthwise_channel_outer": manifest.get("ai_depthwise_channel_outer", False),
            "ai_read_cache_full_lru": manifest.get("ai_read_cache_full_lru", False),
            "camera_overlap_capture": manifest.get("camera_overlap_capture", False),
            "identity_scope": "Firmware identity and successful JTAG record; UART is not a bitstream hash readback.",
            "clock_independently_measured": manifest["clock_independently_measured"]}
