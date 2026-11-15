"""读取本机基础系统和硬件信息。"""

import ctypes
import platform
import struct
import sys


class MEMORYSTATUSEX(ctypes.Structure):
    """Windows 内存状态结构。"""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _get_memory_text() -> str:
    """读取物理内存容量；非 Windows 系统显示为不可用。"""
    if platform.system() != "Windows":
        return "当前系统不支持读取"

    memory = MEMORYSTATUSEX()
    memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    success = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    if not success:
        return "读取失败"

    total_gb = memory.ullTotalPhys / (1024**3)
    available_gb = memory.ullAvailPhys / (1024**3)
    return f"{total_gb:.1f} GB（可用 {available_gb:.1f} GB）"


def collect_system_info() -> dict[str, str]:
    """整理桌面检测页要显示的基础信息。"""
    system_name = platform.system()
    release = platform.release()
    if system_name == "Windows" and release == "11":
        operating_system = f"Windows 11（版本 {platform.version()}）"
    else:
        operating_system = f"{system_name} {release}（版本 {platform.version()}）"

    return {
        "操作系统": operating_system,
        "Python": sys.version.split()[0],
        "程序位数": f"{struct.calcsize('P') * 8} 位",
        "处理器": platform.processor() or "系统未提供型号",
        "逻辑处理器": str(__import__("os").cpu_count() or "未知"),
        "物理内存": _get_memory_text(),
    }