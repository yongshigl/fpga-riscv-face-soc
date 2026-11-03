"""Freeze the complete executable serial receiver with hardware observations."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRANSPORT_SOURCES = ("hardware_transport.py", "serial_bridge.py", "windows_serial_pipeline.py",
                     "windows_serial_native.py", "windows_serial_receiver.dll",
                     "native/windows_serial_receiver.cpp", "native/build.json",
                     "camera_stream.py", "transport_evidence.py")


def snapshot_transport(output):
    hashes = {}
    for name in TRANSPORT_SOURCES:
        data = (ROOT / name).read_bytes()
        target = Path(output) / "driver_sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes
