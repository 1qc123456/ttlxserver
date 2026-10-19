# -*- coding: utf-8 -*-
import random, os, base64
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QCheckBox, QSpinBox, QGroupBox, QScrollArea, QMessageBox, QFileDialog,
    QStackedWidget, QFrame, QSizePolicy, QTextEdit, QButtonGroup, QRadioButton,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QDialog)
from qfluentwidgets import (NavigationInterface, NavigationItemPosition, FluentIcon as FIF,
    PrimaryPushButton, PushButton, CardWidget, BodyLabel, CaptionLabel, StrongBodyLabel,
    SubtitleLabel, TableWidget, ComboBox, SpinBox, CheckBox, LineEdit, InfoBar,
    InfoBarPosition, HyperlinkButton, ProgressBar)
from common.utils import render_detail_html, render_wrong_html
from common.grade_detail import GradeDetailDialog
from common.waiting import net_task

# ---------- 练习列表卡片 ----------
class PracticeCard(CardWidget):
    start_clicked = Signal(int)
    result_clicked = Signal(int)
    def __init__(self, hw, parent=None):
        super().__init__(parent)
        self.hw = hw
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 15)
        left = QVBoxLayout()
        title = StrongBodyLabel(hw.get("title", ""))
        left.addWidget(title)
        score = hw.get("myScore")
        wstate = hw.get("windowState", 0)
        if wstate == 3:
            if score is None:
                sc = BodyLabel("已交卷")
                sc.setStyleSheet("color: #718096;")
            else:
                sc = BodyLabel(f"已交卷 得分 {score}")
                sc.setStyleSheet("color: #4F86F8;")
        elif score is None:
            sc = BodyLabel("未作答")
            sc.setStyleSheet("color: #2ECC71;")
        else:
            sc = BodyLabel(f"已得分 {score}")
            sc.setStyleSheet("color: #4F86F8;")
        left.addWidget(sc)
        wmsg = hw.get("windowMsg") or ""
        if wmsg:
            wl = CaptionLabel(wmsg)
            wl.setStyleSheet("color: #E67E22;")
            left.addWidget(wl)
        layout.addLayout(left, 1)
        if wstate == 3 or (score is not None and wstate != 1):
            btn = PushButton("查看成绩")
            btn.clicked.connect(lambda: self.result_clicked.emit(hw["id"]))
        elif wstate == 1:
            btn = PushButton("未开始")
            btn.setEnabled(False)
            btn.setToolTip(wmsg or "考试未开始")
        elif wstate == 2:
            btn = PushButton("已结束")
            btn.setEnabled(False)
        else:
            btn = PrimaryPushButton("开始答题")
            btn.clicked.connect(lambda: self.start_clicked.emit(hw["id"]))
        btn.setFixedWidth(110)
        layout.addWidget(btn)

