"""独立打开耳控头部鼠标控制窗口。"""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from nodlink.control_window import MouseControlWindow


def main() -> int:
    """显示控制界面；鼠标仍需在窗口内由用户明确启动。"""
    from nodlink.bridge_cli import dispatch_helper
    helper_result = dispatch_helper(sys.argv[1:])
    if helper_result is not None:
        return helper_result
    app = QApplication(sys.argv)
    app.setApplicationName("耳控头部鼠标控制")
    app.setOrganizationName("耳控")
    window = MouseControlWindow()
    window.show()
    # 仅显式命令参数触发桥接连接；不会勾选准备框或启动鼠标。
    if "--enable-bridge" in sys.argv[1:]:
        QTimer.singleShot(0, window.enable_bridge)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
