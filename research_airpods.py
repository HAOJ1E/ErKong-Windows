"""AirPods 4 研究工具：默认只读预检查；显式 --capture 才采集 AAP。"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from nodlink.aap_motion import PROFILES
from nodlink.airpods_inventory import paired_airpods
from nodlink.motion_support import check_motion_support
from nodlink.wpods_transport import bridge_availability
from nodlink.sensor_capture import capture


def preflight() -> dict:
    """所有内容来自本机只读查询；预检查不建立耳机连接。"""
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "read_only_preflight",
        "paired_devices": [device.public_info() for device in paired_airpods()],
        "wpods_bridge": bridge_availability(),
        "magicaap": check_motion_support(),
        "head_motion_verified": False,
        "sensor_values_read": False,
        "note": "SDP 是 Windows 缓存，存在 AAP PSM 不代表当前已连接或可读取运动数据。",
    }



def main() -> int:
    parser = argparse.ArgumentParser(description="AirPods 4 本机传感器接入研究工具", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--capture", action="store_true", help="通过已安装的 WPods 桥接服务执行实验采集")
    parser.add_argument("--format", choices=sorted(PROFILES), default="service16", help="默认采用本机已验证的协议，不自动尝试多种命令")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, help="报告位置；默认在项目 reports 目录生成新文件")
    args = parser.parse_args()
    if not 1.0 <= args.seconds <= 30.0:
        parser.error("采集时间范围为 1 到 30 秒")
    if args.capture:
        print("正在检查实验采集的桥接前提…", flush=True)
        result = capture(args.format, args.seconds)
    else:
        result = preflight()
    output = args.output or Path(__file__).resolve().parent / "reports" / f"airpods-research-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    # 不静默覆盖现有的硬件采集记录。
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("Report:", output.resolve())
    print("Status:", result.get("status", result.get("wpods_bridge", {}).get("status")))
    print("Verified head motion:", result["head_motion_verified"])
    return 0 if not args.capture else (0 if result["status"] == "candidate_frames_need_validation" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
