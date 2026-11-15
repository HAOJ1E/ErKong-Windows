"""只管理 NodLink 自己按下的左键；独立守护进程可在桌面进程退出时松开。"""

import ctypes
from ctypes import wintypes
import os
import weakref


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mouse", MouseInput)]


def send_left_button(down):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user.SendInput.restype = wintypes.UINT
    event = Input(0, MouseInput(0, 0, 0, 0x0002 if down else 0x0004, 0, 0))
    if user.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
        code = ctypes.get_last_error()
        raise ctypes.WinError(code) if code else RuntimeError("Windows 未接受本次鼠标按键操作")


def release_key_pressed():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    return bool(user.GetAsyncKeyState(0x77) & 0x8000)  # 仅检查 F8，不记录其他按键。


def left_button_is_down():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.GetAsyncKeyState.restype = ctypes.c_short
    return bool(user.GetAsyncKeyState(0x01) & 0x8000)


class LeftButtonState:
    """命名事件仅保存本次会话的按键占用标记，不写文件、不阻塞界面。"""

    def __init__(self, parent, token):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        for name in ("SetEvent", "ResetEvent", "CloseHandle"):
            getattr(self.kernel, name).argtypes = [wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        prefix = f"Local\\NodLink.LeftButton.{parent}.{token}"
        self.held_event = self.kernel.CreateEventW(None, True, False, prefix + ".Held")
        self.ready_event = self.kernel.CreateEventW(None, True, False, prefix + ".Ready")
        if not self.held_event or not self.ready_event:
            raise ctypes.WinError(ctypes.get_last_error())
        self._cleanup = weakref.finalize(self, self._close_handles, self.kernel, self.held_event, self.ready_event)

    @staticmethod
    def _close_handles(kernel, held, ready):
        kernel.CloseHandle(held)
        kernel.CloseHandle(ready)

    def ready(self):
        return self.kernel.WaitForSingleObject(self.ready_event, 0) == 0

    def held(self):
        return self.kernel.WaitForSingleObject(self.held_event, 0) == 0

    def set_ready(self, value):
        (self.kernel.SetEvent if value else self.kernel.ResetEvent)(self.ready_event)

    def set_held(self, value):
        (self.kernel.SetEvent if value else self.kernel.ResetEvent)(self.held_event)

    def release_owned(self):
        if self.held():
            send_left_button(False)
            self.set_held(False)


class OwnedLeftButton:
    def __init__(self, token):
        self.state = LeftButtonState(os.getpid(), token)
        self.held = False

    def press(self):
        if self.held:
            return
        if not self.state.ready():
            raise RuntimeError("长按保护尚未就绪，请重新启用头追桥接后重试")
        import pyautogui
        pyautogui.failSafeCheck()
        if left_button_is_down():
            raise RuntimeError("当前左键已被按下，本次不接管鼠标按键")
        self.state.set_held(True)
        self.held = True
        try:
            send_left_button(True)
        except Exception:
            self.release()
            raise

    def release(self):
        if self.held or self.state.held():
            # 松开自己按下的键不能再被角落保护拦住，否则会卡在拖动状态。
            send_left_button(False)
            self.held = False
            self.state.set_held(False)
