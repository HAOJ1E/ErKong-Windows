"""已获用户授权的短时研究探测；不启动服务、不改变暂停配置、不移动鼠标。"""

from datetime import datetime
import argparse
import ctypes
import json
import time

from nodlink.aap_motion import HANDSHAKE, FEATURES, PROFILES, candidate_motion_fields
from nodlink.airpods_inventory import paired_airpods
from nodlink.capture_lease import CaptureLease
from nodlink.motion_validation import quaternion_candidate
from nodlink.wpods_transport import WPodsTransport, AapOpenError


def probe(reconnect_audio: bool = False, connect_wait_seconds: int = 12) -> dict:
    report = {"mode": "authorized_bridge_trial_sensor_probe", "mouse_control_started": False,
              "candidate_frames": 0, "valid_frames": 0, "status": "not_started",
              "received_packets": 0, "opcode_counts": {},
              "started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    lease = CaptureLease()
    transport = None
    requested = False
    try:
        lease.acquire()
        devices = [device for device in paired_airpods() if device.vendor_id == 0x004C and device.product_id == 0x2019]
        if len(devices) != 1:
            raise RuntimeError("没有唯一匹配的普通 AirPods 4")
        if reconnect_audio:
            # 调用已安装桥接自带的音频重连请求；返回成功仅表示请求已发送。
            helper = ctypes.WinDLL(r"C:\Program Files\WPods\wpods_native.dll")
            helper.wpn_bt_connect_audio.argtypes = [ctypes.c_uint64]
            helper.wpn_bt_connect_audio.restype = ctypes.c_int32
            result = helper.wpn_bt_connect_audio(devices[0].address)
            report["audio_reconnect_request_hresult"] = f"0x{result & 0xFFFFFFFF:08X}"
        deadline = time.monotonic() + connect_wait_seconds
        while True:
            try:
                transport = WPodsTransport(devices[0].address, stop_requested=lambda: time.monotonic() >= deadline)
                break
            except AapOpenError as error:
                if not reconnect_audio or error.code != 1167 or time.monotonic() >= deadline - 3:
                    raise
                time.sleep(0.5)
        transport.send(HANDSHAKE)
        while time.monotonic() < deadline:
            try:
                if transport.receive(0.2):
                    report["handshake_reply_received"] = True
                    break
            except TimeoutError:
                continue
        if not report.get("handshake_reply_received"):
            raise RuntimeError("握手阶段未收到回包")
        # 与此前成功采集的实现一致，留出握手后能力初始化的时间。
        time.sleep(0.15)
        transport.send(FEATURES)
        requested = True
        transport.send(PROFILES["service16"]["start"])
        capture_started = time.monotonic()
        end = capture_started + 4
        while time.monotonic() < end:
            try:
                packet = transport.receive(0.2)
            except TimeoutError:
                continue
            if packet is None:
                continue
            report["received_packets"] += 1
            opcode = f"0x{int.from_bytes(packet[4:6], 'little'):04X}" if len(packet) >= 6 else "short"
            report["opcode_counts"][opcode] = report["opcode_counts"].get(opcode, 0) + 1
            sample = candidate_motion_fields(packet)
            if sample is not None:
                report["candidate_frames"] += 1
                report["valid_frames"] += int(quaternion_candidate(sample["orientation_raw"]) is not None)
        report["capture_seconds"] = round(time.monotonic() - capture_started, 3)
        report["status"] = "stream_received" if report["valid_frames"] else "no_valid_motion_frames"
    except Exception as error:
        report.update(status="probe_failed", error_type=type(error).__name__, error=str(error))
    finally:
        try:
            if transport is not None:
                if requested:
                    try:
                        transport.send(PROFILES["service16"]["stop"])
                        report["stop_command_sent"] = True
                    except Exception as error:
                        report.update(stop_command_sent=False, stop_error=type(error).__name__)
                transport.close()
        finally:
            lease.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconnect-audio", action="store_true")
    parser.add_argument("--connect-wait-seconds", type=int, default=12, choices=range(5, 36))
    args = parser.parse_args()
    print(json.dumps(probe(args.reconnect_audio, args.connect_wait_seconds), ensure_ascii=False))
