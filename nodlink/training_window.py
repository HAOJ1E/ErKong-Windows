"""102 秒个人动作训练；训练只显示预览，不调用鼠标控制接口。"""

from datetime import datetime
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QShortcut, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QProgressBar, QVBoxLayout, QHBoxLayout, QTextEdit, QWidget

from nodlink.control_settings import SETTINGS_FOLDER, save_json_atomic, validate_profile
from nodlink.live_samples import LatestSample
from nodlink.motion_control import YAW_AXIS, PITCH_AXIS
from nodlink.motion_training import TRAINING_PHASES, TRAINING_SECONDS, fit_training, training_phase_at
from nodlink.motion_validation import quaternion_candidate, rotation_vector
from nodlink.sensor_capture import capture
from nodlink.runtime_status import RuntimeMonitor, capture_failure_message


REPORTS_FOLDER = Path(__file__).resolve().parents[1] / "reports"


class TrainingWorker(QThread):
    capture_started = Signal(float)
    completed = Signal(object, str)
    failed = Signal(str)

    def __init__(self, parent=None, reports_folder: Path = REPORTS_FOLDER):
        super().__init__(parent)
        self.latest = LatestSample()
        self.reports_folder = reports_folder

    def run(self):
        frames = []

        def receive(sample):
            # 保存训练所需的时间和 XYZ，不保存 AAP 原始包、地址和音频。
            if len(frames) < 12000:
                frames.append({"elapsed_seconds": sample["elapsed_seconds"],
                               "orientation_raw": sample["orientation_raw"]})
            self.latest.put(sample)

        try:
            report = capture("service16", TRAINING_SECONDS, on_sample=receive,
                             on_started=self.capture_started.emit,
                             stop_requested=self.isInterruptionRequested,
                             retain_evidence=False, allowed_product_ids=(0x2019,))
            report.pop("motion_protocol_messages", None)
            report["candidate_frames"] = frames
            report["mode"] = "personal_motion_training"
            report["training_phases"] = [
                {"key": phase.key, "instruction": phase.instruction, "seconds": phase.seconds,
                 "group": phase.group, "target": list(phase.target)} for phase in TRAINING_PHASES
            ]
            analysis = fit_training(frames)
            if self.isInterruptionRequested() or report.get("status") != "candidate_frames_need_validation" or not report.get("stop_command_sent"):
                reason = capture_failure_message(report) if report.get("status") not in (
                    "interrupted", "candidate_frames_need_validation"
                ) else "采集已停止或停止命令未确认，本次结果不会应用"
                analysis.update(passed=False, profile=None, reason=reason)
            report["training_analysis"] = analysis
            report["user_confirmed_training"] = False
            path = self.reports_folder / f"motion-training-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
            save_json_atomic(path, report)
            self.completed.emit(report, str(path))
        except Exception as error:
            self.failed.emit(f"训练失败：{type(error).__name__}: {error}")


