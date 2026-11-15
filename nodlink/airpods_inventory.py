"""读取配对缓存的产品编号和 SDP，地址只在内存中用于目标选择。"""

from dataclasses import dataclass, field
import re
import winreg

from nodlink.sdp_records import l2cap_psms


@dataclass
class PairedAirPods:
    """当前 Windows 缓存条目，不代表耳机此刻在线。"""

    address: int = field(repr=False)
    name: str
    product_id: int | None
    vendor_id: int | None
    record_count: int = 0
    parsed_records: int = 0
    psms: set[int] = field(default_factory=set)
    cache_error: str | None = None

    def public_info(self) -> dict:
        models = {0x2019: "AirPods 4", 0x201B: "AirPods 4 (ANC)"}
        return {
            "name": self.name,
            "model_from_cached_pid": models.get(self.product_id, "未识别"),
            "cached_product_id": f"0x{self.product_id:04X}" if self.product_id is not None else None,
            "cached_vendor_id": self.vendor_id,
            "cached_sdp_record_count": self.record_count,
            "parsed_sdp_record_count": self.parsed_records,
            "cached_l2cap_psms": [f"0x{value:04X}" for value in sorted(self.psms)],
            "cached_aap_psm_present": 0x1001 in self.psms,
            "cache_error": self.cache_error,
            "source": "Windows paired-device registry cache; may be stale",
        }


def _optional_number(key, name: str) -> int | None:
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value if isinstance(value, int) else None
    except OSError:
        return None


def paired_airpods() -> list[PairedAirPods]:
    """只用 KEY_READ 打开配对信息；不访问链路密钥或修改注册表。"""
    root = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
    devices = []
    try:
        registry = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return devices
    with registry:
        for index in range(winreg.QueryInfoKey(registry)[0]):
            address_text = winreg.EnumKey(registry, index)
            if not re.fullmatch(r"[0-9a-fA-F]{12}", address_text):
                continue
            with winreg.OpenKey(registry, address_text, 0, winreg.KEY_READ) as key:
                try:
                    name, _ = winreg.QueryValueEx(key, "Name")
                except FileNotFoundError:
                    name = ""
                if isinstance(name, bytes):
                    name = name.rstrip(b"\0").decode("utf-8", errors="replace")
                product = _optional_number(key, "PID")
                vendor = _optional_number(key, "VID")
                if "airpods" not in str(name).casefold() and not (
                    vendor == 0x004C and product in (0x2019, 0x201B)
                ):
                    continue
                device = PairedAirPods(int(address_text, 16), str(name), product, vendor)
                try:
                    with winreg.OpenKey(key, "CachedServices", 0, winreg.KEY_READ) as cache:
                        for value_index in range(winreg.QueryInfoKey(cache)[1]):
                            _, record, value_type = winreg.EnumValue(cache, value_index)
                            if value_type != winreg.REG_BINARY:
                                continue
                            device.record_count += 1
                            try:
                                device.psms.update(l2cap_psms(record))
                                device.parsed_records += 1
                            except ValueError:
                                pass
                except OSError as error:
                    device.cache_error = type(error).__name__
                devices.append(device)
    return devices
