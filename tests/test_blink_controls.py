"""检查点击门控与界面生命周期，全部点击均使用替身。"""

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from nodlink.blink_controls import BlinkController
from nodlink.control_window import MouseControlWindow
from nodlink.control_settings import load_stability
from nodlink.live_samples import LatestSample
from tests.runtime_fixtures import READY_RUNTIME


class FakeCameraWorker(QThread):
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, camera, parent):
        super().__init__(parent)
        self.latest = LatestSample()

    def run(self):
        while not self.isInterruptionRequested():
            time.sleep(0.005)


class BlinkControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, folder=None):
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=(Path("test.json"), {})):
            return MouseControlWindow(initial_runtime_status=READY_RUNTIME, **({"settings_folder": folder} if folder else {}))

    def test_default_off_and_click_requires_active_fresh_head_control(self):
        window = self.window()
        self.assertFalse(window.blink_checkbox.isChecked())
        self.assertFalse(window._blink_controller.running())
        window._blink_controller.enabled = True
        now = time.monotonic()
        with patch("pyautogui.click") as click:
            window._blink_click(now)
            click.assert_not_called()
            window._accepting_samples = window._control_activated = True
            window._last_valid_sample_at = now
            window._blink_click(now)
            click.assert_called_once_with(button="left", _pause=False)
            window._blink_controller.testing = True
            window._blink_click(now)
            window._blink_controller.testing = False
            window._blink_click(now - 1)
            window._last_valid_sample_at = now - 1
            window._blink_click(now)
            window.stop_control()
            window._blink_click(now)
            self.assertEqual(click.call_count, 1)
        window.close()

    def test_click_fail_safe_disables_blink_and_head_control(self):
        import pyautogui
        window = self.window()
        window._blink_controller.enabled = True
        window._accepting_samples = window._control_activated = True
        window._last_valid_sample_at = time.monotonic()
        with patch("pyautogui.click", side_effect=pyautogui.FailSafeException("TEST_ONLY")):
            window._blink_click(time.monotonic())
        self.assertFalse(window._accepting_samples)
        self.assertFalse(window._blink_controller.enabled)
        self.assertIn("角落保护", window.status_label.text())
        window.close()

    def test_blink_holds_pointer_without_stopping_fresh_sensor_handling(self):
        window = self.window()
        now = time.monotonic()
        window._accepting_samples = window._control_activated = True
        window._stream_started = now - 4
        window._last_pointer = (100, 100)
        window._mapper = Mock()
        window._mapper.update.return_value = (200, 200)
        controller = window._blink_controller
        controller.enabled = controller.click_allowed = True
        controller.last_sample_time = now
        controller.left = controller.right = 0.9
        # 固定时钟，避免首次导入鼠标模块耗时被误认为摄像头数据过期。
        with patch("nodlink.control_window.time.monotonic", return_value=now), patch("pyautogui.moveTo") as move:
            window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
            move.assert_not_called()
        self.assertEqual(window._sample_count, 1)
        self.assertTrue(window._accepting_samples)
        window.close()

    def test_switch_off_requests_worker_stop_and_quick_reenable_waits(self):
        controller = BlinkController()
        with patch("nodlink.blink_controls.BlinkWorker", FakeCameraWorker):
            controller.set_enabled(True)
            first = controller.worker
            controller.set_enabled(False)
            self.assertTrue(first.isInterruptionRequested())
            controller.set_enabled(True)
            deadline = time.monotonic() + 1
            while controller.worker is first and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertIsNot(controller.worker, first)
            controller.shutdown()
            controller.worker.wait(1000)
            self.app.processEvents()
        self.assertFalse(controller.running())

    def test_stability_value_is_saved_and_loaded(self):
        with TemporaryDirectory() as folder:
            window = self.window(Path(folder))
            window.stability_slider.setValue(83)
            window._settings_timer.stop()
            window._save_sensitivity()
            window._settings_writer.wait(2000)
            self.assertEqual(load_stability(Path(folder)), 83)
            window.close()

    def test_preview_counts_blink_without_requesting_clicks(self):
        controller = BlinkController()
        controller.enabled = controller.testing = controller.click_allowed = True
        controller.worker = SimpleNamespace(latest=LatestSample(), isRunning=lambda: False)
        requested = []
        controller.click_requested.connect(requested.append)
        start = time.monotonic()
        frames = [0.1] * 12 + [0.9] * 9 + [0.1] * 12
        for index, score in enumerate(frames):
            stamp = start + index * 0.05
            controller.worker.latest.put({"left": score, "right": score, "face_count": 1,
                                          "received_at_monotonic": stamp})
            with patch("nodlink.blink_controls.time.monotonic", return_value=stamp):
                controller.poll()
        self.assertEqual(controller.detected, 1)
        self.assertEqual(requested, [])
        controller.shutdown()

    def test_windowed_helper_uses_inherited_pipe_without_python_stdout(self):
        from nodlink.blink_cli import emit
        with patch("nodlink.blink_cli.sys.stdout", None), patch("nodlink.blink_cli.write_windows_pipe") as write:
            emit({"kind": "error", "message": "测试中文"})
        self.assertEqual(write.call_count, 1)
        self.assertTrue(write.call_args.args[0].endswith(b"\n"))
