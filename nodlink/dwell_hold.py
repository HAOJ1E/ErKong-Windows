"""固定锚点悬停计时；只产生动作请求，不直接操作鼠标。"""

import math


class DwellHold:
    def __init__(self, seconds=5.0, radius=10.0):
        self.seconds, self.radius = seconds, radius
        self.held = False
        self.reset()

    def reset(self):
        self.anchor = None
        self.started = self.last_time = None
        self.progress = 0.0
        self.wait_for_move = False

    def cancel(self):
        self.held = False
        self.reset()

    def applied(self, held, position):
        self.held = bool(held)
        self.anchor = tuple(position)
        self.started = None
        self.progress = 1.0
        self.wait_for_move = True

    def update(self, position, timestamp, stable=True):
        if not math.isfinite(timestamp) or not all(math.isfinite(value) for value in position):
            self.reset()
            return None
        if self.last_time is not None and (timestamp <= self.last_time or timestamp - self.last_time > 0.25):
            self.started = None
            if not self.wait_for_move:
                self.anchor = None
                self.progress = 0
        self.last_time = timestamp
        if self.anchor is not None and math.dist(position, self.anchor) > self.radius:
            self.anchor = None
            self.started = None
            self.progress = 0
            self.wait_for_move = False
        if self.wait_for_move:
            return None
        if not stable:
            self.anchor = self.started = None
            self.progress = 0
            return None
        if self.anchor is None or self.started is None:
            self.anchor, self.started = tuple(position), timestamp
        elapsed = timestamp - self.started
        self.progress = 1.0 if elapsed + 1e-9 >= self.seconds else max(0.0, elapsed / self.seconds)
        if self.progress >= 1:
            self.wait_for_move = True
            return "release" if self.held else "press"
        return None
