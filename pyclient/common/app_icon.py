# -*- coding: utf-8 -*-
"""应用图标与任务栏分组设置（学生端/教师端共用）。

解决：PyInstaller 打包的 exe 在资源管理器显示 ico 正常，但运行时任务栏图标
变成默认图标（窗口未设图标 + Windows 按默认 AppUserModelID 分组）。
必须在创建任何窗口前调用 setup_app()。
"""
import os
import sys


def resource_path(rel):
    """返回资源文件真实路径（PyInstaller 单文件模式走 sys._MEIPASS）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        return os.path.join(base, rel)
    # 源码运行：本文件在 app/pyclient/common/，rel 相对 app/pyclient/
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", rel))


def setup_app(app, app_id, ico_rel):
    """设置 Windows 任务栏 AppUserModelID 与应用图标（窗口创建前调用）。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
    p = resource_path(ico_rel)
    if os.path.isfile(p):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(p))
