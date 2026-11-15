"""手动恢复已安装的桥接；所有系统操作均在工作线程，失败会还原原生蓝牙。"""

import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from functools import lru_cache

from nodlink.capture_lease import CaptureLease
from nodlink.control_settings import SETTINGS_FOLDER, save_json_atomic
from nodlink.wpods_transport import service_state


PROJECT_FOLDER = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
SERVICE_EXE = Path(r"C:\Program Files\WPods\WPods.Service.exe")
USBIP_EXE = Path(r"C:\Program Files\USBip\usbip.exe")
CONTROLLER_ID = r"ROOT\USB\0000"


class BridgeRestartRequired(RuntimeError):
    pass


@lru_cache(maxsize=1)
def current_boot_time():
    """每个进程只查询一次，用于重启后自动解除旧的阻止重复操作标记。"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    return result.stdout.strip()


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5  # 无权查询时保守地认为仍在运行。
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)


def helper_command(operation: str, *args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, f"--bridge-{operation}", *args]
    python = Path(sys.executable).with_name("python.exe")
    return [str(python), "-m", "nodlink.bridge_cli", operation, *args]


class WindowsBridgeSystem:
    """仅操作本项目已验证的 Intel AX211 与现有 USB/IP 控制器。"""

    def run(self, args: list[str], timeout: float = 15, allowed=(0,)) -> str:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 3010:
            raise BridgeRestartRequired("Windows 要求重新启动以完成虚拟蓝牙控制器重启（3010）。普通蓝牙将恢复；保存工作后手动重启电脑，再启用头追。")
        if result.returncode not in allowed:
            raise RuntimeError(f"系统操作失败（{result.returncode}）：{(result.stderr or result.stdout).strip()[:300]}")
        return result.stdout

    def snapshot(self) -> dict:
        script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$radios=@(Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue)
$native=@($radios | Where-Object { $_.InstanceId -match '^USB\\VID_8087&PID_0033\\' -and $_.InstanceId -notmatch '\\WPODS' -and $_.Status -eq 'OK' })
$virtual=@($radios | Where-Object { $_.InstanceId -match '^USB\\VID_8087&PID_0033\\WPODS' -and $_.Status -eq 'OK' })
$controller=Get-PnpDeviceProperty -InstanceId 'ROOT\USB\0000' -KeyName DEVPKEY_Device_Service -ErrorAction SilentlyContinue
[pscustomobject]@{NativeReady=($native.Count -gt 0);VirtualReady=($virtual.Count -gt 0);ControllerService=$controller.Data;GuiRunning=(@(Get-Process -Name WPods -ErrorAction SilentlyContinue).Count -gt 0);ServiceManual=((Get-Service WPodsService).StartType -eq 'Manual')} | ConvertTo-Json -Compress
"""
        return json.loads(self.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=12))

    def is_running(self):
        return service_state()["running"]

    def start(self):
        self.run(["sc.exe", "start", "WPodsService"], allowed=(0, 1056))

    def stop(self):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = service_state()
            if state.get("state", 4 if state["running"] else 1) == 1:
                return
            self.run(["sc.exe", "stop", "WPodsService"], allowed=(0, 1061, 1062))
            time.sleep(0.3)
        raise TimeoutError("桥接服务尚未停止，暂不能确认原生蓝牙恢复")

    def reset_empty_controller(self):
        state = self.snapshot()
        if self.is_running() or state.get("ControllerService") != "usbip2_ude":
            raise RuntimeError("虚拟控制器条件不符，未重启任何设备")
        ports = self.run([str(USBIP_EXE), "port"])
        if re.search(r"Port\s+\d+", ports):
            raise RuntimeError("另有 USB/IP 设备正在使用，不能重置虚拟控制器")
        self.run(["pnputil.exe", "/restart-device", CONTROLLER_ID], timeout=20)

    def wait_for_radio(self, cancelled):
        # PnP 出现后蓝牙音频接口仍需初始化，保留此前实测流程中的短等待。
        for _ in range(20):
            if cancelled():
                raise InterruptedError("已取消连接")
            time.sleep(0.1)

    def restore_native(self):
        self.stop()
        if not self.snapshot()["NativeReady"]:
            self.run([str(SERVICE_EXE), "--restore-native"], timeout=25)
        self.run(["sc.exe", "start", "bthserv"], allowed=(0, 1056))
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.snapshot()["NativeReady"]:
                return
            time.sleep(0.5)
        raise RuntimeError("尚未确认 Intel 原生蓝牙恢复，请保留本窗口查看日志")


