"""用高频人工输入和慢写入检查 Qt 响应，不使用真实耳机或桌面指针。"""

from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtWidgets import QApplication

from nodlink.control_window import JsonSaveWorker
from nodlink.live_samples import LatestSample
from nodlink.wpods_transport import WPodsTransport


class ResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slow_disk_and_high_frame_rate_do_not_block_qt_heartbeat(self):
        buffer = LatestSample()
        stop = threading.Event()

        def produce():
            while not stop.is_set():
                for value in range(100):
                    buffer.put({"orientation_raw": [value, 1, 2]})
                stop.wait(0.001)

        thread = threading.Thread(target=produce)
        heartbeat_times = []
        poll_results = []
        heartbeat = QTimer()
        heartbeat.setInterval(10)
        heartbeat.timeout.connect(lambda: heartbeat_times.append(time.monotonic()))
        poll = QTimer()
        poll.setInterval(33)
        poll.timeout.connect(lambda: poll_results.append(buffer.take()))
        loop = QEventLoop()
        writer = JsonSaveWorker(Path("TEST_ONLY.json"), {})
        with patch("nodlink.control_window.save_json_atomic", side_effect=lambda *args: time.sleep(0.4)):
            thread.start()
            heartbeat.start()
            poll.start()
            writer.start()
            QTimer.singleShot(650, loop.quit)
            loop.exec()
            writer.wait(1500)
        stop.set()
        thread.join(1)
        heartbeat.stop()
        poll.stop()
        self.assertGreater(len(heartbeat_times), 20)
        self.assertLess(max(b - a for a, b in zip(heartbeat_times, heartbeat_times[1:])), 0.2)
        self.assertGreater(buffer.count, len(poll_results) * 10)
        self.assertGreater(len(poll_results), 5)

    def test_cancel_opening_channel_closes_pipe_without_waiting_15_seconds(self):
        with patch("nodlink.wpods_transport.service_state", return_value={"running": True, "process_id": 123}), patch(
            "nodlink.wpods_transport.MessagePipe"
        ) as pipe:
            with self.assertRaises(InterruptedError):
                WPodsTransport(1, stop_requested=lambda: True)
            pipe.return_value.read.assert_not_called()
            pipe.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
