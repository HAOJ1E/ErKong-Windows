"""检查悬停抖动抑制与主动移动响应，不移动桌面鼠标。"""

import unittest
from nodlink.pointer_stabilizer import PointerStabilizer


class PointerStabilizerTests(unittest.TestCase):
    def test_stationary_noise_holds_pointer(self):
        stabilizer = PointerStabilizer(65)
        stabilizer.reset((500, 300), 0)
        positions = [stabilizer.update((500 + (5 if index % 2 else -5), 300), index * 0.04)
                     for index in range(1, 101)]
        self.assertLessEqual(max(abs(x - 500) for x, y in positions), 1)

    def test_large_move_remains_responsive_and_slow_move_does_not_get_stuck(self):
        stabilizer = PointerStabilizer(65)
        stabilizer.reset((500, 300), 0)
        for index in range(1, 4):
            position = stabilizer.update((800, 300), index * 0.04)
        self.assertGreater(position[0], 790)
        stabilizer.reset((500, 300), 0)
        for index in range(1, 81):
            position = stabilizer.update((500 + index * 0.25, 300), index * 0.04)
        self.assertGreater(position[0], 515)

    def test_zero_strength_is_exact_passthrough_and_stale_time_is_rejected(self):
        stabilizer = PointerStabilizer(0)
        self.assertEqual(stabilizer.update((99, 101), 1), (99, 101))
        self.assertEqual(stabilizer.update((103, 104), 2), (103, 104))
        self.assertIsNone(stabilizer.update((500, 500), 1))

    def test_hover_lock_rejects_slow_wobble_and_single_spike(self):
        stabilizer = PointerStabilizer(65)
        stabilizer.reset((500, 300), 0)
        positions = []
        for index in range(1, 200):
            offset = (index % 40 - 20) * 0.4
            target = (500 + offset, 300)
            if index == 90:
                target = (750, 450)
            positions.append(stabilizer.update(target, index * 0.04))
        self.assertEqual(set(positions), {(500, 300)})

    def test_hover_lock_releases_for_sustained_intentional_motion(self):
        stabilizer = PointerStabilizer(65)
        stabilizer.reset((500, 300), 0)
        for index in range(1, 40):
            position = stabilizer.update((520, 300), index * 0.04)
        self.assertGreater(position[0], 515)
