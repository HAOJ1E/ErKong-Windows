"""在任意悬停位置抑制小幅抖动，大幅移动时减少滤波延迟。"""

import math
from collections import deque
from statistics import median


class PointerStabilizer:
    def __init__(self, strength=65):
        self.set_strength(strength)
        self.reset()

    def set_strength(self, strength):
        self.strength = min(100, max(0, int(strength)))

    def reset(self, position=None, timestamp=None):
        self.filtered = list(map(float, position)) if position is not None else None
        self.output = self.filtered.copy() if self.filtered is not None else None
        self.timestamp = timestamp
        self.history = deque(maxlen=3)
        self.motion_history = deque(maxlen=10)
        self.locked = position is not None and self.strength > 0
        self.settle_anchor = self.filtered.copy() if self.filtered is not None else None
        self.settle_since = timestamp
        self.unlock_since = None

    def update(self, position, timestamp):
        target = list(map(float, position))
        if not all(math.isfinite(value) for value in [*target, timestamp]):
            return None
        if self.timestamp is not None and timestamp <= self.timestamp:
            return None
        if self.filtered is None or self.strength == 0:
            self.reset(target, timestamp)
            return tuple(round(value) for value in target)
        interval = min(0.2, max(0.005, timestamp - self.timestamp)) if self.timestamp is not None else 0.04
        self.timestamp = timestamp
        self.history.append(target)
        # 三帧中值抑制孤立尖峰，锁定采用固定锚点，不能随噪声逐帧漂移。
        if len(self.history) == 3:
            target = [median(point[axis] for point in self.history) for axis in (0, 1)]
        self.motion_history.append(target.copy())
        if self.locked:
            unlock_radius = 3 + self.strength * 0.1
            distance = math.dist(target, self.output)
            if distance <= unlock_radius:
                self.unlock_since = None
                return tuple(round(value) for value in self.output)
            if self.unlock_since is None:
                self.unlock_since = timestamp
            large_move = distance > max(35, unlock_radius * 3) and len(self.history) >= 2
            if not large_move and timestamp - self.unlock_since < 0.12:
                return tuple(round(value) for value in self.output)
            self.locked = False
            self.settle_anchor, self.settle_since = target.copy(), timestamp
        distance = math.dist(target, self.output)
        time_constant = (0.02 + self.strength * 0.0018) / (1 + 9 * min(distance / 80, 1))
        alpha = 1 - math.exp(-interval / time_constant)
        self.filtered = [old + alpha * (new - old) for old, new in zip(self.filtered, target)]
        distance = math.dist(self.filtered, self.output)
        radius = self.strength / 25  # 最大 4 像素；滑块为 0 时完全关闭。
        if distance > radius:
            fraction = (distance - radius) / distance
            self.output = [old + fraction * (new - old) for old, new in zip(self.output, self.filtered)]
        if self.settle_anchor is None or math.dist(target, self.settle_anchor) > 3 + self.strength * 0.06:
            self.settle_anchor, self.settle_since = target.copy(), timestamp
        elif self.settle_since is not None and timestamp - self.settle_since >= 0.3:
            history = list(self.motion_history)
            path = sum(math.dist(left, right) for left, right in zip(history, history[1:]))
            direct = math.dist(history[0], history[-1])
            deliberate_slow_move = path > 1.2 and direct / path > 0.85
            if not deliberate_slow_move:
                self.locked = True
                self.filtered = self.output.copy()
                self.unlock_since = None
        return tuple(round(value) for value in self.output)
