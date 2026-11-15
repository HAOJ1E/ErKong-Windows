"""眨眼开关、无点击测试和后台结果消费；默认关闭且不保存摄像头画面。"""

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout

from nodlink.blink_detector import BlinkDetector
from nodlink.blink_worker import BlinkWorker


class BlinkController(QObject):
    changed = Signal(str)
    enabled_changed = Signal(bool)
    testing_changed = Signal(bool)
    click_requested = Signal(float)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.enabled = self.testing = self.click_allowed = False
        self.camera = 0
        self.detector = BlinkDetector()
        self.detected = 0
        self.left = self.right = None
        self.status_text = "眨眼单击关闭；需要摄像头"
        self.last_sample_time = self.hold_until = 0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.poll)

    def running(self):
        return self.worker is not None and self.worker.isRunning()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.detector.reset()
        self.enabled_changed.emit(self.enabled)
        self._sync_worker()

    def set_testing(self, enabled):
        self.testing = bool(enabled)
        self.testing_changed.emit(self.testing)
        self.detector.reset()
        self._sync_worker()

    def set_click_allowed(self, allowed):
        if self.click_allowed != bool(allowed):
            self.detector.reset()
        self.click_allowed = bool(allowed)

    def _status(self, message):
        self.status_text = message
        self.changed.emit(message)

    def _sync_worker(self):
        if self.enabled or self.testing:
            self.timer.start()
            if not self.running():
                if self.worker is not None:
                    self.worker.deleteLater()
                self.worker = BlinkWorker(self.camera, self)
                self.worker.status.connect(self._status)
                self.worker.failed.connect(self._failed)
                self.worker.finished.connect(self._finished)
                self.last_sample_time = self.hold_until = 0
                self.left = self.right = None
                self.worker.start()
                self._status("正在打开摄像头与预训练模型…")
        else:
            self.timer.stop()
            if self.running():
                self.worker.requestInterruption()
            self.detector.reset()
            self.left = self.right = None
            self.hold_until = 0
            self._status("眨眼单击关闭")

    def _failed(self, message):
        self.enabled = self.testing = False
        self.timer.stop()
        self.detector.reset()
        self.enabled_changed.emit(False)
        self.testing_changed.emit(False)
        self._status("眨眼检测已停止：" + message[:250])

    def _finished(self):
        self.finished.emit()
        # 快速关闭再打开时，等旧子进程退出后才重新占用摄像头。
        if self.enabled or self.testing:
            QTimer.singleShot(0, self._sync_worker)

    def poll(self):
        sample = self.worker.latest.take() if self.worker is not None else None
        now = time.monotonic()
        if sample is None:
            if self.last_sample_time and now - self.last_sample_time > 0.25:
                self.detector.reset()
                self.left = self.right = None
            return
        if not (self.enabled or self.testing):
            return
        timestamp = float(sample.get("received_at_monotonic", 0))
        if not 0 <= now - timestamp <= 0.25:
            self.detector.reset()
            return
        self.last_sample_time = timestamp
        self.left, self.right = sample.get("left"), sample.get("right")
        faces = sample.get("face_count", 0)
        blinked = self.detector.update(self.left, self.right, timestamp, faces)
        if faces != 1 or self.left is None or self.right is None:
            self.left = self.right = None
            self._status("未检测到唯一清晰人脸，点击暂停；请检查遮挡盖、光线并正对摄像头")
            return
        if blinked:
            self.detected += 1
            self.hold_until = now + 0.15
            if self.enabled and self.click_allowed and not self.testing:
                self.click_requested.emit(timestamp)
        mode = "仅检测，不点击" if self.testing or not self.click_allowed else "眨眼单击已启用"
        if not self.enabled and not self.testing:
            mode = "眨眼单击关闭"
        self._status(f"{mode} · 左眼闭合 {self.left:.0%} / 右眼闭合 {self.right:.0%} · 检出 {self.detected} 次")

    def hold_pointer(self, now):
        return self.enabled and not self.testing and self.click_allowed and (
            now < self.hold_until or (now - self.last_sample_time <= 0.25 and self.left is not None
                                     and self.right is not None and min(self.left, self.right) >= 0.5))

    def shutdown(self):
        self.enabled = self.testing = False
        self.enabled_changed.emit(False)
        self.testing_changed.emit(False)
        self._sync_worker()


class BlinkSettingsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("眨眼单击 · 摄像头检测与设置")
        self.resize(560, 260)
        layout = QVBoxLayout(self)
        text = QLabel("使用现成模型，无需个人训练。先睁眼，再稍长闭眼并睁开触发一次左键；短眨眼、单眼闭合、超过 1.2 秒的闭眼不触发。图像仅在本机内存处理。")
        text.setWordWrap(True)
        layout.addWidget(text)
        form = QFormLayout()
        self.camera = QSpinBox()
        self.camera.setRange(0, 9)
        self.camera.setValue(controller.camera)
        self.camera.setEnabled(not controller.running())
        self.camera.valueChanged.connect(lambda value: setattr(controller, "camera", value))
        form.addRow("摄像头编号（默认 0）", self.camera)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0.2, 0.8)
        self.duration.setSingleStep(0.05)
        self.duration.setSuffix(" 秒")
        self.duration.setValue(controller.detector.minimum_seconds)
        self.duration.valueChanged.connect(self._duration_changed)
        form.addRow("最短闭眼时长", self.duration)
        layout.addLayout(form)
        self.test = QCheckBox("测试眨眼（只计数，不点击）")
        self.test.setChecked(controller.testing)
        self.test.toggled.connect(controller.set_testing)
        controller.testing_changed.connect(self._sync_test)
        layout.addWidget(self.test)
        self.status = QLabel(controller.status_text)
        self.status.setWordWrap(True)
        controller.changed.connect(self.status.setText)
        layout.addWidget(self.status)
        close = QPushButton("完成")
        close.clicked.connect(self.close)
        layout.addWidget(close)

    def _duration_changed(self, value):
        self.controller.detector.minimum_seconds = value
        self.controller.detector.reset()

    def _sync_test(self, checked):
        self.test.blockSignals(True)
        self.test.setChecked(checked)
        self.test.blockSignals(False)

    def closeEvent(self, event):
        self.controller.set_testing(False)
        event.accept()
