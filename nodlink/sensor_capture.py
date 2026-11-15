"""可复用的有限时长 AAP 实验采集；回调仅更新显示，不控制鼠标。"""

from datetime import datetime
import time

from nodlink.aap_motion import FEATURES, HANDSHAKE, PROFILES, candidate_motion_fields
from nodlink.airpods_inventory import paired_airpods
from nodlink.capture_lease import CaptureLease, CaptureBusyError
from nodlink.wpods_transport import WPodsTransport, bridge_availability, AapOpenError
from nodlink.runtime_status import read_pause_policy, capture_failure_message


def capture(
    profile_name: str,
    seconds: float,
    on_sample=None,
    on_started=None,
    stop_requested=None,
    retain_evidence: bool = True,
    allowed_product_ids: tuple[int, ...] = (0x2019, 0x201B),
) -> dict:
    """实验采集原始候选帧，始终不标记为已经验证的头部姿态。"""
    report = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "experimental_live_capture",
        "profile": profile_name,
        "status": "not_started",
        "handshake_reply_received": False,
        "received_packet_count": 0,
        "opcode_counts": {},
        "motion_protocol_messages": [],
        "candidate_frames": [],
        "candidate_frame_count": 0,
        "head_motion_verified": False,
        "note": "候选帧字段布局尚未通过本机 AirPods 4 实机运动对应验证，不等同于有效姿态。",
    }
    transport = None
    lease = None
    start_attempted = False
    profile = PROFILES[profile_name]
    try:
        lease = CaptureLease()
        lease.acquire()
        pause = read_pause_policy()
        if pause is not None:
            report.update(status=pause["status"], error=pause["summary"], recovery_hint=pause["detail"])
            return report
        bridge = bridge_availability()
        report["bridge"] = bridge
        if bridge["status"] != "pipe_available_unverified":
            report["status"] = "bridge_unavailable"
            report["error"] = capture_failure_message(report)
            report["recovery_hint"] = report["error"]
            return report
        devices = [device for device in paired_airpods() if device.vendor_id == 0x004C and device.product_id in allowed_product_ids]
        if len(devices) != 1:
            report["status"] = "target_not_unique"
            return report
        device = devices[0]
        report["device"] = device.public_info()
        transport = WPodsTransport(device.address, stop_requested=stop_requested)
        if stop_requested is not None and stop_requested():
            report["status"] = "interrupted"
            return report
        transport.send(HANDSHAKE)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested():
                report["status"] = "interrupted"
                return report
            try:
                reply = transport.receive(min(1.0, deadline - time.monotonic()))
            except TimeoutError:
                continue
            if reply:
                # 这只是收到握手阶段的回包，不将任意回包标记为运动数据。
                report["handshake_reply_received"] = True
                break
        if not report["handshake_reply_received"]:
            report["status"] = "no_handshake_reply"
            return report
        time.sleep(0.1)
        if stop_requested is not None and stop_requested():
            report["status"] = "interrupted"
            return report
        transport.send(FEATURES)
        start_attempted = True
        transport.send(profile["start"])
        print("已发出实验运动请求：先静止，再缓慢左右转头；Ctrl+C 停止。", flush=True)
        started = time.monotonic()
        if on_started is not None:
            on_started(started)
        while time.monotonic() - started < seconds:
            if stop_requested is not None and stop_requested():
                report["status"] = "interrupted"
                break
            try:
                packet = transport.receive(max(0.001, min(0.2, seconds - (time.monotonic() - started))))
            except TimeoutError:
                continue
            if packet is None:
                continue
            report["received_packet_count"] += 1
            opcode = f"0x{int.from_bytes(packet[4:6], 'little'):04X}" if len(packet) >= 6 else "short_packet"
            report["opcode_counts"][opcode] = report["opcode_counts"].get(opcode, 0) + 1
            if retain_evidence and opcode == "0x0017" and len(report["motion_protocol_messages"]) < 64:
                # 仅保留 RTBuddy/运动协议消息，不保存身份、配对密钥或音频包。
                report["motion_protocol_messages"].append({
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                    "length": len(packet),
                    "packet_hex": packet.hex(),
                })
            fields = candidate_motion_fields(packet)
            # 不保存其他 AAP 包内容，避免把身份/密钥等非研究数据写入日志。
            if fields is not None:
                report["candidate_frame_count"] = report.get("candidate_frame_count", 0) + 1
                sample = {
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                    "received_at_monotonic": time.monotonic(),
                    **fields,
                }
                if retain_evidence:
                    sample["packet_hex"] = packet.hex()
                    if len(report["candidate_frames"]) < 4096:
                        report["candidate_frames"].append(sample)
                if on_sample is not None:
                    on_sample(sample)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if report["status"] != "interrupted":
            report["status"] = "candidate_frames_need_validation" if report.get("candidate_frame_count", 0) else "no_recognized_motion_frames"
    except (KeyboardInterrupt, InterruptedError):
        report["status"] = "interrupted"
    except (CaptureBusyError, AapOpenError) as error:
        hint = (
            "请先停止其他耳控窗口的训练或鼠标控制，再重试。"
            if isinstance(error, CaptureBusyError)
            else "请完全退出系统托盘中的 WPods 图形客户端，并停止其他耳机采集程序，然后重试。WPodsService 后台服务需要保持运行。"
        )
        report.update(status="channel_busy" if error.code == 170 else "capture_failed",
                      error_code=error.code, error_type=type(error).__name__, error=str(error))
        if error.code == 170:
            report["recovery_hint"] = hint
    except Exception as error:
        report.update(status="capture_failed", error_type=type(error).__name__, error=str(error))
    finally:
        try:
            if transport is not None:
                if start_attempted:
                    try:
                        transport.send(profile["stop"])
                        report["stop_command_sent"] = True
                    except Exception as error:
                        report.update(stop_command_sent=False, stop_error_type=type(error).__name__)
                transport.close()
        finally:
            if lease is not None:
                lease.close()
    return report
