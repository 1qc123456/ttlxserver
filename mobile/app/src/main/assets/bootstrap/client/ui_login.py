# -*- coding: utf-8 -*-
"""手机版学生端：登录页。

与桌面版一致的行为：登录走后台线程（穿透首次 15~40 秒不冻界面）、
成功后记住服务器地址并从服务端拉取节点列表；失败时可切换节点重试。
界面按触屏设计：大按钮、大字体。
"""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton)

from net_client import NetClient, friendly_conn_error
from node_switch import ask_switch_node
import stu_cfg

BTN_QSS = """
QPushButton { background:#4F86F8; color:white; border:none; border-radius:10px;
              font-size:16pt; font-weight:600; }
QPushButton:disabled { background:#A9C3F5; }
QPushButton:pressed { background:#3A6FD8; }
"""
EDIT_QSS = """
QLineEdit { border:1px solid #C9D4E5; border-radius:10px; padding:10px 12px;
            font-size:15pt; background:white; }
"""


class LoginWindow(QWidget):
    _done = Signal(object)
    _prog = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("学生端（手机版）")
        self._busy = False
        self._done.connect(self._on_done)
        self._prog.connect(self._on_prog)
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 40, 28, 32)
        lay.setSpacing(18)

        title = QLabel("学生端")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Noto Sans CJK SC", 22, QFont.Bold))
        lay.addWidget(title)

        sub = QLabel("手机版 · 在线练习")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color:#888; font-size:12pt;")
        lay.addWidget(sub)
        lay.addSpacing(10)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("服务器地址（可带端口，默认5943）")
        self.host_edit.setStyleSheet(EDIT_QSS)
        self.host_edit.setText(stu_cfg.get_last_host() or "127.0.0.1")
        lay.addWidget(self.host_edit)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("学号 / 账号")
        self.user_edit.setStyleSheet(EDIT_QSS)
        lay.addWidget(self.user_edit)

        self.pwd_edit = QLineEdit()
        self.pwd_edit.setPlaceholderText("密码")
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setStyleSheet(EDIT_QSS)
        lay.addWidget(self.pwd_edit)

        self.tip = QLabel("")
        self.tip.setWordWrap(True)
        self.tip.setAlignment(Qt.AlignCenter)
        self.tip.setStyleSheet("color:#718096; font-size:11pt;")
        lay.addWidget(self.tip)

        lay.addStretch()
        self.login_btn = QPushButton("登 录")
        self.login_btn.setFixedHeight(56)
        self.login_btn.setStyleSheet(BTN_QSS)
        self.login_btn.clicked.connect(self.do_login)
        lay.addWidget(self.login_btn)

    # ---------------- 登录 ----------------
    def do_login(self, host=None):
        if self._busy:
            return
        host = (host or self.host_edit.text()).strip()
        user = self.user_edit.text().strip()
        pwd = self.pwd_edit.text()
        if not host or not user:
            QMessageBox.warning(self, "提示", "请填写服务器地址和账号")
            return
        self._busy = True
        self.login_btn.setEnabled(False)
        self.login_btn.setText("连接中…")
        self.host_edit.setEnabled(False)
        self.user_edit.setEnabled(False)
        self.pwd_edit.setEnabled(False)
        remote = host not in ("127.0.0.1", "localhost")
        self.tip.setText("正在连接…" + ("（穿透首次约需 15~40 秒，请耐心等待）" if remote else ""))
        threading.Thread(target=self._work, args=(host, user, pwd), daemon=True).start()

    def _work(self, host, user, pwd):
        res = {"net": None, "r": None, "err": None, "host": host}
        try:
            net = NetClient(host, progress_cb=self._prog.emit)
            net.connect()
            self._prog.emit("正在登录…")
            res["r"] = net.login(user, pwd)
            res["net"] = net
        except Exception as e:
            res["err"] = e
        self._done.emit(res)

    def _on_prog(self, text):
        self.tip.setText(text)

    def _on_done(self, res):
        self._busy = False
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登 录")
        self.host_edit.setEnabled(True)
        self.user_edit.setEnabled(True)
        self.pwd_edit.setEnabled(True)
        self.tip.setText("")
        err = res.get("err")
        if err is not None:
            nodes = stu_cfg.get_nodes()
            cur = res.get("host") or self.host_edit.text().strip()
            if nodes:
                picked = ask_switch_node(self, nodes, current=cur)
                if picked:
                    self.host_edit.setText(picked)
                    self.do_login(host=picked)
                    return
            QMessageBox.critical(self, "连接失败", friendly_conn_error(cur, err))
            return
        r = res.get("r") or {}
        if r.get("code") == 0:
            if r.get("role") != "student":
                QMessageBox.warning(self, "提示", "该账号不是学生账号")
                return
            host = res.get("host") or self.host_edit.text().strip()
            stu_cfg.set_last_host(host)
            threading.Thread(target=self._fetch_nodes, args=(res["net"],), daemon=True).start()
            self._open_main(res["net"])
        else:
            QMessageBox.warning(self, "登录失败", r.get("msg", "未知错误"))

    def _fetch_nodes(self, net):
        try:
            r = net.call("getNodes", timeout=10)
            nodes = r.get("nodes") or []
            if nodes:
                stu_cfg.set_nodes(nodes)
        except Exception:
            pass

    def _open_main(self, net):
        from ui_main import MainWindow
        self.main = MainWindow(net, on_logout=self._back_to_login)
        self.main.show()
        self.hide()

    def _back_to_login(self):
        try:
            self.main.close()
        except Exception:
            pass
        self.host_edit.setText(stu_cfg.get_last_host() or self.host_edit.text())
        self.show()
