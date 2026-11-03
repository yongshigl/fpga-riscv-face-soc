"""Bounded framing for the shared camera/CPU UART and independent image decoding."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib


CAMERA_MAGIC = b"PGC2"
RESPONSE_MAGIC = b"PGS1"
BRIDGE_READY = b"PG_BRIDGE_READY_v1\n"


@dataclass
class WirePacket:
    magic: bytes
    command: int
    sequence: int
    payload: bytes
    camera: int = -1
    format: int = 0
    flags: int = 0
    received_monotonic: float = 0

    def sensor(self):
        if self.magic != CAMERA_MAGIC or self.command != 2:
            raise ValueError("Packet is not a camera diagnostic")
        names = ("status", "chip_id", "format", "isp", "test_pattern", "control",
                 "width", "height", "nacks", "last_address", "acknowledged_writes")
        result = dict(zip(names, struct.unpack_from(">BHBBBBHHHHB", self.payload)))
        result.update(camera=self.camera, build_id=struct.unpack_from("<I", self.payload, 24)[0],
                      protocol=self.payload[23], flags=self.payload[22],
                      sequence=struct.unpack_from("<I", self.payload, 16)[0])
        jpeg = result["protocol"] == 0xa4 and result["format"] == 0x30 and result["isp"] == 0
        rgb = result["protocol"] == 0xa3 and result["format"] == 0x61 and result["isp"] == 1
        result["verified"] = (result["status"] == 6 and result["chip_id"] == 0x5640 and
                              result["test_pattern"] == 0 and not result["control"] & 0x40 and
                              result["nacks"] == 0 and bool(result["flags"] & 1) and (jpeg or rgb))
        return result

    def image(self, baud=921600, clock_hz=27000000):
        import cv2
        import numpy as np

        if self.magic != CAMERA_MAGIC or self.command != 1:
            raise ValueError("Packet is not a camera image")
        width, height, capture = struct.unpack_from("<HHI", self.payload)
        metadata = 12 if self.format >= 3 else 8
        encoded = self.payload[metadata:]
        if self.format == 5:
            image = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        elif self.format == 4:
            import imagecodecs
            planes = imagecodecs.jpegls_decode(encoded)
            if planes.shape != (height * 3, width):
                raise ValueError("JPEG-LS dimensions differ from frame metadata")
            red, green, blue = [plane.astype(np.uint16) for plane in np.split(planes, 3)]
            red, green, blue = np.minimum(red, 31), np.minimum(green, 63), np.minimum(blue, 31)
            image = np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                              (red << 3) | (red >> 2)], axis=2).astype(np.uint8)
        elif self.format in (2, 3):
            values = np.frombuffer(encoded, "<u2").reshape(height, width)
            red, green, blue = (values >> 11) & 31, (values >> 5) & 63, values & 31
            image = np.stack([(blue << 3) | (blue >> 2), (green << 2) | (green >> 4),
                              (red << 3) | (red >> 2)], axis=2).astype(np.uint8)
        elif self.format == 1:
            image = cv2.cvtColor(np.frombuffer(encoded, np.uint8).reshape(height, width), cv2.COLOR_GRAY2BGR)
        else:
            raise ValueError("Unsupported camera pixel format")
        if image is None or image.shape[:2] != (height, width):
            raise ValueError("Decoded image dimensions differ from frame metadata")
        age_ms = None
        if self.format >= 3:
            send = struct.unpack_from("<I", self.payload, 8)[0]
            age_ms = ((send - capture) & 0xffffffff) * 1000 / clock_hz
            age_ms += (len(self.payload) + 20) * (10 * (clock_hz // baud) + 1) * 1000 / clock_hz
        return image, {"camera": self.camera, "sequence": self.sequence, "width": width, "height": height,
                       "format": self.format, "encoded_bytes": len(encoded), "estimated_frame_age_ms": age_ms,
                       "received_monotonic": self.received_monotonic}


class WireDecoder:
    def __init__(self):
        self.buffer = bytearray()
        self.crc_errors = self.framing_errors = self.valid_camera_packets = self.bytes_received = 0
        self.last_crc_error = None
        directory = os.environ.get("FPGA_WIRE_ERROR_DIRECTORY")
        self.error_capture_directory = Path(directory) if directory else None

    def push(self, data):
        self.buffer.extend(data)
        self.bytes_received += len(data)

    def pop(self):
        while True:
            positions = [position for magic in (CAMERA_MAGIC, RESPONSE_MAGIC)
                         if (position := self.buffer.find(magic)) >= 0]
            if not positions:
                if len(self.buffer) > 3:
                    del self.buffer[:-3]
                return None
            position = min(positions)
            if position:
                del self.buffer[:position]
            if len(self.buffer) < 16:
                return None
            magic = bytes(self.buffer[:4])
            length = struct.unpack_from("<I", self.buffer, 12)[0]
            if magic == RESPONSE_MAGIC:
                valid = 4 <= length <= 4096
                command, sequence = struct.unpack_from("<II", self.buffer, 4)
                camera, fmt, flags = -1, 0, 0
            else:
                command, camera, fmt, flags, sequence = struct.unpack_from("<4BI", self.buffer, 4)
                valid = command in (1, 2) and camera <= 1 and length <= 1024 * 1024
                valid &= (length >= (12 if fmt >= 3 else 8) and 1 <= fmt <= 5) if command == 1 else length in (32, 40)
            if not valid:
                self.framing_errors += 1
                del self.buffer[0]
                continue
            total = length + 20
            if len(self.buffer) < total:
                return None
            frame = bytes(self.buffer[:total])
            actual_crc = zlib.crc32(frame[:-4])
            expected_crc = struct.unpack_from("<I", frame, total - 4)[0]
            if actual_crc != expected_crc:
                self.crc_errors += 1
                embedded = [position for tag in (CAMERA_MAGIC, RESPONSE_MAGIC)
                            if (position := frame.find(tag, 16)) >= 0]
                self.last_crc_error = {"magic": magic.decode("ascii"), "command": command,
                                       "sequence": sequence, "camera": camera, "format": fmt,
                                       "payload_bytes": length, "expected_crc32": expected_crc,
                                       "actual_crc32": actual_crc,
                                       "embedded_packet_offset": min(embedded) if embedded else None,
                                       "received_monotonic": time.monotonic()}
                if self.error_capture_directory and self.crc_errors <= 4:
                    try:
                        self.error_capture_directory.mkdir(parents=True, exist_ok=True)
                        with tempfile.NamedTemporaryFile(dir=self.error_capture_directory,
                                                         prefix="crc_error_", suffix=".bin", delete=False) as capture:
                            capture.write(frame)
                            capture_path = capture.name
                        self.last_crc_error.update(capture_path=capture_path, capture_bytes=len(frame),
                                                   capture_sha256=hashlib.sha256(frame).hexdigest())
                    except OSError as error:
                        self.last_crc_error["capture_error"] = str(error)
                # Lost bytes can place the next valid header inside this packet's declared length.
                del self.buffer[0]
                if magic == RESPONSE_MAGIC:
                    raise ValueError("RISC-V response CRC mismatch")
                continue
            del self.buffer[:total]
            payload = frame[16:-4]
            if magic == CAMERA_MAGIC:
                if command == 1:
                    width, height = struct.unpack_from("<HH", payload)
                    valid = 1 <= width <= 1280 and 1 <= height <= 720
                    if fmt in (1, 2, 3):
                        valid &= length == (12 if fmt == 3 else 8) + width * height * (1 if fmt == 1 else 2)
                    else:
                        valid &= payload[12:14] == b"\xff\xd8" and length >= 38
                        if fmt == 5:
                            valid &= payload[-2:] == b"\xff\xd9"
                else:
                    valid = payload[23] in (0xa3, 0xa4)
                if not valid:
                    self.framing_errors += 1
                    continue
                self.valid_camera_packets += 1
            return WirePacket(magic, command, sequence, payload, camera, fmt, flags, time.monotonic())
