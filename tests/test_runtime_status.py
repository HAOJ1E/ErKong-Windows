"""桥接暂停时禁止启动采集，正确说明环境错误，不更改系统服务。"""

from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from nodlink.control_window import MouseControlWindow
from nodlink.runtime_status import capture_failure_message, get_runtime_status
from nodlink.sensor_capture import capture
from nodlink.training_window import TrainingWindow, TrainingWorker


PAUSED = {"ready": False, "status": "bridge_paused", "summary": "头追已暂停：原生蓝牙模式",
          "detail": "系统蓝牙正常，桥接兼容问题修复前不启动训练或控制。"}


class RuntimeStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pause_policy_takes_priority_and_does_not_probe_service(self):
        with patch("nodlink.runtime_status.read_pause_policy", return_value=PAUSED), patch(
            "nodlink.runtime_status.bridge_availability"
        ) as probe:
            self.assertFalse(get_runtime_status()["ready"])
        probe.assert_not_called()

    def test_stopped_bridge_is_described_before_training(self):
        with patch("nodlink.runtime_status.read_pause_policy", return_value=None), patch(
            "nodlink.runtime_status.bridge_availability", return_value={"status": "not_running"}
        ):
            state = get_runtime_status()
        self.assertFalse(state["ready"])
        self.assertIn("未运行", state["summary"])
        self.assertIn("未运行", capture_failure_message({"status": "bridge_unavailable", "bridge": {"status": "not_running"}}))

    def test_control_and_training_are_disabled_even_with_verified_history(self):
        verified = (Path("test-only-verified.json"), {"head_motion_verified": True})
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=verified):
            window = MouseControlWindow(initial_runtime_status=PAUSED)
        self.assertFalse(window.train_button.isEnabled())
        self.assertFalse(window.ready_checkbox.isEnabled())
        window.ready_checkbox.setChecked(True)
        window.start_control()
        window.open_training()
        self.assertFalse(window.start_button.isEnabled())
        self.assertIsNone(window._worker)
        self.assertIsNone(window._training_window)
        self.assertIn("暂停", window.status_label.text())
        self.assertTrue(window.sensitivity_slider.isEnabled())
        window.close()

    def test_standalone_training_does_not_start_countdown_or_worker(self):
        window = TrainingWindow(initial_runtime_status=PAUSED)
        window.start_training()
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window._prep_timer.isActive())
        self.assertIsNone(window.worker)
        self.assertIn("暂停", window.instruction.text())
        window.close()

    def test_backend_rejects_capture_before_opening_earphone(self):
        with patch("nodlink.sensor_capture.CaptureLease"), patch("nodlink.sensor_capture.read_pause_policy", return_value=PAUSED), patch(
            "nodlink.sensor_capture.WPodsTransport"
        ) as transport, patch("nodlink.sensor_capture.bridge_availability") as probe:
            report = capture("service16", 1, retain_evidence=False)
        self.assertEqual(report["status"], "bridge_paused")
        self.assertEqual(report["candidate_frame_count"], 0)
        self.assertEqual(report["recovery_hint"], PAUSED["detail"])
        transport.assert_not_called()
        probe.assert_not_called()

    def test_failed_precondition_is_not_misreported_as_movement_failure(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            worker = TrainingWorker(reports_folder=Path(folder))
            results = []
            worker.completed.connect(lambda report, path: results.append(report))
            unavailable = {"status": "bridge_unavailable", "bridge": {"status": "not_running"}}
            with patch("nodlink.training_window.capture", return_value=unavailable):
                worker.run()
            self.assertFalse(results[0]["training_analysis"]["passed"])
            self.assertIn("未运行", results[0]["training_analysis"]["reason"])


if __name__ == "__main__":
    unittest.main()
