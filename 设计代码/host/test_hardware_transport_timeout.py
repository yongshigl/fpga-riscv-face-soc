#!/usr/bin/env python3
"""Check real pipe reads when the host transport reaches its deadline."""
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from camera_stream import CAMERA_MAGIC, WireDecoder
from hardware_transport import Board


@unittest.skipIf(os.name == "nt", "Pipe transport is used by WSL")
class TransportDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.reader, self.writer = os.pipe()
        self.stream = os.fdopen(self.reader, "rb", buffering=0)
        self.board = Board.__new__(Board)
        self.board.serial = None
        self.board.process = SimpleNamespace(stdout=self.stream)
        self.board.decoder = WireDecoder()

    def tearDown(self):
        self.stream.close()
        os.close(self.writer)

    def test_clock_crossing_does_not_close_the_connection(self):
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0.999999, 1.000001]))
        with patch("hardware_transport.time", clock):
            self.board._receive(1.0)
        self.assertEqual(self.board.decoder.buffer, b"")

    def test_expired_deadline_does_not_consume_incoming_bytes(self):
        os.write(self.writer, b"partial")
        clock = SimpleNamespace(monotonic=lambda: 1.000001)
        with patch("hardware_transport.time", clock), self.assertRaises(TimeoutError):
            self.board._receive(1.0)
        self.assertEqual(os.read(self.reader, 7), b"partial")

    def test_available_bytes_reach_the_decoder(self):
        os.write(self.writer, CAMERA_MAGIC)
        self.board._receive(time.monotonic() + 1)
        self.assertEqual(self.board.decoder.buffer, CAMERA_MAGIC)

    def test_preview_timeout_preserves_an_incomplete_packet(self):
        partial = CAMERA_MAGIC + b"\x00"
        self.board.decoder.push(partial)
        clock = SimpleNamespace(monotonic=Mock(side_effect=[1.0, 1.25]))
        with patch("hardware_transport.time", clock):
            self.assertIsNone(self.board.read_camera(0.25))
        self.assertEqual(self.board.decoder.buffer, partial)


if __name__ == "__main__":
    unittest.main(verbosity=2)
