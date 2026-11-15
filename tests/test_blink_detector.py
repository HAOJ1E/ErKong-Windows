"""人工分数检查眨眼时序，不打开摄像头、不调用真实点击。"""

import unittest
from nodlink.blink_detector import BlinkDetector


class BlinkDetectorTests(unittest.TestCase):
    def sequence(self, detector, frames, start=0):
        return [detector.update(left, right, start + index * 0.05, faces)
                for index, (left, right, faces) in enumerate(frames)]

    def test_deliberate_blink_clicks_once_only_after_reopening(self):
        detector = BlinkDetector()
        frames = [(0.1, 0.1, 1)] * 12 + [(0.9, 0.9, 1)] * 9 + [(0.1, 0.1, 1)] * 15
        results = self.sequence(detector, frames)
        self.assertEqual(sum(results), 1)
        self.assertTrue(results[21])
        self.assertFalse(any(results[:21]))

    def test_natural_short_blink_initial_closed_and_long_closure_do_not_click(self):
        for closed_frames in [2, 30]:
            frames = [(0.1, 0.1, 1)] * 12 + [(0.9, 0.9, 1)] * closed_frames + [(0.1, 0.1, 1)] * 12
            self.assertFalse(any(self.sequence(BlinkDetector(), frames)))
        self.assertFalse(any(self.sequence(BlinkDetector(), [(0.9, 0.9, 1)] * 10 + [(0.1, 0.1, 1)] * 12)))

    def test_tracking_loss_multiple_faces_and_wink_cancel_the_pending_click(self):
        for invalid in [(None, None, 0), (0.9, 0.9, 2), (0.9, 0.1, 1), (float('nan'), 0.9, 1)]:
            frames = [(0.1, 0.1, 1)] * 12 + [(0.9, 0.9, 1)] * 9 + [invalid] + [(0.1, 0.1, 1)] * 12
            self.assertFalse(any(self.sequence(BlinkDetector(), frames)))

    def test_stream_gap_and_reversed_time_cancel_pending_click(self):
        for reopen in [0.3, 2]:
            detector = BlinkDetector()
            self.sequence(detector, [(0.1, 0.1, 1)] * 12 + [(0.9, 0.9, 1)] * 9)
            self.assertFalse(detector.update(0.1, 0.1, reopen))

    def test_cooldown_prevents_repeated_clicks(self):
        detector = BlinkDetector()
        frames = ([(0.1, 0.1, 1)] * 12 + [(0.9, 0.9, 1)] * 9 + [(0.1, 0.1, 1)] * 2
                  + [(0.9, 0.9, 1)] * 9 + [(0.1, 0.1, 1)] * 12)
        self.assertEqual(sum(self.sequence(detector, frames)), 1)
