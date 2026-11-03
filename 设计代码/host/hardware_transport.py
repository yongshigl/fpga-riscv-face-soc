"""CRC-protected host transport to the bare-metal RISC-V vision service."""
import os
import select
import struct
import subprocess
import time
import zlib
from pathlib import Path

from camera_stream import BRIDGE_READY, CAMERA_MAGIC, WireDecoder

ROOT = Path(__file__).resolve().parents[1]
REQUEST = b"PGR1"
RESPONSE = b"PGS1"
BENCHMARK_END = b"\r\n#BENCH-END\r\n"


class BoardError(RuntimeError):
    def __init__(self, command, status, details):
        super().__init__(f"RISC-V command {command} failed: status={status}, details={details.hex()}")
        self.command, self.status, self.details = command, status, details


class Board:
    def __init__(self, port="COM7", baud=921600, bridge_log=None, camera_callback=None, rx_buffer_bytes=1048576):
        if rx_buffer_bytes <= 0:
            raise ValueError("Serial receive buffer must be positive")
        self.port, self.baud = port, baud
        self.sequence = int(time.monotonic_ns() & 0x7fffffff)
        self.decoder = WireDecoder()
        self.buffer = self.decoder.buffer
        self.camera_callback = camera_callback
        self._identity = self._capabilities = None
        self.last_upload_stats = None
        self.last_download_stats = None
        self.process = self.serial = self.log = None
        self.bridge_startup_ms = None
        self._linux_line_baseline = None
        self._linux_next_line_check = 0
        if os.name == "nt" or port.startswith("/dev/"):
            import serial
            self.serial = serial.Serial(port, baud, timeout=0.05, write_timeout=5)
            if os.name == "nt":
                self.serial.set_buffer_size(rx_size=rx_buffer_bytes, tx_size=65536)
            elif bridge_log:
                path = Path(bridge_log)
                path.parent.mkdir(parents=True, exist_ok=True)
                self.log = path.open("ab")
                self.log.write(f"SERIAL_LINUX_NATIVE port={port} baud={baud}\n".encode("ascii"))
                self.log.flush()
            self.serial.reset_input_buffer()
            if os.name != "nt":
                self._linux_line_baseline = self._linux_line_counts()
                self._linux_log_line_counts("initial", self._linux_line_baseline)
        else:
            startup = time.monotonic()
            bridge_options = []
            status_interval = os.environ.get("FPGA_SERIAL_STATUS_INTERVAL_MS")
            if status_interval is not None:
                interval_ms = int(status_interval)
                if interval_ms not in (0, 100, 250, 500, 1000):
                    raise ValueError("Serial status polling interval must be 0, 100, 250, 500 or 1000 ms")
                bridge_options = ["--status-interval-ms", str(interval_ms)]
            io_priority = os.environ.get("FPGA_SERIAL_IO_PRIORITY")
            if io_priority is not None:
                if io_priority not in ("above_normal", "high"):
                    raise ValueError("Serial process priority must be above_normal or high")
                bridge_options.extend(["--io-priority", io_priority])
            native_read_bytes = os.environ.get("FPGA_SERIAL_NATIVE_READ_BYTES")
            if native_read_bytes is not None:
                request_bytes = int(native_read_bytes)
                if request_bytes not in (4096, 16384, 65536):
                    raise ValueError("Native read request must be 4096, 16384 or 65536 bytes")
                bridge_options.extend(["--native-read-bytes", str(request_bytes)])
            timer_resolution = os.environ.get("FPGA_SERIAL_TIMER_RESOLUTION_MS")
            if timer_resolution is not None:
                milliseconds = int(timer_resolution)
                if milliseconds not in (0, 1):
                    raise ValueError("Serial timer resolution must be 0 or 1 ms")
                bridge_options.extend(["--timer-resolution-ms", str(milliseconds)])
            bridge = subprocess.check_output(["wslpath", "-w", str(ROOT / "vision/serial_bridge.py")], text=True).strip()
            if bridge_log:
                path = Path(bridge_log)
                path.parent.mkdir(parents=True, exist_ok=True)
                self.log = path.open("ab")
            self.process = subprocess.Popen(["/mnt/c/python.exe", "-u", bridge, "--port", port, "--baud", str(baud),
                                             "--rx-buffer", str(rx_buffer_bytes), *bridge_options],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.log or subprocess.DEVNULL, bufsize=0)
            try:
                self._wait_bridge_ready(timeout=90)
                self.bridge_startup_ms = (time.monotonic() - startup) * 1000
                if self.log:
                    self.log.write(f"SERIAL_BRIDGE_HOST_READY startup_ms={self.bridge_startup_ms:.3f}\n".encode("ascii"))
                    self.log.flush()
            except Exception:
                self.close()
                raise

    def close(self):
        line_failure = None
        if self.serial and os.name != "nt":
            try:
                self._check_linux_line_errors(force=True)
            except RuntimeError as error:
                line_failure = error
        if self.serial:
            self.serial.close()
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)
            self.process.stdout.close()
        if self.log:
            self.log.close()
        if line_failure:
            raise line_failure

    def _linux_line_counts(self):
        import fcntl
        import termios
        import errno
        counters = bytearray(80)
        try:
            fcntl.ioctl(self.serial.fileno(), getattr(termios, "TIOCGICOUNT", 0x545d), counters, True)
        except OSError as error:
            if error.errno in (errno.EINVAL, errno.ENOTTY, errno.EOPNOTSUPP):
                return None
            raise
        values = struct.unpack("<20I", counters)
        return dict(zip(("frame", "overrun", "parity", "break", "buffer_overrun"), values[6:11]))

    def _linux_log_line_counts(self, stage, counts):
        if self.log:
            detail = " ".join(f"{key}={value}" for key, value in (counts or {}).items())
            self.log.write(f"SERIAL_LINUX_COUNTERS stage={stage} available={int(counts is not None)} {detail}\n".encode("ascii"))
            self.log.flush()

    def _check_linux_line_errors(self, force=False):
        now = time.monotonic()
        if not force and now < self._linux_next_line_check:
            return
        self._linux_next_line_check = now + 1
        if self._linux_line_baseline is None:
            return
        current = self._linux_line_counts()
        if current is None:
            raise RuntimeError("Linux UART kernel error counters became unavailable")
        delta = {key: (current[key] - value) & 0xffffffff for key, value in self._linux_line_baseline.items()}
        if force or any(delta.values()):
            self._linux_log_line_counts("final_delta" if force else "error_delta", delta)
        if any(delta.values()):
            raise RuntimeError(f"Linux UART kernel reported line or buffer errors: {delta}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _wait_bridge_ready(self, timeout):
        deadline = time.monotonic() + timeout
        received = bytearray()
        while len(received) < len(BRIDGE_READY):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Windows serial bridge did not open the COM port within its startup budget")
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.1, remaining))
            if not ready:
                if self.process.poll() is not None:
                    raise RuntimeError("Windows serial bridge exited before opening the COM port; check its log")
                continue
            data = os.read(self.process.stdout.fileno(), len(BRIDGE_READY) - len(received))
            if not data:
                raise RuntimeError("Windows serial bridge closed before its readiness handshake")
            received.extend(data)
            if not BRIDGE_READY.startswith(received):
                raise RuntimeError("Windows serial bridge returned an invalid readiness handshake")

    def _send(self, data):
        if self.serial:
            self.serial.write(data)
        else:
            if self.process.poll() is not None:
                raise RuntimeError("Windows serial bridge exited; check COM7 ownership and bridge log")
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def _receive(self, deadline):
        # A second clock read could cross the deadline before select starts.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for the RISC-V response")
        if self.serial:
            data = self.serial.read(max(1, min(65536, self.serial.in_waiting)))
        else:
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.1, remaining))
            data = os.read(self.process.stdout.fileno(), 65536) if ready else b""
            if ready and not data:
                raise RuntimeError("Windows serial bridge closed its output")
        self.decoder.push(data)
        if self.serial and os.name != "nt":
            self._check_linux_line_errors()

    def _response(self, command, sequence, timeout):
        deadline = time.monotonic() + timeout
        while True:
            packet = self.decoder.pop()
            if packet is None:
                self._receive(deadline)
                continue
            if packet.magic == CAMERA_MAGIC:
                if self.camera_callback:
                    self.camera_callback(packet)
                continue
            if packet.sequence != sequence or packet.command != command:
                continue
            status = struct.unpack_from("<I", packet.payload)[0]
            if status:
                raise BoardError(command, status, packet.payload[4:])
            return packet.payload[4:]

    def read_camera(self, timeout=0.25):
        deadline = time.monotonic() + timeout
        while True:
            packet = self.decoder.pop()
            if packet and packet.magic == CAMERA_MAGIC:
                if self.camera_callback:
                    self.camera_callback(packet)
                return packet
            if packet:
                continue
            try:
                self._receive(deadline)
            except TimeoutError:
                return None

    def command(self, command, payload=b"", timeout=5):
        if len(payload) > 2048:
            raise ValueError("RISC-V request payload exceeds 2048 bytes")
        self.sequence = (self.sequence + 1) & 0xffffffff
        frame = struct.pack("<4sIII", REQUEST, command, self.sequence, len(payload)) + payload
        frame += struct.pack("<I", zlib.crc32(frame))
        self._send(frame)
        return self._response(command, self.sequence, timeout)

    def ping(self, timeout=5):
        values = struct.unpack("<8I", self.command(1, timeout=timeout))
        names = ("firmware_version", "accelerator_version", "cpu_hz", "ai_hz", "uart_baud", "rx_errors", "max_payload", "gallery_size")
        self._identity = dict(zip(names, values))
        return self._identity

    def capabilities(self):
        if self._capabilities is None:
            identity = self._identity or self.ping()
            if identity["firmware_version"] < 0x26100133:
                flags, maximum = 0, 2044
            else:
                flags, maximum = struct.unpack("<II", self.command(13))
                if not 1 <= maximum <= 4096:
                    raise ValueError("Invalid board decompression limit")
            self._capabilities = {"camera_preview": bool(flags & 1), "compressed_write": bool(flags & 2),
                                  "delta_compressed_write": bool(flags & 8),
                                  "packed_tensor_read": bool(flags & 4), "camera_tensor": bool(flags & 16),
                                  "camera_coherent_capture": bool(flags & 32),
                                  "camera_face_decode": bool(flags & 64),
                                  "benchmark": bool(flags & 128),
                                  "max_decoded_bytes": maximum}
        return self._capabilities

    def upload(self, address, data, progress=None, compress=None, delta=None, compression_level=1):
        if type(compression_level) is not int or not 0 <= compression_level <= 9:
            raise ValueError("Upload compression level must be an integer within 0..9")
        start, last = time.monotonic(), time.monotonic()
        capabilities = self.capabilities() if compress is not False else {"compressed_write": False}
        enabled = capabilities["compressed_write"] and compress is not False
        delta_enabled = enabled and capabilities.get("delta_compressed_write", False) and delta is not False
        offset, wire_bytes, compressed_blocks, raw_blocks, delta_blocks = 0, 0, 0, 0, 0

        def encode(block):
            if not enabled:
                return 12, b""
            encoded = zlib.compress(block, compression_level)
            if delta_enabled:
                differences = bytes([block[0]]) + bytes((current - previous) & 255
                                    for current, previous in zip(block[1:], block[:-1]))
                candidate = zlib.compress(differences, compression_level)
                if len(candidate) < len(encoded):
                    return 15, candidate
            return 12, encoded

        while offset < len(data):
            command = 2
            block = data[offset:offset + (capabilities["max_decoded_bytes"] if enabled else 2044)]
            compressed_command, encoded = encode(block)
            if enabled and len(encoded) + 8 <= 2048 and len(encoded) + 4 < len(block) * 0.9:
                command = compressed_command
            else:
                block = block[:2044]
                compressed_command, encoded = encode(block)
                if enabled and len(encoded) + 8 <= 2048 and len(encoded) + 4 < len(block) * 0.9:
                    command = compressed_command
            payload = (struct.pack("<II", address + offset, len(block)) + encoded if command in (12, 15) else
                       struct.pack("<I", address + offset) + block)
            reply = self.command(command, payload)
            if struct.unpack("<I", reply)[0] != len(block):
                raise RuntimeError("RISC-V acknowledged an incorrect write length")
            offset += len(block)
            wire_bytes += 20 + len(payload) + 28
            compressed_blocks += command in (12, 15)
            delta_blocks += command == 15
            raw_blocks += command == 2
            if progress and time.monotonic() - last >= 1:
                progress(offset, len(data))
                last = time.monotonic()
        milliseconds = (time.monotonic() - start) * 1000
        self.last_upload_stats = {"input_bytes": len(data), "wire_bytes": wire_bytes, "compressed_blocks": compressed_blocks,
                                  "raw_blocks": raw_blocks, "delta_compressed_blocks": delta_blocks,
                                  "compression_level": compression_level, "milliseconds": milliseconds}
        return milliseconds

    def download(self, address, length):
        data = bytearray()
        for offset in range(0, length, 2048):
            count = min(2048, length - offset)
            block = self.command(3, struct.pack("<II", address + offset, count))
            if len(block) != count:
                raise RuntimeError("RISC-V returned an incorrect read length")
            data.extend(block)
        return bytes(data)

    def download_tensor(self, tensor):
        """Read logical NCHW bytes, negotiating padding-free reads with new firmware."""
        shape = tensor["shape"]
        if len(shape) != 4 or shape[0] != 1:
            raise ValueError("Tensor readback requires batch-one NCHW")
        _, channels, height, width = shape
        row, plane, address = tensor["row_stride"], tensor["plane_stride"], tensor["address"]
        values = (channels, height, width, row, plane, address)
        if any(not isinstance(value, int) or value <= 0 or value > 0xffffffff for value in values):
            raise ValueError("Tensor readback dimensions must be positive uint32 values")
        length = channels * height * width
        physical_span = (channels - 1) * plane + (height - 1) * row + width
        if (width > row or height * row > plane or length > 0xffffffff or
                physical_span > tensor["bytes"] or address < 0x80000000 or
                address + physical_span > 0xc0000000):
            raise ValueError("Tensor layout or DDR range is invalid")
        start = time.monotonic()
        packed = self.capabilities().get("packed_tensor_read", False)
        commands = 0
        if packed:
            result = bytearray()
            for offset in range(0, length, 4092):
                count = min(4092, length - offset)
                payload = struct.pack("<8I", address, width, height, channels, row, plane, offset, count)
                block = self.command(14, payload)
                if len(block) != count:
                    raise RuntimeError("RISC-V returned an incorrect packed tensor length")
                result.extend(block)
                commands += 1
            wire_bytes = length + commands * (52 + 24)
        else:
            raw = self.download(address, tensor["bytes"])
            result = bytearray()
            for channel in range(channels):
                for y in range(height):
                    begin = channel * plane + y * row
                    result.extend(raw[begin:begin + width])
            commands = (tensor["bytes"] + 2047) // 2048
            wire_bytes = tensor["bytes"] + commands * (28 + 24)
        self.last_download_stats = {"mode": "packed" if packed else "legacy_padded",
                                    "logical_bytes": length, "padded_bytes": tensor["bytes"],
                                    "wire_bytes": wire_bytes, "commands": commands,
                                    "milliseconds": (time.monotonic() - start) * 1000}
        return bytes(result)

    def run(self, model, first=0, count=None):
        count = len(model["layers"]) - first if count is None else count
        start = time.monotonic()
        reply = self.command(4, struct.pack("<III", model["image_address"], first, count), timeout=180)
        actual_count, cpu_cycles = struct.unpack_from("<IQ", reply)
        if actual_count != count or len(reply) != 12 + 28 * count:
            raise RuntimeError("Malformed RISC-V model profiling response")
        layers = []
        for offset in range(count):
            index, low, high, reads, writes, compute, error = struct.unpack_from("<7I", reply, 12 + offset * 28)
            if index != first + offset or error:
                raise RuntimeError("Invalid RISC-V layer profile")
            layers.append({"index": index, "cycles": low | high << 32, "reads": reads,
                           "writes": writes, "compute_cycles": compute})
        return {"cpu_cycles": cpu_cycles, "layers": layers,
                "command_wall_ms": (time.monotonic() - start) * 1000}

    def control(self, index, value=None):
        reply = self.command(5, struct.pack("<III", int(value is not None), index, value or 0))
        return None if value is not None else struct.unpack("<I", reply)[0]

    def match(self, model):
        reply = self.command(7, struct.pack("<I", model["image_address"]))
        if len(reply) != 340:
            raise RuntimeError("Malformed CPU matching response")
        identity, score, valid, cycles = struct.unpack_from("<IiIQ", reply)
        embedding = struct.unpack_from("<128h", reply, 20)
        scores = struct.unpack_from("<16i", reply, 276)
        return {"identity_index": None if identity == 0xffffffff else identity,
                "cosine": score / 32768, "gallery_valid": valid, "cpu_cycles": cycles,
                "embedding_q15": list(embedding), "gallery_cosines": [value / 32768 for value in scores]}

    def enroll(self, index, embedding=None):
        payload = struct.pack("<I", index)
        if embedding is not None:
            payload += struct.pack("<128h", *embedding)
        return struct.unpack("<I", self.command(6, payload))[0]

    def threshold(self, q15=None):
        if q15 is not None and not 0 <= q15 <= 32767:
            raise ValueError("Matching threshold must be a Q15 value from 0 to 32767")
        payload = b"" if q15 is None else struct.pack("<I", q15)
        return struct.unpack("<I", self.command(10, payload))[0]

    def preview(self, operation=0):
        if operation not in (0, 1, 2) or not self.capabilities()["camera_preview"]:
            raise ValueError("Camera preview requires the combined camera/vision firmware")
        values = struct.unpack("<16I", self.command(11, struct.pack("<I", operation)))
        sensors = []
        names = ("status", "chip_id", "format", "isp", "test_pattern", "control", "width", "height",
                 "nacks", "last_address", "acknowledged_writes")
        for index in range(2):
            data = struct.pack(">4I", *values[4 + index * 4:8 + index * 4])
            sensor = dict(zip(names, struct.unpack(">BHBBBBHHHHB", data)))
            jpeg = (sensor["format"] == 0x30 and sensor["isp"] == 0 and
                    sensor["width"] == 640 and sensor["height"] == 360)
            rgb = (sensor["format"] == 0x61 and sensor["isp"] == 1 and
                   sensor["width"] == 1280 and sensor["height"] == 720)
            sensor["verified"] = (sensor["status"] == 6 and sensor["chip_id"] == 0x5640 and
                                  (jpeg or rgb) and sensor["test_pattern"] == 0 and
                                  not sensor["control"] & 0x40 and sensor["nacks"] == 0)
            sensors.append(sensor)
        return {"active": bool(values[1] & 1), "configured": [bool(values[1] & 2), bool(values[1] & 4)],
                "frame_ready": [bool(values[1] & 8), bool(values[1] & 16)], "build_id": values[2],
                "width": values[12] & 0xffff, "height": values[12] >> 16, "jpeg_quantization": values[13],
                "baud": values[14], "clock_hz": values[15], "sensors": sensors}

    def counters(self):
        cycles, ih, im, dh, dm, errors = struct.unpack("<Q5I", self.command(9))
        return {"cycles": cycles, "icache_hits": ih, "icache_misses": im,
                "dcache_hits": dh, "dcache_misses": dm, "rx_errors": errors}

    def _read_raw(self, deadline):
        """Read UART bytes without feeding them to the PGR1/PGS1 decoder."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for CoreMark output")
            if self.serial:
                data = self.serial.read(self.serial.in_waiting or 1)
            else:
                ready, _, _ = select.select([self.process.stdout], [], [], min(0.1, remaining))
                data = os.read(self.process.stdout.fileno(), 65536) if ready else b""
                if ready and not data:
                    raise RuntimeError("Windows serial bridge closed its output")
            if data:
                return data

    def benchmark(self, iterations, timeout=300):
        """Run the stock CoreMark linked into the application image (op 22).

        The firmware answers the request first and only then prints CoreMark's
        plain-text log, so after the response is consumed the wire carries text
        until the firmware's end marker.
        """
        if not 1 <= iterations <= 1000000:
            raise ValueError("CoreMark iterations must be within 1..1000000")
        reply = self.command(22, struct.pack("<I", iterations), timeout=60)
        if struct.unpack_from("<I", reply)[0] != iterations:
            raise RuntimeError("Board rejected the CoreMark iteration count")
        text = bytes(self.decoder.buffer)
        self.decoder.buffer.clear()
        deadline = time.monotonic() + timeout
        while not text.endswith(BENCHMARK_END):
            text += self._read_raw(deadline)
        # text ends with the marker; the 16-digit cycle count sits just before it.
        log, _, cycle_hex = text[:-len(BENCHMARK_END)].rpartition(b"\r\n")
        cycle_hex = cycle_hex.strip()
        return {"text": log.decode("ascii", "replace"),
                "end_cycles": int(cycle_hex, 16) if cycle_hex else None}
