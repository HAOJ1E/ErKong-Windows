"""测试训练流程、模型应用和最新帧缓冲；不会打开真实耳机或操作鼠标。"""

from pathlib import Path
import tempfile
import unittest
from tests.runtime_fixtures import READY_RUNTIME
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop

from nodlink.control_settings import load_profile, load_sensitivity
from nodlink.control_window import MouseControlWindow
from nodlink.live_samples import LatestSample
from nodlink.motion_training import fit_training
from nodlink.training_window import ProfileSaveWorker, TrainingWindow, TrainingWorker
from tests.test_motion_training import training_frames


class TrainingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_latest_sample_has_no_backlog(self):
        latest = LatestSample()
        for index in range(100000):
            latest.put({"orientation_raw": [index, 0, 0]})
        self.assertEqual(latest.count, 100000)
        self.assertEqual(latest.take()["orientation_raw"][0], 99999)
        self.assertIsNone(latest.take())

    def test_cancel_preparation_never_opens_hardware(self):
        window = TrainingWindow(initial_runtime_status=READY_RUNTIME)
        window.start_training()
        self.assertTrue(window.is_busy())
        window.stop_training()
        self.assertFalse(window.is_busy())
        self.assertIsNone(window.worker)
        self.assertFalse(window.apply_button.isEnabled())
        window.close()

    def test_training_reserves_control_and_never_arms_mouse(self):
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with tempfile.TemporaryDirectory() as folder, patch(
            "nodlink.control_window.latest_verified_validation_report", return_value=verified
        ):
            window = MouseControlWindow(Path(folder), initial_runtime_status=READY_RUNTIME)
            with patch("pyautogui.moveTo") as move:
                window.open_training()
                window.ready_checkbox.setChecked(True)
                window.start_control()
                self.assertIsNone(window._worker)
                self.assertFalse(window.ready_checkbox.isEnabled())
                window._training_window.close()
                self.assertTrue(window.ready_checkbox.isEnabled())
            move.assert_not_called()
            window.close()

    def test_worker_saves_training_xyz_without_packet_hex(self):
        frames = training_frames()
        emitted = []

        def fake_capture(*args, **kwargs):
            self.assertFalse(kwargs["retain_evidence"])
            for sample in frames:
                kwargs["on_sample"]({**sample, "packet_hex": "TEST_ONLY"})
            return {"status": "candidate_frames_need_validation", "stop_command_sent": True}

        with tempfile.TemporaryDirectory() as folder:
            worker = TrainingWorker(reports_folder=Path(folder))
            worker.completed.connect(lambda report, path: emitted.append((report, path)))
            with patch("nodlink.training_window.capture", side_effect=fake_capture):
                worker.run()
            report = emitted[0][0]
            self.assertTrue(report["training_analysis"]["passed"])
            self.assertNotIn("packet_hex", report["candidate_frames"][0])
            self.assertFalse(report["user_confirmed_training"])
            self.assertIsNone(load_profile(Path(folder))[0])

    def test_confirmed_application_is_saved_and_loadable(self):
        with tempfile.TemporaryDirectory() as folder:
            report = {"training_analysis": fit_training(training_frames())}
            worker = ProfileSaveWorker(report, Path(folder) / "report.json", Path(folder))
            worker.run()
            profile, _ = load_profile(Path(folder))
            self.assertIsNotNone(profile)
            self.assertTrue(profile["user_confirmed"])

    def test_sensitivity_controls_save_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            window = MouseControlWindow(Path(folder), initial_runtime_status=READY_RUNTIME)
            window.sensitivity_slider.setValue(180)
            window.horizontal_sensitivity.setValue(1.25)
            loop = QEventLoop()
            QTimer.singleShot(900, loop.quit)
            loop.exec()
            if window._settings_writer is not None:
                window._settings_writer.wait(1000)
            self.assertEqual(load_sensitivity(Path(folder)), {"overall": 1.8, "horizontal": 1.25, "vertical": 1.0})
            window.close()
            restored = MouseControlWindow(Path(folder), initial_runtime_status=READY_RUNTIME)
            self.assertEqual(restored.sensitivity_slider.value(), 180)
            self.assertEqual(restored.horizontal_sensitivity.value(), 1.25)
            self.assertFalse(restored.ready_checkbox.isChecked())
            restored.close()

    def test_parent_close_cancels_training_preparation(self):
        verified = (Path("verified-test-report.json"), {"head_motion_verified": True})
        with tempfile.TemporaryDirectory() as folder, patch(
            "nodlink.control_window.latest_verified_validation_report", return_value=verified
        ):
            window = MouseControlWindow(Path(folder), initial_runtime_status=READY_RUNTIME)
            window.open_training()
            training = window._training_window
            training.start_training()
            window.close()
            self.app.processEvents()
            self.assertFalse(training.is_busy())
            self.assertIsNone(training.worker)
            self.assertFalse(window._training_open)


if __name__ == "__main__":
    unittest.main()
