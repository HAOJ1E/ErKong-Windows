"""检查跨进程通道锁与错误恢复提示，不连接真实耳机。"""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from nodlink.capture_lease import CaptureLease, CaptureBusyError
from nodlink.sensor_capture import capture
from nodlink.wpods_transport import AapOpenError


class ChannelOwnershipTests(unittest.TestCase):
    def setUp(self):
        policy = patch("nodlink.sensor_capture.read_pause_policy", return_value=None)
        policy.start()
        self.addCleanup(policy.stop)

    def test_second_process_is_rejected_then_can_acquire_after_release(self):
        name = "Local\\NodLink.TestCapture." + uuid.uuid4().hex
        lease = CaptureLease(name)
        lease.acquire()
        code = (
            "import sys; from nodlink.capture_lease import CaptureLease, CaptureBusyError; "
            f"lease=CaptureLease({name!r})\n"
            "try:\n lease.acquire()\nexcept CaptureBusyError:\n sys.exit(17)\n"
            "lease.close()\n"
        )
        try:
            busy = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                  capture_output=True, timeout=5)
            self.assertEqual(busy.returncode, 17, busy.stderr)
        finally:
            lease.close()
        available = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, timeout=5)
        self.assertEqual(available.returncode, 0, available.stderr)

    def test_local_busy_does_not_open_bridge_and_closes_lease_handle(self):
        with patch("nodlink.sensor_capture.CaptureLease") as lease, patch(
            "nodlink.sensor_capture.bridge_availability"
        ) as bridge:
            lease.return_value.acquire.side_effect = CaptureBusyError()
            report = capture("service16", 1, retain_evidence=False)
        self.assertEqual(report["status"], "channel_busy")
        self.assertEqual(report["error_code"], 170)
        self.assertIn("NodLink", report["recovery_hint"])
        bridge.assert_not_called()
        lease.return_value.close.assert_called_once()

    def test_external_busy_includes_specific_wpods_recovery_and_releases_lock(self):
        device = SimpleNamespace(address=1, vendor_id=0x004C, product_id=0x2019, public_info=lambda: {})
        with patch("nodlink.sensor_capture.CaptureLease") as lease, patch(
            "nodlink.sensor_capture.bridge_availability", return_value={"status": "pipe_available_unverified"}
        ), patch("nodlink.sensor_capture.paired_airpods", return_value=[device]), patch(
            "nodlink.sensor_capture.WPodsTransport", side_effect=AapOpenError(170, "occupied test channel")
        ):
            report = capture("service16", 1, retain_evidence=False)
        self.assertEqual(report["status"], "channel_busy")
        self.assertEqual(report["error_code"], 170)
        self.assertIn("托盘", report["recovery_hint"])
        self.assertIn("后台服务需要保持运行", report["recovery_hint"])
        self.assertFalse(report["head_motion_verified"])
        lease.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
