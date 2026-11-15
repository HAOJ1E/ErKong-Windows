"""验证倒计时取消不会打开耳机数据通道。"""

import unittest
from PySide6.QtWidgets import QApplication
from nodlink.validation_window import ValidationWindow


class ValidationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cancel_during_countdown_does_not_start_worker(self):
        window = ValidationWindow()
        window.start_capture()
        self.assertTrue(window._countdown_timer.isActive())
        self.assertIsNone(window._worker)
        self.assertEqual(window.progress.maximum(), 540)
        window.stop_capture()
        self.assertFalse(window._countdown_timer.isActive())
        self.assertIsNone(window._worker)
        self.assertTrue(window.start_button.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
