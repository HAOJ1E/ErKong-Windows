"""带个人训练与灵敏度的头部鼠标窗口；只处理最新姿态以免消息积压。"""

from datetime import datetime
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSlider, QTextEdit, QVBoxLayout, QWidget,
)

from nodlink.control_settings import SETTINGS_FOLDER, load_profile, load_sensitivity, load_stability, save_json_atomic
from nodlink.live_samples import LatestSample
from nodlink.motion_control import MotionPointerMapper, latest_verified_validation_report
from nodlink.motion_validation import quaternion_candidate
from nodlink.sensor_capture import capture
from nodlink.training_window import TrainingWindow
from nodlink.runtime_status import RuntimeMonitor, capture_failure_message
from nodlink.bridge_recovery import BridgeRecovery
from nodlink.bridge_worker import BridgeRecoveryWorker
from nodlink.blink_controls import BlinkController, BlinkSettingsDialog
from nodlink.pointer_stabilizer import PointerStabilizer
from nodlink.dwell_hold import DwellHold
from nodlink.dwell_overlay import DwellProgressOverlay
from nodlink.owned_mouse_button import OwnedLeftButton, release_key_pressed


REPORTS_FOLDER = Path(__file__).resolve().parents[1] / "reports"
CALIBRATION_SECONDS = 3.0
SENSOR_TIMEOUT_SECONDS = 0.7


class ControlWorker(QThread):
    """采集线程覆盖最新帧；不逐帧排队发送 Qt 信号。"""

    capture_started = Signal(float)
    capture_completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.latest = LatestSample()

    def run(self):
        result = capture("service16", 8 * 60 * 60, on_sample=self.latest.put,
                         on_started=self.capture_started.emit, stop_requested=self.isInterruptionRequested,
                         retain_evidence=False, allowed_product_ids=(0x2019,))
        self.capture_completed.emit(result)


class JsonSaveWorker(QThread):
    """后台保存文件，避免磁盘慢时阻塞 Qt 主线程。"""

    failed = Signal(str)

    def __init__(self, path: Path, value: dict, parent=None, extra_files=None):
        super().__init__(parent)
        self.path, self.value = path, value
        self.extra_files = extra_files or []

    def run(self):
        try:
            save_json_atomic(self.path, self.value)
            for path, value in self.extra_files:
                save_json_atomic(path, value)
        except Exception as error:
            self.failed.emit(f"保存失败：{self.path.name}：{error}")


