"""检查鼠标控制窗口的显式启动和采集线程配置，不操作桌面鼠标。"""

import time
from pathlib import Path
import unittest
from tests.runtime_fixtures import READY_RUNTIME
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from nodlink.control_window import ControlWorker, MouseControlWindow


class ControlWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_verification_keeps_mouse_control_disabled(self):
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=None):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        window.start_control()
        self.assertIsNone(window._worker)
        window.close()

    def test_start_requires_separate_ready_confirmation(self):
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=verified):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        self.assertFalse(window.ready_checkbox.isChecked())
        self.assertFalse(window.start_button.isEnabled())
        window.start_control()
        self.assertIsNone(window._worker)
        window.ready_checkbox.setChecked(True)
        self.assertTrue(window.start_button.isEnabled())
        window.ready_checkbox.setChecked(False)
        self.assertFalse(window.start_button.isEnabled())
        window.close()

    def test_calibration_samples_do_not_move_pointer(self):
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=verified):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        window._stream_started = time.monotonic()
        window._accepting_samples = True
        sample = {"orientation_raw": [9000, -4000, 5000]}
        with patch("pyautogui.moveTo") as move_pointer:
            for _ in range(10):
                window._handle_sample(sample)
        move_pointer.assert_not_called()
        self.assertFalse(window._control_activated)
        self.assertEqual(window._move_count, 0)
        window.close()

    def test_live_capture_does_not_retain_raw_sensor_evidence(self):
        calls = []

        def fake_capture(*args, **kwargs):
            calls.append(kwargs)
            return {"status": "interrupted", "candidate_frame_count": 0}

        worker = ControlWorker()
        with patch("nodlink.control_window.capture", side_effect=fake_capture):
            worker.run()
        self.assertFalse(calls[0]["retain_evidence"])
        self.assertTrue(callable(calls[0]["stop_requested"]))

    def test_stop_rejects_late_samples_immediately(self):
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=verified):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        window._accepting_samples = window._control_activated = True
        window._stream_started = time.monotonic() - 4
        window.stop_control()
        with patch("pyautogui.moveTo") as move:
            for _ in range(100):
                window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
        move.assert_not_called()
        self.assertFalse(window._accepting_samples)
        window.close()

    def test_mouse_calls_skip_default_pause_and_stale_frames_stop(self):
        from unittest.mock import Mock
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=verified):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        window._accepting_samples = window._control_activated = True
        window._stream_started = time.monotonic() - 4
        window._mapper = Mock()
        window._mapper.update.return_value = (100, 100)
        with patch("pyautogui.moveTo") as move:
            window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
            move.assert_called_once_with(100, 100, duration=0, _pause=False)
            window._handle_sample({"orientation_raw": [9000, -4000, 5000],
                                   "received_at_monotonic": time.monotonic() - 2})
            self.assertEqual(move.call_count, 1)
        self.assertFalse(window._accepting_samples)
        window.close()

    def test_physical_mouse_corner_still_stops_and_shows_chinese_recovery(self):
        from unittest.mock import Mock
        import pyautogui
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=(Path("test.json"), {})):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        window._accepting_samples = window._control_activated = True
        window._stream_started = time.monotonic() - 4
        window._mapper = Mock()
        window._mapper.update.return_value = (100, 100)
        original_failsafe = pyautogui.FAILSAFE
        with patch("pyautogui.moveTo", side_effect=pyautogui.FailSafeException("TEST_ONLY_FAILSAFE")) as move:
            window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
            window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
        self.assertEqual(move.call_count, 1)
        self.assertFalse(window._accepting_samples)
        self.assertEqual(window._move_count, 0)
        self.assertIn("实体鼠标移出屏幕角落", window.status_label.text())
        self.assertNotIn("TEST_ONLY_FAILSAFE", window.status_label.text())
        self.assertFalse(window.ready_checkbox.isChecked())
        self.assertFalse(window.start_button.isEnabled())
        self.assertEqual(pyautogui.FAILSAFE, original_failsafe)
        self.assertTrue(pyautogui.FAILSAFE)
        window.close()


if __name__ == "__main__":
    unittest.main()