# ---------- 练习列表页 ----------
class PracticeListPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("作业 / 考试（自主练习请进「自主训练」）"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.container = QWidget()
        self.clayout = QVBoxLayout(self.container)
        self.clayout.setSpacing(10)
        self.clayout.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)
        self.reload()

    def reload(self):
        net_task(self, "正在加载练习列表…",
                 lambda: self.net.call("stuHomeworks"),
                 on_ok=self._render_hws,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_hws(self, r):
        while self.clayout.count() > 1:
            item = self.clayout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        try:
            hws = r.get("homeworks", [])
            if not hws:
                empty = CaptionLabel("暂无练习，请等待老师发布")
                empty.setStyleSheet("color:#999; padding:40px;")
                self.clayout.insertWidget(0, empty)
                return
            for hw in hws:
                card = PracticeCard(hw)
                card.start_clicked.connect(self.start_exam)
                card.result_clicked.connect(self.show_result)
                self.clayout.insertWidget(self.clayout.count()-1, card)
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def start_exam(self, hw_id):
        def _work():
            r = self.net.call("startHomework", homeworkId=hw_id)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", "未知错误"))
            return r

        def _ok(r):
            from student.exam_window import ExamWindow
            dur = r.get("durationSec", 0) or 0
            self.exam = ExamWindow(self.net, r.get("recordId", 0), r.get("title", ""),
                                   r.get("questions", []), practice=False, duration_sec=dur,
                                   on_exit=self.reload)
            self.exam.show()

        net_task(self, "正在加载试卷题目…", _work, on_ok=_ok)

    def show_result(self, hw_id):
        def _work():
            r = self.net.call("myResult", homeworkId=hw_id)
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
                QMessageBox.information(self, "提示", r.get("msg", "成绩待教师公布")); return
            detail = r.get("detail", [])

            def _refresh():
                """后台判分完成后重拉成绩（判分中的题刷新后即为最终分）。"""
                rr = self.net.call("myResult", homeworkId=hw_id)
                rid2 = rr.get("recordId")
                if rid2:
                    det = self.net.call("recordDetail", recordId=rid2, timeout=60).get("detail", [])
                else:
                    det = rr.get("detail", [])
                return {"score": None, "total": None, "detail": det}

            GradeDetailDialog(score=None, total=None, detail=detail,
                              title="成绩详情", parent=self, refresh_cb=_refresh).exec()

        net_task(self, "正在加载成绩…", _work, on_ok=_ok)

# ---------- 自主训练页 ----------
class SelfTrainingPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("自主训练"))
        layout.addWidget(CaptionLabel("勾选要练的模块并填题量，可混合选择题与操作题"))

        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["选", "题型 / 模块", "练几题"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始训练")
        self.start_btn.setFixedWidth(160)
        self.start_btn.clicked.connect(self.start_custom)
        btn_row.addWidget(self.start_btn)
        self.set_btn = PushButton("套卷训练")
        self.set_btn.setFixedWidth(160)
        self.set_btn.clicked.connect(self.start_set)
        btn_row.addWidget(self.set_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addWidget(CaptionLabel("说明：选择题即时出分看解析；操作题下载素材在Office里完成，保存后交卷自动判分。均不计入作业成绩。"))
        self._load_modules()

    def _load_modules(self):
        self.table.setRowCount(0)
        net_task(self, "正在加载训练模块…",
                 lambda: (self.net.call("listCourses"), self.net.call("listOperateTypes")),
                 on_ok=self._render_modules,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_modules(self, pair):
        cr, tr = pair
        try:
            for c in cr.get("courses", []):
                row = self.table.rowCount()
                self.table.insertRow(row)
                chk = CheckBox()
                self.table.setCellWidget(row, 0, chk)
                self.table.setItem(row, 1, QTableWidgetItem("选择题·" + c.get("name", "")))
                sp = SpinBox()
                sp.setRange(0, 100)
                sp.setValue(5)
                self.table.setCellWidget(row, 2, sp)
                self.table.item(row, 1).setData(Qt.UserRole, {"type": "obj", "course": c.get("course")})
            for t in tr.get("types", []):
                if t.get("key") == "all":
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                chk = CheckBox()
                self.table.setCellWidget(row, 0, chk)
                self.table.setItem(row, 1, QTableWidgetItem(f"操作题·{t.get('name','')} ({t.get('count',0)}题)"))
                sp = SpinBox()
                sp.setRange(0, 50)
                sp.setValue(2)
                self.table.setCellWidget(row, 2, sp)
                self.table.item(row, 1).setData(Qt.UserRole, {"type": "operate", "opType": t.get("key")})
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def _collect_parts(self):
        parts = []
        for row in range(self.table.rowCount()):
            chk = self.table.cellWidget(row, 0)
            if not chk or not chk.isChecked():
                continue
            sp = self.table.cellWidget(row, 2)
            cnt = sp.value() if sp else 0
            if cnt <= 0:
                continue
            tag = self.table.item(row, 1).data(Qt.UserRole)
            part = {"type": tag["type"], "count": cnt}
            if tag["type"] == "operate":
                part["opType"] = tag["opType"]
                part["score"] = 20.0
            else:
                part["course"] = tag["course"]
                part["score"] = 3.0
            parts.append(part)
        return parts

    def start_custom(self):
        parts = self._collect_parts()
        if not parts:
            QMessageBox.warning(self, "提示", "请至少勾选一个模块并填写题量")
            return
        self._launch(parts, "自主训练")

    def start_set(self):
        # 固定套卷：单选50(8模块各至少1道) + win10 5 + win10net 4 + email 1 + word 1 + ppt 1 + excel 1 + python 2
        parts = []
        cnts = [1] * 8
        for _ in range(42):
            cnts[random.randint(0, 7)] += 1
        for i in range(8):
            parts.append({"type": "obj", "course": i + 1, "count": cnts[i]})
        for ot, c in [("win10", 5), ("win10net", 4), ("email", 1), ("word", 1), ("ppt", 1), ("excel", 1), ("python", 2)]:
            parts.append({"type": "operate", "opType": ot, "count": c, "score": 20.0})
        self._launch(parts, "套卷训练")

    def _launch(self, parts, title):
        def _work():
            r = self.net.call("startPractice", parts=parts)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", ""))
            return r

        def _ok(r):
            qs = r.get("questions", [])
            if not qs:
                QMessageBox.warning(self, "提示", "没有抽到可练习的题目")
                return
            from student.exam_window import ExamWindow
            self.exam = ExamWindow(self.net, 0, title, qs, practice=True, duration_sec=0,
                                    on_exit=lambda: None)
            self.exam.show()

        net_task(self, "正在抽题，请稍等…", _work, on_ok=_ok)

# ---------- 错题本页 ----------
COURSE_NAMES = {1:"模块1 信息与数据",2:"模块2 进制编码",3:"模块3 操作系统",
                 4:"模块4 信息安全",5:"模块5 计算机硬件",6:"模块6 软件程序",
                 7:"模块7 多媒体",8:"模块8 计算机网络",9:"模块9 行业术语"}

class WrongBookPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self.wrong = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        # 标题行
        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("错题本"))
        self.count_label = CaptionLabel("")
        top.addWidget(self.count_label)
        top.addStretch()
        self.expand_btn = PushButton("全部展开")
        self.expand_btn.setFixedWidth(100)
        self.expand_btn.clicked.connect(self.expand_all)
        top.addWidget(self.expand_btn)
        self.collapse_btn = PushButton("全部折叠")
        self.collapse_btn.setFixedWidth(100)
        self.collapse_btn.clicked.connect(self.collapse_all)
        top.addWidget(self.collapse_btn)
        self.all_chk = CheckBox("全选")
        self.all_chk.stateChanged.connect(self.toggle_all)
        top.addWidget(self.all_chk)
        self.sel_btn = PrimaryPushButton("重做选中")
        self.sel_btn.setFixedWidth(120)
        self.sel_btn.clicked.connect(self.redo_selected)
        top.addWidget(self.sel_btn)
        layout.addLayout(top)
        # 树形列表
        self.tree = QTreeWidget()
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setHeaderLabels(["错题", "我的答案", "正确答案"])
        self.tree.setColumnWidth(0, 500)
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 120)
        self.tree.setFont(QFont("Microsoft YaHei UI", 10))
        self.tree.setAlternatingRowColors(True)
        self._syncing = False
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._show_wrong_detail)
        layout.addWidget(self.tree, 1)
        self.reload()

    def reload(self):
        self.tree.clear()
        self.all_chk.setChecked(False)
        net_task(self, "正在加载错题…",
                 lambda: self.net.call("wrongList"),
                 on_ok=self._render_wrong,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_wrong(self, r):
        try:
            self.wrong = r.get("wrong", [])
        except Exception:
            self.wrong = []
        # 按模块分组
        groups = {}
        operate_group = []
        for w in self.wrong:
            course = w.get("course")
            if w.get("refType") == "operate" or not course:
                operate_group.append(w)
            else:
                groups.setdefault(course, []).append(w)
        # 按模块顺序添加
        total = 0
        for course in sorted(groups.keys()):
            name = COURSE_NAMES.get(course, f"模块{course}")
            items = groups[course]
            total += len(items)
            group = QTreeWidgetItem(self.tree, [f"{name}（{len(items)}道）", "", ""])
            group.setExpanded(True)
            f = group.font(0); f.setBold(True); group.setFont(0, f)
            group.setFlags(group.flags() | Qt.ItemIsUserCheckable)
            group.setCheckState(0, Qt.Unchecked)
            for i, w in enumerate(items, 1):
                stem = (w.get("stem", "") or "").replace("\n", " ")[:100]
                child = QTreeWidgetItem(group, [f"{i}. {stem}", w.get("my_answer", ""), w.get("right_answer", "")])
                child.setData(0, Qt.UserRole, w)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
        # 操作题分组
        if operate_group:
            total += len(operate_group)
            group = QTreeWidgetItem(self.tree, [f"操作题（{len(operate_group)}道）", "", ""])
            group.setExpanded(True)
            f = group.font(0); f.setBold(True); group.setFont(0, f)
            group.setFlags(group.flags() | Qt.ItemIsUserCheckable)
            group.setCheckState(0, Qt.Unchecked)
            for i, w in enumerate(operate_group, 1):
                stem = (w.get("stem", "") or "").replace("\n", " ")[:100]
                child = QTreeWidgetItem(group, [f"{i}. {stem}", "(操作题)", "(自动判分)"])
                child.setData(0, Qt.UserRole, w)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked)
        self.count_label.setText(f"共 {len(self.wrong)} 道错题")
        if not self.wrong:
            InfoBar.success("提示", "太棒了，没有错题！", duration=2000, position=InfoBarPosition.TOP, parent=self)

    def _show_wrong_detail(self, item, column=0):
        """双击错题子节点，弹出完整题目（题干+选项+我的/正确答案）"""
        w = item.data(0, Qt.UserRole)
        if not w:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('错题详情')
        dlg.resize(720, 560)
        vl = QVBoxLayout(dlg)
        te = QTextEdit(); te.setReadOnly(True)
        te.setFont(QFont('Microsoft YaHei UI', 11))
        te.setHtml(render_wrong_html(w))
        vl.addWidget(te)
        hb = QHBoxLayout(); hb.addStretch()
        btn = PrimaryPushButton('关闭'); btn.setFixedWidth(100); btn.clicked.connect(dlg.accept)
        hb.addWidget(btn); vl.addLayout(hb)
        dlg.exec()

    def _on_item_changed(self, item, column):
        """父子复选框联动：勾选模块自动全选该模块错题"""
        if self._syncing or column != 0:
            return
        self._syncing = True
        try:
            if item.parent() is None:
                # 顶层模块节点：同步所有子节点
                state = item.checkState(0)
                for j in range(item.childCount()):
                    item.child(j).setCheckState(0, state)
            else:
                # 子节点：更新父节点状态
                parent = item.parent()
                total = parent.childCount()
                checked = sum(1 for j in range(total) if parent.child(j).checkState(0) == Qt.Checked)
                if checked == total:
                    parent.setCheckState(0, Qt.Checked)
                elif checked == 0:
                    parent.setCheckState(0, Qt.Unchecked)
                else:
                    parent.setCheckState(0, Qt.PartiallyChecked)
        finally:
            self._syncing = False

    def expand_all(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)

    def collapse_all(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(False)

    def toggle_all(self):
        checked = self.all_chk.isChecked()
        state = Qt.Checked if checked else Qt.Unchecked
        self._syncing = True
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            group.setCheckState(0, state)
            for j in range(group.childCount()):
                group.child(j).setCheckState(0, state)
        self._syncing = False

    def redo_selected(self):
        sel = []
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.checkState(0) == Qt.Checked:
                    w = child.data(0, Qt.UserRole)
                    if w:
                        w["refType"] = w.get("refType", "obj")
                        sel.append(w)
        if not sel:
            QMessageBox.warning(self, "提示", "请先勾选要重做的错题")
            return
        self._launch(sel)

    def _launch(self, qs):
        from student.exam_window import ExamWindow
        self.exam = ExamWindow(self.net, 0, "错题重做", qs, practice=True, duration_sec=0,
                                submit_cmd="wrongSubmit", on_exit=self.reload)
        self.exam.show()

class HistoryPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("我的成绩"))
        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["类型", "标题", "得分", "时间", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self.reload()

    def reload(self):
        self.table.setRowCount(0)
        net_task(self, "正在加载成绩…",
                 lambda: self.net.call("myHistory"),
                 on_ok=self._render_history,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_history(self, r):
        try:
            for h in r.get("history", []):
                row = self.table.rowCount()
                self.table.insertRow(row)
                kn = "正式考试" if h.get("kind") == "exam" else "平时作业"
                self.table.setItem(row, 0, QTableWidgetItem(kn))
                self.table.setItem(row, 1, QTableWidgetItem(h.get("title", "")))
                self.table.setItem(row, 2, QTableWidgetItem(str(h.get("score", ""))))
                self.table.setItem(row, 3, QTableWidgetItem(h.get("at", "") or ""))
                btn = PushButton("详细")
                btn.setFixedWidth(60)
                btn.clicked.connect(lambda checked, hid=h.get("id"): self.show_detail(hid))
                self.table.setCellWidget(row, 4, btn)
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def show_detail(self, rec_id):
        net_task(self, "正在加载答题详情…",
                 lambda: self.net.call("recordDetail", recordId=rec_id, timeout=60),
                 on_ok=self._show_detail_ok)

    def _show_detail_ok(self, r):
        try:
            detail = r.get("detail", [])

            def _refresh():
                rr = self.net.call("recordDetail", recordId=rec_id, timeout=60)
                det = rr.get("detail", [])
                sc = round(sum(float(d.get("score") or 0) for d in det), 1) if det else None
                tot = round(sum(float(d.get("fullScore") or 0) for d in det), 1) if det else None
                return {"score": sc, "total": tot, "detail": det}

            GradeDetailDialog(score=None, total=None, detail=detail,
                              title="答题详情", parent=self, refresh_cb=_refresh).exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

# ---------- 主窗口 ----------
class MainWindow(QWidget):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.setWindowTitle("学生端")
        self.resize(1000, 680)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = NavigationInterface(self, showMenuButton=True)
        self.nav.setFixedWidth(220)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.practice_page = PracticeListPage(self.net)
        self.training_page = SelfTrainingPage(self.net)
        self.wrong_page = WrongBookPage(self.net)
        self.history_page = HistoryPage(self.net)

        self.stack.addWidget(self.practice_page)
        self.stack.addWidget(self.training_page)
        self.stack.addWidget(self.wrong_page)
        self.stack.addWidget(self.history_page)

        self.nav.addItem(routeKey="practice", icon=FIF.HOME, text="练习", onClick=lambda *args: self.switch(0))
        self.nav.addItem(routeKey="training", icon=FIF.ROBOT, text="自主训练", onClick=lambda *args: self.switch(1))
        self.nav.addItem(routeKey="wrong", icon=FIF.LIBRARY, text="错题本", onClick=lambda *args: self.switch(2))
        self.nav.addItem(routeKey="history", icon=FIF.HISTORY, text="我的成绩", onClick=lambda *args: self.switch(3))
        self.nav.setCurrentItem("practice")

        name = self.net.user.get("displayName", "") if self.net.user else ""
        self.nav.addItem(routeKey="user", icon=FIF.PEOPLE, text=name, position=NavigationItemPosition.BOTTOM)

    def switch(self, idx):
        self.stack.setCurrentIndex(idx)
        if idx == 0:
            QTimer.singleShot(10, self.practice_page.reload)
        elif idx == 2:
            QTimer.singleShot(10, self.wrong_page.reload)
        elif idx == 3:
            QTimer.singleShot(10, self.history_page.reload)
