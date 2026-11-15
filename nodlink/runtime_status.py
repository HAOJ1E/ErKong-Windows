"""读取头追运行条件并转换成用户能理解的状态，不启动任何系统服务。"""

import json
import os

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from nodlink.control_settings import SETTINGS_FOLDER
from nodlink.wpods_transport import bridge_availability
from nodlink.bridge_recovery import current_boot_time


def read_pause_policy() -> dict | None:
    """原生蓝牙恢复后保留暂停标记，避免用户误触重新采集。"""
    path = SETTINGS_FOLDER / "bridge-policy.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(policy, dict) or policy.get("schema_version") != 1:
            raise ValueError("暂停状态文件格式不兼容")
        if policy.get("mode") == "native_bluetooth":
            if policy.get("restart_required") and policy.get("restart_boot_time") == current_boot_time():
                return {"ready": False, "status": "restart_required", "summary": "Windows 要求重启后再启用头追",
                        "detail": "普通蓝牙已恢复。请保存工作后重新启动电脑，再点击“启用头追桥接”；当前不重复重置虚拟控制器。"}
            return {
                "ready": False, "status": "bridge_paused",
                "summary": "当前使用普通蓝牙，头追尚未连接",
                "detail": "点击“启用头追桥接”连接运动数据；期间蓝牙会短暂断开。失败时自动恢复普通蓝牙。",
            }
        if policy.get("mode") == "enabling":
            return {"ready": False, "status": "bridge_enabling", "summary": "正在连接头追桥接…",
                    "detail": "请等待虚拟蓝牙与运动数据检查完成；此时不启动训练或鼠标。"}
        if policy.get("mode") == "recovery_required":
            return {"ready": False, "status": "recovery_required", "summary": "普通蓝牙恢复尚未确认",
                    "detail": "请点击“恢复普通蓝牙”重试。" + str(policy.get("reason", ""))}
        if policy.get("mode") != "bridge":
            raise ValueError("未知的桥接运行模式")
        if policy.get("owner_pid") and int(policy["owner_pid"]) != os.getpid():
            return {"ready": False, "status": "bridge_other_session", "summary": "桥接由另一个 NodLink 窗口管理",
                    "detail": "请在启用桥接的那个窗口训练；若原窗口已退出，可在此重新启用。"}
        return None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        return {"ready": False, "status": "runtime_policy_invalid", "summary": "头追运行状态无法读取",
                "detail": "本地运行状态文件无效，暂时禁止采集，请检查配置。"}


def get_runtime_status() -> dict:
    policy = read_pause_policy()
    if policy is not None:
        return policy
    bridge = bridge_availability()
    status = bridge.get("status", "query_failed")
    if status == "pipe_available_unverified":
        return {"ready": True, "status": "bridge_ready", "summary": "桥接接口可用；尚未打开实时采集",
                "detail": "训练与鼠标控制仍需手动启动。接口可用不等于传感器已读到数据。"}
    explanations = {
        "not_running": ("头追桥接服务未运行", "当前不能训练或控制鼠标。普通 Windows 蓝牙无需启动头追桥接；请先解决桥接运行条件。"),
        "not_installed": ("尚未安装头追桥接", "当前无法读取耳机运动数据。"),
        "restart_required": ("桥接安装等待系统重启", "本次不启动采集。"),
        "pipe_unavailable": ("头追桥接接口尚未就绪", "当前不能训练或控制鼠标，请稍后重新检查。"),
    }
    summary, detail = explanations.get(status, ("无法检查头追桥接状态", "请检查运行日志；当前不启动采集。"))
    return {"ready": False, "status": status, "summary": summary, "detail": detail}


def capture_failure_message(report: dict) -> str:
    """统一解释失败原因，避免把环境未就绪显示成动作训练失败。"""
    if report.get("recovery_hint"):
        return report["recovery_hint"]
    if report.get("status") == "bridge_unavailable":
        state = report.get("bridge", {}).get("status")
        if state == "not_running":
            return "头追桥接服务未运行，本次采集没有开始。当前无法执行自动训练或鼠标控制。"
        return "头追桥接接口未就绪，本次采集没有开始。"
    if report.get("status") == "target_not_unique":
        return "未找到唯一匹配的普通 AirPods 4，请检查耳机型号与配对状态。"
    if report.get("status") == "no_handshake_reply":
        return "耳机没有回复连接请求，本次未开始运动数据采集。"
    return report.get("error") or "采集未完成，本次结果不会应用。"


class RuntimeStatusWorker(QThread):
    checked = Signal(object)

    def run(self):
        try:
            self.checked.emit(get_runtime_status())
        except Exception as error:
            self.checked.emit({"ready": False, "status": "query_failed", "summary": "运行条件检查失败", "detail": str(error)})


class RuntimeMonitor(QObject):
    """后台定期检查运行条件，窗口关闭前等待短检查线程完成。"""

    changed = Signal(object)
    finished = Signal()

    def __init__(self, parent=None, initial_status: dict | None = None):
        super().__init__(parent)
        # 注入状态快照供离线界面测试，正式入口始终执行真实检查。
        self.static = initial_status is not None
        self.status = dict(initial_status) if self.static else {
            "ready": False, "status": "checking", "summary": "正在检查头追运行条件…", "detail": "检查完成前不启动训练或控制。"}
        self.worker = None
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        if not self.static:
            self.timer.start()
            self.refresh()

    def refresh(self):
        if self.static or self.is_running():
            return
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = RuntimeStatusWorker(self)
        self.worker.checked.connect(self._checked)
        self.worker.finished.connect(self.finished.emit)
        self.worker.start()

    def _checked(self, status):
        self.status = status
        self.changed.emit(status)

    def stop(self):
        self.timer.stop()

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()
