"""启动 AirPods 4 引导动作验证，数据采集由用户点击开始。"""

import sys
from PySide6.QtWidgets import QApplication
from nodlink.validation_window import ValidationWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NodLink Motion Validation")
    window = ValidationWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
