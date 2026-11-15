"""隔离摄像头驱动和模型推理，关闭开关时可限时结束整个子进程。"""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QThread, Signal
from nodlink.live_samples import LatestSample


class BlinkWorker(QThread):
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, camera=0, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.latest = LatestSample()
        self.last_frame_at = 0

    def run(self):
        process = None
        try:
            project = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--blink-engine"]
            else:
                command = [str(Path(sys.executable).with_name("python.exe")), "-m", "nodlink.blink_cli"]
            command += ["--camera", str(self.camera), "--parent", str(os.getpid())]
            with (project / "reports" / "blink-engine.stderr.log").open("w", encoding="utf-8") as error_log:
                process = subprocess.Popen(command, cwd=project, stdout=subprocess.PIPE, stderr=error_log,
                                           env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                messages = queue.Queue(maxsize=1)

                def read_messages():
                    for line in process.stdout:
                        try:
                            value = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if value.get("kind") == "sample":
                            self.last_frame_at = float(value.get("received_at_monotonic", 0))
                            self.latest.put(value)
                        else:
                            try:
                                messages.put_nowait(value)
                            except queue.Full:
                                pass

                reader = threading.Thread(target=read_messages, daemon=True)
                reader.start()
                deadline = time.monotonic() + 20
                ready = False
                while not self.isInterruptionRequested():
                    try:
                        message = messages.get(timeout=0.1)
                        if message["kind"] == "ready":
                            ready = True
                            self.status.emit("摄像头已打开，正在检测双眼")
                        elif message["kind"] == "error":
                            raise RuntimeError(message.get("message", "摄像头检测失败"))
                    except queue.Empty:
                        pass
                    if self.last_frame_at:
                        deadline = self.last_frame_at + 3
                    if time.monotonic() > deadline:
                        raise TimeoutError("摄像头长时间未返回新数据，已关闭眨眼检测")
                    if process.poll() is not None:
                        raise RuntimeError("摄像头进程已退出，眨眼点击已关闭")
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
        finally:
            if process is not None:
                if process.poll() is None:
                    # venv 启动器还有 Python 子进程，必须一起结束才能释放摄像头。
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                   timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
                    process.wait(timeout=2)
                if process.stdout:
                    process.stdout.close()
