"""只用替身检查恢复状态机，不启停真实服务，不生成硬件成功记录。"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nodlink.bridge_recovery import BridgeRecovery, BridgeRestartRequired, WindowsBridgeSystem, watch_parent
from nodlink.control_window import MouseControlWindow
from nodlink.bridge_cli import main as helper_main


class FakeSystem:
    def __init__(self):
        self.running = False
        self.gui = False
        self.actions = []

    def snapshot(self):
        return {"GuiRunning": self.gui, "ServiceManual": True, "VirtualReady": self.running,
                "NativeReady": not self.running, "ControllerService": "usbip2_ude"}

    def is_running(self):
        return self.running

    def restore_native(self):
        self.actions.append("restore")
        self.running = False

    def reset_empty_controller(self):
        self.actions.append("reset")

    def start(self):
        self.actions.append("start")
        self.running = True

    def wait_for_radio(self, cancelled):
        pass


def test_frames(_cancelled):
    return {"status": "stream_received", "valid_frames": 90, "stop_command_sent": True,
            "mouse_control_started": False, "test_only": True}


class BridgeRecoveryTests(unittest.TestCase):
    def setUp(self):
        lease_patch = patch("nodlink.bridge_recovery.CaptureLease")
        lease_patch.start()
        self.addCleanup(lease_patch.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.system = FakeSystem()
        self.manager = BridgeRecovery(self.folder, self.system, test_frames, guard_starter=Mock())

    def policy(self):
        return json.loads((self.folder / "bridge-policy.json").read_text(encoding="utf-8"))

    def test_success_requires_verified_probe_then_close_restores_native(self):
        result = self.manager.enable()
        self.assertTrue(result["ready"])
        self.assertEqual(self.system.actions, ["restore", "reset", "start"])
        self.assertEqual(self.policy()["mode"], "bridge")
        self.assertTrue(self.policy()["motion_stream_checked"])
        self.assertTrue(self.manager.owns_bridge)
        self.manager.disable()
        self.assertFalse(self.manager.owns_bridge)
        self.assertEqual(self.policy()["mode"], "native_bluetooth")

    def test_no_sensor_frames_rolls_back_and_does_not_enable_training(self):
        self.manager.probe_runner = lambda cancel: {"status": "no_valid_motion_frames", "valid_frames": 0}
        with self.assertRaisesRegex(RuntimeError, "运动帧"):
            self.manager.enable()
        self.assertEqual(self.policy()["mode"], "native_bluetooth")
        self.assertFalse(self.system.running)
        self.assertFalse(self.manager.owns_bridge)

    def test_handshake_without_frames_gets_one_resubscription(self):
        self.manager.probe_runner = Mock(side_effect=[
            {"status": "no_valid_motion_frames", "valid_frames": 0, "handshake_reply_received": True},
            test_frames(None),
        ])
        result = self.manager.enable()
        self.assertTrue(result["ready"])
        self.assertEqual(self.manager.probe_runner.call_count, 2)
        self.assertEqual(len(self.policy()["probe_attempts"]), 2)

    def test_missing_stop_confirmation_never_enables_bridge(self):
        self.manager.probe_runner = lambda cancel: {"status": "stream_received", "valid_frames": 99,
                                                    "stop_command_sent": False}
        with self.assertRaises(RuntimeError):
            self.manager.enable()
        self.assertEqual(self.policy()["mode"], "native_bluetooth")

    def test_guard_launch_failure_also_removes_enabling_policy(self):
        self.manager.guard_starter.side_effect = OSError("TEST_GUARD_FAILURE")
        with self.assertRaises(OSError):
            self.manager.enable()
        self.assertEqual(self.policy()["mode"], "native_bluetooth")

    def test_cancel_rolls_back_without_starting_service(self):
        with self.assertRaises(InterruptedError):
            self.manager.enable(cancelled=lambda: True)
        self.assertEqual(self.system.actions, ["restore"])
        self.assertEqual(self.policy()["mode"], "native_bluetooth")

    def test_competing_gui_is_rejected_without_changing_system(self):
        self.system.gui = True
        with self.assertRaisesRegex(RuntimeError, "WPods"):
            self.manager.enable()
        self.assertEqual(self.system.actions, [])
        self.assertFalse((self.folder / "bridge-policy.json").exists())

    def test_failed_rollback_retains_owned_session_and_requires_recovery(self):
        self.system.restore_native = Mock(side_effect=[None, RuntimeError("TEST_RESTORE_FAILURE")])
        self.manager.probe_runner = Mock(side_effect=RuntimeError("TEST_PROBE_FAILURE"))
        with self.assertRaisesRegex(RuntimeError, "恢复尚未确认"):
            self.manager.enable()
        self.assertTrue(self.manager.owns_bridge)
        self.assertEqual(self.policy()["mode"], "recovery_required")

    def test_other_live_session_cannot_be_replaced_or_disabled(self):
        self.manager._policy("bridge")
        other = BridgeRecovery(self.folder, self.system)
        with self.assertRaisesRegex(RuntimeError, "另一个"):
            other.enable()
        with self.assertRaisesRegex(RuntimeError, "另一个"):
            other.disable()
        self.assertEqual(self.policy()["session_token"], self.manager.token)
        self.assertEqual(self.system.actions, [])

    def test_watchdog_checks_token_and_restores_only_after_owner_exit(self):
        restore = Mock()
        self.manager._policy("bridge")
        watch_parent(os.getpid(), "old-token", self.folder, alive=lambda pid: False, restore=restore)
        restore.assert_not_called()
        watch_parent(os.getpid(), self.manager.token, self.folder, alive=lambda pid: False, restore=restore)
        restore.assert_called_once()
        self.assertEqual(self.policy()["mode"], "native_bluetooth")

    def test_nonempty_usbip_controller_is_never_restarted(self):
        system = WindowsBridgeSystem()
        with patch.object(system, "snapshot", return_value={"ControllerService": "usbip2_ude"}), patch.object(
            system, "is_running", return_value=False
        ), patch.object(system, "run", return_value="Port 00: <Port in Use>") as run:
            with self.assertRaisesRegex(RuntimeError, "正在使用"):
                system.reset_empty_controller()
        self.assertEqual(run.call_count, 1)

    def test_probe_result_file_preserves_chinese_without_console(self):
        path = self.folder / "probe.json"
        with patch("nodlink.bridge_cli.probe", return_value={"status": "probe_failed", "error": "耳机尚未连接"}):
            helper_main(["probe", "--output", str(path)])
        result = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["error"], "耳机尚未连接")

    def test_same_boot_restart_requirement_blocks_repeated_system_changes(self):
        self.manager._policy("native_bluetooth", restart_required=True, restart_boot_time="TEST_BOOT")
        with patch("nodlink.bridge_recovery.current_boot_time", return_value="TEST_BOOT"):
            with self.assertRaises(BridgeRestartRequired):
                self.manager.enable()
        self.assertEqual(self.system.actions, [])

    def test_completed_reboot_allows_manual_enable_again(self):
        self.manager._policy("native_bluetooth", restart_required=True, restart_boot_time="OLD_BOOT")
        with patch("nodlink.bridge_recovery.current_boot_time", return_value="NEW_BOOT"):
            self.assertTrue(self.manager.enable()["ready"])

    def test_windows_3010_is_explained_without_corrupted_console_text(self):
        result = Mock(returncode=3010, stdout="TEST_NATIVE_TEXT", stderr="")
        with patch("nodlink.bridge_recovery.subprocess.run", return_value=result):
            with self.assertRaisesRegex(BridgeRestartRequired, "重新启动"):
                WindowsBridgeSystem().run(["pnputil.exe", "/restart-device", "TEST_ONLY"])


class BridgeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        self.assertTrue(condition())

    @patch("nodlink.bridge_recovery.CaptureLease")
    def test_paused_window_can_connect_without_blocking_and_close_restores(self, _lease):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            system = FakeSystem()
            def slow_probe(cancelled):
                time.sleep(0.15)
                return test_frames(cancelled)
            manager = BridgeRecovery(folder, system, slow_probe, guard_starter=Mock())
            paused = {"ready": False, "status": "bridge_paused", "summary": "暂停", "detail": "TEST_ONLY"}
            with patch("nodlink.control_window.latest_verified_validation_report", return_value=(Path("test.json"), {})):
                window = MouseControlWindow(folder, paused, bridge_manager=manager)
            window.show()
            beats = []
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: beats.append(time.monotonic()))
            timer.start()
            self.assertTrue(window.enable_bridge_button.isEnabled())
            self.assertFalse(window.train_button.isEnabled())
            window.enable_bridge()
            self.assertFalse(window.train_button.isEnabled())
            self.assertFalse(window.start_button.isEnabled())
            self.wait_until(lambda: not window._bridge_running())
            self.assertGreater(len(beats), 5)
            self.assertTrue(window.train_button.isEnabled())
            self.assertTrue(window.ready_checkbox.isEnabled())
            self.assertFalse(window.ready_checkbox.isChecked())
            self.assertFalse(window.start_button.isEnabled())
            self.assertIsNone(window._worker)
            window.close()
            self.wait_until(lambda: not window.isVisible())
            timer.stop()
            self.assertFalse(system.running)
            self.assertFalse(manager.owns_bridge)
