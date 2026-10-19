# -*- coding: utf-8 -*-
"""手机版学生端：主界面（作业 / 练习 / 我的）。

作业：stuHomeworks → 卡片列表（未开始 / 进行中 / 已交卷查看成绩）
练习：套卷训练（与桌面端同构 50 客观 + 操作题）或按模块自选题量
我的：错题本（wrongList）、历史成绩（myHistory）、关于
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QFrame, QStackedWidget, QCheckBox, QSpinBox,
                               QMessageBox, QDialog, QListWidget, QListWidgetItem)

from waiting import net_task

BTN_QSS = """
QPushButton { background:#4F86F8; color:white; border:none; border-radius:10px;
              font-size:14pt; font-weight:600; padding:8px 16px; }
QPushButton:disabled { background:#B9C8E8; }
QPushButton:pressed { background:#3A6FD8; }
"""
GHOST_QSS = """
QPushButton { background:#EDF2FB; color:#2D5FE8; border:none; border-radius:10px;
              font-size:14pt; font-weight:600; padding:8px 16px; }
QPushButton:pressed { background:#DCE7F9; }
"""
TAB_QSS = """
QPushButton { background:transparent; color:#5A6B85; border:none; font-size:14pt;
              padding:10px 0; font-weight:600; }
QPushButton:checked { color:#2D5FE8; border-top:3px solid #2D5FE8; }
"""

COURSE_NAMES = {1: "模块1 信息与数据", 2: "模块2 进制编码", 3: "模块3 操作系统",
                4: "模块4 信息安全", 5: "模块5 计算机硬件", 6: "模块6 软件程序",
                7: "模块7 多媒体", 8: "模块8 计算机网络", 9: "模块9 行业术语"}


# ============================ 通用小部件 ============================
class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QFrame{background:white; border-radius:12px; border:1px solid #E3EAF5;}")


def big_btn(text, primary=True, h=46):
    b = QPushButton(text)
    b.setFixedHeight(h)
    b.setStyleSheet(BTN_QSS if primary else GHOST_QSS)
    return b


# ============================ 作业页 ============================
class HomeworkPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.net = main.net
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{border:none;}")
        self.box = QWidget()
        self.blay = QVBoxLayout(self.box)
        self.blay.setSpacing(10)
        self.blay.addStretch()
        self.scroll.setWidget(self.box)
        lay.addWidget(self.scroll)
        self.reload()

    def reload(self):
        net_task(self, "正在加载作业列表…",
                 lambda: self.net.call("stuHomeworks"),
                 on_ok=self._render,
                 on_err=lambda e: QMessageBox.warning(self, "加载失败", str(e)))

    def _clear(self):
        while self.blay.count() > 1:
            it = self.blay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _render(self, r):
        self._clear()
        hws = r.get("homeworks", [])
        if not hws:
            empty = QLabel("暂无作业，请等待老师发布")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#999; font-size:13pt; padding:40px;")
            self.blay.insertWidget(0, empty)
            return
        for hw in hws:
            self.blay.insertWidget(self.blay.count() - 1, self._card(hw))

    def _card(self, hw):
        c = Card()
        lay = QVBoxLayout(c)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel(hw.get("title", ""))
        title.setWordWrap(True)
        title.setFont(QFont("Noto Sans CJK SC", 13, QFont.Bold))
        lay.addWidget(title)

        score = hw.get("myScore")
        wstate = hw.get("windowState", 0)
        if wstate == 3:
            st = ("已交卷" if score is None else "已交卷  得分 %s" % score)
            color = "#4F86F8"
        elif score is not None:
            st = "已得分 %s" % score
            color = "#4F86F8"
        else:
            st = "未作答"
            color = "#2ECC71"
        sl = QLabel(st)
        sl.setStyleSheet("color:%s; font-size:12pt;" % color)
        lay.addWidget(sl)

        wmsg = hw.get("windowMsg") or ""
        if wmsg:
            ml = QLabel(wmsg)
            ml.setWordWrap(True)
            ml.setStyleSheet("color:#E67E22; font-size:11pt;")
            lay.addWidget(ml)

        row = QHBoxLayout()
        row.addStretch()
        if wstate == 1:
            b = big_btn("未开始")
            b.setEnabled(False)
            row.addWidget(b)
        elif wstate == 3 or (score is not None and wstate != 1 and wstate != 2):
            b = big_btn("查看成绩", primary=False)
            b.clicked.connect(lambda _=False, hid=hw.get("id"): self.main.show_homework_result(hid))
            row.addWidget(b)
        else:
            b = big_btn("开始答题")
            b.clicked.connect(lambda _=False, hid=hw.get("id"): self.main.start_homework(hid))
            row.addWidget(b)
        lay.addLayout(row)
        return c


# ============================ 练习页 ============================
class PracticePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.net = main.net
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)

        tip = QLabel("自主练习不占考试名额、不受时间限制。\n操作题（Office 等）请在容器内用 WPS 完成后再交卷。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#718096; font-size:11pt;")
        lay.addWidget(tip)

        c1 = Card()
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(16, 14, 16, 14)
        t1 = QLabel("套卷训练（50 道客观题 + 常见操作题）")
        t1.setWordWrap(True)
        t1.setFont(QFont("Noto Sans CJK SC", 12, QFont.Bold))
        l1.addWidget(t1)
        b1 = big_btn("开始套卷训练")
        b1.clicked.connect(self.start_set)
        l1.addWidget(b1)
        lay.addWidget(c1)

        c2 = Card()
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(16, 14, 16, 14)
        t2 = QLabel("按模块练习")
        t2.setFont(QFont("Noto Sans CJK SC", 12, QFont.Bold))
        l2.addWidget(t2)
        self.checks = {}
        for cid, name in COURSE_NAMES.items():
            cb = QCheckBox(name)
            cb.setStyleSheet("QCheckBox{font-size:12pt; padding:4px 0;}")
            cb.setChecked(cid == 1)
            self.checks[cid] = cb
            l2.addWidget(cb)
        row = QHBoxLayout()
        row.addWidget(QLabel("题量："))
        self.cnt = QSpinBox()
        self.cnt.setRange(1, 60)
        self.cnt.setValue(10)
        self.cnt.setStyleSheet("QSpinBox{font-size:12pt; padding:6px;}")
        row.addWidget(self.cnt)
        row.addStretch()
        l2.addLayout(row)
        b2 = big_btn("开始模块练习", primary=False)
        b2.clicked.connect(self.start_modules)
        l2.addWidget(b2)
        lay.addWidget(c2)
        lay.addStretch()

    def start_set(self):
        parts = []
        cnts = [1] * 8
        import random
        for _ in range(42):
            cnts[random.randint(0, 7)] += 1
        for i in range(8):
            parts.append({"type": "obj", "course": i + 1, "count": cnts[i]})
        for ot, c in [("win10", 5), ("win10net", 4), ("email", 1),
                      ("word", 1), ("ppt", 1), ("excel", 1), ("python", 2)]:
            parts.append({"type": "operate", "opType": ot, "count": c, "score": 20.0})
        self._launch(parts, "套卷训练")

    def start_modules(self):
        parts = []
        for cid, cb in self.checks.items():
            if cb.isChecked():
                parts.append({"type": "obj", "course": cid, "count": int(self.cnt.value())})
        if not parts:
            QMessageBox.warning(self, "提示", "请至少勾选一个模块")
            return
        self._launch(parts, "模块练习")

    def _launch(self, parts, title):
        def _work():
            r = self.net.call("startPractice", parts=parts, timeout=60)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", ""))
            return r

        def _ok(r):
            qs = r.get("questions", [])
            if not qs:
                QMessageBox.warning(self, "提示", "没有抽到可练习的题目")
                return
            self.main.open_exam(self.net, 0, title, qs, practice=True, duration_sec=0)

        net_task(self, "正在抽题，请稍等…", _work, on_ok=_ok)


# ============================ 我的页 ============================
class MePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        name = ""
        try:
            name = self.main.net.user.get("displayName", "") or self.main.net.user.get("username", "")
        except Exception:
            pass
        who = QLabel("当前账号：%s" % (name or "—"))
        who.setStyleSheet("font-size:13pt; color:#2D3748;")
        lay.addWidget(who)

        b1 = big_btn("错题本", primary=False)
        b1.clicked.connect(self.show_wrong)
        lay.addWidget(b1)
        b2 = big_btn("历史成绩", primary=False)
        b2.clicked.connect(self.show_history)
        lay.addWidget(b2)
        b3 = big_btn("查看/清理本地文件目录", primary=False)
        b3.clicked.connect(self.open_files)
        lay.addWidget(b3)
        lay.addStretch()
        b4 = big_btn("退出登录")
        b4.setStyleSheet("""
            QPushButton { background:#FFEDED; color:#D64545; border:none; border-radius:10px;
                          font-size:14pt; font-weight:600; padding:8px 16px; }
        """)
        b4.clicked.connect(self.logout)
        lay.addWidget(b4)

    def open_files(self):
        import paths
        paths.ensure_dirs()
        paths.open_in_os(paths.WORK_DIR)

    def logout(self):
        if QMessageBox.question(self, "退出登录", "确定退出当前账号？") != QMessageBox.Yes:
            return
        if self.main.on_logout:
            self.main.on_logout()

    def show_wrong(self):
        def _work():
            r = self.net.call("wrongList", timeout=60)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", ""))
            return r

        def _ok(r):
            dlg = QDialog(self)
            dlg.setWindowTitle("错题本")
            dlg.resize(560, 640)
            vl = QVBoxLayout(dlg)
            lw = QListWidget()
            lw.setStyleSheet("QListWidget{font-size:12pt;}")
            for w in r.get("wrong", []):
                stem = (w.get("stem", "") or "").replace("\n", " ")[:80]
                if w.get("refType") == "operate":
                    item = QListWidgetItem("【操作题】%s" % stem)
                else:
                    item = QListWidgetItem("%s   （我:%s  对:%s）"
                                           % (stem, w.get("my_answer", ""), w.get("right_answer", "")))
                lw.addItem(item)
            if lw.count() == 0:
                lw.addItem("暂无错题，继续保持！")
            vl.addWidget(lw)
            b = big_btn("关闭", primary=False)
            b.clicked.connect(dlg.accept)
            vl.addWidget(b)
            dlg.exec()

        net_task(self, "正在加载错题…", _work, on_ok=_ok)

    def show_history(self):
        def _work():
            r = self.net.call("myHistory", timeout=60)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", ""))
            return r

        def _ok(r):
            dlg = QDialog(self)
            dlg.setWindowTitle("历史成绩")
            dlg.resize(560, 640)
            vl = QVBoxLayout(dlg)
            lw = QListWidget()
            lw.setStyleSheet("QListWidget{font-size:12pt;}")
            rows = r.get("history", [])
            for h in rows:
                kn = "正式考试" if h.get("kind") == "exam" else "平时作业"
                lw.addItem("%s | %s | %s分 | %s"
                           % (kn, h.get("title", ""), h.get("score", ""), h.get("at", "")))
                lw.item(lw.count() - 1).setData(Qt.UserRole, h.get("id"))
            if not rows:
                lw.addItem("暂无历史记录")

            def _open(_i):
                it = lw.currentItem()
                if it is None:
                    return
                rec_id = it.data(Qt.UserRole)
                if rec_id is None:
                    return
                self.main.show_record_detail(rec_id)

            lw.itemDoubleClicked.connect(_open)
            vl.addWidget(QLabel("双击一条查看答题详情："))
            vl.addWidget(lw)
            b = big_btn("关闭", primary=False)
            b.clicked.connect(dlg.accept)
            vl.addWidget(b)
            dlg.exec()

        net_task(self, "正在加载成绩…", _work, on_ok=_ok)


# ============================ 主窗口 ============================
class MainWindow(QWidget):
    def __init__(self, net, on_logout=None):
        super().__init__()
        self.net = net
        self.on_logout = on_logout
        self.exam = None
        self.setWindowTitle("学生端（手机版）")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶栏
        top = QFrame()
        top.setStyleSheet("QFrame{background:#F5F8FF;}")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 10, 14, 10)
        tt = QLabel("学生端")
        tt.setFont(QFont("Noto Sans CJK SC", 15, QFont.Bold))
        tl.addWidget(tt)
        tl.addStretch()
        self.host_lab = QLabel("")
        self.host_lab.setStyleSheet("color:#718096; font-size:10pt;")
        try:
            self.host_lab.setText("%s:%s" % (net.host, net.port))
        except Exception:
            pass
        tl.addWidget(self.host_lab)
        lay.addWidget(top)

        # 页面
        self.stack = QStackedWidget()
        self.hw_page = HomeworkPage(self)
        self.practice_page = PracticePage(self)
        self.me_page = MePage(self)
        self.stack.addWidget(self.hw_page)
        self.stack.addWidget(self.practice_page)
        self.stack.addWidget(self.me_page)
        lay.addWidget(self.stack, 1)

        # 底部页签
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:white; border-top:1px solid #E3EAF5;}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.tabs = []
        for i, name in enumerate(("作业", "练习", "我的")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setStyleSheet(TAB_QSS)
            b.clicked.connect(lambda _=False, idx=i: self.switch(idx))
            bl.addWidget(b, 1)
            self.tabs.append(b)
        lay.addWidget(bar)
        self.switch(0)

    def switch(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.tabs):
            b.setChecked(i == idx)
        if idx == 0:
            self.hw_page.reload()
        elif idx == 1:
            pass
        elif idx == 2:
            pass

    # ---------- 作业 ----------
    def start_homework(self, hw_id):
        def _work():
            r = self.net.call("startHomework", homeworkId=hw_id, timeout=60)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", "未知错误"))
            return r

        def _ok(r):
            self.open_exam(self.net, r.get("recordId", 0), r.get("title", ""),
                           r.get("questions", []), practice=False,
                           duration_sec=r.get("durationSec", 0) or 0)

        net_task(self, "正在加载试卷题目…", _work, on_ok=_ok)

    def show_homework_result(self, hw_id):
        def _work():
            r = self.net.call("myResult", homeworkId=hw_id, timeout=60)
            if r.get("scoreHidden"):
                return {"hidden": True, "msg": r.get("msg", "成绩待教师公布")}
            rid = r.get("recordId")
            if rid:
                d = self.net.call("recordDetail", recordId=rid, timeout=60)
                detail = d.get("detail", [])
            else:
                detail = r.get("detail", [])
            return {"hidden": False, "detail": detail}

        def _ok(r):
            if r.get("hidden"):
                QMessageBox.information(self, "提示", r.get("msg", "成绩待教师公布"))
                return
            show_result_dialog(self, r.get("detail", []))

        net_task(self, "正在加载成绩…", _work, on_ok=_ok)

    def show_record_detail(self, rec_id):
        def _work():
            r = self.net.call("recordDetail", recordId=rec_id, timeout=60)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", ""))
            return r

        def _ok(r):
            show_result_dialog(self, r.get("detail", []))

        net_task(self, "正在加载答题详情…", _work, on_ok=_ok)

    # ---------- 答题 ----------
    def open_exam(self, net, record_id, title, questions, practice=False, duration_sec=0):
        from ui_exam import ExamWindow
        self.exam = ExamWindow(self, net, record_id, title, questions,
                               practice=practice, duration_sec=duration_sec)
        self.exam.show()
        self.hide()

    def exam_closed(self, reload_hw=True):
        self.exam = None
        self.show()
        if reload_hw:
            self.hw_page.reload()


# ============================ 成绩对话框 ============================
def show_result_dialog(parent, detail, head=None):
    """成绩/答题详情：总分 + 逐题得分（手机版精简展示）"""
    dlg = QDialog(parent)
    dlg.setWindowTitle("成绩详情")
    dlg.resize(580, 680)
    vl = QVBoxLayout(dlg)

    sc = sum(float(d.get("score") or 0) for d in detail) if detail else 0.0
    tot = sum(float(d.get("fullScore") or 0) for d in detail) if detail else 0.0
    if head is None:
        head = "总分：%s / %s" % (round(sc, 1), round(tot, 1))
    hl = QLabel(head)
    hl.setFont(QFont("Noto Sans CJK SC", 16, QFont.Bold))
    hl.setWordWrap(True)
    hl.setStyleSheet("color:#2D5FE8;")
    vl.addWidget(hl)

    lw = QListWidget()
    lw.setStyleSheet("QListWidget{font-size:11pt;}")
    for i, d in enumerate(detail, 1):
        stem = (d.get("stem") or "").replace("\n", " ")[:60]
        if d.get("refType") == "operate":
            note = d.get("note", "") or "自动判分"
            pend = "（判分中）" if d.get("pending") else ""
            txt = "%d.【操作】%s  %s分 %s%s" % (i, stem, d.get("score", ""), note, pend)
        else:
            your = d.get("your", "")
            right = d.get("right", "")
            ok = "√" if d.get("correct") else "×"
            txt = "%d.%s %s  我:%s 对:%s  %s分" % (i, ok, stem, your, right, d.get("score", ""))
        lw.addItem(txt)
    if not detail:
        lw.addItem("暂无明细")
    vl.addWidget(lw, 1)
    b = big_btn("关闭", primary=False)
    b.clicked.connect(dlg.accept)
    vl.addWidget(b)
    dlg.exec()
