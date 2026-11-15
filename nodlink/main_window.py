"""NodLink 初始硬件检测窗口。"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nodlink.bluetooth_devices import list_registered_bluetooth_devices
from nodlink.control_window import MouseControlWindow
from nodlink.motion_support import check_motion_support
from nodlink.motion_control import latest_verified_validation_report
from nodlink.report_exporter import export_report
from nodlink.system_info import collect_system_info


class DetectionWorker(QThread):
    """在后台线程读取系统和蓝牙信息，避免窗口卡住。"""

    completed = Signal(object, object, object, object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            system_info = collect_system_info()
            devices, error = list_registered_bluetooth_devices()
            motion_support = check_motion_support()
            self.completed.emit(system_info, devices, error, motion_support)
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    """显示本机基础信息、Windows 蓝牙设备列表和运行日志。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("NodLink Windows · 运动通道诊断")
        self.resize(920, 820)
        self.setMinimumSize(760, 680)
        self._worker: DetectionWorker | None = None
        self._log_entries: list[str] = []
        self._system_info: dict[str, str] = {}
        self._bluetooth_devices: list[dict[str, str]] = []
        self._bluetooth_error: str | None = None
        self._motion_support: dict | None = None
        self._control_window: MouseControlWindow | None = None
        self._build_ui()
        self._log("检测程序已启动。")
        self.start_detection()

    def _build_ui(self) -> None:
        """创建界面控件和布局。"""
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(16)

        title = QLabel("NodLink Windows")
        title.setObjectName("title")
        subtitle = QLabel("AirPods 4 · 运动通道诊断与头部鼠标控制")
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        motion_group = QGroupBox("头部追踪可用性")
        motion_layout = QVBoxLayout(motion_group)
        self.motion_summary = QLabel("正在检查 Windows 运动数据接口…")
        self.motion_summary.setWordWrap(True)
        self.motion_summary.setObjectName("motionSummary")
        motion_layout.addWidget(self.motion_summary)
        self.motion_detail = QLabel("检查已有驱动接口，不需要重新配对耳机。")
        self.motion_detail.setWordWrap(True)
        motion_layout.addWidget(self.motion_detail)
        motion_layout.addWidget(QLabel("本页只检查接口与历史验证记录，不打开实时传感器流。"))
        root.addWidget(motion_group)

        self.system_group = QGroupBox("电脑与运行环境")
        self.system_grid = QGridLayout(self.system_group)
        self.system_values: dict[str, QLabel] = {}
        root.addWidget(self.system_group)

        bluetooth_group = QGroupBox("Windows 已登记的蓝牙设备")
        bluetooth_layout = QVBoxLayout(bluetooth_group)
        self.bluetooth_table = QTableWidget(0, 2)
        self.bluetooth_table.setHorizontalHeaderLabels(["设备名称", "Windows 状态"])
        self.bluetooth_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bluetooth_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.bluetooth_table.verticalHeader().setVisible(False)
        self.bluetooth_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.bluetooth_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        bluetooth_layout.addWidget(self.bluetooth_table)

        note = QLabel(
            "说明：上表是 Windows 已登记的设备条目；状态 OK 不代表运动传感器可读。\n"
            "运动数据接入需要兼容的 AAP 通道及协议实现；本页同时检查已知驱动接口。"
        )
        note.setWordWrap(True)
        note.setObjectName("notice")
        bluetooth_layout.addWidget(note)
        root.addWidget(bluetooth_group, 1)

        action_row = QGridLayout()
        action_row.setColumnStretch(0, 1)
        self.status_label = QLabel("等待检测")
        self.status_label.setObjectName("status")
        self.export_button = QPushButton("导出检测报告")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_diagnostic_report)
        self.scan_button = QPushButton("重新检查设备与通道")
        self.scan_button.clicked.connect(self.start_detection)
        self.control_button = QPushButton("打开头部鼠标控制")
        self.control_button.clicked.connect(self.open_mouse_control)
        action_row.addWidget(self.status_label, 0, 0)
        action_row.addWidget(self.control_button, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        action_row.addWidget(self.export_button, 0, 2, alignment=Qt.AlignmentFlag.AlignRight)
        action_row.addWidget(self.scan_button, 0, 3, alignment=Qt.AlignmentFlag.AlignRight)
        root.addLayout(action_row)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(125)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_group)

        self.setCentralWidget(central)
        self.setStyleSheet(
            "QMainWindow { background: #f4f7fb; }"
            "QGroupBox { background: white; border: 1px solid #d9e1ec;"
            " border-radius: 10px; margin-top: 10px; padding: 12px;"
            " font-weight: 600; color: #243247; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }"
            "QLabel#title { font-size: 25px; font-weight: 700; color: #172b4d; }"
            "QLabel#subtitle { color: #64748b; font-size: 14px; }"
            "QLabel#notice { color: #52647a; padding: 8px; background: #f3f7ff;"
            " border-radius: 6px; }"
            "QLabel#status { color: #52647a; }"
            "QLabel#motionSummary { color: #9a4510; font-size: 14px; font-weight: 600; }"
            "QPushButton { background: #2463eb; color: white; border: 0;"
            " border-radius: 6px; padding: 9px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #174fcb; }"
            "QPushButton:disabled { background: #9aa9bf; }"
            "QTableWidget, QTextEdit { background: white; border: 1px solid #e2e8f0;"
            " border-radius: 6px; color: #243247; }"
            "QHeaderView::section { background: #f3f6fa; padding: 7px;"
            " border: 0; color: #52647a; font-weight: 600; }"
        )

    def _log(self, message: str) -> None:
        """将日志同时保存到窗口和当前运行记录。"""
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._log_entries.append(entry)
        self.log_view.append(entry)

    def start_detection(self) -> None:
        """启动一次系统和 Windows 蓝牙设备列表检测。"""
        if self._worker is not None and self._worker.isRunning():
            return

        self.scan_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.status_label.setText("正在检测…")
        self.motion_summary.setText("正在检查 Windows 运动数据接口…")
        self.motion_detail.setText("检查已有驱动接口，不需要重新配对耳机。")
        self._log("开始读取电脑信息、Windows 蓝牙设备列表和运动通道接口。")
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = DetectionWorker(self)
        self._worker.completed.connect(self._show_detection_results)
        self._worker.failed.connect(self._show_detection_error)
        self._worker.start()

    def _show_detection_error(self, error: str) -> None:
        """将后台异常显示出来，并允许重新检查。"""
        self.status_label.setText("检测失败")
        self.motion_summary.setText("检测未完成，无法判断运动通道状态。")
        self.motion_detail.setText("请查看下方日志。")
        self._log(f"检测失败：{error}")
        self.scan_button.setEnabled(True)
        self.export_button.setEnabled(bool(self._system_info))

    def _clear_system_grid(self) -> None:
        """清除上一次检测显示的电脑信息。"""
        while self.system_grid.count():
            item = self.system_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_detection_results(
        self, system_info: dict, devices: list, error: str | None, motion_support: dict
    ) -> None:
        """将后台检测结果更新到界面并保存为报告数据。"""
        self._clear_system_grid()
        self.system_values.clear()
        self._system_info = dict(system_info)
        self._bluetooth_devices = [dict(device) for device in devices]
        self._bluetooth_error = error
        self._motion_support = dict(motion_support)
        self.motion_summary.setText(motion_support["summary"])
        verification = latest_verified_validation_report(
            Path(__file__).resolve().parents[1] / "reports"
        )
        if motion_support.get("acquisition_paused"):
            verification_note = "历史动作验证记录仍保留；当前暂停实时采集。"
        elif verification is not None:
            verification_note = (
                "本机普通 AirPods 4 曾通过真实动作验证；"
                "如需控制鼠标，请在独立窗口中手动启动并重新校准。"
            )
        else:
            verification_note = "尚无本机用户确认的动作验证记录；鼠标控制窗口会阻止未验证设备启动。"
        self.motion_detail.setText(motion_support.get("detail", "") + " " + verification_note)

        for row, (name, value) in enumerate(system_info.items()):
            name_label = QLabel(name)
            name_label.setStyleSheet("color: #64748b;")
            value_label = QLabel(value)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.system_grid.addWidget(name_label, row, 0)
            self.system_grid.addWidget(value_label, row, 1)
            self.system_values[name] = value_label

        self.bluetooth_table.setRowCount(0)
        for device in devices:
            row = self.bluetooth_table.rowCount()
            self.bluetooth_table.insertRow(row)
            self.bluetooth_table.setItem(row, 0, QTableWidgetItem(device["name"]))
            self.bluetooth_table.setItem(row, 1, QTableWidgetItem(device["status"]))

        if error:
            self.status_label.setText("电脑信息已读取；蓝牙列表读取失败")
            self._log(f"蓝牙设备读取失败：{error}")
        else:
            self.status_label.setText(f"检测完成 · 找到 {len(devices)} 个已登记设备")
            self._log(f"检测完成，系统已登记 {len(devices)} 个蓝牙设备。")
            if any("airpods" in device["name"].casefold() for device in devices):
                self._log("发现名称包含 AirPods 的 Windows 设备；传感器数据尚未检测。")
            if not devices:
                self._log("Windows 蓝牙设备列表为空。")

        self._log(motion_support["summary"])
        if motion_support.get("detail"):
            self._log(motion_support["detail"])
        self._log("本页未打开耳机数据通道；实时鼠标控制需在独立窗口手动启动。")
        self.scan_button.setEnabled(True)
        self.export_button.setEnabled(bool(system_info))

    def open_mouse_control(self) -> None:
        """打开独立鼠标控制窗口；本窗口仅查看，不会启动控制。"""
        if self._control_window is None:
            self._control_window = MouseControlWindow()
        self._control_window.show()
        self._control_window.raise_()
        self._control_window.activateWindow()

    def export_diagnostic_report(self) -> None:
        """让用户选择保存位置，并导出当前检测报告。"""
        if not self._system_info:
            QMessageBox.information(self, "尚无检测结果", "请先完成一次设备检测。")
            return

        documents_folder = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        save_folder = Path(documents_folder) if documents_folder else Path.home()
        default_name = f"NodLink-检测报告-{datetime.now():%Y%m%d-%H%M%S}.json"
        default_path = str(save_folder / default_name)
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出检测报告",
            default_path,
            "JSON 报告 (*.json)",
        )
        if not selected_path:
            return

        output_path = Path(selected_path)
        if output_path.suffix.lower() != ".json":
            output_path = output_path.with_suffix(".json")

        try:
            saved_path = export_report(
                output_path,
                self._system_info,
                self._bluetooth_devices,
                self._bluetooth_error,
                list(self._log_entries),
                motion_support=self._motion_support,
            )
        except OSError as error:
            self._log(f"报告导出失败：{error}")
            QMessageBox.warning(self, "导出失败", f"无法保存检测报告：\n{error}")
            return

        self._log(f"检测报告已导出：{saved_path}")
        self.status_label.setText("检测报告已导出")


    def closeEvent(self, event) -> None:
        """避免后台线程仍运行时销毁窗口导致 Qt 崩溃。"""
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("正在读取设备信息，请检测结束后再关闭。")
            event.ignore()
            return
        event.accept()
