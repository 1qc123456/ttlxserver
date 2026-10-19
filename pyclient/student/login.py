# -*- coding: utf-8 -*-
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QMessageBox
from qfluentwidgets import LineEdit, PrimaryPushButton
from common.net_client import NetClient, friendly_conn_error
from common.utils import resource_path
from common import client_cfg
from common.node_switch import ask_switch_node

class LoginWindow(QWidget):
    """登录放后台线程：经内网穿透时首帧握手实测要 15~40 秒，不能冻住界面。
    - 首次登录成功 → 记住服务器地址（下次默认填）并从服务器获取候选节点列表
    - 连接失败/超时 → 弹出「更换节点」窗口（节点带延迟显示），选节点后重试"""

    _done = Signal(object)
    _prog = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("学生端")
        self.setFixedSize(420, 560)
        self.setObjectName("loginWindow")
        self._busy = False
        self._done.connect(self._on_done)
        self._prog.connect(self._on_prog)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 50, 40, 40)
        layout.setSpacing(16)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pix = QPixmap(resource_path("assets/stu.jpg"))
        logo.setPixmap(pix.scaled(130, 130, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(logo)

        title = QLabel("学生端")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        layout.addWidget(title)

        sub = QLabel("本地题库 · 在线训练")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        self.host_edit = LineEdit()
        self.host_edit.setPlaceholderText("服务器地址（可带端口，默认5943）")
        self.host_edit.setText(client_cfg.get_last_host() or "127.0.0.1")
        layout.addWidget(self.host_edit)

        self.user_edit = LineEdit()
        self.user_edit.setPlaceholderText("学号 / 账号")
        layout.addWidget(self.user_edit)

        self.pwd_edit = LineEdit()
        self.pwd_edit.setPlaceholderText("密码")
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.pwd_edit)

        self.tip = QLabel("")
        self.tip.setWordWrap(True)
        self.tip.setAlignment(Qt.AlignCenter)
        self.tip.setStyleSheet("color: #888;")
        layout.addWidget(self.tip)

        layout.addStretch()
        self.login_btn = PrimaryPushButton("登 录")
        self.login_btn.setFixedHeight(44)
        self.login_btn.clicked.connect(self.do_login)
        layout.addWidget(self.login_btn)

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
        self.login_btn.setText("连接中…（远程首次约需15-40秒）")
        self.host_edit.setEnabled(False)
        self.user_edit.setEnabled(False)
        self.pwd_edit.setEnabled(False)
        threading.Thread(target=self._work, args=(host, user, pwd), daemon=True).start()

    def _work(self, host, user, pwd):
        res = {"net": None, "r": None, "err": None, "host": host}
        try:
            net = NetClient(host, progress_cb=self._prog.emit)
            net.connect()               # 单次建连；首个请求自动放宽到 60s（穿透首帧慢）
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
            # 连接失败/超时：有候选节点就弹「更换节点」窗口
            nodes = client_cfg.get_nodes()
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
            # 记住成功地址 + 后台拉取节点列表（首次登录就获取，之后登录超时可切换）
            host = res.get("host") or self.host_edit.text().strip()
            client_cfg.set_last_host(host)
            threading.Thread(target=self._fetch_nodes, args=(res["net"],), daemon=True).start()
            self._open_main(res["net"])
        else:
            QMessageBox.warning(self, "登录失败", r.get("msg", "未知错误"))

    def _fetch_nodes(self, net):
        """登录成功后从服务器获取候选节点列表，存本地供超时切换用"""
        try:
            r = net.call("getNodes", timeout=10)
            nodes = r.get("nodes") or []
            if nodes:
                client_cfg.set_nodes(nodes)
        except Exception:
            pass

    def _open_main(self, net):
        from student.main_window import MainWindow
        self.main = MainWindow(net)
        self.main.show()
        self.close()
