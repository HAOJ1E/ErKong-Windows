"""实验性的 WPods 本机 AAP 管道客户端，不安装或启动外部服务。"""

import ctypes
import struct
import time
from ctypes import wintypes
import _winapi
import winreg


PIPE_NAME = r"\\.\pipe\WPods.Aap"
MAX_AAP_PACKET = 1691


class AapOpenError(RuntimeError):
    """保留服务端错误码，供界面提供准确的恢复操作。"""

    def __init__(self, code: int, message: str):
        self.code = int(code)
        super().__init__(f"AAP 通道未打开：{message}（{code}）")


def service_state() -> dict:
    """只查询服务状态，用进程编号验证管道归属。"""
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenSCManagerW.restype = wintypes.HANDLE
    api.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenServiceW.restype = wintypes.HANDLE
    api.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    api.CloseServiceHandle.restype = wintypes.BOOL
    api.QueryServiceStatusEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.QueryServiceStatusEx.restype = wintypes.BOOL
    manager = api.OpenSCManagerW(None, None, 1)  # SC_MANAGER_CONNECT
    if not manager:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        service = api.OpenServiceW(manager, "WPodsService", 4)  # SERVICE_QUERY_STATUS
        if not service:
            error = ctypes.get_last_error()
            if error == 1060:
                return {"installed": False, "running": False, "process_id": None}
            raise ctypes.WinError(error)
        try:
            status = (wintypes.DWORD * 9)()
            needed = wintypes.DWORD()
            if not api.QueryServiceStatusEx(service, 0, status, ctypes.sizeof(status), ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            return {"installed": True, "running": status[1] == 4, "state": int(status[1]), "process_id": int(status[7])}
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)


def bridge_availability() -> dict:
    """检查先决条件，不打开 AAP 管道，也不向耳机发包。"""
    try:
        result = service_state()
    except OSError as error:
        return {"status": "query_failed", "error_code": error.winerror}
    # 对外报告不需要内部服务进程编号。
    result.pop("process_id", None)
    # 安装器会在首次安装 USB/IP 后延迟启动，不能为测试而绕过重启标记。
    restart_required = False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WPods\Service", 0, winreg.KEY_READ) as key:
            restart_required = winreg.QueryValueEx(key, "WaitsForRestart")[0] == 1
    except FileNotFoundError:
        pass
    except OSError as error:
        result["restart_marker_error"] = getattr(error, "winerror", None)
    result["restart_required"] = restart_required
    if not result["installed"]:
        result["status"] = "not_installed"
    elif restart_required:
        result["status"] = "restart_required"
    elif not result["running"]:
        result["status"] = "not_running"
    else:
        try:
            _winapi.WaitNamedPipe(PIPE_NAME, 1)
            result["status"] = "pipe_available_unverified"
        except OSError as error:
            result.update(status="pipe_unavailable", error_code=error.winerror)
    return result


def _finish_io(operation, error: int, timeout: float) -> int:
    """等待有限时间，并在超时时取消 I/O 后回收底层请求。"""
    try:
        if error == 997:  # ERROR_IO_PENDING
            outcome = _winapi.WaitForMultipleObjects([operation.event], False, max(1, int(timeout * 1000)))
            if outcome == 258:  # WAIT_TIMEOUT
                raise TimeoutError("本机桥接管道等待超时")
            if outcome != 0:
                raise OSError(f"等待管道事件失败：{outcome}")
        transferred, completed_error = operation.GetOverlappedResult(False)
        if completed_error:
            raise ctypes.WinError(completed_error)
        return transferred
    except BaseException:
        operation.cancel()
        operation.GetOverlappedResult(True)
        raise


class MessagePipe:
    """保留 Windows 消息边界，读写均设超时，拒绝错误的服务进程。"""

    def __init__(self, pipe_name: str, expected_server_pid: int):
        self._handle = None
        if not pipe_name.startswith("\\\\.\\pipe\\"):
            raise ValueError("仅允许本机命名管道")
        # 与 WPods 的交互用户 ACL 相符；限制服务端只能识别用户而不能模拟用户。
        handle = _winapi.CreateFile(pipe_name, 0x00100183, 0, 0, 3, 0x40000000 | 0x00110000, 0)
        try:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
            kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
            server = wintypes.ULONG()
            if not kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(server)):
                raise ctypes.WinError(ctypes.get_last_error())
            if expected_server_pid <= 0 or server.value != expected_server_pid:
                raise PermissionError("管道所属进程与预期的本机服务不一致")
            _winapi.SetNamedPipeHandleState(handle, 2, None, None)  # PIPE_READMODE_MESSAGE
            self._handle = handle
        except BaseException:
            _winapi.CloseHandle(handle)
            raise

    def write(self, data: bytes, timeout: float = 3.0) -> None:
        if self._handle is None:
            raise OSError("管道尚未打开")
        if not data or len(data) > MAX_AAP_PACKET:
            raise ValueError("管道消息大小超出协议范围")
        operation, error = _winapi.WriteFile(self._handle, data, overlapped=True)
        if _finish_io(operation, error, timeout) != len(data):
            raise OSError("管道消息未完整写入")

    def read(self, timeout: float = 3.0) -> bytes:
        if self._handle is None:
            raise OSError("管道尚未打开")
        operation, error = _winapi.ReadFile(self._handle, MAX_AAP_PACKET + 1, overlapped=True)
        _finish_io(operation, error, timeout)
        return bytes(operation.getbuffer())

    def close(self) -> None:
        if self._handle is not None:
            _winapi.CloseHandle(self._handle)
            self._handle = None


class WPodsTransport:
    """每个连接对应一台明确选择的配对耳机的 AAP 通道。"""

    def __init__(self, address: int, stop_requested=None):
        self._pipe = None
        state = service_state()
        if not state["running"]:
            raise RuntimeError("WPods 桥接服务尚未运行；未尝试连接耳机。")
        pipe = MessagePipe(PIPE_NAME, state["process_id"])
        try:
            pipe.write(b"AP" + struct.pack("<Q", address))
            deadline = time.monotonic() + 15.0
            while True:
                if stop_requested is not None and stop_requested():
                    raise InterruptedError("用户取消打开耳机通道")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("耳机通道打开超时")
                try:
                    reply = pipe.read(min(0.2, remaining))
                    break
                except TimeoutError:
                    continue
            if len(reply) != 5 or reply[0] != 2:
                raise ValueError("WPods AAP 打开响应格式不匹配")
            error = struct.unpack("<I", reply[1:])[0]
            if error:
                messages = {1167: "耳机未连接", 170: "AAP 通道正被其他程序占用", 1225: "耳机拒绝连接", 121: "耳机未及时响应"}
                raise AapOpenError(error, messages.get(error, "桥接错误"))
            self._pipe = pipe
        except BaseException:
            pipe.close()
            raise

    def send(self, packet: bytes) -> None:
        self._pipe.write(packet)

    def receive(self, timeout: float = 1.0) -> bytes | None:
        message = self._pipe.read(timeout)
        if not message:
            raise EOFError("AAP 管道已关闭")
        if message[0] == 0:
            return message[1:]
        if message[0] == 1 and len(message) == 5:
            raise EOFError(f"AAP 通道已结束（{struct.unpack('<I', message[1:])[0]}）")
        if message[0] == 3 and len(message) == 2:
            return None  # 通话音频状态提示，不是传感器数据。
        raise ValueError("未知的 WPods 管道消息格式")

    def close(self) -> None:
        if self._pipe is not None:
            self._pipe.close()
            self._pipe = None
