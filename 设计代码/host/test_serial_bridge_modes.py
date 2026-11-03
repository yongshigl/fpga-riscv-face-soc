"""Offline Win32 driver doubles verify modes, actual queue diagnostics and ABI."""
import contextlib
import ctypes
import io
from types import SimpleNamespace
import unittest
import struct
import zlib
from unittest.mock import Mock

import serial
from serial_bridge import NativeReceivePolicy, configure_windows_receive, windows_queue_properties


class NativePolicyTests(unittest.TestCase):
    def frame(self, command, payload=b""):
        data = struct.pack("<4sIII", b"PGR1", command, 17, len(payload)) + payload
        return data + struct.pack("<I", zlib.crc32(data))

    def test_fragmented_preview_and_stop_switch_once_without_transforming_payloads(self):
        policy = NativeReceivePolicy()
        frame = self.frame(11, struct.pack("<I", 1))
        self.assertEqual(policy.observe(frame[:5]), [])
        self.assertEqual(policy.observe(frame[5:19]), [])
        self.assertEqual(policy.observe(frame[19:]), ["native_coalesced"])
        self.assertEqual(policy.observe(frame), [])
        self.assertEqual(policy.observe(self.frame(11, struct.pack("<I", 2))), ["native_first_byte"])
        self.assertEqual(policy.observe(self.frame(1)), [])

    def test_bad_crc_or_embedded_request_does_not_switch_preview(self):
        policy = NativeReceivePolicy()
        valid = self.frame(11, struct.pack("<I", 1))
        invalid = valid[:-1] + bytes([valid[-1] ^ 1])
        self.assertEqual(policy.observe(invalid), [])
        self.assertEqual(policy.observe(self.frame(2, valid)), [])
        self.assertEqual(policy.observe(valid), ["native_coalesced"])
        self.assertEqual(policy.observe(self.frame(3, b"PGR1")), ["native_first_byte"])

    def test_stream_preview_returns_to_first_byte_for_cpu_handoff(self):
        policy = NativeReceivePolicy("native_stream")
        self.assertEqual(policy.observe(self.frame(11, struct.pack("<I", 1))), ["native_stream"])
        self.assertEqual(policy.observe(self.frame(11, struct.pack("<I", 2))), ["native_first_byte"])


class Timeouts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("ReadIntervalTimeout", "ReadTotalTimeoutMultiplier", "ReadTotalTimeoutConstant",
                 "WriteTotalTimeoutMultiplier", "WriteTotalTimeoutConstant")]


