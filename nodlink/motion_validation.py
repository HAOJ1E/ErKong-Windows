"""对真实采集的候选四元数做动作分段分析；不自动宣称动作已验证。"""

import math
import numpy as np


# 每阶段留出转头时间，统计时使用该阶段最后两秒。
PHASES = [
    ("neutral_start", "保持正视，头部不动", 6),
    ("left", "缓慢向左转头，并保持", 6),
    ("neutral_left", "回到正视，并保持", 6),
    ("right", "缓慢向右转头，并保持", 6),
    ("neutral_right", "回到正视，并保持", 6),
    ("down", "轻轻低头，并保持", 6),
    ("neutral_down", "回到正视，并保持", 6),
    ("up", "轻轻抬头，并保持", 6),
    ("neutral_final", "回到正视，并保持", 6),
]


def quaternion_candidate(raw: list[int]) -> np.ndarray | None:
    """按公开参考布局解释 Q15 XYZ；当前只用于实验验证预览。"""
    try:
        values = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        return None
    if values.shape != (3,) or not np.isfinite(values).all() or np.any(values < -32768) or np.any(values > 32767):
        return None
    xyz = values / 32768.0
    length2 = float(xyz @ xyz)
    if not 0.01 <= length2 <= 1.01:
        return None  # 排除初始化零帧和不可能的向量长度。
    quaternion = np.append(xyz, math.sqrt(max(0.0, 1.0 - length2)))
    return quaternion / np.linalg.norm(quaternion)


def rotation_vector(reference: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """计算相对于基准的轴角旋转向量，单位为度；轴名暂不映射到头部方向。"""
    vector = reference[3] * quaternion[:3] - quaternion[3] * reference[:3] - np.cross(reference[:3], quaternion[:3])
    scalar = float(reference @ quaternion)
    if scalar < 0:
        vector, scalar = -vector, -scalar
    length = float(np.linalg.norm(vector))
    if length < 1e-10:
        return np.zeros(3)
    return vector * (2.0 * math.atan2(length, scalar) * 180.0 / math.pi / length)


def phase_at(elapsed: float) -> tuple[int, str, str, float]:
    start = 0.0
    for index, (key, instruction, duration) in enumerate(PHASES):
        if elapsed < start + duration:
            return index, key, instruction, start + duration - elapsed
        start += duration
    return len(PHASES), "complete", "动作采集结束", 0.0


def analyze_guided_capture(frames: list[dict]) -> dict:
    """判断连续性、回正和成对反向动作；等待用户确认确实按提示完成。"""
    samples = []
    for frame in frames:
        quaternion = quaternion_candidate(frame["orientation_raw"])
        if quaternion is not None:
            samples.append((float(frame["elapsed_seconds"]), quaternion))
    result = {"passed": False, "valid_frames": len(samples), "user_confirmation_required": True}
    if not samples:
        return {**result, "reason": "没有有效的候选四元数帧"}
    times = np.array([item[0] for item in samples])
    neutral_end = float(PHASES[0][2])
    calibration = [quaternion for elapsed, quaternion in samples if neutral_end - 2.0 <= elapsed < neutral_end]
    if len(calibration) < 10:
        return {**result, "reason": "正视阶段的有效样本不足"}
    reference = np.mean(calibration, axis=0)
    reference /= np.linalg.norm(reference)
    rotations = np.array([rotation_vector(reference, quaternion) for _, quaternion in samples])
    phase_medians = {}
    phase_counts = {}
    start = 0.0
    for key, _, duration in PHASES:
        mask = (times >= start + duration - 2.0) & (times < start + duration)
        phase_counts[key] = int(mask.sum())
        if mask.sum() >= 10:
            phase_medians[key] = np.median(rotations[mask], axis=0)
        start += duration
    result["phase_sample_counts"] = phase_counts
    if len(phase_medians) != len(PHASES):
        return {**result, "reason": "部分动作阶段样本不足或采集提前结束"}
    span = times[-1] - times[0]
    rate = (len(times) - 1) / span if span else 0.0
    max_gap = float(np.diff(times).max()) if len(times) > 1 else 0.0
    left = phase_medians["left"] - phase_medians["neutral_left"]
    right = phase_medians["right"] - phase_medians["neutral_right"]
    down = phase_medians["down"] - phase_medians["neutral_down"]
    up = phase_medians["up"] - phase_medians["neutral_final"]

    def cosine(a, b):
        length = np.linalg.norm(a) * np.linalg.norm(b)
        return float(a @ b / length) if length > 1e-6 else 1.0

    neutral_error = max(float(np.linalg.norm(value)) for key, value in phase_medians.items() if key.startswith("neutral"))
    amplitudes = {key: float(np.linalg.norm(value)) for key, value in (("left", left), ("right", right), ("down", down), ("up", up))}
    criteria = {
        "continuous_stream": span >= sum(item[2] for item in PHASES) - 4.0 and rate >= 15.0 and max_gap < 0.6,
        "movement_amplitude": min(amplitudes.values()) >= 5.0,
        "left_right_opposite": cosine(left, right) < -0.5,
        "down_up_opposite": cosine(down, up) < -0.5,
        "directions_distinct": abs(cosine(left - right, down - up)) < 0.7,
        "returns_neutral": neutral_error < 12.0,
    }
    result.update(
        passed=all(criteria.values()),
        criteria=criteria,
        candidate_rate_hz=round(rate, 2),
        maximum_gap_seconds=round(max_gap, 4),
        phase_medians_degrees={key: value.tolist() for key, value in phase_medians.items()},
        movement_amplitudes_degrees=amplitudes,
        maximum_neutral_error_degrees=neutral_error,
        reason="需结合用户动作确认，才能认定头部运动对应关系" if all(criteria.values()) else "部分动作对应条件未通过，请检查动作执行和候选解析布局",
    )
    return result
