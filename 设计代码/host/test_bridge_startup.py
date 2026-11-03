#!/usr/bin/env python3
"""Check COM-bridge readiness and preservation of the following binary stream."""
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from camera_stream import BRIDGE_READY, CAMERA_MAGIC
from hardware_transport import Board


@unittest.skipIf(os.name == "nt", "Pipe transport is used by WSL")
class BridgeStartupTests(unittest.TestCase):
    def setUp(self):
        self.reader, self.writer = os.pipe()
        self.stream = os.fdopen(self.reader, "rb", buffering=0)
        self.board = Board.__new__(Board)
        self.board.process = SimpleNamespace(stdout=self.stream, poll=lambda: None)

    def tearDown(self):
        self.stream.close()
        if self.writer is not None:
            os.close(self.writer)

    def test_fragmented_readiness_preserves_the_next_packet(self):
        def write():
            os.write(self.writer, BRIDGE_READY[:5])
            time.sleep(0.02)
            os.write(self.writer, BRIDGE_READY[5:] + CAMERA_MAGIC)

        sender = threading.Thread(target=write)
        sender.start()
        try:
            self.board._wait_bridge_ready(1)
            self.assertEqual(os.read(self.reader, len(CAMERA_MAGIC)), CAMERA_MAGIC)
        finally:
            sender.join(timeout=1)

    def test_invalid_readiness_is_rejected(self):
        os.write(self.writer, b"invalid")
        with self.assertRaisesRegex(RuntimeError, "invalid readiness"):
            self.board._wait_bridge_ready(1)

    def test_closed_bridge_is_rejected(self):
        os.close(self.writer)
        self.writer = None
        with self.assertRaisesRegex(RuntimeError, "closed before"):
            self.board._wait_bridge_ready(1)

    def test_silent_bridge_has_a_bounded_startup_wait(self):
        with self.assertRaisesRegex(TimeoutError, "startup budget"):
            self.board._wait_bridge_ready(0.03)

    def test_cold_start_does_not_consume_the_command_timeout(self):
        program = f"""import struct, sys, time, zlib
time.sleep(0.5)
sys.stdout.buffer.write({BRIDGE_READY!r})
sys.stdout.buffer.flush()
request = sys.stdin.buffer.read(20)
magic, command, sequence, length = struct.unpack('<4sIII', request[:16])
assert magic == b'PGR1' and command == 1 and length == 0
payload = struct.pack('<9I', 0, 0x26100133, 0x26100123, 81000000, 63000000, 921600, 0, 2048, 16)
response = struct.pack('<4sIII', b'PGS1', command, sequence, len(payload)) + payload
sys.stdout.buffer.write(response + struct.pack('<I', zlib.crc32(response)))
sys.stdout.buffer.flush()
sys.stdin.buffer.read()
"""
        popen = subprocess.Popen

        def start(_command, **options):
            return popen([sys.executable, "-u", "-c", program], **options)

        with patch("hardware_transport.subprocess.check_output", return_value="unused_bridge.py"), \
                patch("hardware_transport.subprocess.Popen", side_effect=start):
            with Board() as board:
                self.assertGreaterEqual(board.bridge_startup_ms, 500)
                identity = board.ping(timeout=0.25)
                self.assertEqual(identity["cpu_hz"], 81000000)
                self.assertEqual(board.decoder.crc_errors, 0)

    def test_failed_startup_closes_the_process_and_pipes(self):
        popen, children = subprocess.Popen, []

        def start(_command, **options):
            child = popen([sys.executable, "-u", "-c",
                           "import sys; sys.stdout.buffer.write(b'bad'); sys.stdout.buffer.flush(); sys.stdin.buffer.read()"],
                          **options)
            children.append(child)
            return child

        with patch("hardware_transport.subprocess.check_output", return_value="unused_bridge.py"), \
                patch("hardware_transport.subprocess.Popen", side_effect=start):
            with self.assertRaisesRegex(RuntimeError, "invalid readiness"):
                Board()
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdin.closed)
        self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
