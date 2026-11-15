"""仅验证数学和动作分析边界；人工字节不写入真实采集报告。"""

import math
import struct
import unittest
import numpy as np

from nodlink.aap_motion import candidate_motion_fields
from nodlink.motion_validation import PHASES, analyze_guided_capture, phase_at, rotation_vector


def _varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


class MotionAnalysisTests(unittest.TestCase):
    def test_protobuf_sequence_length_does_not_shift_sensor_fields(self):
        for sequence in (1, 130, 17000, 2**32):
            payload = bytearray(58)
            payload[0], payload[9] = 1, 3
            struct.pack_into("<hhh", payload, 20, -26000, 4000, 5000)
            command = b"\x08\x10\x1a\x3a" + payload
            body = b"\x08" + _varint(sequence) + b"\x10\x03\x3a\x3e" + command
            packet = bytes.fromhex("04000400170000001000") + struct.pack("<H", len(body)) + body
            fields = candidate_motion_fields(packet)
            self.assertEqual(fields["orientation_raw"], [-26000, 4000, 5000])
            self.assertEqual(fields["service"], 16)
            self.assertIsNone(candidate_motion_fields(packet[:-1]))

    def test_axis_angle_math(self):
        identity = np.array([0., 0., 0., 1.])
        rotation = np.array([math.sqrt(0.5), 0., 0., math.sqrt(0.5)])
        np.testing.assert_allclose(rotation_vector(identity, rotation), [90., 0., 0.], atol=1e-8)
        np.testing.assert_allclose(rotation_vector(rotation, rotation), [0., 0., 0.], atol=1e-8)

    def test_stationary_frames_cannot_pass_guided_validation(self):
        total_samples = int(sum(item[2] for item in PHASES) * 25)
        frames = [{"elapsed_seconds": i / 25, "orientation_raw": [16000, 0, 0]} for i in range(total_samples)]
        result = analyze_guided_capture(frames)
        self.assertFalse(result["passed"])
        self.assertFalse(result["criteria"]["movement_amplitude"])

    def test_incomplete_capture_cannot_pass(self):
        self.assertFalse(analyze_guided_capture([])["passed"])
        frames = [{"elapsed_seconds": i / 25, "orientation_raw": [16000, 0, 0]} for i in range(100)]
        self.assertFalse(analyze_guided_capture(frames)["passed"])

    def test_synthetic_analysis_still_requires_human_confirmation(self):
        # 独立数学输入，仅验证判定器；不会被标记为真实头部数据。
        frames = []
        phase_rotations = {"left": (1, -20), "right": (1, 20), "down": (2, 15), "up": (2, -15)}
        base = np.array([0.5, 0., 0., math.sqrt(0.75)])
        for index in range(int(sum(item[2] for item in PHASES) * 25)):
            elapsed = index / 25
            _, phase, _, _ = phase_at(elapsed)
            axis, degrees = phase_rotations.get(phase, (1, 0))
            relative = np.zeros(4)
            relative[axis] = math.sin(math.radians(degrees) / 2)
            relative[3] = math.cos(math.radians(degrees) / 2)
            vector = base[3] * relative[:3] + relative[3] * base[:3] + np.cross(base[:3], relative[:3])
            frames.append({"elapsed_seconds": elapsed, "orientation_raw": np.rint(vector * 32768).astype(int).tolist()})
        result = analyze_guided_capture(frames)
        self.assertTrue(result["passed"])
        self.assertTrue(result["user_confirmation_required"])
        self.assertNotIn("head_motion_verified", result)
        self.assertEqual(len(PHASES), 9)


if __name__ == "__main__":
    unittest.main()
