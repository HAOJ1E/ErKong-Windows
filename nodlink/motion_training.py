"""引导多幅度动作训练；拟合与独立复测均通过后才生成候选配置。"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from nodlink.motion_validation import quaternion_candidate, rotation_vector


@dataclass(frozen=True)
class TrainingPhase:
    key: str
    instruction: str
    seconds: float
    group: str
    target: tuple[float, float] = (0.0, 0.0)


TRAINING_PHASES = [TrainingPhase("neutral_start", "正视屏幕，保持自然静止", 6, "neutral")]
for group, amplitude, description in (
    ("near", 0.5, "小幅转动：约舒适幅度的一半"),
    ("far", 1.0, "舒适范围转动：不要用力或转到极限"),
    ("check", 0.5, "独立复测：重复刚才的小幅动作"),
):
    for direction, instruction, target in (
        ("left", "向左转头并保持", (-amplitude, 0.0)),
        ("right", "向右转头并保持", (amplitude, 0.0)),
        ("down", "轻轻低头并保持", (0.0, amplitude)),
        ("up", "轻轻抬头并保持", (0.0, -amplitude)),
    ):
        TRAINING_PHASES.append(TrainingPhase(f"{group}_{direction}", f"{description}\n{instruction}", 5, group, target))
        TRAINING_PHASES.append(TrainingPhase(f"neutral_{group}_{direction}", "回到正视，保持自然静止", 3, "neutral"))

TRAINING_SECONDS = sum(phase.seconds for phase in TRAINING_PHASES)


def training_phase_at(elapsed: float) -> tuple[int, TrainingPhase | None, float]:
    """按采集时间寻找当前提示；完整训练 102 秒。"""
    start = 0.0
    for index, phase in enumerate(TRAINING_PHASES):
        if elapsed < start + phase.seconds:
            return index, phase, start + phase.seconds - elapsed
        start += phase.seconds
    return len(TRAINING_PHASES), None, 0.0


def fit_training(frames: list[dict]) -> dict:
    """用各阶段末尾稳定数据训练；check 阶段从不参与模型拟合。"""
    result = {"passed": False, "profile": None, "reason": "训练数据不足", "criteria": {}}
    samples = []
    for frame in frames:
        quaternion = quaternion_candidate(frame.get("orientation_raw", []))
        try:
            elapsed = float(frame["elapsed_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if quaternion is not None and np.isfinite(elapsed) and 0 <= elapsed <= TRAINING_SECONDS + 1:
            samples.append((elapsed, quaternion))
    result["valid_frames"] = len(samples)
    if len(samples) < 100:
        return result
    times = np.array([sample[0] for sample in samples])
    if np.any(np.diff(times) < 0):
        return {**result, "reason": "帧时间倒退，不能进行训练"}
    span = times[-1] - times[0]
    maximum_gap = float(np.max(np.diff(times)))
    rate = (len(times) - 1) / span if span > 0 else 0
    reference_samples = [q for elapsed, q in samples if 4 <= elapsed < 6]
    if len(reference_samples) < 20:
        return {**result, "reason": "初始正视阶段样本不足"}
    reference = np.median(reference_samples, axis=0)
    reference /= np.linalg.norm(reference)
    rotations = np.array([rotation_vector(reference, q) for _, q in samples])
    medians, spreads, stable_frames, counts = {}, {}, {}, {}
    start = 0.0
    for phase in TRAINING_PHASES:
        tail = 2.0 if phase.group != "neutral" else 1.5
        values = rotations[(times >= start + phase.seconds - tail) & (times < start + phase.seconds)]
        counts[phase.key] = len(values)
        if len(values) >= 20:
            median = np.median(values, axis=0)
            medians[phase.key] = median
            stable_frames[phase.key] = values - median
            spreads[phase.key] = float(np.percentile(np.linalg.norm(values - median, axis=1), 90))
        start += phase.seconds
    result.update(phase_sample_counts=counts, rate_hz=round(rate, 2), maximum_gap_seconds=round(maximum_gap, 4))
    if len(medians) != len(TRAINING_PHASES):
        return {**result, "reason": "采集提前结束或某些阶段缺帧，请完整重做训练"}

    # 用每个动作之后的回正值去除缓慢漂移，避免把回正偏差学成方向。
    movement = {phase.key: medians[phase.key] - medians[f"neutral_{phase.key}"]
                for phase in TRAINING_PHASES if phase.group != "neutral"}
    amplitudes = {key: float(np.linalg.norm(value)) for key, value in movement.items()}
    neutral_drift = max(float(np.linalg.norm(value)) for key, value in medians.items() if key.startswith("neutral"))
    criteria = {
        "连续数据": span >= TRAINING_SECONDS - 1 and rate >= 15 and maximum_gap < 0.6,
        "动作稳定": max(spreads.values()) < 4.0,
        "回正稳定": neutral_drift < 10.0,
        "舒适幅度": all(6 <= amplitudes[f"far_{d}"] <= 55 for d in ("left", "right", "down", "up")),
        "幅度有区分": all(2.5 <= amplitudes[f"near_{d}"] < 0.85 * amplitudes[f"far_{d}"]
                           for d in ("left", "right", "down", "up")),
    }
    result.update(criteria=criteria, movement_amplitudes_degrees=amplitudes,
                  maximum_neutral_drift_degrees=round(neutral_drift, 3))

    training_phases = [phase for phase in TRAINING_PHASES if phase.group in ("near", "far")]
    features = np.array([movement[phase.key] for phase in training_phases])
    targets = np.array([phase.target for phase in training_phases])
    # 限定在主要的二维动作平面，避免从第三轴的少量噪声学出极大增益。
    _, singular_values, axes = np.linalg.svd(features, full_matrices=False)
    if singular_values[1] < 1 or singular_values[0] / singular_values[1] > 10:
        return {**result, "reason": "横纵动作无法区分，请按提示分别左右转头、低头和抬头"}
    basis = axes[:2].T
    projected = features @ basis
    weights = np.linalg.solve(projected.T @ projected + np.eye(2) * 0.05, projected.T @ targets)
    matrix = (basis @ weights).T
    training_error = float(np.sqrt(np.mean((features @ matrix.T - targets) ** 2)))

    checks = [phase for phase in TRAINING_PHASES if phase.group == "check"]
    predictions = np.array([movement[phase.key] @ matrix.T for phase in checks])
    expected = np.array([phase.target for phase in checks])
    validation_error = float(np.sqrt(np.mean((predictions - expected) ** 2)))
    correct_signs = all(float(prediction @ target) > 0.10 for prediction, target in zip(predictions, expected))
    cross_axis_error = max(abs(prediction[1] if target[0] else prediction[0])
                           for prediction, target in zip(predictions, expected))
    criteria.update({
        "拟合误差": training_error < 0.18,
        "独立复测": validation_error < 0.20 and float(np.max(np.abs(predictions - expected))) < 0.35,
        "复测方向": bool(correct_signs and cross_axis_error < 0.20),
    })
    # 静止抖动去掉每阶段的均值后统计，避免把漂移当作随机噪声。
    stationary = np.concatenate([value for key, value in stable_frames.items() if key.startswith("neutral")])
    jitter = np.percentile(np.abs(stationary @ matrix.T), 95, axis=0)
    dead_zone = np.clip(jitter * 1.5 + 0.008, 0.015, 0.12)
    criteria["静止抖动"] = float(np.max(jitter)) < 0.08
    result.update(
        passed=all(criteria.values()),
        training_rmse=round(training_error, 4),
        validation_rmse=round(validation_error, 4),
        validation_predictions=predictions.tolist(),
        stationary_jitter=jitter.tolist(),
        phase_medians_degrees={key: value.tolist() for key, value in medians.items()},
        error_units="归一化舒适控制范围；不是屏幕像素定位精度",
    )
    if not result["passed"]:
        failed = "、".join(key for key, passed in criteria.items() if not passed)
        result["reason"] = f"未通过：{failed}。原来的控制配置保持有效，请重做训练。"
        return result
    result["reason"] = "拟合与独立复测通过，请确认确实完成了全部提示动作后应用"
    result["profile"] = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "device_product_id": "0x2019",
        "transport_profile": "service16",
        "matrix": matrix.tolist(),
        "dead_zone": dead_zone.tolist(),
        "smoothing_seconds": float(np.clip(0.07 + np.max(jitter) * 2, 0.07, 0.18)),
        "validation_rmse": validation_error,
        "user_confirmed": False,
    }
    return result
