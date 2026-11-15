"""只解析 Windows 已缓存的 SDP 记录，不向耳机发起服务搜索。"""


def _element(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """读取一个带边界检查的 Bluetooth SDP 数据元素。"""
    if offset >= len(data):
        raise ValueError("SDP 元素头缺失")
    header = data[offset]
    kind, size_index = header >> 3, header & 7
    offset += 1
    if size_index < 5:
        size = 0 if kind == 0 else 1 << size_index
    else:
        width = 1 << (size_index - 5)
        if offset + width > len(data):
            raise ValueError("SDP 长度字段不完整")
        size = int.from_bytes(data[offset:offset + width], "big")
        offset += width
    end = offset + size
    if end > len(data):
        raise ValueError("SDP 元素超出记录边界")
    return kind, data[offset:end], end


def _items(data: bytes):
    """按顺序遍历序列，不递归扫描任意二进制内容。"""
    offset = 0
    while offset < len(data):
        kind, content, offset = _element(data, offset)
        yield kind, content


def l2cap_psms(record: bytes) -> set[int]:
    """只从 ProtocolDescriptorList（属性 0x0004）提取 L2CAP PSM。"""
    kind, attributes, end = _element(record, 0)
    if kind != 6 or end != len(record):
        raise ValueError("不是完整 SDP 记录序列")
    fields = iter(_items(attributes))
    psms = set()
    for key_kind, key in fields:
        try:
            value_kind, value = next(fields)
        except StopIteration as error:
            raise ValueError("SDP 属性缺少对应值") from error
        if key_kind != 1 or key != b"\x00\x04" or value_kind != 6:
            continue
        for layer_kind, layer in _items(value):
            if layer_kind != 6:
                continue
            values = list(_items(layer))
            if len(values) < 2:
                continue
            uuid_kind, protocol = values[0]
            number_kind, number = values[1]
            # 标准 L2CAP UUID 可以用 16、32 或 128 位编码。
            if uuid_kind != 3 or len(protocol) not in (2, 4, 16):
                continue
            if len(protocol) == 16:
                is_l2cap = protocol.hex() == "0000010000001000800000805f9b34fb"
            else:
                is_l2cap = int.from_bytes(protocol, "big") == 0x0100
            if is_l2cap and number_kind == 1 and len(number) in (1, 2):
                psms.add(int.from_bytes(number, "big"))
    return psms
