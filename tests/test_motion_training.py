"""使用明确标注的人工数据验证训练算法，不生成真实耳机报告。"""

import copy
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from nodlink.control_settings import load_profile, load_sensitivity, save_json_atomic, validate_profile
from nodlink.motion_training import TRAINING_PHASES, TRAINING_SECONDS, fit_training, training_phase_at
from nodlink.motion_validation import quaternion_candidate


def training_frames(reverse_check=False, same_amplitude=False):
    """测试专用：已知横纵轴、轻微静止噪声、三组独立动作。"""
    base = np.array([0.25, -0.10, 0.12, math.sqrt(1 - 0.25**2 - 0.1**2 - 0.12**2)])
    frames = []
    rng = np.random.default_rng(421)
    for index in range(int(TRAINING_SECONDS * 25)):
        elapsed = index / 25
        _, phase, _ = training_phase_at(elapsed)
        target = np.array(phase.target)
        if reverse_check and phase.group == "check":
            target = -target
        if same_amplitude and phase.group == "near":
            target *= 2
        rotation = np.array([-target[0] * 24, target[1] * 20, 0.0]) + rng.normal(0, 0.04, 3)
        degrees = np.linalg.norm(rotation)
        relative = np.append(rotation / degrees * math.sin(math.radians(degrees) / 2), math.cos(math.radians(degrees) / 2))
        vector = base[3] * relative[:3] + relative[3] * base[:3] + np.cross(base[:3], relative[:3])
        frames.append({"elapsed_seconds": elapsed, "orientation_raw": np.rint(vector * 32768).astype(int).tolist()})
    return frames


class MotionTrainingTests(unittest.TestCase):
    def test_fits_known_axes_and_checks_separate_repetition(self):
        result = fit_training(training_frames())
        self.assertTrue(result["passed"], result)
        self.assertLess(result["validation_rmse"], 0.02)
        matrix = np.array(result["profile"]["matrix"])
        np.testing.assert_allclose(matrix @ [-24, 0, 0], [1, 0], atol=0.02)
        self.assertFalse(result["profile"]["user_confirmed"])
        with self.assertRaisesRegex(ValueError, "未经用户确认"):
            validate_profile(result["profile"])

    def test_reverse_holdout_cannot_pass_even_with_good_training_fit(self):
        result = fit_training(training_frames(reverse_check=True))
        self.assertFalse(result["passed"])
        self.assertLess(result["training_rmse"], 0.02)
        self.assertFalse(result["criteria"]["复测方向"])
        self.assertIsNone(result["profile"])

    def test_missing_and_undifferentiated_data_are_rejected(self):
        self.assertFalse(fit_training([])["passed"])
        self.assertFalse(fit_training(training_frames()[:1200])["passed"])
        result = fit_training(training_frames(same_amplitude=True))
        self.assertFalse(result["criteria"]["幅度有区分"])
        self.assertIsNone(result["profile"])

    def test_stream_gap_invalidates_training(self):
        frames = [frame for frame in training_frames() if not 10 <= frame["elapsed_seconds"] < 11]
        result = fit_training(frames)
        self.assertFalse(result["criteria"]["连续数据"])
        self.assertFalse(result["passed"])

    def test_corrupt_settings_and_unconfirmed_profile_cannot_override_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            profile = fit_training(training_frames())["profile"]
            save_json_atomic(folder / "motion-profile.json", profile)
            self.assertIsNone(load_profile(folder)[0])
            profile["user_confirmed"] = True
            save_json_atomic(folder / "motion-profile.json", profile)
            self.assertIsNotNone(load_profile(folder)[0])
            profile["matrix"][0][0] = 99
            save_json_atomic(folder / "motion-profile.json", profile)
            self.assertIsNone(load_profile(folder)[0])
            save_json_atomic(folder / "sensitivity.json", {"overall": 2, "horizontal": 1.2, "vertical": 0.8})
            self.assertEqual(load_sensitivity(folder)["overall"], 2)
            save_json_atomic(folder / "sensitivity.json", {"overall": 999})
            self.assertEqual(load_sensitivity(folder), {"overall": 1, "horizontal": 1, "vertical": 1})

    def test_malformed_orientation_is_rejected(self):
        for value in ([], [1, 2], [1, 2, 3, 4], [float("nan"), 0, 0], [40000, 0, 0], None, "bad"):
            self.assertIsNone(quaternion_candidate(value))


if __name__ == "__main__":
    unittest.main()
