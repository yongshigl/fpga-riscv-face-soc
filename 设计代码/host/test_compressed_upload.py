#!/usr/bin/env python3
"""Verify host packet selection against the standard zlib decoder."""
import struct
import zlib

import numpy as np

from hardware_transport import Board


class MemoryBoard(Board):
    def __init__(self, enabled, delta=False):
        self.enabled = enabled
        self.delta = delta
        self.memory = bytearray(32768)
        self.commands = []

    def capabilities(self):
        return {"compressed_write": self.enabled, "delta_compressed_write": self.delta, "max_decoded_bytes": 4096}

    def command(self, command, payload):
        assert len(payload) <= 2048
        self.commands.append(command)
        address = struct.unpack_from("<I", payload)[0] - 0x80000000
        if command in (12, 15):
            count = struct.unpack_from("<I", payload, 4)[0]
            block = zlib.decompress(payload[8:])
            assert len(block) == count <= 4096
            if command == 15:
                previous = 0
                restored = bytearray()
                for difference in block:
                    previous = (previous + difference) & 255
                    restored.append(previous)
                block = bytes(restored)
        else:
            assert command == 2
            block = payload[4:]
        self.memory[address:address + len(block)] = block
        return struct.pack("<I", len(block))


def main():
    random = np.random.default_rng(261001)
    for enabled in (False, True):
        for data in (bytes(12031), bytes(range(256)) * 40,
                     random.integers(0, 256, 8191, dtype=np.uint8).tobytes(), b"x", b""):
            for level in (1, 6, 9):
                board = MemoryBoard(enabled)
                board.upload(0x80000003, data, compression_level=level)
                assert board.memory[3:3 + len(data)] == data
                assert board.last_upload_stats["input_bytes"] == len(data)
                assert board.last_upload_stats["compression_level"] == level
                if enabled and data and len(set(data)) < 2 and len(data) > 64:
                    assert 12 in board.commands
                if not enabled:
                    assert 12 not in board.commands
            board = MemoryBoard(enabled)
            board.upload(0x80000003, data, compress=False)
            assert board.memory[3:3 + len(data)] == data and 12 not in board.commands
    print("PASS host uploads: compressed/raw fallback, incompressible data, unaligned addresses, tails and explicit raw mode")
    for data in (bytes(range(256)) * 40, bytes(12031),
                 random.integers(0, 256, 8191, dtype=np.uint8).tobytes(), b"\xff\x00\x01", b"x", b""):
        for disable_delta, disable_compression in ((False, False), (True, False), (False, True)):
            for level in (1, 6, 9):
                board = MemoryBoard(True, delta=True)
                board.upload(0x80000003, data, delta=False if disable_delta else None,
                             compress=False if disable_compression else None, compression_level=level)
                assert board.memory[3:3 + len(data)] == data
                if disable_delta or disable_compression:
                    assert 15 not in board.commands
                assert board.last_upload_stats["delta_compressed_blocks"] == board.commands.count(15)
    board = MemoryBoard(True, delta=True)
    board.upload(0x80000003, bytes(range(256)) * 40)
    assert 15 in board.commands
    for level in (-1, 10, True, "9", 1.5):
        board = MemoryBoard(True, delta=True)
        try:
            board.upload(0x80000003, b"unchanged", compression_level=level)
        except ValueError:
            assert not board.commands
        else:
            raise AssertionError("Invalid compression level reached the board")
    print("PASS adaptive delta uploads: modulo256 wraps, independent blocks, exact bytes, legacy/raw modes and bounded fallback")


if __name__ == "__main__":
    main()
