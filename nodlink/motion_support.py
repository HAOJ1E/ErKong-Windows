"""检查 Windows 是否公开了已知的 AirPods AAP 桥接接口。"""

import ctypes
import platform
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from nodlink.motion_control import latest_verified_validation_report
from nodlink.runtime_status import read_pause_policy
from nodlink.wpods_transport import bridge_availability


# 这是设备接口类别标识，不是个人耳机的地址。
# 来源：https://github.com/StarNumber12046/airpods-head-track
MAGICAAP_INTERFACE_GUID = "9eec98bb-3c54-45d4-a843-7900c4635e08"


class GUID(ctypes.Structure):
    """与 Windows GUID 结构保持相同的内存布局。"""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class DeviceInterfaceData(ctypes.Structure):
    """Windows SetupAPI 返回的设备接口描述。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


def count_present_interfaces(interface_guid: str) -> int:
    """只枚举当前存在的接口；不打开设备、不安装或启停驱动。"""
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    guid = GUID.from_buffer_copy(uuid.UUID(interface_guid).bytes_le)

    get_devices = setupapi.SetupDiGetClassDevsW
    get_devices.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    get_devices.restype = wintypes.HANDLE

    enum_interfaces = setupapi.SetupDiEnumDeviceInterfaces
    enum_interfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
        wintypes.DWORD, ctypes.POINTER(DeviceInterfaceData),
    ]
    enum_interfaces.restype = wintypes.BOOL

    destroy_list = setupapi.SetupDiDestroyDeviceInfoList
    destroy_list.argtypes = [wintypes.HANDLE]
    destroy_list.restype = wintypes.BOOL

    # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE：只读取当前设备接口列表。
    device_list = get_devices(ctypes.byref(guid), None, None, 0x02 | 0x10)
    if device_list == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    count = 0
    try:
        while True:
            data = DeviceInterfaceData()
            data.cbSize = ctypes.sizeof(data)
            success = enum_interfaces(device_list, None, ctypes.byref(guid), count, ctypes.byref(data))
            if not success:
                error_code = ctypes.get_last_error()
                if error_code == 259:  # ERROR_NO_MORE_ITEMS：已枚举完毕。
                    return count
                raise ctypes.WinError(error_code)
            count += 1
    finally:
        destroy_list(device_list)


def check_motion_support() -> dict:
    """报告当前访问前提，不将接口存在误报为传感器读取成功。"""
    validation = latest_verified_validation_report(Path(__file__).resolve().parents[1] / "reports")
    locally_verified = validation is not None
    result = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "transport": "Bluetooth Classic AAP/AACP, L2CAP PSM 0x1001",
        "interface_checked": "MagicAAP",
        "interface_count": None,
        "app_motion_backend_implemented": True,
        "sensor_values_read": False,
        "airpods_4_protocol_verified": locally_verified,
        "local_user_confirmed_validation": locally_verified,
        "mouse_control_requires_explicit_start": True,
        "scope": "仅检查已知 MagicAAP 接口；未发现接口不等于证明所有驱动方案均不可行。",
        "sources": [
            "https://github.com/gintmr/NodLink/blob/main/mac/NodLinkBridge/Sources/main.swift",
            "https://github.com/FIocker/aap-head-tracker",
            "https://github.com/StarNumber12046/airpods-head-track",
        ],
    }
    if platform.system() != "Windows":
        result.update(status="unsupported_platform", summary="此检查需要 Windows。")
        return result

    pause = read_pause_policy()
    if pause is not None:
        result.update(status=pause["status"], summary=pause["summary"], detail=pause["detail"],
                      acquisition_paused=True)
        return result

    try:
        count = count_present_interfaces(MAGICAAP_INTERFACE_GUID)
    except OSError as error:
        result.update(
            status="query_failed",
            summary="传感器通道检查失败，无法判断接口是否存在。",
            error_code=getattr(error, "winerror", None),
            error_type=type(error).__name__,
        )
        return result

    result["interface_count"] = count
    bridge = bridge_availability()
    result["wpods_bridge"] = bridge
    result["experimental_python_transport_prepared"] = True
    if bridge["status"] == "restart_required":
        result.update(
            status="bridge_restart_required",
            summary="WPods 桥接已安装；首次启用前需要重启 Windows。",
            detail="安装器已延迟启动服务。重启后再验证真实运动数据，当前传感器读取未验证。",
        )
        return result
    if bridge["status"] == "pipe_available_unverified":
        result.update(
            status="experimental_bridge_available",
            summary=(
                "WPods AAP 桥接管道可用；本机 AirPods 4 动作验证已确认。"
                if locally_verified
                else "WPods AAP 桥接管道可用；仍需本机真实动作验证。"
            ),
            detail="本页未打开实时传感器流。鼠标控制需在独立窗口中手动启动并重新校准。",
        )
        return result
    if bridge.get("installed"):
        result.update(
            status="experimental_bridge_not_ready",
            summary="WPods 桥接已安装，服务或 AAP 管道尚未就绪。",
            detail="请检查桥接服务状态；当前传感器数据仍未读取、未验证。",
        )
        return result
    if count == 0:
        result.update(
            status="aap_interface_missing",
            summary="缺少已知的 AirPods 运动数据通道：未发现 MagicAAP 设备接口。",
            detail="可能未安装对应驱动，或驱动未绑定当前耳机。普通音频连接与 BLE 扫描不能代替该接口。",
        )
    else:
        result.update(
            status="interface_found_backend_missing",
            summary=f"发现 {count} 个 MagicAAP 接口；当前运行仍使用独立 WPods AAP 桥接。",
            detail="本页只枚举设备接口，不读取实时数据。请在独立控制窗口中显式启动。",
        )
    return result
