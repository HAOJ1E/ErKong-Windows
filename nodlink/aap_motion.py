"""候选 AAP 运动包定义与原始字段解析，尚未经本机 AirPods 4 验证。"""

import struct

from nodlink.rtbuddy import motion_payload


# 协议字节依据 LibrePods AAP Definitions 与公开参考实现。
# https://github.com/librepods-org/librepods/blob/main/docs/AAP%20Definitions.md
# 这些命令只在显式启动实验采集后使用，默认预检查不会发包。
HANDSHAKE = bytes.fromhex("00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00")
FEATURES = bytes.fromhex("04 00 04 00 4D 00 FF 00 00 00 00 00 00 00")
PROFILES = {
    "service14": {
        "start": bytes.fromhex("04 00 04 00 17 00 00 00 10 00 10 00 08 A1 02 42 0B 08 0E 10 02 1A 05 01 40 9C 00 00"),
        "stop": bytes.fromhex("04 00 04 00 17 00 00 00 10 00 11 00 08 7E 10 02 42 0B 08 4E 10 02 1A 05 01 00 00 00 00"),
    },
    "service16": {
        "start": bytes.fromhex("04 00 04 00 17 00 00 00 10 00 0F 00 08 73 42 0B 08 10 10 02 1A 05 01 40 9C 00 00"),
        "stop": bytes.fromhex("04 00 04 00 17 00 00 00 10 00 0F 00 08 75 42 0B 08 10 10 02 1A 05 01 00 00 00 00"),
    },
}


def candidate_motion_fields(packet: bytes) -> dict | None:
    """只接受已记录的候选帧头；原始字段不标注成已验证的角度。"""
    parsed = motion_payload(packet)
    if parsed is None:
        return None
    service, payload = parsed
    o1, o2, o3 = struct.unpack_from("<hhh", payload, 20)
    horizontal, vertical = struct.unpack_from("<hh", payload, 28)
    return {
        "service": service,
        "orientation_raw": [o1, o2, o3],
        "acceleration_raw": [horizontal, vertical],
        "gravity_candidate_raw": list(struct.unpack_from("<hhh", payload, 42)),
        "layout": "rtbuddy_devmotion_payload_candidate_unverified",
    }
