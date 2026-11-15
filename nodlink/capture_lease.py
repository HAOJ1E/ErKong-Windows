"""跨进程保护耳控的采集通道；进程退出后由 Windows 释放占用。"""

import ctypes
from ctypes import wintypes


class CaptureBusyError(RuntimeError):
    code = 170

    def __init__(self):
        super().__init__("另一个耳控采集任务正在运行，请先停止该窗口的训练或控制")


class CaptureLease:
    """非阻塞 Windows 命名互斥量；创建、持有和释放均在采集线程。"""

    def __init__(self, name: str = r"Local\NodLink.AapCapture.v1"):
        self.name = name
        self._handle = None
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self._kernel.ReleaseMutex.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL

    def acquire(self):
        if self._handle is not None:
            raise RuntimeError("当前采集任务已经持有通道锁")
        handle = self._kernel.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        outcome = self._kernel.WaitForSingleObject(handle, 0)
        if outcome in (0, 0x80):  # WAIT_OBJECT_0 或上一个持有进程异常退出。
            self._handle = handle
            return
        error = ctypes.get_last_error()
        self._kernel.CloseHandle(handle)
        if outcome == 0x102:
            raise CaptureBusyError()
        raise ctypes.WinError(error)

    def close(self):
        if self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                self._kernel.ReleaseMutex(handle)
            finally:
                self._kernel.CloseHandle(handle)
