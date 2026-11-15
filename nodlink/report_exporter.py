"""生成并导出耳控本地设备检测报告。"""

import json
from datetime import datetime
from pathlib import Path


SENSOR_STATUS = "未读取、未验证"
SENSOR_NOTE = "当前程序不读取 AirPods 4 头部运动传感器数据。"


def build_report(
    system_info: dict[str, str],
    bluetooth_devices: list[dict[str, str]],
    bluetooth_error: str | None,
    logs: list[str],
    ble_probe_result: dict | None = None,
    motion_support: dict | None = None,
) -> dict:
    """整理报告内容，明确记录蓝牙与传感器检测范围。"""
    return {
        "report_name": "耳控 Windows 设备检测报告",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "system_info": system_info,
        "bluetooth": {
            "source": "Windows Get-PnpDevice -Class Bluetooth",
            "meaning": "Windows 已登记的设备列表，不代表正在进行附近设备扫描。",
            "registered_device_count": len(bluetooth_devices),
            "read_error": bluetooth_error,
            "devices": bluetooth_devices,
        },
        "airpods_4_sensor": {
            "status": SENSOR_STATUS,
            "note": SENSOR_NOTE,
        },
        "ble_probe": ble_probe_result or {
            "status": "not_run",
            "sensor_values_read": False,
            "note": "尚未运行 AirPods BLE 广播和 GATT 服务结构探测。",
        },
        "motion_support": motion_support or {"status": "not_checked", "sensor_values_read": False},
        "runtime_logs": logs,
    }


def export_report(
    file_path: str | Path,
    system_info: dict[str, str],
    bluetooth_devices: list[dict[str, str]],
    bluetooth_error: str | None,
    logs: list[str],
    ble_probe_result: dict | None = None,
    motion_support: dict | None = None,
) -> Path:
    """以 UTF-8 JSON 格式保存报告，并返回保存位置。"""
    output_path = Path(file_path)
    report = build_report(
        system_info,
        bluetooth_devices,
        bluetooth_error,
        logs,
        ble_probe_result,
        motion_support,
    )

    with output_path.open("w", encoding="utf-8", newline="\n") as report_file:
        json.dump(report, report_file, ensure_ascii=False, indent=2)
        report_file.write("\n")

    return output_path
