"""读取 Windows 已登记的蓝牙设备，不执行空中 BLE 扫描。"""

import json
import platform
import shutil
import subprocess


def list_registered_bluetooth_devices() -> tuple[list[dict[str, str]], str | None]:
    """读取设备管理器中的蓝牙设备名称和状态。"""
    if platform.system() != "Windows":
        return [], "蓝牙设备列表目前仅支持 Windows。"

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        return [], "找不到 PowerShell，无法读取 Windows 蓝牙设备列表。"

    # 使用 Windows 自带的 PnP 查询命令；这里只列出系统已登记的设备。
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
try {
    $devices = @(Get-PnpDevice -Class Bluetooth | ForEach-Object {
        [PSCustomObject]@{
            Name = [string]$_.FriendlyName
            Status = [string]$_.Status
        }
    })
    ConvertTo-Json -InputObject $devices -Compress
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
"""

    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], "读取蓝牙设备列表超时。"
    except OSError as error:
        return [], f"无法启动 PowerShell：{error}"

    if result.returncode != 0:
        details = result.stderr.strip() or "Windows 蓝牙设备查询失败。"
        return [], details

    output = result.stdout.strip()
    if not output:
        return [], None

    try:
        raw_devices = json.loads(output)
    except json.JSONDecodeError as error:
        return [], f"无法解析 Windows 返回的设备列表：{error}"

    if isinstance(raw_devices, dict):
        raw_devices = [raw_devices]

    devices = []
    for item in raw_devices:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "未命名设备").strip()
        status = str(item.get("Status") or "未知").strip()
        devices.append({"name": name, "status": status})

    return devices, None