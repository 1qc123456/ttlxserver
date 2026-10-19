# -*- coding: utf-8 -*-
"""手机版学生端：答题窗口。

与桌面端共用同一套服务端协议：
  getMaterialZip / uploadOperate / practiceGradeOperate / heartbeat /
  submit / submitPractice / recordDetail
操作题在容器内完成：素材下载 → xdg-open 用 WPS 打开 → 保存回作答文件夹 →
交卷时整目录打包上传判分（与桌面端 _auto_upload_operate 同构）。
MySQL 题因手机容器无 MySQL 5.7（无官方 arm64 版）仅支持查看素材/上传已有文件；
虚拟程序题（win10 设置等）为实验性：走容器内 Wine。
"""
import base64
import glob
import io
import os
import threading
import time
import zipfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QFrame, QMessageBox, QRadioButton,
                               QTextEdit, QButtonGroup)

import paths
from waiting import net_task

BTN_QSS = """
QPushButton { background:#4F86F8; color:white; border:none; border-radius:10px;
              font-size:13pt; font-weight:600; padding:8px 14px; }
QPushButton:disabled { background:#B9C8E8; }
QPushButton:pressed { background:#3A6FD8; }
"""
GHOST_QSS = """
QPushButton { background:#EDF2FB; color:#2D5FE8; border:none; border-radius:10px;
              font-size:13pt; font-weight:600; padding:8px 14px; }
QPushButton:pressed { background:#DCE7F9; }
"""
NAV_QSS = """
QPushButton { background:white; border:1px solid #C9D4E5; border-radius:8px;
              font-size:11pt; min-width:40px; min-height:40px; }
QPushButton[state="cur"] { border:2px solid #4F86F8; color:#2D5FE8; font-weight:700; }
QPushButton[state="done"] { background:#27AE60; color:white; border:none; }
"""

VAPP_NAMES = {"win10": "Win10 系统设置", "win10net": "网络设置",
              "email": "邮件收发", "edge": "浏览器设置"}


