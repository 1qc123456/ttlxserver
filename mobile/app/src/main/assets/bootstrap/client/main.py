# -*- coding: utf-8 -*-
"""手机版学生端入口。

运行环境：Android + proot Debian arm64 容器（小小电脑 / Termux 均可），
也可在 Windows / 桌面 Linux 下直接运行做开发调试。

启动：
    python3 main.py
（容器内由 setup/start_student.sh 统一拉起）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

import paths


def main():
    paths.ensure_dirs()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 触屏友好的全局字体（容器内一般预装 Noto Sans CJK）
    for fam in ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "Microsoft YaHei UI"):
        f = QFont(fam, 11)
        if f.exactMatch() or fam.startswith("Microsoft"):
            app.setFont(f)
            break

    from ui_login import LoginWindow
    w = LoginWindow()
    w.setWindowTitle("学生端（手机版）")

    # 按屏幕大小开窗（手机全屏铺满，桌面开发时给个合适窗口）
    try:
        scr = app.primaryScreen().availableGeometry()
        if scr.width() < 900:                       # 手机竖屏
            w.resize(scr.width(), scr.height())
            w.move(0, 0)
        else:
            w.resize(480, 860)
    except Exception:
        w.resize(480, 860)

    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
