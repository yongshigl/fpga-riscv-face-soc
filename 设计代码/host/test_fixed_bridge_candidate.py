"""Candidate fixed requests, launch binding and raw failed-frame capture stay explicit."""
import collections
import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from camera_stream import WireDecoder
from serial_bridge_fixed_candidate import wrap_fixed_reads
from verify_fixed_bridge_candidate import candidate_transport, FaultRecorder


def frame(sequence, payload=b"test_payload_bytes"):
    payload = struct.pack("<HHII", 640, 360, 1, 2) + b"\xff\xd8" + payload + b"\xff\xd9"
    header = struct.pack("<4s4BII", b"PGC2", 1, 0, 5, 0, sequence, len(payload))
    data = header + payload
    return data + struct.pack("<I", zlib.crc32(data))


class CandidateTests(unittest.TestCase):
    def test_empty_queue_and_nonempty_queue_both_post4096_without_padding_output(self):
        original = Mock(side_effect=(b"x", b"camera bytes", b""))
        port, stats = SimpleNamespace(read=original), collections.defaultdict(int)
        wrap_fixed_reads(port, stats)
        self.assertEqual(port.read(1), b"x")
        self.assertEqual(port.read(512), b"camera bytes")
        self.assertEqual(port.read(1), b"")
        self.assertEqual([call.args[0] for call in original.call_args_list], [4096] * 3)
        self.assertEqual(stats["calls"], 3)
        self.assertEqual(stats["requested_bytes"], 12288)
        self.assertEqual(stats["available_request_1_calls"], 2)
        self.assertEqual(stats["returned_bytes"], 13)
        self.assertEqual(stats["empty_calls"], 1)

    def test_driver_read_exception_is_not_converted_to_empty_success(self):
        port, stats = SimpleNamespace(read=Mock(side_effect=OSError("read failed"))), collections.defaultdict(int)
        wrap_fixed_reads(port, stats)
        with self.assertRaisesRegex(OSError, "read failed"):
            port.read(1)
        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["returned_bytes"], 0)

    def test_only_bridge_process_is_redirected_and_original_arguments_preserved(self):
        original = Mock(return_value="process")
        with tempfile.TemporaryDirectory() as output, patch("hardware_transport.subprocess.Popen", original):
            with candidate_transport(output) as (_, launched):
                import hardware_transport
                self.assertEqual(hardware_transport.subprocess.Popen(["wslpath", "-w", "source"]), "process")
                lookup = ["wslpath", "-w", "/mnt/d/fpga竞赛/fpga-riscv/vision/serial_bridge.py"]
                self.assertEqual(hardware_transport.subprocess.Popen(lookup), "process")
                self.assertEqual(original.call_args.args[0], lookup)
                self.assertEqual(launched, [])
                result = hardware_transport.subprocess.Popen(
                    ["/mnt/c/python.exe", "-u", "D:\\FPGA竞赛\\fpga-riscv\\vision\\serial_bridge.py", "--rx-buffer", "1048576"],
                    stdout="pipe")
                self.assertEqual(result, "process")
                self.assertEqual(len(launched), 1)
                self.assertTrue(launched[0]["command"][2].endswith("serial_bridge_fixed_candidate.py"))
                self.assertEqual(launched[0]["command"][3:], ["--rx-buffer", "1048576"])
                self.assertEqual(original.call_args.kwargs, {"stdout": "pipe"})

    def test_lost_camera_bytes_are_saved_without_resetting_crc_or_interfering_with_resync(self):
        first = frame(12, b"jpeg_placeholder" * 4)
        following = frame(13, b"jpeg_placeholder" * 4)
        incoming = first[:35] + first[40:] + following
        with tempfile.TemporaryDirectory() as output:
            with candidate_transport(output) as (recorder, _):
                decoder = WireDecoder()
                decoder.push(incoming)
                recovered = decoder.pop()
                self.assertEqual(recovered.sequence, 13)
                self.assertEqual(decoder.crc_errors, 1)
                self.assertEqual(decoder.framing_errors, 0)
                self.assertEqual(len(recorder.events), 1)
                event = recorder.events[0]
                self.assertEqual(Path(event["raw_path"]).read_bytes(), incoming)
                self.assertEqual(event["raw_sha256"], hashlib.sha256(incoming).hexdigest())
                self.assertEqual(event["last_crc_error"]["sequence"], 12)
                self.assertEqual(event["last_crc_error"]["embedded_packet_offset"], len(first) - 5)
                self.assertEqual(event["recovered_packet"]["sequence"], 13)
                self.assertEqual(Path(event["context_path"]).read_bytes(), incoming)
            self.assertEqual(decoder.crc_errors, 1)

    def test_incomplete_embedded_packet_keeps_raw_context_until_full_crc_valid_recovery(self):
        first, following = frame(20, b"a" * 200), frame(21, b"b" * 200)
        damaged = first[:70] + first[95:]
        # Enough bytes to reject the first declared packet, but not verify the next.
        initial = damaged + following[:30]
        with tempfile.TemporaryDirectory() as output, candidate_transport(output) as (recorder, _):
            decoder = WireDecoder()
            decoder.push(initial)
            self.assertIsNone(decoder.pop())
            self.assertEqual(decoder.crc_errors, 1)
            self.assertNotIn("context_path", recorder.events[0])
            decoder.push(following[30:])
            recovered = decoder.pop()
            self.assertEqual(recovered.sequence, 21)
            event = recorder.events[0]
            self.assertEqual(Path(event["context_path"]).read_bytes(), damaged + following)
            self.assertEqual(event["recovered_packet"]["sequence"], 21)
            self.assertEqual(decoder.crc_errors, 1)

    def test_cpu_crc_exception_is_preserved_with_raw_failure_evidence(self):
        payload = struct.pack("<I", 0)
        data = struct.pack("<4sIII", b"PGS1", 1, 9, len(payload)) + payload
        incoming = data + struct.pack("<I", zlib.crc32(data) ^ 1)
        with tempfile.TemporaryDirectory() as output, candidate_transport(output) as (recorder, _):
            decoder = WireDecoder()
            decoder.push(incoming)
            with self.assertRaisesRegex(ValueError, "RISC-V response CRC mismatch"):
                decoder.pop()
            self.assertEqual(decoder.crc_errors, 1)
            self.assertEqual(recorder.events[0]["last_crc_error"]["magic"], "PGS1")
            self.assertEqual(Path(recorder.events[0]["raw_path"]).read_bytes(), incoming)

    def test_raw_snapshot_limit_does_not_hide_later_error_counts(self):
        with tempfile.TemporaryDirectory() as output:
            recorder = FaultRecorder(output, maximum=1)
            decoder = SimpleNamespace(crc_errors=1, framing_errors=0, bytes_received=15,
                                      buffer=bytearray(), last_crc_error={"sequence": 1})
            recorder.record(decoder, b"bad one", 0)
            decoder.crc_errors = 2
            recorder.record(decoder, b"bad two", 1)
            self.assertEqual(len(recorder.events), 2)
            self.assertEqual(recorder.suppressed_snapshots, 1)
            self.assertEqual(recorder.events[1]["crc_errors"], 2)
            self.assertNotIn("raw_path", recorder.events[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
