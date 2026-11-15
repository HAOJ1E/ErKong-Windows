"""只检查按键所有权和退出清理；所有输入函数均使用替身。"""

import ctypes
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from nodlink.owned_mouse_button import OwnedLeftButton, Input
from nodlink.bridge_recovery import watch_parent


class OwnedButtonTests(unittest.TestCase):
    def setUp(self):
        state_patch = patch("nodlink.owned_mouse_button.LeftButtonState")
        factory = state_patch.start()
        self.addCleanup(state_patch.stop)
        self.state = factory.return_value
        self.state.ready.return_value = True
        self.state.held.return_value = False
        self.state.set_held.side_effect = lambda value: setattr(self.state.held, "return_value", value)
        self.button = OwnedLeftButton("TEST_ONLY")

    def test_owned_release_bypasses_fail_safe_without_changing_global_switch(self):
        import pyautogui
        with patch("nodlink.owned_mouse_button.send_left_button") as send, patch(
            "nodlink.owned_mouse_button.left_button_is_down", return_value=False
        ), patch("pyautogui.failSafeCheck") as check:
            self.button.press()
            self.assertTrue(self.state.held())
            check.side_effect = pyautogui.FailSafeException("TEST_ONLY")
            self.button.release()
            self.button.release()
        self.assertEqual([call.args[0] for call in send.call_args_list], [True, False])
        self.assertFalse(self.button.held)
        self.assertTrue(pyautogui.FAILSAFE)

    def test_guard_not_ready_and_physical_mouse_already_down_are_not_taken_over(self):
        with patch("nodlink.owned_mouse_button.send_left_button") as send, patch(
            "nodlink.owned_mouse_button.left_button_is_down", return_value=True
        ), patch("pyautogui.failSafeCheck"):
            self.state.ready.return_value = False
            with self.assertRaisesRegex(RuntimeError, "保护"):
                self.button.press()
            self.state.ready.return_value = True
            with self.assertRaisesRegex(RuntimeError, "接管"):
                self.button.press()
            self.button.release()
            send.assert_not_called()

    def test_failed_press_attempt_sends_cleanup_up(self):
        with patch("nodlink.owned_mouse_button.send_left_button", side_effect=[RuntimeError("TEST_ONLY"), None]) as send, patch(
            "nodlink.owned_mouse_button.left_button_is_down", return_value=False
        ), patch("pyautogui.failSafeCheck"):
            with self.assertRaises(RuntimeError):
                self.button.press()
        self.assertEqual(send.call_count, 2)
        self.assertFalse(self.button.held)

    def test_input_structure_has_windows_abi_size(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_parent_exit_releases_before_waiting_for_bluetooth_restore(self):
        actions = []
        with TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "bridge-policy.json").write_text(json.dumps({"mode": "bridge", "session_token": "TEST_ONLY"}), encoding="utf-8")
            with patch("nodlink.owned_mouse_button.LeftButtonState") as factory, patch("nodlink.bridge_recovery.CaptureLease"):
                state = factory.return_value
                state.release_owned.side_effect = lambda: actions.append("release")
                watch_parent(os.getpid(), "TEST_ONLY", path, alive=lambda pid: False,
                             restore=lambda: actions.append("restore"))
                self.assertEqual(actions[:2], ["release", "restore"])
                self.assertEqual(state.set_ready.call_args.args, (False,))
