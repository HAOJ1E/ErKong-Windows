"""解析 AAP RTBuddy 的有限 protobuf 子集，按消息结构定位传感器负载。"""

import struct


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for index in range(10):
        if offset >= len(data):
            raise ValueError("protobuf varint 不完整")
        value = data[offset]
        offset += 1
        if index == 9 and value > 1:
            raise ValueError("protobuf varint 超过 64 位")
        result |= (value & 0x7F) << (7 * index)
        if value < 0x80:
            return result, offset
    raise ValueError("protobuf varint 超长")


def fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    """检查字段长度，拒绝未知分组、截断和不合法字段编号。"""
    if len(data) > 1691:
        raise ValueError("protobuf 消息过长")
    result = []
    offset = 0
    while offset < len(data):
        if len(result) >= 96:
            raise ValueError("protobuf 字段过多")
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if not 1 <= number <= 0x1FFFFFFF:
            raise ValueError("protobuf 字段编号非法")
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = _varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            end = offset + length
            if end > len(data):
                raise ValueError("protobuf 字段超出消息边界")
            value = bytes(data[offset:end])
            offset = end
        else:
            raise ValueError("不支持 protobuf 分组类型")
        result.append((number, wire, value))
    return result


def motion_payload(packet: bytes) -> tuple[int, bytes] | None:
    """验证 RTBuddy 帧、服务编号与负载版本，不再依赖整包固定偏移。"""
    if len(packet) < 12 or packet[:6] != bytes.fromhex("04 00 04 00 17 00"):
        return None
    descriptor, declared = struct.unpack_from("<IH", packet, 6)
    if descriptor != 0x00100000 or declared != len(packet) - 12:
        return None
    try:
        for number, wire, command in fields(packet[12:]):
            if number != 7 or wire != 2:
                continue
            service = None
            payload = None
            for child_number, child_wire, value in fields(command):
                if child_number == 1 and child_wire == 0:
                    service = value
                elif child_number == 3 and child_wire == 2:
                    payload = value
            if service in (6, 14, 16) and payload is not None and len(payload) >= 58:
                if payload[0] == 1 and payload[9] == 3:
                    return service, payload
    except ValueError:
        return None
    return None
