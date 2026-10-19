# -*- coding: utf-8 -*-
"""通用"请稍等"等待界面 + 后台网络任务执行器（学生端/教师端共用）

用法：
    net_task(self, "正在加载题目…", lambda: net.call("browseQuestions", timeout=60),
             on_ok=lambda r: ...主线程刷新UI...,
             on_err=lambda e: ...主线程报错...)
"""
import threading
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QCoreApplication
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
                               QMessageBox, QProgressBar)


def post_to_main(func, *args, **kwargs):
    """从工作线程把回调排队到主线程执行（安全版）。

    ⚠ 不要用 QTimer.singleShot(0, cb) 的简写形式：它会在**调用它的线程**里创建
    定时器，而 threading.Thread 起的线程没有 Qt 事件循环，回调永远不会触发——
    等待框 accept 不掉，界面就一直卡在「请稍等」。实测踩坑（PySide6）。
    带 context 的重载不同：context 在主线程时会以 QueuedConnection 投递调用事件。
    """
    app = QCoreApplication.instance()
    if app is None:
        return                      # 没有 QApplication（不应发生）：忽略
    QTimer.singleShot(0, app, lambda: func(*args, **kwargs))


class WaitingDialog(QDialog):
    """模态"请稍等"等待框：半透明遮罩 + 居中卡片 + 转圈进度条。
    网络任务在后台线程执行，本对话框由自己的事件循环驱动动画，不卡界面。"""

    def __init__(self, parent, text="请稍等…"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        if parent is not None:
            try:
                # 覆盖父窗口所属的顶层窗口（parent 可能是页面控件，geometry 相对父级）
                win = parent.window() if hasattr(parent, "window") else parent
                self.setGeometry(win.frameGeometry())
            except Exception:
                self.resize(360, 200)
        else:
            self.resize(360, 200)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setStyleSheet(
            "QFrame{background:#FFFFFF; border-radius:14px;}"
            "QLabel{color:#2D3748; background:transparent;}"
            "QProgressBar{border:none; border-radius:4px; background:#E2E8F0;}"
            "QProgressBar::chunk{border-radius:4px; background:#4F86F8;}")
        card.setFixedWidth(320)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 26, 28, 26)
        cl.setSpacing(16)

        bar = QProgressBar(card)
        bar.setRange(0, 0)          # 不确定模式：持续滚动
        bar.setFixedHeight(8)
        cl.addWidget(bar)

        tip = QLabel(text, card)
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        tip.setStyleSheet("font: 12pt 'Microsoft YaHei UI'; color:#4A5568; background:transparent;")
        cl.addWidget(tip)

        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row, 1)
        outer.addStretch(1)

        # 淡入动画
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(160)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._anim.start()
        except Exception:
            pass


def net_task(parent, text, fn, on_ok=None, on_err=None, show_wait=True):
    """后台执行网络任务 + 模态"请稍等"框。
    - fn: 在后台线程执行的函数，返回结果（同步网络调用）
    - on_ok: 主线程回调(result)
    - on_err: 主线程回调(exception)；缺省弹 QMessageBox
    - show_wait=False: 不显示等待框（用于定时器轮询等静默场景），结果仍回主线程
    返回 WaitingDialog 引用（show_wait=False 时返回 None）。"""
    dlg = WaitingDialog(parent, text) if show_wait else None
    box = {}

    def _run():
        try:
            box["r"] = fn()
        except Exception as e:
            box["e"] = e
        post_to_main(_finish)       # 跨线程回主线程（见 post_to_main 注释）

    def _finish():
        try:
            if dlg is not None:
                dlg.accept()
        except Exception:
            pass
        if "e" in box:
            if on_err is not None:
                try:
                    on_err(box["e"])
                except Exception:
                    pass
            else:
                try:
                    QMessageBox.critical(parent, "操作失败", str(box["e"]))
                except Exception:
                    pass
        else:
            if on_ok is not None:
                try:
                    on_ok(box["r"])
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()
    if dlg is not None:
        try:
            dlg.exec()
        except Exception:
            pass
    return dlg
