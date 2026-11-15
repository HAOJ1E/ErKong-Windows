"""手动扫描 AirPods BLE 广播，并只列出公开 GATT 服务结构。"""

import asyncio
import json
import re
import subprocess

from bleak import BleakClient, BleakScanner


SENSOR_NOTE = "本探测只列举广播和 GATT 服务/特征值属性；不会读取特征值，也不会写入设备。"


def _empty_result(status: str, **details: object) -> dict:
    """创建包含传感器安全状态的统一结果。"""
    result = {
        "status": status,
        "advertiser_count": 0,
        "apple_manufacturer_advertisement_count": 0,
        "airpods_name_match_count": 0,
        "paired_airpods_address_available": False,
        "paired_airpods_address_match": False,
        "paired_address_lookup_status": "not_checked",
        "advertising_service_uuids": [],
        "gatt_services": [],
        "gatt_service_count": 0,
        "gatt_characteristic_count": 0,
        "sensor_values_read": False,
        "note": SENSOR_NOTE,
    }
    result.update(details)
    return result


def _get_paired_airpods_addresses() -> tuple[set[str], str]:
    """读取 Windows 已登记的 AirPods 地址，只在内存中用于本次匹配。"""
    command = r"""
$ErrorActionPreference = 'Stop'
$instanceIds = @(Get-PnpDevice -Class Bluetooth | Where-Object {
    $_.FriendlyName -match 'AirPods'
} | ForEach-Object {
    [string]$_.InstanceId
})
ConvertTo-Json -InputObject $instanceIds -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return set(), type(error).__name__

    if completed.returncode != 0:
        return set(), "PowerShellQueryFailed"

    try:
        instance_ids = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return set(), "InvalidDeviceList"

    if isinstance(instance_ids, str):
        instance_ids = [instance_ids]

    addresses = set()
    for instance_id in instance_ids:
        match = re.search(r"DEV_([0-9A-F]{12})", str(instance_id), re.IGNORECASE)
        if match:
            addresses.add(match.group(1).upper())

    return addresses, "found" if addresses else "not_found"


def _address_matches_ble_device(paired_addresses: set[str], ble_address: str) -> bool:
    """兼容带冒号地址、Windows 设备 ID 和字节倒序地址。"""
    normalized_ble_address = re.sub(r"[^0-9A-F]", "", ble_address.upper())
    for paired_address in paired_addresses:
        reversed_address = "".join(
            reversed([paired_address[index:index + 2] for index in range(0, 12, 2)])
        )
        if paired_address in normalized_ble_address or reversed_address in normalized_ble_address:
            return True
    return False


async def _probe_async(timeout_seconds: float) -> dict:
    """扫描 BLE 广播，并优先通过 Windows 配对地址识别目标耳机。"""
    paired_addresses, lookup_status = _get_paired_airpods_addresses()
    advertisements = await BleakScanner.discover(
        timeout=timeout_seconds,
        scanning_mode="active",
        return_adv=True,
    )

    paired_matches = []
    name_candidates = []
    apple_advertisement_count = 0
    for device, advertisement in advertisements.values():
        if 0x004C in advertisement.manufacturer_data:
            apple_advertisement_count += 1

        if _address_matches_ble_device(paired_addresses, str(device.address)):
            paired_matches.append((device, advertisement))
            continue

        advertised_name = device.name or advertisement.local_name or ""
        if "airpods" in advertised_name.casefold():
            name_candidates.append((device, advertisement))

    common_details = {
        "advertiser_count": len(advertisements),
        "apple_manufacturer_advertisement_count": apple_advertisement_count,
        "airpods_name_match_count": len(name_candidates),
        "paired_airpods_address_available": bool(paired_addresses),
        "paired_airpods_address_match": bool(paired_matches),
        "paired_address_lookup_status": lookup_status,
    }

    if len(paired_matches) > 1:
        return _empty_result("multiple_airpods_candidates", **common_details)

    if not paired_matches:
        if paired_addresses:
            status = "paired_airpods_not_seen"
        elif len(name_candidates) > 1:
            status = "multiple_airpods_candidates"
        elif name_candidates:
            # 没有可核对的 Windows 配对地址时，不自动连接名称相同的陌生设备。
            status = "airpods_name_not_paired"
        else:
            status = "no_airpods_candidate"
        return _empty_result(status, **common_details)

    device, advertisement = paired_matches[0]
    result = _empty_result(
        "paired_airpods_address_match",
        **common_details,
        advertising_service_uuids=list(advertisement.service_uuids or []),
    )

    try:
        # 只连接到地址与 Windows 已登记 AirPods 匹配的设备。
        # 仅查询服务 UUID、特征值 UUID 与属性，不读写特征值数据。
        async with BleakClient(device, timeout=15.0) as client:
            discovered_services = []
            characteristic_count = 0
            for service in client.services:
                characteristics = []
                for characteristic in service.characteristics:
                    characteristics.append(
                        {
                            "uuid": characteristic.uuid,
                            "properties": list(characteristic.properties),
                        }
                    )
                    characteristic_count += 1

                discovered_services.append(
                    {
                        "uuid": service.uuid,
                        "characteristics": characteristics,
                    }
                )

            result["status"] = "gatt_services_discovered"
            result["gatt_services"] = discovered_services
            result["gatt_service_count"] = len(discovered_services)
            result["gatt_characteristic_count"] = characteristic_count
    except Exception as error:
        # 仅记录异常类型，避免把设备地址或系统内部标识写入报告。
        result["status"] = "gatt_connection_failed"
        result["error_type"] = type(error).__name__

    return result


def probe_airpods_ble(timeout_seconds: float = 8.0) -> dict:
    """提供给桌面后台线程调用的同步入口。"""
    return asyncio.run(_probe_async(timeout_seconds))