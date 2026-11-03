import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from camera_stream import WireDecoder


def response(sequence, corrupt=False):
    frame = b"PGS1" + struct.pack("<III", 1, sequence, 4) + bytes(4)
    crc = zlib.crc32(frame) ^ int(corrupt)
    return frame + struct.pack("<I", crc)


class ErrorCaptureTests(unittest.TestCase):
    def test_original_failed_bytes_are_preserved_and_following_packet_recovers(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"FPGA_WIRE_ERROR_DIRECTORY": directory}):
            decoder = WireDecoder()
            failed = response(1, corrupt=True)
            decoder.push(failed + response(2))
            with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                decoder.pop()
            evidence = decoder.last_crc_error
            self.assertEqual(Path(evidence["capture_path"]).read_bytes(), failed)
            self.assertEqual(evidence["capture_sha256"], hashlib.sha256(failed).hexdigest())
            self.assertEqual(evidence["capture_bytes"], len(failed))
            self.assertEqual(decoder.pop().sequence, 2)
            self.assertEqual(decoder.crc_errors, 1)

    def test_capture_count_is_bounded_and_corruption_is_still_counted(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"FPGA_WIRE_ERROR_DIRECTORY": directory}):
            decoder = WireDecoder()
            for sequence in range(7):
                decoder.push(response(sequence, corrupt=True))
                with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                    decoder.pop()
            self.assertEqual(len(list(Path(directory).glob("*.bin"))), 4)
            self.assertEqual(decoder.crc_errors, 7)

    def test_unwritable_capture_does_not_mask_corruption_or_prevent_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "regular_file"
            invalid.write_bytes(b"occupied")
            with patch.dict(os.environ, {"FPGA_WIRE_ERROR_DIRECTORY": str(invalid)}):
                decoder = WireDecoder()
            decoder.push(response(1, corrupt=True) + response(2))
            with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                decoder.pop()
            self.assertIn("capture_error", decoder.last_crc_error)
            self.assertEqual(decoder.pop().sequence, 2)

    def test_disabled_capture_has_no_filesystem_side_effects(self):
        with patch.dict(os.environ), patch("camera_stream.tempfile.NamedTemporaryFile") as create:
            os.environ.pop("FPGA_WIRE_ERROR_DIRECTORY", None)
            decoder = WireDecoder()
            decoder.push(response(1, corrupt=True))
            with self.assertRaisesRegex(ValueError, "CRC mismatch"):
                decoder.pop()
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
