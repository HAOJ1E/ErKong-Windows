"""将经过本机动作验证的头部旋转映射为屏幕坐标。"""

import json
from pathlib import Path

import numpy as np

from nodlink.control_settings import validate_profile
from nodlink.motion_validation import quaternion_candidate, rotation_vector
from nodlink.pointer_safety import avoid_screen_corners


# 这些轴由 reports/motion-validation-20261115-155805-906195.json 中
# 用户确认的左右、抬头和低头动作拟合而来，不是 AirPods 官方坐标定义。
YAW_AXIS = np.array([0.67904454, -0.70874675, -0.19124996], dtype=float)
PITCH_AXIS = np.array([0.10069561, -0.16813393, 0.98060766], dtype=float)


def latest_verified_validation_report(reports_folder: Path) -> tuple[Path, dict] | None:
    """只接受用户确认过的本机普通 AirPods 4 动作验证报告。"""
    for path in sorted(reports_folder.glob("motion-validation-*.json"), reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        device = report.get("device", {})
        analysis = report.get("guided_analysis", {})
        if (
            report.get("mode") == "experimental_live_capture"
            and report.get("profile") == "service16"
            and report.get("head_motion_verified") is True
            and report.get("user_confirmed_movements") is True
            and analysis.get("passed") is True
            and device.get("cached_vendor_id") == 0x004C
            and device.get("cached_product_id") == "0x2019"
        ):
            return path, report
    return None


def angle_from_axis(rotation: np.ndarray, axis: np.ndarray) -> float:
    """取三维轴角旋转在指定头部动作轴上的投影角度。"""
    return float(np.dot(rotation, axis))


def median_quaternion(samples: list[np.ndarray], minimum_samples: int = 30) -> np.ndarray:
    """从静止校准帧求稳健中位姿态，并拒绝明显移动中的校准。"""
    if len(samples) < minimum_samples:
        raise ValueError(f"校准帧不足：需要至少 {minimum_samples} 帧")

    values = np.asarray(samples, dtype=float)
    reference = np.median(values, axis=0)
    length = float(np.linalg.norm(reference))
    if length < 1e-6:
        raise ValueError("无法计算正视校准姿态")
    reference /= length

    # 90% 的样本应在基准姿态 8 度范围内，避免用户在校准时持续转头。
    deviations = [float(np.linalg.norm(rotation_vector(reference, value))) for value in values]
    if float(np.percentile(deviations, 90)) > 8.0:
        raise ValueError("校准期间头部移动较多，请正视屏幕后重试")
    return reference


class MotionPointerMapper:
    """使用正视基准、死区和平滑，把头部姿态映射为鼠标屏幕位置。"""

    def __init__(
        self,
        yaw_limit_degrees: float = 25.0,
        pitch_limit_degrees: float = 18.0,
        dead_zone_degrees: float = 1.5,
        smoothing: float = 0.28,
        profile: dict | None = None,
    ) -> None:
        if yaw_limit_degrees <= 0 or pitch_limit_degrees <= 0:
            raise ValueError("头部控制范围必须大于 0")
        if not 0.0 < smoothing <= 1.0:
            raise ValueError("平滑系数必须在 0 到 1 之间")
        self.yaw_limit = float(yaw_limit_degrees)
        self.pitch_limit = float(pitch_limit_degrees)
        self.dead_zone = float(dead_zone_degrees)
        self.smoothing = float(smoothing)
        self.reference: np.ndarray | None = None
        self.origin: tuple[int, int] | None = None
        self.screen_size: tuple[int, int] | None = None
        self.filtered_angles = np.zeros(2, dtype=float)
        self.matrix = np.array([-YAW_AXIS / self.yaw_limit, PITCH_AXIS / self.pitch_limit])
        self.normalized_dead_zone = np.array([self.dead_zone / self.yaw_limit, self.dead_zone / self.pitch_limit])
        self.smoothing_seconds = None
        if profile is not None:
            validated = validate_profile(profile)
            self.matrix = np.array(validated["matrix"], dtype=float)
            self.normalized_dead_zone = np.array(validated["dead_zone"], dtype=float)
            self.smoothing_seconds = float(validated["smoothing_seconds"])
        self.sensitivity = np.ones(2)
        self._previous_time = None
        self._previous_target = np.zeros(2)

    def set_sensitivity(self, overall: float, horizontal: float = 1.0, vertical: float = 1.0) -> None:
        """总体与横纵倍率相乘，允许用户在控制期间调节。"""
        values = np.asarray([overall, horizontal, vertical], dtype=float)
        if not np.isfinite(values).all() or np.any(values < 0.25) or np.any(values > 3):
            raise ValueError("灵敏度倍率需在 0.25 到 3 之间")
        self.sensitivity = values[0] * values[1:]

    def calibrate(
        self,
        samples: list[np.ndarray],
        pointer_position: tuple[int, int],
        screen_size: tuple[int, int],
    ) -> np.ndarray:
        """设定正视姿态、当前鼠标位置与屏幕尺寸作为本次控制基准。"""
        width, height = screen_size
        if width < 3 or height < 3:
            raise ValueError("屏幕尺寸无效")
        self.reference = median_quaternion(samples)
        self.origin = (
            min(max(int(pointer_position[0]), 0), width - 1),
            min(max(int(pointer_position[1]), 0), height - 1),
        )
        self.screen_size = (int(width), int(height))
        self.filtered_angles[:] = 0.0
        self._previous_time = None
        self._previous_target[:] = 0.0
        return self.reference.copy()

    @staticmethod
    def _outside_dead_zone(value: float, dead_zone: float) -> float:
        """死区外保留连续变化，死区内压到零以减轻指针抖动。"""
        magnitude = abs(value)
        if magnitude <= dead_zone:
            return 0.0
        return (magnitude - dead_zone) * (1.0 if value >= 0 else -1.0)

    def update(self, orientation_raw: list[int], sample_time: float | None = None) -> tuple[int, int] | None:
        """处理一帧传感器数据；无效帧或未校准时不输出鼠标位置。"""
        if self.reference is None or self.origin is None or self.screen_size is None:
            return None
        quaternion = quaternion_candidate(orientation_raw)
        if quaternion is None:
            return None

        rotation = rotation_vector(self.reference, quaternion)
        target = self.matrix @ rotation
        interval = 0.04
        if sample_time is not None:
            if not np.isfinite(sample_time):
                return None
            if self._previous_time is not None:
                if sample_time <= self._previous_time:
                    return None
                interval = float(np.clip(sample_time - self._previous_time, 0.005, 0.2))
            self._previous_time = sample_time
        if self.smoothing_seconds is None:
            alpha = 1.0 - (1.0 - self.smoothing) ** (interval * 25)
        else:
            # 慢动作按训练噪声平滑，快动作缩短时间常数，兼顾抖动与响应。
            speed = min(float(np.linalg.norm(target - self._previous_target)) / interval, 3.0)
            time_constant = self.smoothing_seconds / (1 + speed * 1.5)
            alpha = 1 - np.exp(-interval / time_constant)
        self._previous_target = target.copy()
        self.filtered_angles += alpha * (target - self.filtered_angles)
        normalized = np.array([
            self._outside_dead_zone(value, zone) / (1.0 - zone)
            for value, zone in zip(self.filtered_angles, self.normalized_dead_zone)
        ])
        horizontal, vertical = np.clip(normalized * self.sensitivity, -1.0, 1.0)

        width, height = self.screen_size
        origin_x, origin_y = self.origin
        # 分别使用当前起点到左右/上下边缘的距离，避免非居中起点过早碰边。
        horizontal_room = width - 1 - origin_x if horizontal >= 0 else origin_x
        vertical_room = height - 1 - origin_y if vertical >= 0 else origin_y
        x = round(origin_x + horizontal * horizontal_room)
        y = round(origin_y + vertical * vertical_room)
        return avoid_screen_corners(x, y, width, height)
