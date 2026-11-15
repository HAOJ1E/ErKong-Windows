"""将桥接准备和恢复放到后台，界面只接收阶段消息。"""

from PySide6.QtCore import QThread, Signal


class BridgeRecoveryWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager, operation, parent=None):
        super().__init__(parent)
        self.manager, self.operation = manager, operation

    def run(self):
        try:
            if self.operation == "enable":
                result = self.manager.enable(self.progress.emit, self.isInterruptionRequested)
            else:
                result = self.manager.disable(self.progress.emit)
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
