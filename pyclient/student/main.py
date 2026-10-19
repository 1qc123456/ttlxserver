# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from qfluentwidgets import setTheme, Theme
from common.utils import make_light_palette
from common.app_icon import setup_app
from common import client_cfg
from student.login import LoginWindow

if __name__ == "__main__":
    # 本地配置固定写 C:\studytemp\stu.json（目录不存在会自动创建）
    client_cfg.set_role("stu")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 任务栏图标与资源管理器 exe 图标一致（须在创建窗口前设置）
    setup_app(app, "ttlx.student", "assets/stu.ico")
    # 强制浅色主题 + 浅色调色板：屏蔽系统深色模式影响，保证文字始终清晰可读
    setTheme(Theme.LIGHT)
    app.setPalette(make_light_palette())
    w = LoginWindow()
    w.show()
    sys.exit(app.exec())
