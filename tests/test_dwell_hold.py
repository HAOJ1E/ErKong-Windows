"""悬停动作时序检查；不按下真实鼠标。"""

import unittest
from nodlink.dwell_hold import DwellHold


class DwellHoldTests(unittest.TestCase):
    def advance(self, dwell, start, seconds, position=(500, 300), stable=True):
        events = []
        for step in range(round(seconds / 0.05) + 1):
            event = dwell.update(position, start + step * 0.05, stable)
            if event:
                events.append(event)
        return events

    def test_exact_five_seconds_press_once_with_full_circle(self):
        dwell = DwellHold()
        self.assertEqual(self.advance(dwell, 0, 4.95), [])
        self.assertLess(dwell.progress, 1)
        self.assertEqual(dwell.update((500, 300), 5), "press")
        self.assertEqual(dwell.progress, 1)
        self.assertEqual(self.advance(dwell, 5.05, 5), [])

    def test_drag_requires_moving_then_five_second_hover_to_release(self):
        dwell = DwellHold()
        self.advance(dwell, 0, 5)
        dwell.applied(True, (500, 300))
        self.assertEqual(self.advance(dwell, 5.05, 6), [])
        self.assertEqual(self.advance(dwell, 11.1, 5, position=(700, 300)), ["release"])
        dwell.applied(False, (700, 300))
        self.assertEqual(self.advance(dwell, 16.15, 6, position=(700, 300)), [])

    def test_small_jitter_keeps_fixed_anchor_but_slow_drift_resets_timer(self):
        dwell = DwellHold(radius=10)
        self.advance(dwell, 0, 2)
        self.assertEqual(dwell.anchor, (500, 300))
        self.advance(dwell, 2.05, 1, position=(507, 300))
        self.assertEqual(dwell.anchor, (500, 300))
        dwell.update((514, 300), 3.1)
        self.assertEqual(dwell.progress, 0)
        self.assertEqual(dwell.anchor, (514, 300))

    def test_gap_and_unstable_motion_never_complete_old_countdown(self):
        for case in ("gap", "moving"):
            dwell = DwellHold()
            self.advance(dwell, 0, 4.9)
            if case == "gap":
                self.assertIsNone(dwell.update((500, 300), 6))
            else:
                dwell.update((500, 300), 4.95, stable=False)
                self.assertIsNone(dwell.update((500, 300), 5))
            self.assertEqual(dwell.progress, 0)

    def test_cancel_clears_hold_and_countdown(self):
        dwell = DwellHold()
        dwell.applied(True, (500, 300))
        dwell.cancel()
        self.assertFalse(dwell.held)
        self.assertEqual(dwell.progress, 0)
        self.assertIsNone(dwell.anchor)
