"""桌面程序的短探测和独立守护子进程入口，不创建界面或移动鼠标。"""

import argparse
import json
from pathlib import Path

from nodlink.channel_probe import probe
from nodlink.bridge_recovery import watch_parent


def dispatch_helper(arguments):
    """打包后的同一个 EXE 也能充当探测/守护子进程。"""
    if arguments and arguments[0] == "--blink-engine":
        from nodlink.blink_cli import main as blink_main
        return blink_main(arguments[1:])
    if arguments and arguments[0] in ("--bridge-probe", "--bridge-watchdog"):
        return main([arguments[0].removeprefix("--bridge-"), *arguments[1:]])
    return None


def main(arguments=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    check = commands.add_parser("probe")
    check.add_argument("--reconnect-audio", action="store_true")
    check.add_argument("--connect-wait-seconds", type=int, default=35, choices=range(5, 36))
    check.add_argument("--output", type=Path)
    guard = commands.add_parser("watchdog")
    guard.add_argument("--parent", type=int, required=True)
    guard.add_argument("--token", required=True)
    guard.add_argument("--settings-folder", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.operation == "probe":
        result = json.dumps(probe(args.reconnect_audio, args.connect_wait_seconds), ensure_ascii=False)
        if args.output:
            args.output.write_text(result, encoding="utf-8")
        else:
            print(result, flush=True)
    else:
        watch_parent(args.parent, args.token, args.settings_folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
