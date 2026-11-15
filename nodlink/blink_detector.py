"""无需个人训练的双眼闭合状态机；自然短眨眼不触发单击。"""

import math


class BlinkDetector:
    def __init__(self, minimum_seconds=0.35):
        self.minimum_seconds = minimum_seconds
        self.cooldown_until = 0
        self.reset()

    def reset(self):
        self.armed = False
        self.open_since = self.closed_since = self.last_time = None
        self.closed_frames = 0

    def update(self, left, right, timestamp, face_count=1):
        """先稳定睁眼，再闭眼并重新睁开；最多返回一次 True。"""
        values = [left, right, timestamp]
        if face_count != 1 or any(value is None or not math.isfinite(value) for value in values):
            self.reset()
            return False
        if not (0 <= left <= 1 and 0 <= right <= 1):
            self.reset()
            return False
        if self.last_time is not None and (timestamp <= self.last_time or timestamp - self.last_time > 0.25):
            self.reset()
        self.last_time = timestamp
        opened = max(left, right) <= 0.35
        closed = min(left, right) >= 0.65
        if (left >= 0.65 and right <= 0.35) or (right >= 0.65 and left <= 0.35):
            self.reset()  # 单眼闭合不算本功能的双眼眨眼。
            return False
        if opened:
            clicked = (self.armed and self.closed_since is not None and self.closed_frames >= 3
                       and self.minimum_seconds <= timestamp - self.closed_since <= 1.2
                       and timestamp >= self.cooldown_until)
            if self.closed_since is not None:
                self.armed = False
                self.open_since = timestamp
                self.closed_since = None
                self.closed_frames = 0
            if self.open_since is None:
                self.open_since = timestamp
            if timestamp - self.open_since >= 0.4 and timestamp >= self.cooldown_until:
                self.armed = True
            if clicked:
                self.cooldown_until = timestamp + 0.9
                self.armed = False
            return clicked
        self.open_since = None
        if closed and self.armed:
            if self.closed_since is None:
                self.closed_since = timestamp
            self.closed_frames += 1
        if self.closed_since is not None and timestamp - self.closed_since > 1.2:
            self.armed = False
            self.closed_since = None
            self.closed_frames = 0
        return False
