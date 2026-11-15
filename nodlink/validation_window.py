"""AirPods 4 真实运动数据的引导验证窗口。"""

from datetime import datetime
import json
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout, QLabel, QMainWindow, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)

from nodlink.motion_validation import PHASES, analyze_guided_capture, phase_at, quaternion_candidate, rotation_vector
from nodlink.sensor_capture import capture


class ValidationWorker(QThread):
    sample_received = Signal(object)
    capture_started = Signal(float)
    completed = Signal(object, str)
    failed = Signal(str)

    def run(self) -> None:
        try:
            report = capture(
                "service16", sum(item[2] for item in PHASES),
                on_sample=self.sample_received.emit,
                on_started=self.capture_started.emit,
                stop_requested=self.isInterruptionRequested,
            )
            report["guided_phases"] = [
                {"key": key, "instruction": text, "duration": duration}
                for key, text, duration in PHASES
            ]
            report["guided_analysis"] = analyze_guided_capture(report["candidate_frames"])
            report["user_confirmed_movements"] = False
            folder = Path(__file__).resolve().parents[1] / "reports"
            folder.mkdir(exist_ok=True)
            path = folder / f"motion-validation-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
            with path.open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            self.completed.emit(report, str(path))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")


class ValidationWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("耳控 · AirPods 4 动作验证")
        self.resize(820, 670)
        self._worker = None
        self._started = None
        self._calibration = []
        self._reference = None
        self._frame_count = 0
        self._report = None
        self._report_path = None
        self._closing = False
        self._phase_index = -1
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._update_phase)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._countdown_remaining = 0

    def _build_ui(self):
        root_widget = QWidget()
        root = QVBoxLayout(root_widget)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(15)
        title = QLabel("AirPods 4 · 真实动作验证")
        title.setStyleSheet("font-size: 25px; font-weight: 700; color: #173251;")
        root.addWidget(title)
        introduction = QLabel(
            "佩戴并连接耳机，5 秒准备后，按大字提示完成 9 个阶段，共 54 秒。\n"
            "请缓慢转动，不必大幅度。此窗口不会移动鼠标。"
        )
        introduction.setWordWrap(True)
        root.addWidget(introduction)
        self.instruction = QLabel("准备好后，点击“开始动作验证”")
        self.instruction.setWordWrap(True)
        self.instruction.setStyleSheet("background: #eaf2ff; color: #164ca5; padding: 22px; font-size: 25px; font-weight: 700; border-radius: 10px;")
        root.addWidget(self.instruction)
        self.phase_label = QLabel("尚未开始")
        root.addWidget(self.phase_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, int(sum(item[2] for item in PHASES) * 10))
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        values = QGridLayout()
        self.axis_labels = []
        for column, axis in enumerate(("X", "Y", "Z")):
            label = QLabel(f"相对旋转 {axis}\n—")
            label.setStyleSheet("background: white; border: 1px solid #d6e0ec; border-radius: 8px; padding: 16px; font-size: 20px;")
            self.axis_labels.append(label)
            values.addWidget(label, 0, column)
        root.addLayout(values)
        self.frame_label = QLabel("数据：尚未采集；上方为候选相对旋转，控制轴仅按本机普通 AirPods 4 实测拟合。")
        self.frame_label.setWordWrap(True)
        root.addWidget(self.frame_label)
        buttons = QGridLayout()
        self.start_button = QPushButton("开始动作验证")
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_capture)
        self.confirm_button = QPushButton("我已按提示完成动作")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.confirm_movements)
        buttons.addWidget(self.start_button, 0, 0)
        buttons.addWidget(self.stop_button, 0, 1)
        buttons.addWidget(self.confirm_button, 0, 2)
        root.addLayout(buttons)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        self.log.setPlainText("等待用户准备；所有显示数据均来自本机耳机，不生成模拟传感器值。")
        root.addWidget(self.log)
        self.setCentralWidget(root_widget)
        self.setStyleSheet("QMainWindow { background: #f5f8fc; } QPushButton { padding: 10px; } QLabel { color: #243c55; }")

    def start_capture(self):
        if self._countdown_timer.isActive():
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._started = None
        self._calibration = []
        self._reference = None
        self._frame_count = 0
        self._phase_index = -1
        self._report = None
        self._report_path = None
        self.progress.setValue(0)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.confirm_button.setEnabled(False)
        self._countdown_remaining = 5
        self.instruction.setText("5 秒后开始，请保持正视")
        self.phase_label.setText("每个阶段 6 秒；转到提示方向后保持，看到回正提示再回正。")
        self.frame_label.setText("准备中，尚未开始本次采集。")
        for label, axis in zip(self.axis_labels, ("X", "Y", "Z")):
            label.setText(f"相对旋转 {axis}\n—")
        self._countdown_timer.start()

    def _countdown_tick(self):
        self._countdown_remaining -= 1
        if self._countdown_remaining > 0:
            self.instruction.setText(f"{self._countdown_remaining} 秒后开始，请保持正视")
            return
        self._countdown_timer.stop()
        self.instruction.setText("正在打开耳机数据通道…")
        self.log.append("打开唯一配对的 AirPods 4，使用已收到连续数据的 service16 协议。")
        self._worker = ValidationWorker(self)
        self._worker.sample_received.connect(self._show_sample)
        self._worker.capture_started.connect(self._capture_started)
        self._worker.completed.connect(self._capture_completed)
        self._worker.failed.connect(self._capture_failed)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()

    def _capture_started(self, started):
        self._started = started
        self._timer.start()
        self._update_phase()

    def _update_phase(self):
        if self._started is None:
            return
        elapsed = time.monotonic() - self._started
        index, _, instruction, remaining = phase_at(elapsed)
        self.instruction.setText(instruction)
        self.phase_label.setText(f"阶段 {min(index + 1, len(PHASES))}/{len(PHASES)} · 本阶段剩余 {remaining:.1f} 秒")
        self.progress.setValue(min(self.progress.maximum(), int(elapsed * 10)))
        if index != self._phase_index:
            self.log.append(instruction)
            self._phase_index = index

    def _show_sample(self, sample):
        self._frame_count += 1
        elapsed = sample["elapsed_seconds"]
        rate = self._frame_count / elapsed if elapsed > 0 else 0
        self.frame_label.setText(f"真实候选帧：{self._frame_count} · 平均约 {rate:.1f} Hz · 坐标方向待动作验证")
        quaternion = quaternion_candidate(sample["orientation_raw"])
        if quaternion is None:
            return
        if self._reference is None:
            self._calibration.append(quaternion)
            if len(self._calibration) < 25:
                return
            self._reference = np.mean(self._calibration, axis=0)
            self._reference /= np.linalg.norm(self._reference)
        rotation = rotation_vector(self._reference, quaternion)
        for label, axis, value in zip(self.axis_labels, ("X", "Y", "Z"), rotation):
            label.setText(f"相对旋转 {axis}\n{value:+.1f}°")

    def stop_capture(self):
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.instruction.setText("已取消准备，尚未打开耳机通道")
            return
        if self._worker is not None:
            self._worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.instruction.setText("正在发送停止请求并保存报告…")

    def _capture_completed(self, report, path):
        self._timer.stop()
        self._report, self._report_path = report, Path(path)
        self.stop_button.setEnabled(False)
        analysis = report["guided_analysis"]
        if analysis["passed"]:
            self.instruction.setText("动作数据检查通过，请确认动作执行情况")
            self.confirm_button.setEnabled(True)
            self.log.append("连续性、反向动作、方向区分与回正检查通过；尚需用户确认动作。")
        else:
            self.instruction.setText("采集已结束，动作对应关系尚未验证")
            self.log.append(analysis["reason"])
            self.log.append(f"采集状态：{report['status']}；错误：{report.get('error', '无')}")
            if analysis.get("criteria"):
                self.log.append(str(analysis["criteria"]))
        self.phase_label.setText(f"报告已保存 · 有效候选帧 {analysis['valid_frames']}")
        self.log.append(f"报告：{path}")

    def _capture_failed(self, message):
        self._timer.stop()
        self.stop_button.setEnabled(False)
        self.instruction.setText("采集未完成")
        self.log.append(message)

    def _worker_finished(self):
        self.start_button.setEnabled(True)
        if self._closing:
            self.close()

    def confirm_movements(self):
        if not self._report or not self._report["guided_analysis"]["passed"]:
            return
        self._report["user_confirmed_movements"] = True
        self._report["user_confirmed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._report["head_motion_verified"] = True
        self._report["verification_scope"] = "本次普通 AirPods 4 的连续数据与引导动作对应；鼠标控制需在独立窗口另行启动，不代表所有型号或固件兼容。"
        try:
            self._report_path.write_text(json.dumps(self._report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self.log.append(f"确认结果保存失败：{error}")
            return
        self.instruction.setText("本次真实头部动作验证已完成")
        self.log.append("用户已确认按照提示完成动作；本次验证结果已保存。")
        self.confirm_button.setEnabled(False)

    def closeEvent(self, event):
        self._countdown_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._closing = True
            self.stop_capture()
            event.ignore()
        else:
            event.accept()
