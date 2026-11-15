"""验证 AirPods 姿态到鼠标坐标的映射，不移动真实鼠标。"""

import math
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from nodlink.motion_control import MotionPointerMapper


def multiply_quaternions(left, right):
    """测试用四元数乘法。"""
    vector = left[3] * right[:3] + right[3] * left[:3] + np.cross(left[:3], right[:3])
    scalar = left[3] * right[3] - float(np.dot(left[:3], right[:3]))
    return np.append(vector, scalar)


def as_sensor_values(quaternion):
    """将测试四元数转换为设备候选字段使用的 Q15 XYZ。"""
    if quaternion[3] < 0:
        quaternion = -quaternion
    return np.rint(quaternion[:3] * 32768).astype(int).tolist()


class MotionControlTests(unittest.TestCase):
    def setUp(self):
        self.mapper = MotionPointerMapper(smoothing=1.0, dead_zone_degrees=1.5)
        self.neutral = np.array([0.2, -0.1, 0.15, math.sqrt(1 - 0.2**2 - 0.1**2 - 0.15**2)])
        self.mapper.calibrate([self.neutral.copy() for _ in range(40)], (960, 540), (1920, 1080))

    def rotate(self, axis, degrees):
        axis = np.asarray(axis, dtype=float)
        axis /= np.linalg.norm(axis)
        half_angle = math.radians(degrees) / 2
        turn = np.append(axis * math.sin(half_angle), math.cos(half_angle))
        return as_sensor_values(multiply_quaternions(self.neutral, turn))

    def test_neutral_holds_start_position_and_invalid_frame_is_ignored(self):
        self.assertEqual(self.mapper.update(as_sensor_values(self.neutral)), (960, 540))
        self.assertIsNone(self.mapper.update([0, 0, 0]))

    def test_verified_left_turn_moves_pointer_left(self):
        from nodlink.motion_control import YAW_AXIS

        x, y = self.mapper.update(self.rotate(YAW_AXIS, 15))
        self.assertLess(x, 960)
        self.assertEqual(y, 540)

    def test_verified_down_turn_moves_pointer_down(self):
        from nodlink.motion_control import PITCH_AXIS

        x, y = self.mapper.update(self.rotate(PITCH_AXIS, 12))
        self.assertEqual(x, 960)
        self.assertGreater(y, 540)

    def test_large_movements_stay_inside_screen(self):
        from nodlink.motion_control import YAW_AXIS, PITCH_AXIS

        turned = self.rotate(YAW_AXIS, 80)
        x, y = self.mapper.update(turned)
        self.assertTrue(0 <= x < 1920)
        self.assertTrue(0 <= y < 1080)
        turned = self.rotate(PITCH_AXIS, -80)
        x, y = self.mapper.update(turned)
        self.assertTrue(0 <= x < 1920)
        self.assertTrue(0 <= y < 1080)

    def test_combined_extreme_turns_never_output_a_screen_corner(self):
        corners = {(0, 0), (0, 1079), (1919, 0), (1919, 1079)}
        self.mapper.set_sensitivity(3, 3, 3)
        # 分别从四角附近和中央校准，以覆盖不同起点与组合转动。
        for origin in [(960, 540), (0, 0), (1919, 1079), (100, 900)]:
            self.mapper.calibrate([self.neutral.copy()] * 40, origin, (1920, 1080))
            for axis in ([1, 1, 0], [-1, 1, 0], [1, -1, 0], [-1, -1, 0]):
                with self.subTest(origin=origin, axis=axis):
                    position = self.mapper.update(self.rotate(axis, 80))
                    self.assertNotIn(position, corners)
                    self.assertTrue(0 <= position[0] < 1920 and 0 <= position[1] < 1080)

    def test_corner_guard_preserves_edges_and_handles_small_screens(self):
        from nodlink.pointer_safety import avoid_screen_corners
        for corner, expected in [((0, 0), (8, 8)), ((1919, 0), (1911, 8)),
                                 ((0, 1079), (8, 1071)), ((1919, 1079), (1911, 1071))]:
            self.assertEqual(avoid_screen_corners(*corner, 1920, 1080), expected)
        for edge in [(960, 0), (960, 1079), (0, 540), (1919, 540)]:
            self.assertEqual(avoid_screen_corners(*edge, 1920, 1080), edge)
        self.assertEqual(avoid_screen_corners(0, 0, 3, 3), (1, 1))
        with self.assertRaises(ValueError):
            avoid_screen_corners(0, 0, 2, 2)

    def test_calibration_rejects_moving_head(self):
        from nodlink.motion_control import YAW_AXIS

        left = np.asarray(self.rotate(YAW_AXIS, -20), dtype=float) / 32768
        quaternion_left = np.append(left, math.sqrt(max(0, 1 - float(left @ left))))
        right = np.asarray(self.rotate(YAW_AXIS, 20), dtype=float) / 32768
        quaternion_right = np.append(right, math.sqrt(max(0, 1 - float(right @ right))))
        with self.assertRaisesRegex(ValueError, "校准期间头部移动"):
            self.mapper.calibrate([quaternion_left] * 20 + [quaternion_right] * 20, (960, 540), (1920, 1080))

    def test_calibration_requires_enough_samples(self):
        with self.assertRaisesRegex(ValueError, "校准帧不足"):
            self.mapper.calibrate([self.neutral], (960, 540), (1920, 1080))

    def test_only_user_confirmed_airpods4_report_unlocks_control(self):
        from nodlink.motion_control import latest_verified_validation_report

        valid = {
            "mode": "experimental_live_capture",
            "profile": "service16",
            "head_motion_verified": True,
            "user_confirmed_movements": True,
            "guided_analysis": {"passed": True},
            "device": {"cached_vendor_id": 0x004C, "cached_product_id": "0x2019"},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "motion-validation-20261115-155805-906195.json"
            path.write_text(json.dumps(valid), encoding="utf-8")
            self.assertEqual(latest_verified_validation_report(Path(folder))[0], path)
            valid["user_confirmed_movements"] = False
            path.write_text(json.dumps(valid), encoding="utf-8")
            self.assertIsNone(latest_verified_validation_report(Path(folder)))

    def test_anc_report_does_not_unlock_unverified_model(self):
        from nodlink.motion_control import latest_verified_validation_report

        report = {
            "mode": "experimental_live_capture",
            "profile": "service16",
            "head_motion_verified": True,
            "user_confirmed_movements": True,
            "guided_analysis": {"passed": True},
            "device": {"cached_vendor_id": 0x004C, "cached_product_id": "0x201B"},
        }
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "motion-validation-20261115-155805-906195.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            self.assertIsNone(latest_verified_validation_report(Path(folder)))

    def test_sensitivity_increases_distance_and_axes_are_independent(self):
        from nodlink.motion_control import YAW_AXIS
        raw = self.rotate(YAW_AXIS, -8)
        ordinary = self.mapper.update(raw)
        self.mapper.set_sensitivity(2.0, horizontal=1.5, vertical=0.5)
        sensitive = self.mapper.update(raw)
        self.assertGreater(sensitive[0] - 960, ordinary[0] - 960)
        self.assertEqual(sensitive[1], ordinary[1])
        with self.assertRaises(ValueError):
            self.mapper.set_sensitivity(float("nan"))

    def test_trained_matrix_changes_mapping_and_respects_new_neutral(self):
        from tests.test_motion_training import training_frames
        from nodlink.motion_training import fit_training
        profile = fit_training(training_frames())["profile"]
        profile["user_confirmed"] = True
        trained = MotionPointerMapper(profile=profile)
        trained.calibrate([self.neutral.copy()] * 40, (960, 540), (1920, 1080))
        raw = self.rotate([1, 0, 0], 12)
        for i in range(30):
            position = trained.update(raw, i * 0.04)
        self.assertLess(position[0], 700)
        self.assertAlmostEqual(position[1], 540, delta=3)

    def test_sample_time_does_not_accept_out_of_order_frames(self):
        raw = self.rotate([1, 0, 0], 10)
        self.assertIsNotNone(self.mapper.update(raw, 1.0))
        self.assertIsNone(self.mapper.update(raw, 0.8))


if __name__ == "__main__":
    unittest.main()
