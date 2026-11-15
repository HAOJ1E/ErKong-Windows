"""保存本地灵敏度与已确认训练配置，不读取或修改系统设置。"""

import json
from pathlib import Path
import uuid

import numpy as np


SETTINGS_FOLDER = Path(__file__).resolve().parents[1] / "settings"


def validate_profile(profile: dict, require_confirmation: bool = True) -> dict:
    """拒绝损坏的文件与异常增益，返回可用于控制的训练参数。"""
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("训练配置格式不兼容")
    if profile.get("device_product_id") != "0x2019" or profile.get("transport_profile") != "service16":
        raise ValueError("训练配置与已验证的普通 AirPods 4 不匹配")
    if require_confirmation and profile.get("user_confirmed") is not True:
        raise ValueError("训练配置尚未经用户确认")
    matrix = np.asarray(profile.get("matrix"), dtype=float)
    dead_zone = np.asarray(profile.get("dead_zone"), dtype=float)
    smoothing = float(profile.get("smoothing_seconds", 0))
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all():
        raise ValueError("训练方向参数无效")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms < 0.005) or np.any(norms > 0.5) or np.linalg.matrix_rank(matrix) != 2:
        raise ValueError("训练增益超出有效范围")
    if dead_zone.shape != (2,) or not np.isfinite(dead_zone).all() or np.any(dead_zone < 0.005) or np.any(dead_zone > 0.15):
        raise ValueError("训练死区参数无效")
    if not np.isfinite(smoothing) or not 0.02 <= smoothing <= 0.3:
        raise ValueError("训练平滑参数无效")
    return dict(profile)


def save_json_atomic(path: Path, value: dict) -> None:
    """先写临时文件，再替换，避免写入中断破坏原配置。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_profile(folder: Path = SETTINGS_FOLDER) -> tuple[dict | None, str]:
    try:
        profile = json.loads((folder / "motion-profile.json").read_text(encoding="utf-8"))
        return validate_profile(profile), "已加载个人训练配置"
    except FileNotFoundError:
        return None, "使用初始控制配置；可训练个人配置"
    except (OSError, ValueError, TypeError, OverflowError) as error:
        return None, f"个人配置无法加载，使用初始配置：{error}"


def load_sensitivity(folder: Path = SETTINGS_FOLDER) -> dict:
    defaults = {"overall": 1.0, "horizontal": 1.0, "vertical": 1.0}
    try:
        data = json.loads((folder / "sensitivity.json").read_text(encoding="utf-8"))
        for key in defaults:
            value = float(data[key])
            if not np.isfinite(value) or not 0.25 <= value <= 3.0:
                return defaults
        return {key: float(data[key]) for key in defaults}
    except (OSError, ValueError, TypeError, KeyError):
        return defaults


def load_stability(folder: Path = SETTINGS_FOLDER) -> int:
    try:
        data = json.loads((folder / "pointer-stability.json").read_text(encoding="utf-8"))
        value = data["strength"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            return 65
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return 65
