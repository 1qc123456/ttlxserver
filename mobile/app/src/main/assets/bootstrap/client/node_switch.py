# -*- coding: utf-8 -*-
"""更换节点窗口（学生端/教师端共用）：
服务器连不上（超时/未响应）时弹出，下拉框列出候选节点并并发测延迟，
延迟显示在选项右边（超时显示「无法连接」），选定后重试连接。
"""
import threading, time
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                               QPushButton, QMessageBox)


def measure_latency(hostport, timeout=3.0):
    """TCP 连接延迟（毫秒）；失败返回 None。hostport 形如 host:port 或 host（缺省 5943）。"""
    import socket
    hp = (hostport or "").strip()
    if not hp:
        return None
    host, _, port = hp.rpartition(":")
    if not host:
        host = hp; port = "5943"
    try:
        port = int(port)
    except Exception:
        port = 5943
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return int((time.time() - t0) * 1000)
    except Exception:
        return None


class NodeSwitchDialog(QDialog):
    """下拉框选项形如：  addr（延迟 45ms）  /  addr（无法连接）"""

    def __init__(self, parent, nodes, current="", timeout=3.0):
        super().__init__(parent)
        self.setWindowTitle("更换节点")
        self.resize(520, 240)
        self._nodes = list(dict.fromkeys([n for n in nodes if n and n != current]))
        if current and current not in self._nodes:
            self._nodes.insert(0, current)
        if not self._nodes:
            self._nodes = [current or "127.0.0.1:5943"]
        self._latency = {}      # addr -> ms 或 None
        self._timeout = timeout
        self._picked = None
        self._build_ui()
        self._measure_all()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        tip = QLabel("当前服务器地址无法连接，请选择可用的节点后重试：")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#4A5568;")
        lay.addWidget(tip)

        self.combo = QComboBox()
        self.combo.setFont(QFont("Microsoft YaHei UI", 11))
        for addr in self._nodes:
            self.combo.addItem(addr, addr)   # userData=addr，供延迟刷新时按值替换文本
        lay.addWidget(self.combo)

        self.lat_lab = QLabel("正在检测各节点延迟…")
        self.lat_lab.setStyleSheet("color:#718096;")
        lay.addWidget(self.lat_lab)

        row = QHBoxLayout()
        row.addStretch(1)
        self.ok_btn = QPushButton("切换到该节点并重试")
        self.ok_btn.setFixedWidth(180)
        self.ok_btn.clicked.connect(self._on_ok)
        row.addWidget(self.ok_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedWidth(90)
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)

    def _measure_all(self):
        def _one(addr):
            # 工作线程只写数据（dict 赋值受 GIL 保护），
            # **绝不在这里碰任何控件** —— Qt 控件只能在主线程操作
            self._latency[addr] = measure_latency(addr, self._timeout)

        for a in self._nodes:
            threading.Thread(target=_one, args=(a,), daemon=True).start()
        # 主线程定时器统一刷新界面（已完成的结果即时显示；全部完成则停表）
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(150)

    def _tick(self):
        """主线程刷新：把已测出的延迟写到对应选项，全测完则停表并提示"""
        try:
            done = 0
            for a in self._nodes:
                if a in self._latency:
                    done += 1
                    self._update_item(a, self._latency[a])
            if done >= len(self._nodes):
                self.lat_lab.setText("检测完成：延迟显示在节点后面")
                self._timer.stop()
            else:
                self.lat_lab.setText("正在检测各节点延迟… %d/%d" % (done, len(self._nodes)))
        except Exception:
            try:
                self._timer.stop()
            except Exception:
                pass

    def _update_item(self, addr, ms):
        try:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == addr:
                    self.combo.setItemText(i, self._fmt(addr, ms))
                    return
            self.combo.addItem(self._fmt(addr, ms), addr)
        except Exception:
            pass

    @staticmethod
    def _fmt(addr, ms):
        return "%s（延迟 %dms）" % (addr, ms) if ms is not None else "%s（无法连接）" % addr

    def _on_ok(self):
        addr = self.combo.currentText()
        # 去掉延迟后缀：取前括号前的部分
        base = addr.split("（")[0].strip()
        if not base:
            QMessageBox.warning(self, "提示", "请选择节点")
            return
        self._picked = base
        self.accept()

    def picked(self):
        return self._picked


def ask_switch_node(parent, nodes, current="", timeout=3.0):
    """弹出更换节点窗口；用户选择并确认后返回新地址字符串，取消返回 None。"""
    dlg = NodeSwitchDialog(parent, nodes, current, timeout=timeout)
    dlg.exec()
    return dlg.picked()