class ProfileSaveWorker(QThread):
    saved = Signal(object)
    failed = Signal(str)

    def __init__(self, report: dict, report_path: Path, settings_folder: Path, parent=None):
        super().__init__(parent)
        self.report, self.report_path, self.settings_folder = report, report_path, settings_folder

    def run(self):
        try:
            profile = dict(self.report["training_analysis"]["profile"])
            profile.update(user_confirmed=True, source_report=str(self.report_path))
            validate_profile(profile)
            confirmed = dict(self.report)
            confirmed.update(user_confirmed_training=True, user_confirmed_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            save_json_atomic(self.report_path, confirmed)
            save_json_atomic(self.settings_folder / "motion-profile.json", profile)
            self.saved.emit(profile)
        except Exception as error:
            self.failed.emit(f"配置保存失败，未应用新参数：{error}")


class MotionPreview(QWidget):
    """仅在控件里画参考点和姿态点，不移动 Windows 光标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.position = np.zeros(2)
        self.target = np.zeros(2)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        cx, cy = self.width() / 2, self.height() / 2
        scale = min(cx - 15, cy - 15)
        painter.setPen(QPen(QColor("#d7e2ef"), 1))
        painter.drawLine(12, round(cy), self.width() - 12, round(cy))
        painter.drawLine(round(cx), 8, round(cx), self.height() - 8)
        for values, color, radius in ((self.target, "#2463eb", 7), (self.position, "#e87d19", 5)):
            x, y = np.clip(values, -1.0, 1.0)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(round(cx + x * scale) - radius, round(cy + y * scale) - radius, radius * 2, radius * 2)


class TrainingWindow(QMainWindow):
    profile_applied = Signal(object)
    busy_changed = Signal(bool)
    closed = Signal()

    def __init__(self, parent=None, settings_folder: Path = SETTINGS_FOLDER, reports_folder: Path = REPORTS_FOLDER,
                 initial_runtime_status: dict | None = None):
        super().__init__(parent)
        self.settings_folder, self.reports_folder = settings_folder, reports_folder
        self.setWindowTitle("NodLink · 自动训练与独立复测")
        self.resize(760, 720)
        self.worker = None
        self.save_worker = None
        self._started = None
        self._last_sample = None
        self._reference = None
        self._reference_samples = []
        self._closing = False
        self._countdown = 0
        self._report = None
        self._report_path = None
        self._phase_index = -1
        self._runtime_monitor = RuntimeMonitor(self, initial_runtime_status)
        self._runtime_status = self._runtime_monitor.status
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel("个人自动训练 · 102 秒")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)
        info = QLabel("佩戴耳机，按大字提示完成小幅动作、舒适范围动作和独立复测。\n"
                      "5 秒准备后开始。保持到回正提示出现；不必转到极限。训练期间鼠标保持手动操作。\n"
                      "训练数据仅保存在本机 reports 文件夹，包含时间和姿态 XYZ。")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.runtime_banner = QLabel()
        self.runtime_banner.setWordWrap(True)
        self.runtime_banner.setStyleSheet("background: #fff3e8; color: #863b16; padding: 10px;")
        layout.addWidget(self.runtime_banner)
        self.instruction = QLabel("准备好后点击“开始自动训练”")
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet("font-size: 23px; font-weight: 700; color: #164ca5; background: #eaf2ff; padding: 15px;")
        layout.addWidget(self.instruction)
        self.phase_label = QLabel("原有配置会保留；新配置需数据检查通过并由你确认动作后应用。")
        self.phase_label.setWordWrap(True)
        layout.addWidget(self.phase_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, int(TRAINING_SECONDS * 10))
        layout.addWidget(self.progress)
        self.preview = MotionPreview()
        layout.addWidget(self.preview)
        self.data_label = QLabel("蓝点：提示方向；橙点：初始映射预览。两者距离不是定位精度评分。")
        self.data_label.setWordWrap(True)
        layout.addWidget(self.data_label)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始自动训练")
        self.stop_button = QPushButton("停止训练")
        self.apply_button = QPushButton("我完成了全部动作，应用训练结果")
        self.stop_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.apply_button)
        layout.addLayout(row)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        self.setCentralWidget(central)
        self.start_button.clicked.connect(self.start_training)
        self.stop_button.clicked.connect(self.stop_training)
        self.apply_button.clicked.connect(self.apply_training)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh)
        self._prep_timer = QTimer(self)
        self._prep_timer.setInterval(1000)
        self._prep_timer.timeout.connect(self._countdown_tick)
        self._escape = QShortcut(QKeySequence("Esc"), self)
        self._escape.activated.connect(self.stop_training)
        self._runtime_monitor.changed.connect(self._runtime_changed)
        self._runtime_monitor.finished.connect(self._runtime_check_finished)
        self._runtime_changed(self._runtime_status)
        self._runtime_monitor.start()

    def _runtime_changed(self, status):
        self._runtime_status = status
        self.runtime_banner.setText(status["summary"] + "\n" + status["detail"])
        self.runtime_banner.setVisible(not status.get("ready", False))
        if not status.get("ready", False):
            if self._prep_timer.isActive() or (self.worker is not None and self.worker.isRunning()):
                self.stop_training()
            self.instruction.setText(status["summary"])
        self.start_button.setEnabled(status.get("ready", False) and not self.is_busy())

    def _runtime_check_finished(self):
        if self._closing:
            self.close()

    def is_busy(self):
        return (self._prep_timer.isActive() or (self.worker is not None and self.worker.isRunning())
                or (self.save_worker is not None and self.save_worker.isRunning()))

    def start_training(self):
        if self.is_busy():
            return
        if not self._runtime_status.get("ready", False):
            self.instruction.setText(self._runtime_status["summary"])
            return
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self._report = self._report_path = None
        self._closing = False
        self._started = self._last_sample = self._reference = None
        self._reference_samples.clear()
        self._phase_index = -1
        self.progress.setValue(0)
        self.preview.position[:] = 0
        self.preview.target[:] = 0
        self.preview.update()
        self._countdown = 5
        self.instruction.setText("5 秒后开始，请正视屏幕")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.apply_button.setEnabled(False)
        self.busy_changed.emit(True)
        self._prep_timer.start()

    def _countdown_tick(self):
        if not self._runtime_status.get("ready", False):
            self.stop_training()
            self.instruction.setText(self._runtime_status["summary"])
            return
        self._countdown -= 1
        if self._countdown > 0:
            self.instruction.setText(f"{self._countdown} 秒后开始，请正视屏幕")
            return
        self._prep_timer.stop()
        self.instruction.setText("正在打开运动通道…")
        self.worker = TrainingWorker(self, self.reports_folder)
        self.worker.capture_started.connect(self._capture_started)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _capture_started(self, started):
        self._started = started
        self._timer.start()

    def _refresh(self):
        sample = self.worker.latest.take() if self.worker is not None else None
        if sample is not None:
            quaternion = quaternion_candidate(sample["orientation_raw"])
            if quaternion is not None:
                self._last_sample = sample["received_at_monotonic"]
                if sample["elapsed_seconds"] < 6:
                    self._reference_samples.append(quaternion)
                    self._reference = np.mean(self._reference_samples, axis=0)
                    self._reference /= np.linalg.norm(self._reference)
                if self._reference is not None:
                    rotation = rotation_vector(self._reference, quaternion)
                    self.preview.position = np.array([-float(YAW_AXIS @ rotation) / 25, float(PITCH_AXIS @ rotation) / 18])
        if self._started is None:
            return
        elapsed = time.monotonic() - self._started
        index, phase, remaining = training_phase_at(elapsed)
        if phase is None:
            self.instruction.setText("采集结束，后台拟合与检查中…")
        else:
            self.instruction.setText(phase.instruction)
            self.phase_label.setText(f"阶段 {index + 1}/{len(TRAINING_PHASES)} · 剩余 {remaining:.1f} 秒 · 总计 {TRAINING_SECONDS - elapsed:.0f} 秒")
            self.preview.target = np.array(phase.target)
            if index != self._phase_index:
                self.log.append(f"{elapsed:.1f}s  {phase.instruction.replace(chr(10), '，')}")
                self._phase_index = index
        self.progress.setValue(min(self.progress.maximum(), int(elapsed * 10)))
        self.preview.update()
        # 初始零帧可能需要短暂预热；训练中失联则中止，保留旧配置。
        if elapsed < TRAINING_SECONDS and time.monotonic() - (self._last_sample or self._started) > 2.0:
            self.instruction.setText("有效姿态中断，正在停止训练")
            self.stop_training()

    def stop_training(self):
        if self._prep_timer.isActive():
            self._prep_timer.stop()
            self.instruction.setText("已取消准备，耳机通道未打开")
            self.start_button.setEnabled(self._runtime_status.get("ready", False))
            self.stop_button.setEnabled(False)
            self.busy_changed.emit(False)
        elif self.worker is not None and self.worker.isRunning():
            self._timer.stop()
            self.worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.instruction.setText("正在停止采集并保存训练记录…")

    def _completed(self, report, path):
        self._timer.stop()
        self._report, self._report_path = report, Path(path)
        analysis = report["training_analysis"]
        if report.get("status") in ("bridge_paused", "bridge_unavailable", "runtime_policy_invalid"):
            self.instruction.setText(report.get("error") or "头追运行条件未就绪，本次训练未开始")
            self.log.append(capture_failure_message(report))
        elif report.get("status") == "channel_busy":
            self.instruction.setText("运动通道被占用，本次训练未开始")
            self.log.append(report.get("recovery_hint", "请先退出其他耳机采集程序，再重试。"))
        else:
            self.instruction.setText("训练与复测通过，等待你的动作确认" if analysis["passed"] else "本次训练未通过，原有配置保留")
        self.log.append(analysis["reason"])
        self.log.append(f"训练记录：{path}")
        if "validation_rmse" in analysis:
            self.log.append(f"独立复测 RMSE：{analysis['validation_rmse']:.3f}（单位为舒适控制范围，非像素精度）")
        self.phase_label.setText(f"有效帧 {analysis.get('valid_frames', 0)} · {analysis.get('rate_hz', 0)} Hz")

    def _failed(self, message):
        self._timer.stop()
        self.instruction.setText("操作未完成，原有控制参数仍有效")
        self.log.append(message)

    def _finished(self):
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(self._runtime_status.get("ready", False))
        self.apply_button.setEnabled(bool(self._report and self._report["training_analysis"]["passed"]))
        self.busy_changed.emit(False)
        if self._closing:
            self.close()

    def apply_training(self):
        if self.is_busy() or not self._report or not self._report["training_analysis"]["passed"]:
            return
        self.apply_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.busy_changed.emit(True)
        if self.save_worker is not None:
            self.save_worker.deleteLater()
        self.save_worker = ProfileSaveWorker(self._report, self._report_path, self.settings_folder, self)
        self.save_worker.saved.connect(self._profile_saved)
        self.save_worker.failed.connect(self._failed)
        self.save_worker.finished.connect(self._save_finished)
        self.save_worker.start()

    def _profile_saved(self, profile):
        self.instruction.setText("个人训练配置已保存，下次启动控制时使用")
        self.log.append("训练结束；鼠标控制仍需在控制窗口手动启动。")
        self.profile_applied.emit(profile)

    def _save_finished(self):
        self.start_button.setEnabled(self._runtime_status.get("ready", False))
        self.busy_changed.emit(False)
        if self._closing:
            self.close()

    def closeEvent(self, event):
        self._prep_timer.stop()
        self._runtime_monitor.stop()
        if self.is_busy() or self._runtime_monitor.is_running():
            self._closing = True
            self.stop_training()
            event.ignore()
            return
        self.busy_changed.emit(False)
        self.closed.emit()
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._runtime_monitor.start()
