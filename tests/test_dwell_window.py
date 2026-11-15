"""检查真实动作门控、停止松开与圆环，禁止发送实际鼠标输入。"""

from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from nodlink.control_window import MouseControlWindow
from tests.runtime_fixtures import READY_RUNTIME


class FakeButton:
    def __init__(self):
        self.held = False
        self.presses = self.releases = 0
        self.state = Mock()
        self.state.ready.return_value = True

    def press(self):
        self.held = True
        self.presses += 1

    def release(self):
        if self.held:
            self.releases += 1
        self.held = False


class DwellWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        with patch("nodlink.control_window.latest_verified_validation_report", return_value=(Path("test.json"), {})):
            window = MouseControlWindow(initial_runtime_status=READY_RUNTIME)
        window._left_button = FakeButton()
        return window

    def activate(self, window):
        window._accepting_samples = window._control_activated = True
        window._pointer_stabilizer.locked = True
        window.dwell_checkbox.setChecked(True)
        window._dwell_timer.stop()

    def tick(self, window, timestamp, position=(500, 300)):
        window._last_valid_sample_at = timestamp
        with patch("nodlink.control_window.time.monotonic", return_value=timestamp), patch(
            "pyautogui.position", return_value=position
        ), patch("pyautogui.failSafeCheck"), patch("nodlink.control_window.release_key_pressed", return_value=False):
            window._poll_dwell()

    def test_default_off_and_idle_head_control_never_press(self):
        window = self.window()
        self.assertFalse(window.dwell_checkbox.isChecked())
        window.dwell_checkbox.setChecked(True)
        self.tick(window, 5)
        self.assertEqual(window._left_button.presses, 0)
        self.assertFalse(window._dwell_overlay.isVisible())
        window.close()

    def test_five_second_press_full_ring_and_switch_off_release(self):
        window = self.window()
        self.activate(window)
        for index in range(101):
            self.tick(window, index * 0.05)
        self.assertTrue(window._left_button.held)
        self.assertEqual(window._left_button.presses, 1)
        self.assertEqual(window._dwell_overlay.progress, 1)
        self.assertTrue(window.release_button.isEnabled())
        window.dwell_checkbox.setChecked(False)
        self.assertFalse(window._left_button.held)
        self.assertEqual(window._left_button.releases, 1)
        self.assertFalse(window._dwell_overlay.isVisible())
        window.close()

    def test_stop_timeout_close_and_f8_release_owned_button(self):
        for reason in ("stop", "stale", "close", "f8"):
            with self.subTest(reason=reason):
                window = self.window()
                self.activate(window)
                window._left_button.held = True
                if reason == "stop":
                    window.stop_control()
                elif reason == "close":
                    window.close()
                elif reason == "stale":
                    window._last_valid_sample_at = time.monotonic() - 2
                    window._poll_dwell()
                else:
                    window._last_valid_sample_at = time.monotonic()
                    with patch("nodlink.control_window.release_key_pressed", return_value=True), patch("pyautogui.position", return_value=(500, 300)):
                        window._poll_dwell()
                self.assertFalse(window._left_button.held)
                window.close()

    def test_blink_click_is_suppressed_while_dragging(self):
        window = self.window()
        self.activate(window)
        window._left_button.held = True
        window._blink_controller.enabled = True
        window._last_valid_sample_at = time.monotonic()
        with patch("pyautogui.click") as click:
            window._blink_click(time.monotonic())
            click.assert_not_called()
        window.close()

    def test_overlay_is_transparent_to_input_and_does_not_accept_focus(self):
        window = self.window()
        flags = window._dwell_overlay.windowFlags()
        self.assertTrue(flags & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        window.close()

    def test_locked_pointer_still_updates_live_frame_count(self):
        window = self.window()
        self.activate(window)
        window._stream_started = time.monotonic() - 4
        window._last_pointer = (500, 300)
        window._pointer_stabilizer.reset((500, 300), time.monotonic() - 0.04)
        window._mapper = Mock()
        window._mapper.update.return_value = (500, 300)
        with patch("pyautogui.moveTo") as move:
            window._handle_sample({"orientation_raw": [9000, -4000, 5000]})
            move.assert_not_called()
        self.assertIn("悬停锁定", window.live_label.text())
        self.assertIn("已处理 1 帧", window.live_label.text())
        window.close()

    def test_short_blink_does_not_erase_hover_or_rearm_a_completed_hold(self):
        window = self.window()
        self.activate(window)
        for index in range(99):
            self.tick(window, index * 0.05)
        with patch.object(window._blink_controller, "hold_pointer", return_value=True):
            self.tick(window, 4.95)
            self.tick(window, 5)
        self.assertEqual(window._left_button.presses, 0)
        self.tick(window, 5.05)
        self.assertTrue(window._left_button.held)
        with patch.object(window._blink_controller, "hold_pointer", return_value=True):
            for index in range(102, 140):
                self.tick(window, index * 0.05)
        for index in range(140, 250):
            self.tick(window, index * 0.05)
        self.assertTrue(window._left_button.held)
        self.assertEqual(window._left_button.releases, 0)
        window.close()