class MouseControlWindow(QMainWindow):
    def __init__(self, settings_folder: Path = SETTINGS_FOLDER, initial_runtime_status: dict | None = None, bridge_manager=None):
        super().__init__()
        self.settings_folder = settings_folder
        self.setWindowTitle("NodLink · 自动训练与头部鼠标控制")
        self.resize(820, min(850, self.screen().availableGeometry().height() - 40))
        self._worker = None
        self._bridge_manager = bridge_manager or BridgeRecovery(settings_folder)
        self._bridge_worker = None
        self._bridge_error = False
        self._report_writer = self._settings_writer = None
        self._training_window = None
        self._training_open = False
        self._profile, profile_message = load_profile(settings_folder)
        self._sensitivity = load_sensitivity(settings_folder)
        self._stability = load_stability(settings_folder)
        self._pointer_stabilizer = PointerStabilizer(self._stability)
        self._blink_controller = BlinkController(self)
        self._blink_dialog = None
        self._click_count = 0
        self._dwell = DwellHold()
        self._left_button = OwnedLeftButton(self._bridge_manager.token)
        self._dwell_overlay = DwellProgressOverlay(self)
        self._dwell_flash_until = 0
        self._dwell_eye_hold_since = None
        self._dwell_actions = 0
        self._mapper = MotionPointerMapper()
        self._calibration_samples = []
        self._stream_started = self._last_valid_sample_at = self._last_pointer = None
        self._sample_count = self._move_count = 0
        self._control_activated = self._ever_activated = False
        self._accepting_samples = False
        self._stopping_reason = "用户停止"
        self._closing = self._has_verified_report = False
        self._runtime_monitor = RuntimeMonitor(self, initial_runtime_status)
        self._runtime_status = self._runtime_monitor.status
        self._build_ui()
        self.profile_label.setText(profile_message)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(33)
        self._poll_timer.timeout.connect(self._poll_sample)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(100)
        self._watchdog.timeout.connect(self._check_sensor_timeout)
        self._dwell_timer = QTimer(self)
        self._dwell_timer.setInterval(33)
        self._dwell_timer.timeout.connect(self._poll_dwell)
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.timeout.connect(self._save_sensitivity)
        self._escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._escape_shortcut.activated.connect(self.stop_control)
        self._runtime_monitor.changed.connect(self._runtime_changed)
        self._runtime_monitor.finished.connect(self._runtime_check_finished)
        self._blink_controller.click_requested.connect(self._blink_click)
        self._blink_controller.finished.connect(self._runtime_check_finished)

        verified = latest_verified_validation_report(REPORTS_FOLDER)
        if verified is None:
            self.status_label.setText("请先完成本机普通 AirPods 4 动作验证")
        else:
            self._validation_report_path, self._validation_report = verified
            self._has_verified_report = True
            self.status_label.setText("本机 AirPods 4 动作已验证；控制当前关闭")
            self.log.append(f"动作依据：{self._validation_report_path.name}")
        self._refresh_availability()
        self._runtime_changed(self._runtime_status)
        self._runtime_monitor.start()

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel("AirPods 4 · 自动训练与鼠标控制")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #173251;")
        root.addWidget(title)
        info = QLabel("先训练个人方向和动作范围，再调整灵敏度。启动控制后请正视 3 秒；正视对应启动时的指针位置。")
        info.setWordWrap(True)
        root.addWidget(info)
        safety = QLabel("停止：红色按钮，或本窗口有焦点时按 Esc。0.7 秒没有有效新姿态会自动停止。屏幕角落保护保持启用。")
        safety.setWordWrap(True)
        safety.setStyleSheet("background: #fff3e8; color: #863b16; padding: 12px;")
        root.addWidget(safety)

        self.runtime_banner = QLabel()
        self.runtime_banner.setWordWrap(True)
        self.runtime_banner.setStyleSheet("background: #fff3e8; color: #863b16; padding: 12px; font-weight: 600;")
        root.addWidget(self.runtime_banner)
        self.refresh_runtime_button = QPushButton("重新检查运行条件")
        self.refresh_runtime_button.clicked.connect(self._runtime_monitor.refresh)
        root.addWidget(self.refresh_runtime_button)
        bridge_row = QHBoxLayout()
        self.enable_bridge_button = QPushButton("启用头追桥接（会短暂断开蓝牙）")
        self.enable_bridge_button.clicked.connect(self.enable_bridge)
        self.restore_bluetooth_button = QPushButton("恢复普通蓝牙")
        self.restore_bluetooth_button.clicked.connect(self.restore_bluetooth)
        bridge_row.addWidget(self.enable_bridge_button)
        bridge_row.addWidget(self.restore_bluetooth_button)
        root.addLayout(bridge_row)

        # 设置区域可滚动，停止按钮和实时状态始终留在主窗口。
        settings_body = QWidget()
        settings_layout = QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(0, 0, 8, 0)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setWidget(settings_body)
        self.settings_scroll.setMinimumHeight(150)
        root.addWidget(self.settings_scroll, 2)

        training_group = QGroupBox("个人训练")
        training_layout = QVBoxLayout(training_group)
        training_row = QHBoxLayout()
        self.train_button = QPushButton("自动训练（102 秒）")
        self.train_button.clicked.connect(self.open_training)
        self.use_profile_checkbox = QCheckBox("使用个人训练配置")
        self.use_profile_checkbox.setChecked(self._profile is not None)
        self.use_profile_checkbox.setEnabled(self._profile is not None)
        self.use_profile_checkbox.toggled.connect(self._show_profile_selection)
        training_row.addWidget(self.train_button)
        training_row.addWidget(self.use_profile_checkbox)
        training_layout.addLayout(training_row)
        self.profile_label = QLabel()
        self.profile_label.setWordWrap(True)
        training_layout.addWidget(self.profile_label)
        settings_layout.addWidget(training_group)

        sensitivity_group = QGroupBox("灵敏度 · 调大后相同转头幅度移动更远")
        grid = QGridLayout(sensitivity_group)
        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(25, 300)
        self.sensitivity_slider.setValue(round(self._sensitivity["overall"] * 100))
        self.sensitivity_value = QLabel(f"{self._sensitivity['overall']:.2f} 倍")
        grid.addWidget(QLabel("总体"), 0, 0)
        grid.addWidget(self.sensitivity_slider, 0, 1)
        grid.addWidget(self.sensitivity_value, 0, 2)
        self.horizontal_sensitivity = QDoubleSpinBox()
        self.vertical_sensitivity = QDoubleSpinBox()
        for index, (spin, key, name) in enumerate((
            (self.horizontal_sensitivity, "horizontal", "横向倍率"),
            (self.vertical_sensitivity, "vertical", "纵向倍率"),
        ), start=1):
            spin.setRange(0.25, 3.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setValue(self._sensitivity[key])
            grid.addWidget(QLabel(name), index, 0)
            grid.addWidget(spin, index, 1)
            spin.valueChanged.connect(self._sensitivity_changed)
        reset = QPushButton("重置为 1 倍")
        reset.clicked.connect(self._reset_sensitivity)
        grid.addWidget(reset, 1, 2, 2, 1)
        self.sensitivity_slider.valueChanged.connect(self._sensitivity_changed)
        self.stability_slider = QSlider(Qt.Orientation.Horizontal)
        self.stability_slider.setRange(0, 100)
        self.stability_slider.setValue(self._stability)
        self.stability_value = QLabel(f"{self._stability}%")
        self.stability_slider.valueChanged.connect(self._stability_changed)
        grid.addWidget(QLabel("悬停防抖"), 3, 0)
        grid.addWidget(self.stability_slider, 3, 1)
        grid.addWidget(self.stability_value, 3, 2)
        settings_layout.addWidget(sensitivity_group)

        blink_row = QHBoxLayout()
        self.blink_checkbox = QCheckBox("启用眨眼单击（摄像头）")
        self.blink_checkbox.toggled.connect(self._blink_controller.set_enabled)
        self._blink_controller.enabled_changed.connect(self._sync_blink_checkbox)
        self.blink_settings_button = QPushButton("眨眼检测 / 设置")
        self.blink_settings_button.clicked.connect(self.open_blink_settings)
        blink_row.addWidget(self.blink_checkbox)
        blink_row.addWidget(self.blink_settings_button)
        settings_layout.addLayout(blink_row)
        self.blink_status = QLabel(self._blink_controller.status_text)
        self.blink_status.setWordWrap(True)
        self._blink_controller.changed.connect(self.blink_status.setText)
        settings_layout.addWidget(self.blink_status)

        dwell_row = QHBoxLayout()
        self.dwell_checkbox = QCheckBox("启用悬停长按（5 秒）")
        self.dwell_checkbox.toggled.connect(self._dwell_changed)
        self.release_button = QPushButton("立即松开左键（F8）")
        self.release_button.setEnabled(False)
        self.release_button.clicked.connect(self._manual_release)
        dwell_row.addWidget(self.dwell_checkbox)
        dwell_row.addWidget(self.release_button)
        root.addLayout(dwell_row)
        self.dwell_status = QLabel("悬停长按关闭；按住后移动，再悬停 5 秒松开。")
        self.dwell_status.setWordWrap(True)
        root.addWidget(self.dwell_status)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: 600; color: #244e7a;")
        root.addWidget(self.status_label)
        self.live_label = QLabel("实时数据未连接；鼠标控制关闭")
        root.addWidget(self.live_label)
        self.ready_checkbox = QCheckBox("我已佩戴耳机并准备测试鼠标移动")
        self.ready_checkbox.setEnabled(False)
        self.ready_checkbox.toggled.connect(self._update_start_button)
        root.addWidget(self.ready_checkbox)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("启动头部鼠标控制")
        self.start_button.clicked.connect(self.start_control)
        self.stop_button = QPushButton("停止控制")
        self.stop_button.clicked.connect(self.stop_control)
        self.stop_button.setStyleSheet("QPushButton { background: #c0392b; color: white; font-weight: 700; } QPushButton:disabled { background: #aeb8c6; }")
        for button in (self.start_button, self.stop_button):
            button.setMinimumHeight(44)
            button.setEnabled(False)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(120)
        self.log.setPlainText("等待用户操作；训练与控制均需手动启动。")
        root.addWidget(self.log, 1)
        self.setCentralWidget(central)
        self.setStyleSheet("QMainWindow { background: #f5f8fc; } QPushButton { padding: 9px; } QGroupBox { margin-top: 12px; padding-top: 12px; }")

    def _worker_running(self):
        return self._worker is not None and self._worker.isRunning()

    def _bridge_running(self):
        return self._bridge_worker is not None and self._bridge_worker.isRunning()

    def enable_bridge(self):
        if not self.enable_bridge_button.isEnabled():
            return
        self._start_bridge_work("enable")

    def restore_bluetooth(self):
        if not self.restore_bluetooth_button.isEnabled():
            return
        self._start_bridge_work("disable")

    def _start_bridge_work(self, operation):
        self.ready_checkbox.setChecked(False)
        self._bridge_error = False
        if self._bridge_worker is not None:
            self._bridge_worker.deleteLater()
        self._bridge_worker = BridgeRecoveryWorker(self._bridge_manager, operation, self)
        self._bridge_worker.progress.connect(self._bridge_progress)
        self._bridge_worker.completed.connect(self._bridge_completed)
        self._bridge_worker.failed.connect(self._bridge_failed)
        self._bridge_worker.finished.connect(self._bridge_finished)
        self.status_label.setText("正在启用头追桥接…" if operation == "enable" else "正在恢复普通蓝牙…")
        self._bridge_worker.start()
        self.stop_button.setText("取消连接" if operation == "enable" else "正在恢复…")
        self.stop_button.setEnabled(operation == "enable")
        self._refresh_availability()

    def _bridge_progress(self, message):
        self.status_label.setText(message)
        self.runtime_banner.setText(message)
        self.runtime_banner.show()
        self.log.append(message)

    def _bridge_completed(self, result):
        if result.get("ready"):
            probe = result["probe"]
            self.log.append(f"真实短测：{probe['valid_frames']} 个有效帧；鼠标未启动。")
            status = {"ready": True, "status": "bridge_ready", "summary": "头追桥接已连接",
                      "detail": f"短测收到 {probe['valid_frames']} 个有效运动帧，可以开始自动训练。"}
        else:
            status = {"ready": False, "status": "bridge_paused", "summary": "已恢复普通蓝牙；头追已断开",
                      "detail": "需要训练时，请再次点击“启用头追桥接”。"}
            self.log.append(status["summary"])
        self._runtime_changed(status)
        self.status_label.setText(status["summary"] + "；鼠标控制关闭")

    def _bridge_failed(self, error):
        self._bridge_error = True
        self._closing = self._closing and not self._bridge_manager.owns_bridge
        self._runtime_status = {"ready": False, "status": "bridge_failed", "summary": "桥接操作未完成", "detail": error}
        self._bridge_progress("桥接操作未完成：" + error)
        self._runtime_monitor.start()

    def _bridge_finished(self):
        self.stop_button.setText("停止控制")
        self.stop_button.setEnabled(False)
        self._runtime_changed(self._runtime_status)
        self._refresh_availability()
        self._runtime_monitor.refresh()

    def _runtime_changed(self, status):
        was_ready = self._runtime_status.get("ready", False)
        self._runtime_status = status
        if self._bridge_running():
            self._refresh_availability()
            return
        self.runtime_banner.setText(status["summary"] + "\n" + status["detail"])
        self.runtime_banner.setVisible(not status.get("ready", False))
        if not status.get("ready", False):
            self.ready_checkbox.setChecked(False)
            if self._accepting_samples:
                self._stopping_reason = status["summary"]
                self.stop_control()
            elif not self._worker_running():
                self.status_label.setText(status["summary"])
        elif not was_ready and not self._worker_running():
            self.status_label.setText("头追接口已就绪；实时采集未启动")
        self._refresh_availability()

    def _runtime_check_finished(self):
        if self._closing:
            self.close()

    def _refresh_availability(self):
        running = self._worker_running()
        saving = self._report_writer is not None and self._report_writer.isRunning()
        idle = not running and not saving and not self._training_open and not self._closing and not self._bridge_running()
        available = self._runtime_status.get("ready", False)
        self.enable_bridge_button.setEnabled(idle and not self._bridge_manager.owns_bridge)
        self.restore_bluetooth_button.setEnabled(idle and (self._bridge_manager.owns_bridge or self._runtime_status.get("status") == "recovery_required"))
        self.ready_checkbox.setEnabled(self._has_verified_report and idle and available)
        self.train_button.setEnabled(self._has_verified_report and idle and available)
        self.use_profile_checkbox.setEnabled(self._profile is not None and idle)
        self._update_start_button()
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _update_start_button(self, _checked=False):
        ready = self._has_verified_report and self.ready_checkbox.isEnabled() and self.ready_checkbox.isChecked()
        self.start_button.setEnabled(ready and self._runtime_status.get("ready", False) and not self._worker_running() and not self._training_open)

    def _show_profile_selection(self, _checked=False):
        self.profile_label.setText("下次控制使用个人训练参数" if self.use_profile_checkbox.isChecked()
                                   else "使用初始控制参数；可随时重新训练")

    def _sensitivity_changed(self, _value=None):
        self._sensitivity = {"overall": self.sensitivity_slider.value() / 100,
                             "horizontal": self.horizontal_sensitivity.value(),
                             "vertical": self.vertical_sensitivity.value()}
        self.sensitivity_value.setText(f"{self._sensitivity['overall']:.2f} 倍")
        self._mapper.set_sensitivity(**self._sensitivity)
        self._settings_timer.start(500)

    def _stability_changed(self, value):
        self._stability = value
        self.stability_value.setText(f"{value}%")
        self._pointer_stabilizer.set_strength(value)
        self._pointer_stabilizer.reset(self._last_pointer)
        self._settings_timer.start(500)

    def _sync_blink_checkbox(self, checked):
        self.blink_checkbox.blockSignals(True)
        self.blink_checkbox.setChecked(checked)
        self.blink_checkbox.blockSignals(False)

    def open_blink_settings(self):
        if self._blink_dialog is None:
            self._blink_dialog = BlinkSettingsDialog(self._blink_controller, self)
        self._blink_dialog.show()
        self._blink_dialog.raise_()
        self._blink_dialog.activateWindow()

    def _blink_click(self, timestamp):
        now = time.monotonic()
        if (self._left_button.held or not self._blink_controller.enabled or self._blink_controller.testing
                or not self._accepting_samples or not self._control_activated or self._training_open
                or not self._runtime_status.get("ready") or not 0 <= now - timestamp <= 0.25
                or self._last_valid_sample_at is None or now - self._last_valid_sample_at > 0.3):
            return
        pyautogui = None
        try:
            import pyautogui
            self._dwell.applied(False, tuple(pyautogui.position()))
            self._dwell_overlay.hide()
            pyautogui.click(button="left", _pause=False)
            self._click_count += 1
            self.log.append(f"眨眼左键单击 · 本次 {self._click_count} 次")
        except Exception as error:
            self._pointer_failed(error, pyautogui)

    def _pointer_failed(self, error, pyautogui):
        if pyautogui is not None and isinstance(error, pyautogui.FailSafeException):
            self._stopping_reason = "角落保护已停止控制：请用实体鼠标移出屏幕角落，重新勾选准备后再启动"
        else:
            self._stopping_reason = f"鼠标操作失败：{error}"
        self.log.append(self._stopping_reason)
        self._blink_controller.set_enabled(False)
        self.stop_control()

    def _dwell_changed(self, checked):
        if not checked:
            self._cancel_dwell()
            self.dwell_status.setText("悬停长按关闭")
        else:
            self._dwell.cancel()
            self.dwell_status.setText("头控启动后悬停 5 秒按住；移动后再悬停 5 秒松开。F8 随时松开。")
            if self._accepting_samples and self._control_activated:
                self._dwell_timer.start()

    def _cancel_dwell(self):
        self._dwell_timer.stop()
        self._dwell_overlay.hide()
        self._dwell_eye_hold_since = None
        try:
            self._left_button.release()
        except Exception as error:
            self.log.append(f"左键松开失败，请按实体鼠标左键后松开：{error}")
            self.release_button.setEnabled(True)
            return False
        self._dwell.cancel()
        self.release_button.setEnabled(False)
        return True

    def _manual_release(self):
        if self._cancel_dwell():
            self.dwell_status.setText("左键已松开；移动到新位置后才重新计时")
            if self.dwell_checkbox.isChecked() and self._accepting_samples and self._control_activated:
                import pyautogui
                self._dwell.applied(False, tuple(pyautogui.position()))
                self._dwell_timer.start()

    def _poll_dwell(self):
        now = time.monotonic()
        if (not self.dwell_checkbox.isChecked() or not self._accepting_samples or not self._control_activated
                or self._training_open or not self._runtime_status.get("ready")
                or self._last_valid_sample_at is None or now - self._last_valid_sample_at > SENSOR_TIMEOUT_SECONDS):
            self._cancel_dwell()
            return
        pyautogui = None
        try:
            import pyautogui
            guard = self._bridge_manager.guard_process
            if guard is not None and guard.poll() is not None:
                raise RuntimeError("长按保护进程已退出，请重新启用头追桥接")
            if self._left_button.held and release_key_pressed():
                self._manual_release()
                return
            if self._left_button.held and not self._left_button.state.ready():
                raise RuntimeError("长按保护已离线，正在松开左键")
            pyautogui.failSafeCheck()
            pointer = tuple(pyautogui.position())
            stable = self._pointer_stabilizer.locked or self._stability == 0
            if self._blink_controller.hold_pointer(now):
                if self._dwell_eye_hold_since is None:
                    self._dwell_eye_hold_since = now
                # 短眨眼不清空悬停；闭眼时不发动作，长时间闭眼取消尚未完成的计时。
                if now - self._dwell_eye_hold_since > 1.2 and not self._dwell.wait_for_move:
                    self._dwell.reset()
                self._dwell.last_time = now
                self._dwell_overlay.hide()
                return
            self._dwell_eye_hold_since = None
            action = self._dwell.update(pointer, now, stable)
            if action is not None:
                if action == "press":
                    self._left_button.press()
                else:
                    self._left_button.release()
                self._dwell.applied(self._left_button.held, pointer)
                self._dwell_actions += 1
                self._dwell_flash_until = now + 0.4
                self.log.append("悬停 5 秒：左键已按住" if self._left_button.held else "悬停 5 秒：左键已松开")
            self.release_button.setEnabled(self._left_button.held)
            if self._dwell.wait_for_move:
                caption = "左键已按住" if self._left_button.held else "左键已松开"
                if self._left_button.held or now < self._dwell_flash_until:
                    self._dwell_overlay.display(self._dwell.anchor, 1, self._left_button.held, caption)
                else:
                    self._dwell_overlay.hide()
                self.dwell_status.setText(caption + "；移动后才重新计时。F8 松开。")
            elif self._dwell.anchor is not None:
                action_name = "松开" if self._left_button.held else "按住"
                caption = f"{action_name} {self._dwell.progress * 5:.1f}s"
                self._dwell_overlay.display(self._dwell.anchor, self._dwell.progress, self._left_button.held, caption)
                self.dwell_status.setText("悬停计时：" + caption)
            else:
                self._dwell_overlay.hide()
                self.dwell_status.setText("左键按住中，停稳后计时松开" if self._left_button.held else "等待指针停稳")
        except Exception as error:
            self._pointer_failed(error, pyautogui)

    def _reset_sensitivity(self):
        self.sensitivity_slider.setValue(100)
        self.horizontal_sensitivity.setValue(1.0)
        self.vertical_sensitivity.setValue(1.0)

    def _save_sensitivity(self):
        if self._settings_writer is not None and self._settings_writer.isRunning():
            self._settings_timer.start(100)
            return
        if self._settings_writer is not None:
            self._settings_writer.deleteLater()
        self._settings_writer = JsonSaveWorker(self.settings_folder / "sensitivity.json", dict(self._sensitivity), self,
                                              extra_files=[(self.settings_folder / "pointer-stability.json",
                                                            {"schema_version": 1, "strength": self._stability})])
        self._settings_writer.failed.connect(self.log.append)
        self._settings_writer.finished.connect(self._settings_saved)
        self._settings_writer.start()

    def _settings_saved(self):
        if self._closing:
            self.close()

    def open_training(self):
        if self._worker_running() or self._bridge_running() or self._closing or not self._has_verified_report or self._training_open:
            return
        if not self._runtime_status.get("ready", False):
            self.status_label.setText(self._runtime_status["summary"])
            return
        self.ready_checkbox.setChecked(False)
        self._training_open = True
        snapshot = self._runtime_status if self._runtime_monitor.static else None
        self._training_window = TrainingWindow(self, self.settings_folder, initial_runtime_status=snapshot)
        self._training_window.setWindowFlag(Qt.WindowType.Window, True)
        self._training_window.profile_applied.connect(self._profile_applied)
        self._training_window.closed.connect(self._training_closed)
        self._refresh_availability()
        self._training_window.show()

    def _profile_applied(self, profile):
        self._profile = profile
        self.use_profile_checkbox.setChecked(True)
        self.profile_label.setText(f"个人配置已保存 · 独立复测 RMSE {profile['validation_rmse']:.3f}（控制范围单位）")
        self.log.append("个人训练结果已应用；下次启动控制将重新校准正视姿态。")

    def _training_closed(self):
        if not self._training_open:
            return
        self._training_open = False
        self._refresh_availability()

    def start_control(self):
        if self._worker_running() or self._bridge_running() or self._closing or self._training_open:
            return
        if not self._runtime_status.get("ready", False):
            self.status_label.setText(self._runtime_status["summary"])
            return
        if not self._has_verified_report or not self.ready_checkbox.isChecked() or not self.start_button.isEnabled():
            self.status_label.setText("请先确认已佩戴耳机并准备测试鼠标移动")
            return
        if self._worker is not None:
            self._worker.deleteLater()
        profile = self._profile if self.use_profile_checkbox.isChecked() else None
        self._mapper = MotionPointerMapper(profile=profile)
        self._mapper.set_sensitivity(**self._sensitivity)
        self._calibration_samples.clear()
        self._stream_started = self._last_valid_sample_at = self._last_pointer = None
        self._sample_count = self._move_count = 0
        self._click_count = 0
        self._dwell_actions = 0
        self._cancel_dwell()
        self._pointer_stabilizer.reset()
        self._blink_controller.set_click_allowed(False)
        self._control_activated = self._ever_activated = False
        self._accepting_samples = True
        self._stopping_reason = "用户停止"
        self.ready_checkbox.setChecked(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText("正在连接 AirPods 4…请准备正视 3 秒")
        self.log.append("用户启动控制；正视校准期间鼠标保持不动。")
        self._worker = ControlWorker(self)
        self._worker.capture_started.connect(self._capture_started)
        self._worker.capture_completed.connect(self._capture_completed)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()
        self._refresh_availability()

    def _capture_started(self, started):
        if not self._accepting_samples:
            return
        self._stream_started = started
        self._watchdog.start()
        self._poll_timer.start()
        self.status_label.setText("通道已打开，请保持正视，校准中…")

    def _poll_sample(self):
        sample = self._worker.latest.take() if self._worker is not None else None
        if sample is not None:
            self._handle_sample(sample)

    def _handle_sample(self, sample):
        # 停止后立即拒绝任何已收到或晚到的姿态，不等待后台线程结束。
        if not self._accepting_samples or self._stream_started is None:
            return
        now = time.monotonic()
        source_time = float(sample.get("received_at_monotonic", now))
        if not np.isfinite(source_time) or now - source_time > SENSOR_TIMEOUT_SECONDS:
            self._stopping_reason = "姿态已过期，已停止控制"
            self.stop_control()
            return
        quaternion = quaternion_candidate(sample.get("orientation_raw", []))
        if quaternion is None:
            return
        self._sample_count += 1
        self._last_valid_sample_at = source_time
        if not self._control_activated:
            self._calibration_samples.append(quaternion)
            if now - self._stream_started < CALIBRATION_SECONDS:
                self.live_label.setText(f"校准有效帧 {len(self._calibration_samples)}；鼠标未移动")
                return
            try:
                import pyautogui

                pointer, size = pyautogui.position(), pyautogui.size()
                self._mapper.calibrate(self._calibration_samples, (pointer.x, pointer.y), (size.width, size.height))
            except Exception as error:
                self._stopping_reason = f"校准失败：{error}"
                self.stop_control()
                return
            self._control_activated = self._ever_activated = True
            self._last_pointer = (pointer.x, pointer.y)
            self._pointer_stabilizer.reset(self._last_pointer, source_time - 0.001)
            self._blink_controller.set_click_allowed(True)
            if self.dwell_checkbox.isChecked():
                self._dwell_timer.start()
            self.status_label.setText("头部鼠标控制运行中 · 可实时调节灵敏度")
        position = self._mapper.update(sample["orientation_raw"], source_time)
        if self._blink_controller.hold_pointer(now):
            self._pointer_stabilizer.reset(self._last_pointer, source_time)
            self.live_label.setText(f"眨眼期间保持指针 · 已处理 {self._sample_count} 帧")
            return
        if position is not None:
            position = self._pointer_stabilizer.update(position, source_time)
        mode = "悬停锁定" if self._pointer_stabilizer.locked else "实时跟踪"
        self.live_label.setText(f"{mode} · 已处理 {self._sample_count} 帧 · 移动 {self._move_count} 次")
        if position is None or position == self._last_pointer:
            return
        pyautogui = None
        try:
            import pyautogui

            # 保留角落保护，只跳过每次调用后的默认 0.1 秒等待。
            pyautogui.moveTo(*position, duration=0, _pause=False)
            self._last_pointer = position
            self._move_count += 1
            self.live_label.setText(f"{mode} · 已处理 {self._sample_count} 帧 · 移动 {self._move_count} 次")
        except Exception as error:
            self._pointer_failed(error, pyautogui)

    def _check_sensor_timeout(self):
        if not self._accepting_samples or self._stream_started is None:
            return
        if time.monotonic() - (self._last_valid_sample_at or self._stream_started) > SENSOR_TIMEOUT_SECONDS:
            self._stopping_reason = "传感器数据中断，已自动停止"
            self.stop_control()

    def stop_control(self):
        self._cancel_dwell()
        self._blink_controller.set_click_allowed(False)
        if self._bridge_running():
            if self._bridge_worker.operation == "enable":
                self._bridge_worker.requestInterruption()
                self.status_label.setText("已请求取消，正在等待连接结束并恢复普通蓝牙…")
                self.stop_button.setEnabled(False)
            return
        self._accepting_samples = self._control_activated = False
        self._poll_timer.stop()
        self._watchdog.stop()
        self.stop_button.setEnabled(False)
        self.status_label.setText(f"{self._stopping_reason}；鼠标控制已关闭")
        if self._worker_running():
            self._worker.requestInterruption()

    def _capture_completed(self, report):
        self._cancel_dwell()
        self._blink_controller.set_click_allowed(False)
        self._accepting_samples = self._control_activated = False
        self._poll_timer.stop()
        self._watchdog.stop()
        if report.get("status") == "channel_busy":
            self._stopping_reason = "运动通道被占用（170），本次没有启动鼠标控制"
            self.log.append(report.get("recovery_hint", "请先退出其他耳机采集程序，再重试。"))
        elif report.get("status") not in ("interrupted", "candidate_frames_need_validation"):
            self._stopping_reason = report.get("error") or capture_failure_message(report)
            self.log.append(capture_failure_message(report))
        self.status_label.setText(f"{self._stopping_reason}；控制已关闭")
        self.live_label.setText(f"接收帧 {report.get('candidate_frame_count', 0)} · 处理帧 {self._sample_count} · 移动 {self._move_count} 次")
        summary = {
            "session_type": "airpods4_head_mouse_control", "started_at": report.get("started_at"),
            "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": report.get("status"), "stop_reason": self._stopping_reason,
            "error_code": report.get("error_code"), "recovery_hint": report.get("recovery_hint"),
            "candidate_frame_count": report.get("candidate_frame_count", 0),
            "processed_frame_count": self._sample_count, "mouse_move_count": self._move_count,
            "blink_click_count": self._click_count, "pointer_stability_strength": self._stability,
            "dwell_hold_actions": self._dwell_actions,
            "mouse_control_activated": self._ever_activated, "stop_command_sent": bool(report.get("stop_command_sent")),
            "personal_profile_used": self.use_profile_checkbox.isChecked(), "sensitivity": dict(self._sensitivity),
            "raw_sensor_frames_saved": False, "cursor_coordinates_saved": False,
        }
        path = REPORTS_FOLDER / f"head-control-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        if self._report_writer is not None:
            self._report_writer.deleteLater()
        self._report_writer = JsonSaveWorker(path, summary, self)
        self._report_writer.failed.connect(self.log.append)
        self._report_writer.finished.connect(self._refresh_availability)
        self._report_writer.start()
        self.log.append(f"控制结束，后台保存摘要：{path.name}")

    def _worker_finished(self):
        self._cancel_dwell()
        self.stop_button.setEnabled(False)
        self._stream_started = self._last_valid_sample_at = None
        self._refresh_availability()

    def closeEvent(self, event):
        if not self._cancel_dwell():
            event.ignore()
            return
        self._closing = True
        if self._blink_dialog is not None:
            self._blink_dialog.close()
        self._blink_controller.shutdown()
        self._runtime_monitor.stop()
        if self._training_open and self._training_window is not None:
            self._training_window.close()
        self._accepting_samples = False
        if self._worker_running():
            self._stopping_reason = "关闭控制窗口"
            self.stop_control()
        if self._settings_timer.isActive():
            self._settings_timer.stop()
            self._save_sensitivity()
        writers_busy = any(writer is not None and writer.isRunning() for writer in (self._settings_writer, self._report_writer))
        if self._bridge_running():
            if self._bridge_worker.operation == "enable":
                self._bridge_worker.requestInterruption()
            event.ignore()
            return
        if self._worker_running() or writers_busy or self._training_open or self._runtime_monitor.is_running() or self._blink_controller.running():
            event.ignore()
            return
        if self._bridge_manager.owns_bridge:
            self._start_bridge_work("disable")
            event.ignore()
            return
        self._closing = False
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._runtime_monitor.start()