def run_sensor_probe(cancelled) -> dict:
    """隔离短采集并设置硬超时；取消时结束本次探测进程树。"""
    descriptor, filename = tempfile.mkstemp(prefix="nodlink-probe-", suffix=".json")
    os.close(descriptor)
    result_path = Path(filename)
    process = None
    try:
        # 使用 UTF-8 文件传结果，兼容 PyInstaller 无控制台 EXE 的 sys.stdout=None。
        process = subprocess.Popen(helper_command("probe", "--reconnect-audio", "--connect-wait-seconds", "35",
                                                 "--output", str(result_path)),
                                   cwd=PROJECT_FOLDER, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 45
        while process.poll() is None:
            if cancelled() or time.monotonic() > deadline:
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                raise InterruptedError("连接已取消" if cancelled() else "短采集超时")
            time.sleep(0.1)
        _, error = process.communicate(timeout=2)
        if process.returncode:
            raise RuntimeError(error.decode("utf-8", errors="replace")[:300] or "短采集进程异常退出")
        return json.loads(result_path.read_text(encoding="utf-8-sig"))
    finally:
        if process is not None and process.stderr:
            process.stderr.close()
        result_path.unlink(missing_ok=True)


class BridgeRecovery:
    def __init__(self, settings_folder: Path = SETTINGS_FOLDER, system=None, probe_runner=None, guard_starter=None):
        self.folder = settings_folder
        self.system = system or WindowsBridgeSystem()
        self.probe_runner = probe_runner or run_sensor_probe
        self.guard_starter = guard_starter or self._start_guard
        self.token = uuid.uuid4().hex
        self.owns_bridge = False
        self.guard_process = None

    def _policy(self, mode: str, **extra):
        value = {"schema_version": 1, "mode": mode, "owner_pid": os.getpid(), "session_token": self.token,
                 "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"), **extra}
        save_json_atomic(self.folder / "bridge-policy.json", value)

    def _start_guard(self):
        # 独立守护进程监视桌面进程，异常退出时尝试恢复原生蓝牙。
        return subprocess.Popen(helper_command("watchdog", "--parent", str(os.getpid()), "--token", self.token,
                                                "--settings-folder", str(self.folder)), cwd=PROJECT_FOLDER,
                                creationflags=subprocess.CREATE_NO_WINDOW)

    def enable(self, progress=lambda text: None, cancelled=lambda: False) -> dict:
        lease = CaptureLease()
        touched = False
        probes = []
        try:
            lease.acquire()
            progress("正在检查适配器与现有桥接…")
            state = self.system.snapshot()
            if state["GuiRunning"]:
                raise RuntimeError("请先完全退出系统托盘中的 WPods 图形客户端，再启用头追桥接")
            if not state["ServiceManual"]:
                raise RuntimeError("桥接服务必须保持手动启动，当前未改变系统配置")
            try:
                policy = json.loads((self.folder / "bridge-policy.json").read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                policy = {}
            owner = int(policy.get("owner_pid") or 0)
            if policy.get("restart_required") and policy.get("restart_boot_time") == current_boot_time():
                raise BridgeRestartRequired("本次 Windows 尚未重新启动，暂不重复重置虚拟控制器。请保存工作并重启电脑后再启用头追。")
            if policy.get("mode") in ("bridge", "enabling", "recovery_required") and policy.get("session_token") != self.token and process_is_alive(owner):
                raise RuntimeError("另一个 NodLink 窗口正在使用桥接，请返回那个窗口操作")
            self._policy("enabling")
            self.owns_bridge = touched = True
            self.guard_process = self.guard_starter()
            if cancelled():
                raise InterruptedError("已取消连接")
            if not (self.system.is_running() and state["VirtualReady"]):
                progress("正在准备虚拟蓝牙，耳机会短暂断开…")
                self.system.restore_native()
                self.system.reset_empty_controller()
                if cancelled():
                    raise InterruptedError("已取消连接")
                self.system.start()
                deadline = time.monotonic() + 20
                while not self.system.snapshot()["VirtualReady"]:
                    if cancelled() or time.monotonic() > deadline:
                        raise RuntimeError("Windows 未识别虚拟蓝牙，正在恢复普通蓝牙")
                    time.sleep(0.4)
                self.system.wait_for_radio(cancelled)
            progress("虚拟蓝牙已出现，正在重连耳机并检查真实运动数据（最多 45 秒）…")
            # 探测子进程自己取得采集锁；应用此时保持 enabling，不允许训练或控制。
            lease.close()
            probe = self.probe_runner(cancelled)
            probes.append(probe)
            if probe.get("status") == "no_valid_motion_frames" and probe.get("handshake_reply_received") and not cancelled():
                progress("耳机已连接但姿态尚未到达，正在重新订阅一次运动数据…")
                probe = self.probe_runner(cancelled)
                probes.append(probe)
            lease.acquire()
            if cancelled():
                raise InterruptedError("已取消连接")
            if probe.get("status") != "stream_received" or probe.get("valid_frames", 0) < 20 or not probe.get("stop_command_sent"):
                raise RuntimeError(probe.get("error") or "未收到足够的真实运动帧")
            if not self.system.snapshot()["VirtualReady"]:
                raise RuntimeError("运动检查后虚拟蓝牙已离线")
            self._policy("bridge", motion_stream_checked=True, probe=probe, probe_attempts=probes)
            save_json_atomic(self.folder / "bridge-session-last.json", {"ready": True, "probe_attempts": probes,
                             "mouse_control_started": False, "recorded_at": datetime.now().astimezone().isoformat()})
            progress(f"桥接就绪：收到 {probe['valid_frames']} 个有效运动帧，可以开始训练")
            return {"ready": True, "probe": probe, "mode": "bridge"}
        except Exception as error:
            if touched:
                progress("连接未完成，正在恢复普通蓝牙…")
                try:
                    self.system.restore_native()
                    self._policy("native_bluetooth", last_error=str(error), probe_attempts=probes,
                                 restart_required=isinstance(error, BridgeRestartRequired),
                                 restart_boot_time=current_boot_time() if isinstance(error, BridgeRestartRequired) else None)
                    save_json_atomic(self.folder / "bridge-session-last.json", {"ready": False, "error": str(error),
                                     "probe_attempts": probes, "native_restored": True, "mouse_control_started": False})
                    self.owns_bridge = False
                except Exception as restore_error:
                    self._policy("recovery_required", reason=str(restore_error))
                    raise RuntimeError(f"{error}；原生蓝牙恢复尚未确认：{restore_error}") from error
            raise
        finally:
            lease.close()

    def disable(self, progress=lambda text: None) -> dict:
        lease = CaptureLease()
        touched = False
        try:
            lease.acquire()
            try:
                policy = json.loads((self.folder / "bridge-policy.json").read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                policy = {}
            if policy.get("mode") in ("bridge", "enabling", "recovery_required") and policy.get("session_token") != self.token and process_is_alive(int(policy.get("owner_pid") or 0)):
                raise RuntimeError("另一个 NodLink 窗口正在使用桥接，请在那个窗口恢复普通蓝牙")
            touched = True
            self.owns_bridge = True
            progress("正在关闭桥接并恢复普通蓝牙…")
            self.system.restore_native()
            self._policy("native_bluetooth")
            self.owns_bridge = False
            return {"ready": False, "mode": "native_bluetooth", "native_restored": True}
        except Exception as error:
            if touched:
                self._policy("recovery_required", reason=str(error))
            raise
        finally:
            lease.close()


def watch_parent(parent: int, token: str, folder: Path, alive=process_is_alive, restore=None):
    from nodlink.owned_mouse_button import LeftButtonState
    button_state = LeftButtonState(parent, token)
    button_state.set_ready(True)
    try:
        _watch_parent_and_restore(parent, token, folder, alive, restore, button_state)
    finally:
        try:
            button_state.release_owned()
        finally:
            button_state.set_ready(False)


def _watch_parent_and_restore(parent, token, folder, alive, restore, button_state):
    """只清理匹配的会话，旧守护进程不能关闭后来创建的新会话。"""
    while True:
        try:
            policy = json.loads((folder / "bridge-policy.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        if policy.get("session_token") != token or policy.get("mode") == "native_bluetooth":
            return
        if not alive(parent):
            button_state.release_owned()  # 先松开自有左键，再等待蓝牙采集锁与恢复。
            # 探测子进程可能仍短暂持锁；等它退出后重新核对会话，再做恢复。
            deadline = time.monotonic() + 55
            while True:
                lease = CaptureLease()
                try:
                    lease.acquire()
                    current = json.loads((folder / "bridge-policy.json").read_text(encoding="utf-8-sig"))
                    if current.get("session_token") != token or current.get("mode") == "native_bluetooth":
                        return
                    recover = BridgeRecovery(folder)
                    recover.token = token
                    try:
                        (restore or recover.system.restore_native)()
                        recover._policy("native_bluetooth")
                    except Exception as error:
                        recover._policy("recovery_required", reason=str(error))
                    return
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        return
                    time.sleep(0.5)
                finally:
                    lease.close()
            return
        time.sleep(1)
