"""后台只保留最新帧，界面定时取样，避免逐帧 Qt 信号积压。"""

from threading import Lock
import time


class LatestSample:
    def __init__(self):
        self._lock = Lock()
        self._latest = None
        self.count = 0

    def put(self, sample: dict):
        """新姿态覆盖尚未显示的旧姿态，训练原始数据由训练线程独立留存。"""
        item = dict(sample)
        item.setdefault("received_at_monotonic", time.monotonic())
        with self._lock:
            self._latest = item
            self.count += 1

    def take(self) -> dict | None:
        with self._lock:
            item, self._latest = self._latest, None
        return item
