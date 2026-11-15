"""验证短探测的协议时序和资源释放；人工输入不会写入实机报告。"""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nodlink.aap_motion import PROFILES
from nodlink.channel_probe import probe


class BridgeProbeTests(unittest.TestCase):
    def test_late_connection_still_leaves_full_capture_window_and_always_stops(self):
        device = SimpleNamespace(vendor_id=0x004C, product_id=0x2019, address=1)
        with patch("nodlink.channel_probe.CaptureLease") as lease, patch(
            "nodlink.channel_probe.paired_airpods", return_value=[device]
        ), patch("nodlink.channel_probe.WPodsTransport") as transport, patch(
            "nodlink.channel_probe.time.monotonic", side_effect=[100,109,111.8,111.8,113,115.8,115.8]
        ), patch("nodlink.channel_probe.time.sleep") as wait, patch(
            "nodlink.channel_probe.candidate_motion_fields", return_value={"orientation_raw": [-26000, 4000, 5000]}
        ):
            transport.return_value.receive.side_effect = [b"TEST_ACK", b"TEST_FRAME1", b"TEST_FRAME2"]
            report = probe()
        self.assertEqual(report["valid_frames"], 2)
        self.assertAlmostEqual(report["capture_seconds"], 4)
        self.assertTrue(report["stop_command_sent"])
        wait.assert_called_once_with(0.15)
        self.assertEqual(transport.return_value.send.call_args.args[0], PROFILES["service16"]["stop"])
        transport.return_value.close.assert_called_once()
        lease.return_value.close.assert_called_once()

    def test_open_failure_still_releases_lease_without_claiming_frames(self):
        device = SimpleNamespace(vendor_id=0x004C, product_id=0x2019, address=1)
        with patch("nodlink.channel_probe.CaptureLease") as lease, patch(
            "nodlink.channel_probe.paired_airpods", return_value=[device]
        ), patch("nodlink.channel_probe.WPodsTransport", side_effect=RuntimeError("TEST_ONLY_FAILURE")):
            report = probe()
        self.assertEqual(report["status"], "probe_failed")
        self.assertEqual(report["valid_frames"], 0)
        self.assertFalse(report["mouse_control_started"])
        lease.return_value.close.assert_called_once()
