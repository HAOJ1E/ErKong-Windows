"""NodLink Windows 头部追踪项目的桌面程序入口。"""

import sys

from PySide6.QtWidgets import QApplication

from nodlink.main_window import MainWindow


def main() -> int:
    """创建并运行桌面窗口。"""
    from nodlink.bridge_cli import dispatch_helper
    helper_result = dispatch_helper(sys.argv[1:])
    if helper_result is not None:
        return helper_result
    app = QApplication(sys.argv)
    app.setApplicationName("NodLink Windows")
    app.setOrganizationName("NodLink")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
