import importlib.util
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("bridge", Path(__file__).parents[1] / "omarchy-ht3" / "bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ProtocolTests(unittest.TestCase):
    def test_paired_device_discovery_accepts_bluez_ansi_coloring(self):
        from types import SimpleNamespace
        result = SimpleNamespace(stdout="\x1b[1;30mDevice 94:4B:F8:99:08:45 TOZO HT3\x1b[0m\nDevice AA:AA:AA:AA:AA:AA Other\n")
        with patch.object(bridge.subprocess, "run", return_value=result):
            self.assertEqual(bridge.devices(), [{"address": "94:4B:F8:99:08:45", "name": "TOZO HT3"}])

    def test_captured_fragmented_stream_with_unsolicited_vendor_frame(self):
        raw = bytes.fromhex("0002015959fedcba80c200130105d60000000022944bf8990845025a000077ef000b14000b00f6f8f1ed080a05050607080a0c0e10121462")
        parser = bridge.Frames()
        results = []
        for byte in raw:
            results.extend(parser.feed(bytes([byte])))
        self.assertEqual([key for key, _ in results], [2, 11])
        self.assertEqual(bridge.decode(*results[0]), {"battery": 89})
        self.assertEqual(bridge.decode(*results[1])["eq"], [0, 11, 0, -10, -8, -15, -19, 8, 10, 5])

    def test_bad_checksum_and_set_ack_do_not_become_confirmed_state(self):
        parser = bridge.Frames()
        data = bytes.fromhex("103001010100300101990030010202")
        self.assertEqual(parser.feed(data), [(48, b"\x02")])

    def test_set_packet_signed_eq_and_trailing_commit_byte(self):
        payload = [(-50) & 255] + [0] * 8 + [50] + bridge.FREQUENCIES + [1]
        result = bridge.packet(11, payload)
        self.assertEqual(result[:4], bytes.fromhex("100b15ce"))
        self.assertEqual(len(result), 25)
        self.assertEqual(result[-1], sum(payload) & 255)
        self.assertEqual(bridge.packet(48), bytes.fromhex("00300000"))

    def test_unknown_mode_and_wrong_eq_profile_rejected(self):
        with self.assertRaises(ValueError):
            bridge.decode(48, b"\xff")
        with self.assertRaises(ValueError):
            bridge.decode(11, bytes(20))

    def test_eq_validation_before_io(self):
        headphones = bridge.Headphones()
        for gains in ([0] * 9, [51] * 10, [True] * 10, "flat"):
            with self.assertRaises(ValueError):
                headphones.set_eq(gains)

    def test_spatial_audio_blocks_eq_write(self):
        headphones = bridge.Headphones()
        with patch.object(headphones, "query", return_value={"spatial": True}):
            with self.assertRaisesRegex(ValueError, "spatial"):
                headphones.set_eq([0] * 10)

    def test_failed_refresh_clears_old_capabilities(self):
        headphones = bridge.Headphones()
        headphones.state = {"connected": True, "eq": [0]*10, "mode": "anc", "battery": 89, "spatial": False}
        with patch.object(headphones, "query", side_effect=TimeoutError):
            headphones.refresh()
        self.assertEqual(headphones.state, {"connected": True})

    def test_mode_change_requires_matching_readback(self):
        headphones = bridge.Headphones()
        from unittest.mock import Mock
        headphones.sock = Mock()
        with patch.object(headphones, "query", return_value={"mode": "normal"}), patch.object(bridge.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "did not confirm"):
                headphones.set_mode("anc")
        headphones.sock.sendall.assert_called_once_with(bytes.fromhex("1004010101"))


if __name__ == "__main__":
    unittest.main()