class ReceiveModeTests(unittest.TestCase):
    def kernel(self, rx=1048576, success=True):
        def properties(handle, pointer):
            value = pointer._obj
            self.assertEqual(handle, 0x123456789abcdef0)
            self.assertEqual(value.wPacketLength, 64)
            self.assertEqual(value.dwProvSpec1, 0x0E73CF52)
            self.assertEqual(type(value).dwCurrentRxQueue.offset, 48)
            self.assertEqual(type(value).wcProvChar.offset, 60)
            value.dwCurrentRxQueue, value.dwCurrentTxQueue = rx, 65536
            value.dwMaxRxQueue, value.dwMaxTxQueue = 0, 0
            return int(success)

        return Mock(GetCommProperties=Mock(side_effect=properties))

    def driver(self):
        current = [0, 0, 50, 0, 5000]

        def get_timeouts(handle, pointer):
            for (name, _), value in zip(Timeouts._fields_, current):
                setattr(pointer._obj, name, value)
            return 1

        def set_timeouts(handle, pointer):
            current[:] = [getattr(pointer._obj, name) for name, _ in Timeouts._fields_]
            return 1

        return SimpleNamespace(COMMTIMEOUTS=Timeouts, MAXDWORD=0xffffffff,
                               GetCommTimeouts=Mock(side_effect=get_timeouts),
                               SetCommTimeouts=Mock(side_effect=set_timeouts))

    def test_properties_use_64_bit_handle_and_driver_values_not_requested_size(self):
        kernel, log = self.kernel(rx=65536), io.StringIO()
        with contextlib.redirect_stderr(log):
            result = windows_queue_properties(0x123456789abcdef0, "buffer_configured", kernel)
        self.assertEqual(result["current_rx"], 65536)
        self.assertEqual(kernel.GetCommProperties.argtypes[0], ctypes.c_void_p)
        self.assertEqual(kernel.GetCommProperties.restype, ctypes.c_int)
        self.assertIn("available=1 current_rx=65536", log.getvalue())

    def test_unsupported_properties_are_reported_without_claiming_buffer_capacity(self):
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            result = windows_queue_properties(0x123456789abcdef0, "opened", self.kernel(success=False))
        self.assertIsNone(result)
        self.assertIn("available=0 error=", log.getvalue())
        self.assertNotIn("current_rx=", log.getvalue())

    def test_native_preserves_pyserial_native_timeouts_and_reports_every_stage(self):
        port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
        kernel, driver, log = self.kernel(), self.driver(), io.StringIO()
        with contextlib.redirect_stderr(log):
            configure_windows_receive(port, 1048576, "native_adaptive", driver, kernel)
        port.set_buffer_size.assert_called_once_with(rx_size=1048576, tx_size=65536)
        driver.SetCommTimeouts.assert_not_called()
        self.assertEqual(kernel.GetCommProperties.call_count, 3)
        for stage in ("opened", "buffer_configured", "timeouts_configured"):
            self.assertIn(f"stage={stage}", log.getvalue())
        self.assertIn("read_mode=native_adaptive interval=0 multiplier=0 constant=50", log.getvalue())

    def test_first_byte_modes_read_back_actual_timeouts(self):
        for mode in ("fixed_first_byte", "queued_first_byte", "pipelined_first_byte", "native_first_byte"):
            with self.subTest(mode=mode):
                port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
                driver, log = self.driver(), io.StringIO()
                with contextlib.redirect_stderr(log):
                    configure_windows_receive(port, 1048576, mode, driver, self.kernel())
                driver.SetCommTimeouts.assert_called_once()
                self.assertEqual(driver.GetCommTimeouts.call_count, 2)
                constant = 100 if mode in ("pipelined_first_byte", "native_first_byte") else 10
                self.assertIn(f"read_mode={mode} interval=4294967295 multiplier=4294967295 constant={constant}",
                              log.getvalue())

    def test_live_timeout_switch_does_not_reconfigure_the_active_receive_buffer(self):
        port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
        driver, kernel, log = self.driver(), self.kernel(), io.StringIO()
        with contextlib.redirect_stderr(log):
            configure_windows_receive(port, 1048576, "native_coalesced", driver, kernel, resize_buffer=False)
            configure_windows_receive(port, 1048576, "native_first_byte", driver, kernel, resize_buffer=False)
        port.set_buffer_size.assert_not_called()
        self.assertEqual(driver.SetCommTimeouts.call_count, 2)
        self.assertEqual(driver.GetCommTimeouts.call_count, 4)
        self.assertNotIn("stage=buffer_configured", log.getvalue())
        self.assertIn("read_mode=native_coalesced interval=2", log.getvalue())
        self.assertIn("read_mode=native_first_byte interval=4294967295", log.getvalue())

    def test_native_timeout_query_failure_stops_before_ready_handshake(self):
        port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
        driver = self.driver()
        driver.GetCommTimeouts.return_value, driver.GetCommTimeouts.side_effect = 0, None
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(serial.SerialException):
            configure_windows_receive(port, 1048576, "native_adaptive", driver, self.kernel())

    def test_native_coalesced_returns_short_replies_after_a_bounded_gap(self):
        port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
        driver, log = self.driver(), io.StringIO()
        with contextlib.redirect_stderr(log):
            configure_windows_receive(port, 1048576, "native_coalesced", driver, self.kernel())
        driver.SetCommTimeouts.assert_called_once()
        self.assertIn("read_mode=native_coalesced interval=2 multiplier=0 constant=100", log.getvalue())

    def test_stream_has_inter_byte_gap_without_expiring_idle_reads(self):
        port = SimpleNamespace(_port_handle=0x123456789abcdef0, set_buffer_size=Mock())
        driver, log = self.driver(), io.StringIO()
        with contextlib.redirect_stderr(log):
            configure_windows_receive(port, 1048576, "native_stream", driver, self.kernel())
        self.assertIn("read_mode=native_stream interval=2 multiplier=0 constant=0", log.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