class ExamWindow(QWidget):
    def __init__(self, main, net, record_id, title, questions, practice=False, duration_sec=0):
        super().__init__()
        self.main = main
        self.net = net
        self.record_id = record_id
        self.title = title
        self.qs = questions
        self.practice = practice
        self.duration_sec = duration_sec
        self.submit_cmd = "submitPractice" if practice else "submit"

        self.idx = 0
        self.sel = [-1] * len(questions)
        self.op_texts = [""] * len(questions)
        self.op_grades = {}          # qid -> 判分结果缓存
        self._submitted = False

        self.setWindowTitle(title or "答题")

        # 心跳
        self._hb_stop = threading.Event()
        threading.Thread(target=self._hb_loop, daemon=True).start()

        self._build_ui()
        self.render()

        # 计时
        if duration_sec > 0:
            self.remain = int(duration_sec)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(1000)
            self._update_time_label()

    # ================= UI =================
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶栏：标题 + 计时 + 进度
        top = QFrame()
        top.setStyleSheet("QFrame{background:#F5F8FF;}")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 8, 12, 8)
        tt = QLabel(self.title or "答题")
        tt.setFont(QFont("Noto Sans CJK SC", 12, QFont.Bold))
        tt.setWordWrap(False)
        tl.addWidget(tt, 1)
        self.time_label = QLabel("")
        self.time_label.setStyleSheet("color:#D64545; font-size:12pt; font-weight:700;")
        tl.addWidget(self.time_label)
        lay.addWidget(top)

        # 题号导航条（横向滚动）
        navwrap = QScrollArea()
        navwrap.setWidgetResizable(True)
        navwrap.setFixedHeight(64)
        navwrap.setStyleSheet("QScrollArea{border:none; background:white;}")
        self.navbox = QWidget()
        self.navlay = QHBoxLayout(self.navbox)
        self.navlay.setContentsMargins(8, 6, 8, 6)
        self.navlay.setSpacing(6)
        self.navlay.addStretch()
        navwrap.setWidget(self.navbox)
        lay.addWidget(navwrap)
        self.nav_btns = []
        for i in range(len(self.qs)):
            b = QPushButton(str(i + 1))
            b.setStyleSheet(NAV_QSS)
            b.setProperty("state", "")
            b.clicked.connect(lambda _=False, k=i: self.goto(k))
            self.navlay.insertWidget(self.navlay.count() - 1, b)
            self.nav_btns.append(b)

        # 题干
        self.stem_box = QTextEdit()
        self.stem_box.setReadOnly(True)
        self.stem_box.setFont(QFont("Noto Sans CJK SC", 12))
        self.stem_box.setStyleSheet(
            "QTextEdit{border:none; border-bottom:1px solid #E3EAF5; background:white; padding:10px;}")
        lay.addWidget(self.stem_box, 3)

        # 客观题选项
        self.opt_widget = QWidget()
        ol = QVBoxLayout(self.opt_widget)
        ol.setContentsMargins(12, 8, 12, 8)
        ol.setSpacing(6)
        self.opt_scroll = QScrollArea()
        self.opt_scroll.setWidgetResizable(True)
        self.opt_scroll.setStyleSheet("QScrollArea{border:none;}")
        self.rb_group = QButtonGroup(self)
        self.rbs = []
        for i in range(6):
            rb = QRadioButton()
            rb.setStyleSheet("QRadioButton{font-size:13pt; padding:10px 4px;}")
            rb.toggled.connect(self._on_choose)
            ol.addWidget(rb)
            self.rb_group.addButton(rb, i)
            self.rbs.append(rb)
        ol.addStretch()
        self.opt_scroll.setWidget(self.opt_widget)
        lay.addWidget(self.opt_scroll, 2)

        # 操作题作答区
        self.op_widget = QWidget()
        pl = QVBoxLayout(self.op_widget)
        pl.setContentsMargins(12, 8, 12, 8)
        pl.setSpacing(8)
        self.op_note = QLabel("")
        self.op_note.setWordWrap(True)
        self.op_note.setStyleSheet("color:#718096; font-size:11pt;")
        pl.addWidget(self.op_note)
        r1 = QHBoxLayout()
        self.mat_btn = QPushButton("下载素材并打开")
        self.mat_btn.setStyleSheet(BTN_QSS)
        self.mat_btn.clicked.connect(self.open_material)
        r1.addWidget(self.mat_btn, 1)
        self.folder_btn = QPushButton("打开作答文件夹")
        self.folder_btn.setStyleSheet(GHOST_QSS)
        self.folder_btn.clicked.connect(self.open_folder)
        r1.addWidget(self.folder_btn, 1)
        pl.addLayout(r1)
        self.vapp_btn = QPushButton("打开虚拟程序（实验）")
        self.vapp_btn.setStyleSheet(GHOST_QSS)
        self.vapp_btn.setVisible(False)
        self.vapp_btn.clicked.connect(self.open_vapp)
        pl.addWidget(self.vapp_btn)
        pl.addStretch()
        self.op_widget.setVisible(False)
        lay.addWidget(self.op_widget, 2)

        # 底部操作条
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:white; border-top:1px solid #E3EAF5;}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.setSpacing(8)
        self.prev_btn = QPushButton("上一题")
        self.prev_btn.setStyleSheet(GHOST_QSS)
        self.prev_btn.clicked.connect(self.prev)
        bl.addWidget(self.prev_btn, 1)
        self.next_btn = QPushButton("下一题")
        self.next_btn.setStyleSheet(GHOST_QSS)
        self.next_btn.clicked.connect(self.next)
        bl.addWidget(self.next_btn, 1)
        self.submit_btn = QPushButton("交 卷")
        self.submit_btn.setStyleSheet("""
            QPushButton { background:#FF5252; color:white; border:none; border-radius:10px;
                          font-size:14pt; font-weight:700; padding:10px 18px; }
            QPushButton:pressed { background:#E23B3B; }
        """)
        self.submit_btn.clicked.connect(self.submit)
        bl.addWidget(self.submit_btn, 1)
        lay.addWidget(bar)

    # ================= 渲染 =================
    def is_operate(self, q):
        if q.get("refType") == "operate":
            return True
        return len(q.get("options", [])) == 0

    def _is_py(self, q):
        return int(q.get("type") or 0) == 25

    def _is_sql(self, q):
        return int(q.get("type") or 0) == 24

    def _is_web(self, q):
        return int(q.get("subType") or 0) == 6

    def _kind_name(self, q):
        if self._is_sql(q):
            return "MySQL 数据库操作题"
        if self._is_py(q):
            return "Python 编程题"
        if q.get("vapp"):
            return VAPP_NAMES.get(q.get("vapp"), "虚拟程序操作题") + "（实验）"
        if int(q.get("subType") or 0) == 2:
            return "文件管理操作题"
        if self._is_web(q):
            return "网页操作题"
        return "Office / 设计软件操作题"

    def render(self):
        q = self.qs[self.idx]
        op = self.is_operate(q)
        if op:
            self.stem_box.setPlainText(self._format_operate_stem(q.get("stem", "")))
        else:
            self.stem_box.setPlainText("%d. %s" % (self.idx + 1, q.get("stem", "")))

        # 选项
        self.opt_scroll.setVisible(not op)
        if not op:
            opts = q.get("options", [])
            for i in range(6):
                if i < len(opts):
                    o = opts[i] if isinstance(opts[i], dict) else {"k": chr(65 + i), "t": str(opts[i])}
                    k = o.get("k", "") or chr(65 + i)
                    t = o.get("t", "") or ""
                    self.rbs[i].setText("%s. %s" % (k, t))
                    self.rbs[i].setVisible(bool(t))
                    self.rbs[i].setChecked(self.sel[self.idx] == i)
                else:
                    self.rbs[i].setVisible(False)
                    self.rbs[i].setChecked(False)
        else:
            for rb in self.rbs:
                rb.setChecked(False)

        # 操作题区
        self.op_widget.setVisible(op)
        if op:
            kind = self._kind_name(q)
            if self._is_sql(q):
                self.op_note.setText(
                    "本题为 %s。手机端没有 MySQL 5.7 环境（官方无 arm64 版），"
                    "请在电脑上完成；这里可查看素材、上传已有作答文件。" % kind)
                self.mat_btn.setText("下载素材")
            elif self._is_py(q):
                self.op_note.setText("本题为 %s。点下方按钮生成 answer.py 并用编辑器完成。"
                                     % kind)
                self.mat_btn.setText("生成并打开代码文件")
            elif q.get("vapp"):
                self.op_note.setText(
                    "本题为 %s。将尝试用容器内 Wine 打开虚拟程序（实验性，可能不稳定）；"
                    "若打不开请到电脑完成。" % kind)
                self.mat_btn.setText("下载素材")
            elif self._is_web(q):
                import web_util
                self.op_note.setText(
                    "本题为 %s。点下方按钮用「%s」打开素材网页，按题目要求"
                    "收藏/另存到作答文件夹后交卷（收藏夹会自动随卷上传判分）。"
                    % (kind, web_util.browser_label()))
                self.mat_btn.setText("打开网页素材")
            else:
                self.op_note.setText("本题为 %s。下载素材 → 用 WPS 打开完成 → 保存回作答文件夹 → 交卷。"
                                     % kind)
                self.mat_btn.setText("下载素材并打开")
            self.vapp_btn.setVisible(bool(q.get("vapp")))

        self.prev_btn.setEnabled(self.idx > 0)
        is_last = self.idx == len(self.qs) - 1
        self.next_btn.setVisible(not is_last)
        self._refresh_nav()

    def _format_operate_stem(self, raw):
        try:
            from utils import format_operate_stem
            return format_operate_stem(raw)
        except Exception:
            return raw or ""

    def _refresh_nav(self):
        for i, b in enumerate(self.nav_btns):
            if i == self.idx:
                b.setProperty("state", "cur")
            elif self._answered(i):
                b.setProperty("state", "done")
            else:
                b.setProperty("state", "")
            b.style().unpolish(b)
            b.style().polish(b)

    def _answered(self, i):
        q = self.qs[i]
        if self.is_operate(q):
            qid = str(q.get("qid"))
            d = paths.q_dir(qid)
            try:
                files = [f for f in os.listdir(d) if f != "material.zip" and not f.startswith(".")]
            except Exception:
                files = []
            return bool((self.op_texts[i] or "").strip() or files)
        return self.sel[i] >= 0

    def _on_choose(self):
        btn = self.sender()
        if btn and btn.isChecked():
            self.sel[self.idx] = self.rb_group.id(btn)
            self._refresh_nav()

    def goto(self, k):
        if 0 <= k < len(self.qs) and k != self.idx:
            self._save_op_text()
            self.idx = k
            self.render()

    def prev(self):
        self.goto(self.idx - 1)

    def next(self):
        self.goto(self.idx + 1)

    def _save_op_text(self):
        pass    # 手机版操作题以文件夹作答为主，op_texts 仅作占位

    # ================= 素材 / 作答 =================
    def _q_dir(self, q):
        return paths.q_dir(str(q.get("qid")))

    def _ensure_material(self, q):
        """下载并解压本题素材（material.zip），返回作答目录；失败返回 None"""
        qid = str(q.get("qid"))
        d = self._q_dir(q)
        zp = os.path.join(d, "material.zip")
        if not os.path.exists(zp):
            try:
                r = self.net.call("getMaterialZip", timuID=qid, timeout=120)
            except Exception as e:
                QMessageBox.warning(self, "素材下载失败", str(e))
                return None
            if r.get("code") != 0 or not r.get("zip"):
                QMessageBox.warning(self, "素材下载失败", r.get("msg") or "该题没有素材包")
                return None
            with open(zp, "wb") as f:
                f.write(base64.b64decode(r["zip"]))
            try:
                with zipfile.ZipFile(zp) as z:
                    z.extractall(d)
            except Exception as e:
                QMessageBox.warning(self, "素材解压失败", str(e))
                return None
        return d

    @staticmethod
    def _main_doc(d):
        for ext in ("*.docx", "*.xlsx", "*.pptx", "*.accdb", "*.psd",
                    "*.html", "*.htm", "*.doc", "*.xls", "*.ppt"):
            fs = [x for x in glob.glob(os.path.join(d, ext))
                  if not os.path.basename(x).startswith("answer_")]
            if fs:
                return fs[0]
        return None

    def open_material(self):
        q = self.qs[self.idx]

        # Python 题：生成 answer.py 模板并打开
        if self._is_py(q):
            d = self._q_dir(q)
            p = os.path.join(d, "answer.py")
            if not os.path.exists(p):
                try:
                    with open(p, "w", encoding="utf-8") as f:
                        f.write((q.get("stem") or "").replace("\r\n", "\n"))
                except Exception as e:
                    QMessageBox.warning(self, "生成失败", str(e))
                    return
            if not paths.open_in_os(p):
                paths.open_in_os(d)
            self._refresh_nav()
            return

        d = self._ensure_material(q)
        if not d:
            return
        doc = self._main_doc(d)
        if doc and self._is_web(q):
            # 网页题：用浏览器打开（容器内优先 Edge，没有则 Chromium——同内核、
            # 书签格式与 Edge 一致，判分链路兼容）
            import web_util
            web_util.open_url(doc)
        elif doc:
            paths.open_in_os(doc)
        else:
            paths.open_in_os(d)
        self._refresh_nav()

    def open_folder(self):
        q = self.qs[self.idx]
        d = self._q_dir(q)
        paths.open_in_os(d)

    def open_vapp(self):
        """虚拟程序题：容器内 Wine 打开（实验性）"""
        q = self.qs[self.idx]
        vapp = (q.get("vapp") or "").strip()
        if not vapp:
            return
        if not paths.which("wine"):
            QMessageBox.information(
                self, "未安装 Wine",
                "容器内还没有 Wine 环境。\n可在终端运行：bash setup/install_wine.sh 安装后再试；"
                "或到电脑上完成本题。")
            return
        d = self._q_dir(q)
        exe = self._vapp_exe(vapp)
        if not exe:
            QMessageBox.information(self, "未找到虚拟程序",
                                    "虚拟程序文件未就绪（vapp 资源缺失）。")
            return
        paths.run_bg(["wine", exe], cwd=d)
        QMessageBox.information(
            self, "已尝试启动",
            "已用 Wine 打开「%s」。\n作答完成后关闭程序，回到这里交卷（上传作答记录）。"
            % VAPP_NAMES.get(vapp, vapp))

    @staticmethod
    def _vapp_exe(vapp):
        """在 client/vapp/ 目录里找虚拟程序 exe（随包提供 vapp_exes.zip 时已解压）"""
        root = os.path.join(paths.APP_DIR, "vapp")
        if not os.path.isdir(root):
            return None
        for pat in ("*%s*.exe" % vapp, "*.exe"):
            fs = glob.glob(os.path.join(root, "**", pat), recursive=True)
            if fs:
                return fs[0]
        return None

    # ================= 交卷 =================
    def submit(self):
        if self._submitted:
            return
        if QMessageBox.question(self, "确认交卷", "确定要交卷吗？还在做的题不会计分。") \
                != QMessageBox.Yes:
            return
        self._submitted = True
        self.hide()
        net_task(self.main, "正在交卷，请稍候…\n（操作题需要先上传，可能需要几十秒）",
                 self._work_submit, on_ok=self._on_submit_ok, on_err=self._on_submit_err)

    def _work_submit(self):
        """后台线程：上传操作题 → 提交客观题 → 合并明细（不碰 UI）"""
        # 1) 操作题上传
        for q in self.qs:
            if not self.is_operate(q):
                continue
            self._upload_one(q)

        # 2) 客观题答案
        answers = []
        for i, q in enumerate(self.qs):
            if self.is_operate(q):
                answers.append({"qid": q.get("qid"), "refType": "operate",
                                "answer": self.op_texts[i]})
            else:
                a = ""
                if self.sel[i] >= 0:
                    a = ["A", "B", "C", "D", "E", "F"][self.sel[i]]
                answers.append({"qid": q.get("qid"), "refType": "obj", "answer": a})

        # 3) 提交
        if self.practice:
            r = self.net.call("submitPractice", answers=answers, timeout=120)
        else:
            r = self.net.call("submit", recordId=self.record_id, answers=answers, timeout=120)

        score_hidden = bool(r.get("scoreHidden", False))
        score = r.get("score", 0) if not score_hidden else None
        total = r.get("total", 0) if not score_hidden else None
        detail = list(r.get("detail", []) or []) if not score_hidden else []

        # 4) 合并操作题明细（服务端未返回的用本地判分缓存补全）
        if not score_hidden:
            have = {str(d.get("qid")) for d in detail if d.get("refType") == "operate"}
            for q in self.qs:
                if not self.is_operate(q):
                    continue
                qid = str(q.get("qid"))
                if qid in have:
                    continue
                g = self.op_grades.get(qid)
                if g:
                    pend = bool(g.get("pending")) or g.get("score") is None
                    sc_g = None if pend else (g.get("score", 0) or 0)
                    detail.append({"qid": q.get("qid"), "refType": "operate",
                                   "stem": q.get("stem", ""), "your": "(见上传文件)",
                                   "right": "自动判分", "score": sc_g, "fullScore": 20.0,
                                   "hits": g.get("hits", []), "miss": g.get("miss", []),
                                   "total": g.get("total", 0), "note": g.get("note", ""),
                                   "pending": pend})
                else:
                    detail.append({"qid": q.get("qid"), "refType": "operate",
                                   "stem": q.get("stem", ""), "your": "(未上传文件)",
                                   "right": "自动判分", "score": 0, "fullScore": 20.0,
                                   "hits": [], "miss": [], "total": 0,
                                   "note": "未检测到作答文件", "pending": False})
            if self.practice:
                op_got = sum(d.get("score", 0) or 0 for d in detail if d.get("refType") == "operate")
                op_full = sum(d.get("fullScore", 20) or 20 for d in detail if d.get("refType") == "operate")
                score = (score or 0) + op_got
                total = (total or 0) + op_full

        return {"score": score, "total": total, "detail": detail,
                "score_hidden": score_hidden, "msg": r.get("msg", "")}

    def _upload_one(self, q):
        """单道操作题打包上传（与桌面端同构；手机端简化：仅文件夹/代码两种）"""
        qid = str(q.get("qid", ""))
        d = self._q_dir(q)
        cmd = "practiceGradeOperate" if self.practice else "uploadOperate"

        def _payload_common(payload):
            if self.practice:
                payload["timuID"] = qid
            else:
                payload["recordId"] = self.record_id
                payload["qid"] = qid

        try:
            # MySQL 题：整个文件夹（datadir + tj*.sql）打包上传
            if self._is_sql(q):
                if not os.path.isdir(d) or not [f for f in os.listdir(d) if f != "material.zip"]:
                    return
                zb = self._zip_dir(d, exclude={"material.zip", "_tmp_mysqld.log"})
                payload = {"mysqlZip": base64.b64encode(zb).decode()}
                _payload_common(payload)
                r = self.net.call(cmd, timeout=180, **payload)
                self._store_grade(qid, r, "MySQL题")
                return

            # Python 题：读 answer.py 上传
            if self._is_py(q):
                p = os.path.join(d, "answer.py")
                if not os.path.exists(p):
                    return
                with open(p, "rb") as f:
                    raw = f.read()
                code = ""
                for enc in ("utf-8-sig", "gbk"):
                    try:
                        code = raw.decode(enc)
                        break
                    except Exception:
                        continue
                if not code.strip() or code.strip() == (q.get("stem") or "").strip():
                    return
                payload = {"code": code}
                _payload_common(payload)
                r = self.net.call(cmd, timeout=120, **payload)
                self._store_grade(qid, r, "Python编程题")
                return

            # 虚拟程序题：找最近生成的作答记录文件上传
            vapp = (q.get("vapp") or "").strip()
            if vapp:
                logf = self._find_vapp_log(d)
                if not logf:
                    return
                try:
                    with open(logf, "rb") as f:
                        log = f.read().decode("utf-8", "replace")
                except Exception:
                    return
                payload = {"vapp": vapp, "log": log}
                _payload_common(payload)
                r = self.net.call(cmd, timeout=120, **payload)
                self._store_grade(qid, r, "虚拟程序题")
                return

            # 其余（Office/文件管理/网页）：整目录打包上传
            if not os.path.isdir(d):
                return
            if not [f for f in os.listdir(d) if f != "material.zip"]:
                return
            zb = self._zip_dir(d, exclude={"material.zip"})
            payload = {"paperZip": base64.b64encode(zb).decode()}
            if self._is_web(q):
                # 网页收藏题：附带浏览器收藏夹（Edge/Chromium 格式一致），用于 hf3381 判定
                try:
                    import web_util
                    payload["favorites"] = web_util.read_bookmarks()
                except Exception:
                    pass
            _payload_common(payload)
            r = self.net.call(cmd, timeout=180, **payload)
            self._store_grade(qid, r, "操作题")
        except Exception as e:
            self.op_grades[qid] = {"pending": True, "score": None,
                                   "note": "上传失败：%s" % str(e)[:80],
                                   "hits": [], "miss": [], "total": 0}

    def _store_grade(self, qid, r, tag):
        if r.get("pending"):
            self.op_grades[qid] = {"pending": True, "score": None,
                                   "note": r.get("note", "判分中"), "hits": [], "miss": [], "total": 0}
        elif r.get("auto"):
            self.op_grades[qid] = {"score": r.get("score", 0), "hits": r.get("hits", []),
                                   "miss": r.get("miss", []), "total": r.get("total", 0),
                                   "note": r.get("note", "")}
        else:
            self.op_grades[qid] = {"pending": True, "score": None,
                                   "note": "%s未能判分：%s" % (tag, r.get("msg") or r.get("note") or ""),
                                   "hits": [], "miss": [], "total": 0}

    @staticmethod
    def _find_vapp_log(d):
        cands = []
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn == "material.zip":
                    continue
                p = os.path.join(root, fn)
                if fn.lower().endswith((".log", ".txt")) or "." not in fn:
                    try:
                        cands.append((os.path.getmtime(p), p))
                    except Exception:
                        pass
        cands.sort(reverse=True)
        return cands[0][1] if cands else None

    @staticmethod
    def _zip_dir(qdir, exclude=None):
        exclude = set(exclude or [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, files in os.walk(qdir):
                for fn in files:
                    if fn == ".keep" or fn in exclude:
                        continue
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, qdir).replace("\\", "/")
                    try:
                        z.write(full, rel)
                    except Exception:
                        pass
        return buf.getvalue()

    def _on_submit_ok(self, res):
        self._hb_stop.set()
        if res.get("score_hidden"):
            QMessageBox.information(self.main, "交卷成功",
                                    res.get("msg") or "本次考试成绩暂未公布，请等待教师发布。")
        else:
            from ui_main import show_result_dialog
            show_result_dialog(self.main, res.get("detail", []),
                               head="交卷成功   得分 %s / %s" % (res.get("score"), res.get("total")))
        self._submitted = True
        self.close()
        self.main.exam_closed()

    def _on_submit_err(self, e):
        self._submitted = False
        self.show()
        QMessageBox.critical(self, "交卷失败",
                             "交卷失败：%s\n\n请检查网络后重新交卷。" % e)

    # ================= 心跳 / 计时 =================
    def _hb_loop(self):
        while not self._hb_stop.is_set():
            try:
                qids = [str(q.get("qid")) for q in self.qs]
                answered = []
                for i, q in enumerate(self.qs):
                    if self.is_operate(q):
                        if (self.op_texts[i] or "").strip():
                            answered.append({"qid": str(q.get("qid")),
                                             "refType": "operate", "sel": -1})
                    else:
                        if self.sel[i] >= 0:
                            answered.append({"qid": str(q.get("qid")),
                                             "refType": "obj", "sel": self.sel[i]})
                cur = str(self.qs[self.idx].get("qid")) if self.qs else ""
                self.net.call("heartbeat", timeout=8,
                              mode="practice" if self.practice else "homework",
                              id=self.record_id, current=cur, qids=qids, answered=answered)
            except Exception:
                pass
            self._hb_stop.wait(5)

    def _tick(self):
        self.remain -= 1
        if self.remain <= 0:
            try:
                self.timer.stop()
            except Exception:
                pass
            QMessageBox.information(self, "提示", "时间到，自动交卷！")
            self.submit()
            return
        self._update_time_label()

    def _update_time_label(self):
        if self.duration_sec > 0:
            m, s = divmod(max(0, self.remain), 60)
            self.time_label.setText("%02d:%02d" % (m, s))

    def closeEvent(self, e):
        self._hb_stop.set()
        if not self._submitted:
            if QMessageBox.question(self, "确认", "答题还没交卷，确定关闭吗？") != QMessageBox.Yes:
                e.ignore()
                return
            try:
                self.main.exam_closed(reload_hw=True)
            except Exception:
                pass
        e.accept()
