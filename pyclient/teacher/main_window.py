# -*- coding: utf-8 -*-
import os, base64
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QCheckBox, QSpinBox,
    QMessageBox, QFileDialog, QStackedWidget, QFrame, QTextEdit, QListWidget,
    QListWidgetItem, QLineEdit, QDialog, QFormLayout, QDialogButtonBox, QScrollArea, QGroupBox,
    QAbstractItemView, QDateTimeEdit)
from qfluentwidgets import (NavigationInterface, NavigationItemPosition, FluentIcon as FIF,
    PrimaryPushButton, PushButton, CardWidget, BodyLabel, CaptionLabel, StrongBodyLabel,
    SubtitleLabel, TableWidget, ComboBox, SpinBox, CheckBox, LineEdit, InfoBar,
    InfoBarPosition, ProgressBar)
from common.utils import render_detail_html, format_operate_stem
from common.grade_detail import GradeDetailDialog
from common.waiting import net_task

# ==================== 布置作业 ====================
class QuestionPickerDialog(QDialog):
    """手动选题对话框：浏览题库，勾选具体题目，支持搜索和分页"""
    PAGE = 80

    def __init__(self, net, qtype, course_or_optype, module_name, parent=None):
        super().__init__(parent)
        self.net = net
        self.qtype = qtype
        self.course = course_or_optype if qtype == "obj" else 0
        self.opType = course_or_optype if qtype == "operate" else ""
        self.selected = {}   # id -> 显示文本
        self.offset = 0
        self.total = 0
        self.setWindowTitle(f"手动选题 - {module_name}")
        self.resize(950, 650)
        vl = QVBoxLayout(self)
        # 搜索行
        sr = QHBoxLayout()
        self.kw_edit = LineEdit(); self.kw_edit.setPlaceholderText("输入关键词搜索题目…")
        self.kw_edit.returnPressed.connect(self.search)
        sr.addWidget(self.kw_edit, 1)
        self.search_btn = PrimaryPushButton("搜索"); self.search_btn.setFixedWidth(80)
        self.search_btn.clicked.connect(self.search)
        sr.addWidget(self.search_btn)
        self.clear_btn = PushButton("清除"); self.clear_btn.setFixedWidth(80)
        self.clear_btn.clicked.connect(self.clear_search)
        sr.addWidget(self.clear_btn)
        vl.addLayout(sr)
        # 题目表格
        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        if qtype == "obj":
            self.table.setColumnCount(4)
            self.table.setHorizontalHeaderLabels(["选", "题号", "题目内容", "答案"])
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            self.table.setColumnWidth(0, 40); self.table.setColumnWidth(1, 70); self.table.setColumnWidth(3, 60)
        else:
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(["选", "编号", "操作内容"])
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            self.table.setColumnWidth(0, 40); self.table.setColumnWidth(1, 70)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self.show_detail)
        vl.addWidget(self.table, 1)
        # 分页行
        pr = QHBoxLayout()
        self.prev_btn = PushButton("上一页"); self.prev_btn.setFixedWidth(90)
        self.prev_btn.clicked.connect(self.prev_page)
        pr.addWidget(self.prev_btn)
        self.page_label = CaptionLabel("")
        pr.addWidget(self.page_label)
        self.next_btn = PushButton("下一页"); self.next_btn.setFixedWidth(90)
        self.next_btn.clicked.connect(self.next_page)
        pr.addWidget(self.next_btn)
        pr.addStretch()
        self.sel_label = BodyLabel("已选 0 题")
        pr.addWidget(self.sel_label)
        self.clear_sel_btn = PushButton("清空选择"); self.clear_sel_btn.setFixedWidth(100)
        self.clear_sel_btn.clicked.connect(self.clear_selected)
        pr.addWidget(self.clear_sel_btn)
        vl.addLayout(pr)
        # 确定取消
        br = QHBoxLayout(); br.addStretch()
        self.ok_btn = PrimaryPushButton("确定"); self.ok_btn.setFixedWidth(100)
        self.ok_btn.clicked.connect(self.accept)
        br.addWidget(self.ok_btn)
        self.cancel_btn = PushButton("取消"); self.cancel_btn.setFixedWidth(100)
        self.cancel_btn.clicked.connect(self.reject)
        br.addWidget(self.cancel_btn)
        vl.addLayout(br)
        self.load_page()

    def _params(self):
        p = {"type": self.qtype, "offset": self.offset, "limit": self.PAGE}
        if self.qtype == "obj":
            p["course"] = self.course
        else:
            p["opType"] = self.opType
        kw = self.kw_edit.text().strip()
        if kw: p["keyword"] = kw
        return p

    def load_page(self):
        net_task(self, "正在加载题目…",
                 lambda: self.net.call("browseQuestions", timeout=60, **self._params()),
                 on_ok=self._render_page,
                 on_err=lambda e: QMessageBox.critical(self, "错误", str(e)))

    def _render_page(self, r):
        try:
            if r.get("code") != 0:
                QMessageBox.warning(self, "提示", r.get("msg", "加载失败")); return
            items = r.get("items", [])
            self.total = r.get("total", 0)
            self.table.setRowCount(0)
            for it in items:
                row = self.table.rowCount(); self.table.insertRow(row)
                chk = CheckBox()
                qid = it.get("id")
                chk.setChecked(qid in self.selected)
                chk.stateChanged.connect(lambda state, i=qid, it=it: self.on_check(i, state, it))
                self.table.setCellWidget(row, 0, chk)
                if self.qtype == "obj":
                    self.table.setItem(row, 1, QTableWidgetItem(str(qid)))
                    stem = it.get("stem", "")
                    opts = it.get("options", [])
                    letters = ["A", "B", "C", "D"]
                    opt_text = ""
                    for li, ot in enumerate(opts[:4]):
                        if ot: opt_text += f"\n{letters[li]}. {ot}"
                    full = stem + opt_text
                    item2 = QTableWidgetItem(full[:200] + ("…" if len(full) > 200 else ""))
                    item2.setData(Qt.UserRole, full)
                    self.table.setItem(row, 2, item2)
                    self.table.setItem(row, 3, QTableWidgetItem(it.get("answer", "")))
                else:
                    self.table.setItem(row, 1, QTableWidgetItem(str(qid)))
                    title = (it.get("title", "") or "").strip()
                    desc = (it.get("desc", "") or "").strip()
                    content = format_operate_stem(((title + "\n") if title else "") + desc)
                    item2 = QTableWidgetItem(content[:200] + ("…" if len(content) > 200 else ""))
                    item2.setData(Qt.UserRole, content)
                    self.table.setItem(row, 2, item2)
            # 分页状态
            cur_start = self.offset + 1
            cur_end = min(self.offset + self.PAGE, self.total)
            self.page_label.setText(f"{cur_start}-{cur_end} / 共{self.total}题")
            self.prev_btn.setEnabled(self.offset > 0)
            self.next_btn.setEnabled(self.offset + self.PAGE < self.total)
            self.sel_label.setText(f"已选 {len(self.selected)} 题")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def on_check(self, qid, state, it):
        if state == Qt.Checked.value or state == 2:
            if self.qtype == "obj":
                self.selected[qid] = it.get("stem", "")[:50]
            else:
                self.selected[qid] = (it.get("title", "") or it.get("desc", ""))[:50]
        else:
            self.selected.pop(qid, None)
        self.sel_label.setText(f"已选 {len(self.selected)} 题")

    def show_detail(self, index):
        row = index.row()
        item = self.table.item(row, 2)
        if not item: return
        full = item.data(Qt.UserRole) or item.text()
        dlg = QDialog(self); dlg.setWindowTitle("题目详情"); dlg.resize(700, 500)
        vl = QVBoxLayout(dlg)
        te = QTextEdit(); te.setReadOnly(True); te.setPlainText(full)
        te.setFont(QFont("Microsoft YaHei UI", 11))
        vl.addWidget(te)
        btn = PrimaryPushButton("关闭"); btn.setFixedWidth(100); btn.clicked.connect(dlg.accept)
        hb = QHBoxLayout(); hb.addStretch(); hb.addWidget(btn); vl.addLayout(hb)
        dlg.exec()

    def search(self):
        self.offset = 0; self.load_page()

    def clear_search(self):
        self.kw_edit.clear(); self.offset = 0; self.load_page()

    def prev_page(self):
        self.offset = max(0, self.offset - self.PAGE); self.load_page()

    def next_page(self):
        if self.offset + self.PAGE < self.total:
            self.offset += self.PAGE; self.load_page()

    def clear_selected(self):
        self.selected.clear()
        for row in range(self.table.rowCount()):
            chk = self.table.cellWidget(row, 0)
            if chk: chk.setChecked(False)
        self.sel_label.setText("已选 0 题")


class HomeworkPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("布置作业（自由组卷）"))

        form = QHBoxLayout()
        form.addWidget(BodyLabel("作业标题"))
        self.title_edit = LineEdit()
        self.title_edit.setPlaceholderText("请输入作业标题")
        form.addWidget(self.title_edit, 2)
        form.addWidget(BodyLabel("类型"))
        self.kind_combo = ComboBox()
        self.kind_combo.addItems(["平时作业", "正式考试"])
        form.addWidget(self.kind_combo)
        form.addWidget(BodyLabel("时长(分)"))
        self.dur_spin = SpinBox()
        self.dur_spin.setRange(0, 180)
        self.dur_spin.setValue(10)
        form.addWidget(self.dur_spin)
        form.addWidget(BodyLabel("客观每题分"))
        self.score_spin = SpinBox()
        self.score_spin.setRange(1, 10)
        self.score_spin.setValue(3)
        form.addWidget(self.score_spin)
        self.show_result_chk = CheckBox("展示成绩给学生")
        self.show_result_chk.setChecked(True)
        form.addWidget(self.show_result_chk)
        layout.addLayout(form)

        # 考试时间窗：仅在规定时间可参加（正式考试生效），留空=随时可参加
        tw = QHBoxLayout()
        tw.addWidget(BodyLabel("考试时间窗:"))
        self.window_chk = CheckBox("限定时间（考试不可重复参加）")
        self.window_chk.setChecked(False)
        tw.addWidget(self.window_chk)
        self.start_dt = QDateTimeEdit()
        self.start_dt.setCalendarPopup(True)
        self.start_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.start_dt.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self.start_dt.setEnabled(False)
        tw.addWidget(self.start_dt)
        tw.addWidget(BodyLabel("至"))
        self.end_dt = QDateTimeEdit()
        self.end_dt.setCalendarPopup(True)
        self.end_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.end_dt.setDateTime(QDateTime.currentDateTime().addSecs(7200))
        self.end_dt.setEnabled(False)
        tw.addWidget(self.end_dt)
        tw.addStretch()
        layout.addLayout(tw)
        self.window_chk.stateChanged.connect(
            lambda chk: (self.start_dt.setEnabled(bool(chk)), self.end_dt.setEnabled(bool(chk))))

        # 发布班级选择行
        cls_row = QHBoxLayout()
        cls_row.addWidget(BodyLabel("发布班级:"))
        self.cls_btn = PushButton("选择班级")
        self.cls_btn.setFixedWidth(280)
        self.cls_btn.clicked.connect(self.pick_classes)
        cls_row.addWidget(self.cls_btn)
        cls_row.addStretch()
        layout.addLayout(cls_row)
        self.classes = []
        self.selected_class_ids = []
        self._load_classes()

        layout.addWidget(CaptionLabel("勾选需要的模块并填写题量，可混合选择题与操作题（手动选题优先于随机抽题）。"))

        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["选", "题型 / 模块", "随机抽几题", "手动选题"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(3, 120)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self.picked = {}   # row -> {"qids":[...], "names":[...]}

        self.create_btn = PrimaryPushButton("生成并发布")
        self.create_btn.setFixedWidth(180)
        self.create_btn.clicked.connect(self.create)
        layout.addWidget(self.create_btn)
        self._load_modules()

    def _load_classes(self):
        net_task(self, "正在加载班级…",
                 lambda: self.net.call("listClasses", timeout=30),
                 on_ok=self._render_classes,
                 on_err=lambda e: InfoBar.error("班级加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_classes(self, r):
        try:
            self.classes = r.get("classes", [])
            if self.classes:
                self.selected_class_ids = [self.classes[0]["id"]]
                self.cls_btn.setText("已选: " + self.classes[0]["name"])
        except Exception as e:
            InfoBar.error("班级加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def pick_classes(self):
        if not self.classes:
            QMessageBox.warning(self, "提示", "暂无班级，请先在学生管理中添加"); return
        dlg = QDialog(self); dlg.setWindowTitle("选择发布班级"); dlg.resize(350, 400)
        vl = QVBoxLayout(dlg)
        from PySide6.QtWidgets import QListWidgetItem as QLWI
        lw = QListWidget()
        lw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for cl in self.classes:
            it = QListWidgetItem(cl["name"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if cl["id"] in self.selected_class_ids else Qt.Unchecked)
            it.setData(Qt.UserRole, cl["id"])
            lw.addItem(it)
        vl.addWidget(lw)
        br = QHBoxLayout(); br.addStretch()
        ok = PrimaryPushButton("确定"); ok.setFixedWidth(90)
        ok.clicked.connect(dlg.accept)
        cancel = PushButton("取消"); cancel.setFixedWidth(90)
        cancel.clicked.connect(dlg.reject)
        br.addWidget(ok); br.addWidget(cancel); vl.addLayout(br)
        if dlg.exec() == QDialog.Accepted:
            ids, names = [], []
            for i in range(lw.count()):
                it = lw.item(i)
                if it.checkState() == Qt.Checked:
                    ids.append(it.data(Qt.UserRole)); names.append(it.text())
            if not ids:
                QMessageBox.warning(self, "提示", "请至少选择一个班级"); return
            self.selected_class_ids = ids
            self.cls_btn.setText(f"已选 {len(ids)} 个班: " + "、".join(names[:3]) + ("…" if len(names) > 3 else ""))

    def _load_modules(self):
        self.table.setRowCount(0)
        net_task(self, "正在加载模块…",
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
                self.table.setCellWidget(row, 0, CheckBox())
                self.table.setItem(row, 1, QTableWidgetItem("选择题·" + c.get("name", "")))
                sp = SpinBox(); sp.setRange(0, 100); sp.setValue(5)
                self.table.setCellWidget(row, 2, sp)
                pick_btn = PushButton("手动选题")
                pick_btn.clicked.connect(lambda checked, r=row, qt="obj", co=c.get("course"), nm=c.get("name",""): self.pick_questions(r, qt, co, "选择题·"+nm))
                self.table.setCellWidget(row, 3, pick_btn)
                self.table.item(row, 1).setData(Qt.UserRole, {"type": "obj", "course": c.get("course")})
            for t in tr.get("types", []):
                if t.get("key") == "all": continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setCellWidget(row, 0, CheckBox())
                self.table.setItem(row, 1, QTableWidgetItem(f"操作题·{t.get('name','')} ({t.get('count',0)}题)"))
                sp = SpinBox(); sp.setRange(0, 50); sp.setValue(2)
                self.table.setCellWidget(row, 2, sp)
                pick_btn2 = PushButton("手动选题")
                pick_btn2.clicked.connect(lambda checked, r=row, qt="operate", co=t.get("key"), nm=t.get("name",""): self.pick_questions(r, qt, co, "操作题·"+nm))
                self.table.setCellWidget(row, 3, pick_btn2)
                self.table.item(row, 1).setData(Qt.UserRole, {"type": "operate", "opType": t.get("key")})
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def pick_questions(self, row, qtype, course_or_op, module_name):
        dlg = QuestionPickerDialog(self.net, qtype, course_or_op, module_name, self)
        if dlg.exec() == QDialog.Accepted:
            qids = list(dlg.selected.keys())
            self.picked[row] = {"qids": qids, "names": list(dlg.selected.values())}
            btn = self.table.cellWidget(row, 3)
            if btn: btn.setText(f"已选{len(qids)}题")
            # 手动选了题就勾选该行，随机抽题数清零
            chk = self.table.cellWidget(row, 0)
            if chk and qids: chk.setChecked(True)
            sp = self.table.cellWidget(row, 2)
            if sp and qids: sp.setValue(0)

    def create(self):
        parts = []
        for row in range(self.table.rowCount()):
            chk = self.table.cellWidget(row, 0)
            if not chk or not chk.isChecked(): continue
            sp = self.table.cellWidget(row, 2)
            cnt = sp.value() if sp else 0
            tag = self.table.item(row, 1).data(Qt.UserRole)
            picked = self.picked.get(row)
            has_picked = bool(picked and picked.get("qids"))
            # 既没有随机抽题数，也没有手动选题，才跳过
            if cnt <= 0 and not has_picked: continue
            part = {"type": tag["type"], "count": cnt}
            if tag["type"] == "operate":
                part["opType"] = tag["opType"]; part["score"] = 20.0
            else:
                part["course"] = tag["course"]; part["score"] = float(self.score_spin.value())
            if has_picked:
                part["qids"] = picked["qids"]
                part["count"] = 0
            parts.append(part)
        if not parts:
            QMessageBox.warning(self, "提示", "请至少勾选一个模块并填写题量或手动选题")
            return
        if not self.selected_class_ids:
            QMessageBox.warning(self, "提示", "请先选择发布班级"); return
        title = self.title_edit.text().strip() or "未命名作业"
        kind = "exam" if self.kind_combo.currentIndex() == 1 else "homework"
        start_at = end_at = ""
        if self.window_chk.isChecked() and kind == "exam":
            start_at = self.start_dt.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            end_at = self.end_dt.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            if start_at >= end_at:
                QMessageBox.warning(self, "提示", "结束时间必须晚于开始时间"); return
        cls_ids = list(self.selected_class_ids)

        def _work():
            total_picked = 0
            for cid in cls_ids:
                r = self.net.call("createHomework", title=title, classId=cid, kind=kind,
                                   parts=parts, durationSec=self.dur_spin.value() * 60,
                                   showResult=1 if self.show_result_chk.isChecked() else 0)
                if r.get("code") == 0:
                    self.net.call("publish", homeworkId=r.get("homeworkId"),
                                  startAt=start_at, endAt=end_at)
                    total_picked += r.get("picked", 0)
                else:
                    raise RuntimeError(r.get("msg", "发布失败"))
            return total_picked

        def _ok(total_picked):
            QMessageBox.information(self, "成功",
                f"已向 {len(cls_ids)} 个班级发布，每份 {total_picked} 题！")
            self.title_edit.clear()

        net_task(self, "正在发布练习/考试…", _work, on_ok=_ok)

# ==================== 试卷管理 ====================
class MonitorLitePanel(QWidget):
    """发布页内嵌实时监控：学生状态+答题进度整合为单列表，无账号操作日志。
    列表项显示 姓名(账号) 状态 已答x/共y 错z；点击后下方显示题目方框（与学生端一致）。"""
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self._stu_data = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(8, 8, 8, 8)
        vl.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(StrongBodyLabel("实时监控"))
        top.addStretch()
        ref = PushButton("刷新")
        ref.setFixedWidth(72)
        ref.clicked.connect(self.refresh)
        top.addWidget(ref)
        vl.addLayout(top)
        self.stu_list = QListWidget()
        self.stu_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stu_list.currentRowChanged.connect(self._on_selected)
        vl.addWidget(self.stu_list, 2)
        grp = QGroupBox("答题进度（绿=答对 红=错 白=未答 蓝=正在做）")
        gv = QVBoxLayout(grp)
        self.qscroll = QScrollArea()
        self.qscroll.setWidgetResizable(True)
        self.qscroll.setFrameShape(QFrame.NoFrame)
        self.qbody = QWidget()
        self.qv = QVBoxLayout(self.qbody)
        self.qv.setContentsMargins(4, 2, 4, 2)
        self.qv.setSpacing(6)
        self.qscroll.setWidget(self.qbody)
        gv.addWidget(self.qscroll)
        vl.addWidget(grp, 3)
        self._timer.start(3000)

    def reload(self):
        self.refresh()

    def _on_selected(self, row):
        item = self.stu_list.item(row)
        if item:
            self._draw_questions(item.data(Qt.UserRole))

    def _clear_questions(self):
        while self.qv.count():
            it = self.qv.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def refresh(self):
        # 轮询静默拉取（不弹等待框、不阻塞界面），结果回主线程刷新。
        # 上一轮还在路上就跳过本轮：慢网络下 3 秒一轮会把线程堆起来。
        if getattr(self, "_polling", False):
            return
        self._polling = True

        def _ok(r):
            self._polling = False
            self._render_monitor(r)

        def _err(_e):
            self._polling = False

        net_task(self, "正在刷新监控…",
                 lambda: self.net.call("monitorList", timeout=15),
                 on_ok=_ok, on_err=_err, show_wait=False)

    def _render_monitor(self, r):
        try:
            if r.get("code") != 0:
                return
            students = r.get("students", [])
            self._stu_data = {s["username"]: s for s in students}
            cur_user = None
            item = self.stu_list.currentItem()
            if item:
                cur_user = item.data(Qt.UserRole)
            self.stu_list.blockSignals(True)
            self.stu_list.clear()
            for s in students:
                if s.get("submitted"):
                    sttxt = "已交卷"
                elif s.get("online"):
                    sttxt = "做题中"
                else:
                    sttxt = "离线"
                dot = "●" if s.get("online") else "○"
                qids = s.get("qids") or []
                answered = s.get("answered") or []
                wrong = s.get("wrong") or []
                prog = f"已答{len(answered)}/{len(qids)} 错{len(wrong)}" if qids else "未进入答题"
                it = QListWidgetItem(f"{s.get('name') or s.get('username')}（{s['username']}） {dot}{sttxt}  {prog}")
                it.setData(Qt.UserRole, s["username"])
                self.stu_list.addItem(it)
            if cur_user and cur_user in self._stu_data:
                for i in range(self.stu_list.count()):
                    if self.stu_list.item(i).data(Qt.UserRole) == cur_user:
                        self.stu_list.setCurrentRow(i)
                        break
            self.stu_list.blockSignals(False)
            if cur_user and cur_user in self._stu_data:
                self._draw_questions(cur_user)
            else:
                self._clear_questions()
        except Exception as e:
            print("monitor lite refresh err:", e)

    def _draw_questions(self, username):
        st = self._stu_data.get(username) or {}
        self._clear_questions()
        qids = st.get("qids") or []
        if not qids:
            self.qv.addWidget(BodyLabel("该学生尚未进入答题"))
            self.qv.addStretch()
            return
        answered = set(st.get("answered") or [])
        wrong = set(st.get("wrong") or [])
        cur = st.get("current")
        groups = []
        for item in qids:
            if isinstance(item, dict):
                qid = str(item.get("qid") or "")
                mod = item.get("module") or "题目"
            else:
                qid = str(item)
                mod = "题目"
            if not qid:
                continue
            if not groups or groups[-1][0] != mod:
                groups.append([mod, []])
            groups[-1][1].append(qid)
        total = sum(len(g[1]) for g in groups)
        info = CaptionLabel(f"共 {total} 题 · 已答 {len(answered)} · 错 {len(wrong)}")
        info.setStyleSheet("color:#4A5568; font-weight:bold;")
        self.qv.addWidget(info)
        for mod, ql in groups:
            lbl = CaptionLabel(mod)
            lbl.setStyleSheet("color:#4F86F8; font-weight:bold;")
            self.qv.addWidget(lbl)
            grid = QGridLayout()
            grid.setSpacing(5)
            for i, qid in enumerate(ql):
                grid.addWidget(self._mk_box(i + 1, qid, answered, wrong, cur), i // 8, i % 8)
            self.qv.addLayout(grid)
        self.qv.addStretch()

    def _mk_box(self, no, qid, answered, wrong, cur):
        box = QFrame()
        box.setFixedSize(34, 28)
        lab = QLabel(str(no), box)
        lab.setAlignment(Qt.AlignCenter)
        lab.setGeometry(0, 0, 34, 28)
        lab.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
        if qid == cur:
            box.setStyleSheet("QFrame{background:#4F86F8; border-radius:4px; border:2px solid #2B5FD9;}")
            lab.setStyleSheet("color:white;")
        elif qid in wrong:
            box.setStyleSheet("QFrame{background:#FF5252; border-radius:4px;}")
            lab.setStyleSheet("color:white;")
        elif qid in answered:
            box.setStyleSheet("QFrame{background:#34A853; border-radius:4px;}")
            lab.setStyleSheet("color:white;")
        else:
            box.setStyleSheet("QFrame{background:#FFFFFF; border:1px solid #CBD5E1; border-radius:4px;}")
            lab.setStyleSheet("color:#1F2937;")
        box.setToolTip(f"第 {no} 题")
        return box


class PapersPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self.papers = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(30, 20, 30, 20)
        outer.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(SubtitleLabel("试卷管理（勾选可批量发布/结束/删除，双击预览）"))
        # 全选行
        sel_row = QHBoxLayout()
        self.all_chk = CheckBox("全选")
        self.all_chk.stateChanged.connect(self.toggle_all)
        sel_row.addWidget(self.all_chk)
        self.count_label = CaptionLabel("")
        sel_row.addWidget(self.count_label)
        sel_row.addStretch()
        left.addLayout(sel_row)
        # 试卷列表（带复选框）
        self.list = QListWidget()
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setFont(QFont("Microsoft YaHei UI", 10.5))
        self.list.itemDoubleClicked.connect(self.preview_current)
        left.addWidget(self.list, 1)
        # 按钮行
        btn_row = QHBoxLayout()
        self.prev_btn = PrimaryPushButton("预览试卷"); self.prev_btn.setFixedWidth(110)
        self.prev_btn.clicked.connect(self.preview_current)
        btn_row.addWidget(self.prev_btn)
        self.pub_btn = PushButton("批量发布"); self.pub_btn.setFixedWidth(110)
        self.pub_btn.clicked.connect(self.batch_publish)
        btn_row.addWidget(self.pub_btn)
        self.close_btn = PushButton("批量结束"); self.close_btn.setFixedWidth(110)
        self.close_btn.clicked.connect(self.batch_close)
        btn_row.addWidget(self.close_btn)
        self.del_btn = PushButton("批量删除"); self.del_btn.setFixedWidth(110)
        self.del_btn.clicked.connect(self.batch_delete)
        btn_row.addWidget(self.del_btn)
        self.ref_btn = PushButton("刷新"); self.ref_btn.setFixedWidth(90)
        self.ref_btn.clicked.connect(self.reload)
        btn_row.addWidget(self.ref_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        outer.addLayout(left, 3)
        # 右侧内嵌实时监控（无日志，学生状态+答题进度单列表）
        right = MonitorLitePanel(net)
        outer.addWidget(right, 2)
        self.reload()

    def reload(self):
        self.list.clear()
        self.papers = []
        self.all_chk.setChecked(False)
        net_task(self, "正在加载练习/试卷…",
                 lambda: self.net.call("tchHomeworks", timeout=30),
                 on_ok=self._render_papers,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_papers(self, r):
        try:
            for p in r.get("homeworks", []):
                kn = "考试" if p.get("kind") == "exam" else "作业"
                qt = "操作题" if p.get("qtype") == "operate" else "客观题"
                status_map = {"draft": "草稿", "published": "已发布", "closed": "已结束"}
                st = status_map.get(p.get("status", ""), p.get("status", ""))
                item = QListWidgetItem(f"[{st}] {p.get('title','')}  ({kn}/{qt})")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.list.addItem(item)
                self.papers.append(p)
            self.count_label.setText(f"共 {len(self.papers)} 份试卷")
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def _checked_papers(self):
        sel = []
        for i in range(self.list.count()):
            if self.list.item(i).checkState() == Qt.Checked:
                sel.append(self.papers[i])
        return sel

    def toggle_all(self):
        state = Qt.Checked if self.all_chk.isChecked() else Qt.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def preview_current(self, *args):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.papers):
            # 双击时从 item 取行
            if args and hasattr(args[0], "listWidget"):
                row = self.list.row(args[0])
        if row < 0 or row >= len(self.papers):
            QMessageBox.warning(self, "提示", "请先选择一份试卷")
            return
        p = self.papers[row]

        def _ok(r):
            try:
                qs = r.get("questions", [])
                dlg = QDialog(self)
                dlg.setWindowTitle(f"试卷预览 - {p.get('title','')}")
                dlg.resize(900, 600)
                vl = QVBoxLayout(dlg)
                table = TableWidget()
                table.setEditTriggers(QAbstractItemView.NoEditTriggers)
                table.setColumnCount(4)
                table.setHorizontalHeaderLabels(["#", "题型", "题目内容（双击查看完整）", "答案"])
                table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
                table.verticalHeader().setVisible(False)
                table.setWordWrap(True)
                full_map = {}
                for i, q in enumerate(qs, 1):
                    is_op = q.get("refType") == "operate"
                    raw = q.get("stem", "") or ""
                    ans = "(操作题·自动判分)" if is_op else (q.get("answer", "") or "")
                    # 完整文本
                    if is_op:
                        full = format_operate_stem(raw)
                    else:
                        lines = [raw]
                        for o in (q.get("options") or []):
                            if o.get("t"):
                                lines.append(f"{o.get('k')}. {o.get('t')}")
                        lines.append(f"【答案】{q.get('answer','')}")
                        full = "\n".join(lines)
                    full_map[i-1] = full
                    # 摘要：保留换行，操作题最多显示前600字
                    brief = full if len(full) <= 600 else full[:600] + " …（双击看完整）"
                    table.insertRow(table.rowCount())
                    table.setItem(i-1, 0, QTableWidgetItem(str(i)))
                    table.setItem(i-1, 1, QTableWidgetItem("操作题" if is_op else "选择题"))
                    item2 = QTableWidgetItem(brief)
                    item2.setData(Qt.UserRole, full)
                    table.setItem(i-1, 2, item2)
                    table.setItem(i-1, 3, QTableWidgetItem(ans))
                    table.resizeRowToContents(i-1)
                def show_paper_detail(index):
                    row = index.row()
                    full = full_map.get(row, "")
                    d2 = QDialog(dlg); d2.setWindowTitle(f"第{row+1}题 完整内容"); d2.resize(760, 560)
                    v2 = QVBoxLayout(d2)
                    te = QTextEdit(); te.setReadOnly(True); te.setPlainText(full)
                    te.setFont(QFont("Microsoft YaHei UI", 11))
                    v2.addWidget(te)
                    hb = QHBoxLayout(); hb.addStretch()
                    cb = PrimaryPushButton("关闭"); cb.setFixedWidth(100); cb.clicked.connect(d2.accept)
                    hb.addWidget(cb); v2.addLayout(hb)
                    d2.exec()
                table.doubleClicked.connect(show_paper_detail)
                hint = CaptionLabel("提示：双击任意题目行可查看完整题干、选项与答案；操作题为完整操作要求。")
                vl.addWidget(hint)
                vl.addWidget(table)
                dlg.resize(1000, 680)
                dlg.exec()
            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

        net_task(self, "正在加载试卷内容…",
                 lambda: self.net.call("previewPaper", timeout=60, homeworkId=p["id"]),
                 on_ok=_ok)

    def _batch_net(self, text, pairs, done_msg):
        """批量操作：pairs=[(cmd, homeworkId), ...]；后台执行+等待框"""
        def _work():
            ok = 0
            for cmd, hid in pairs:
                try:
                    self.net.call(cmd, homeworkId=hid); ok += 1
                except Exception:
                    pass
            return ok
        def _ok(ok):
            QMessageBox.information(self, "成功", done_msg % ok)
            self.reload()
        net_task(self, text, _work, on_ok=_ok)

    def batch_publish(self):
        sel = self._checked_papers()
        if not sel:
            QMessageBox.warning(self, "提示", "请先勾选要发布的试卷"); return
        drafts = [p for p in sel if p.get("status") == "draft"]
        if not drafts:
            QMessageBox.information(self, "提示", "勾选的试卷都不是草稿状态"); return
        self._batch_net("正在发布试卷…", [("publish", p["id"]) for p in drafts], "已发布 %d 份试卷")

    def batch_close(self):
        sel = self._checked_papers()
        if not sel:
            QMessageBox.warning(self, "提示", "请先勾选要结束的试卷"); return
        active = [p for p in sel if p.get("status") == "published"]
        if not active:
            QMessageBox.information(self, "提示", "勾选的试卷都不是已发布状态"); return
        if QMessageBox.question(self, "确认", f"确定结束 {len(active)} 份已发布试卷？") != QMessageBox.Yes:
            return
        self._batch_net("正在结束试卷…", [("closeHomework", p["id"]) for p in active], "已结束 %d 份试卷")

    def batch_delete(self):
        sel = self._checked_papers()
        if not sel:
            QMessageBox.warning(self, "提示", "请先勾选要删除的试卷"); return
        if QMessageBox.question(self, "确认", f"确定删除选中的 {len(sel)} 份试卷？此操作不可恢复！") != QMessageBox.Yes:
            return
        self._batch_net("正在删除试卷…", [("deletePaper", p["id"]) for p in sel], "已删除 %d 份试卷")
        self.reload()


class ResultsPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("成绩查询"))
        top = QHBoxLayout()
        self.hw_combo = ComboBox()
        self.hw_combo.setMinimumWidth(400)
        top.addWidget(self.hw_combo)
        self.show_btn = PrimaryPushButton("查看成绩")
        self.show_btn.setFixedWidth(100)
        self.show_btn.clicked.connect(self.show_results)
        top.addWidget(self.show_btn)
        top.addStretch()
        layout.addLayout(top)
        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["学生", "账号", "得分", "状态", "交卷时间", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self._load_homeworks()

    def _load_homeworks(self):
        self.hw_combo.clear()
        self.hws = []
        net_task(self, "正在加载练习列表…",
                 lambda: self.net.call("tchHomeworks", timeout=30),
                 on_ok=self._render_hws,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_hws(self, r):
        try:
            for h in r.get("homeworks", []):
                self.hw_combo.addItem(f"{h.get('title','')}  [{h.get('status','')}]")
                self.hws.append(h)
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def show_results(self):
        idx = self.hw_combo.currentIndex()
        if idx < 0: return
        h = self.hws[idx]
        self.table.setRowCount(0)
        net_task(self, "正在加载成绩…",
                 lambda: self.net.call("classResults", homeworkId=h["id"], timeout=60),
                 on_ok=self._render_results)

    def _render_results(self, r):
        try:
            for x in r.get("results", []):
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(x.get("name", "")))
                self.table.setItem(row, 1, QTableWidgetItem(x.get("username", "")))
                self.table.setItem(row, 2, QTableWidgetItem("未交" if x.get("score") is None else str(x.get("score"))))
                self.table.setItem(row, 3, QTableWidgetItem(x.get("status", "")))
                self.table.setItem(row, 4, QTableWidgetItem(x.get("submittedAt", "") or ""))
                btn = PushButton("详情")
                btn.setFixedWidth(60)
                btn.clicked.connect(lambda checked, rid=x.get("recordId"): self.show_detail(rid, x.get("name","")))
                self.table.setCellWidget(row, 5, btn)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def show_detail(self, rec_id, name):
        if not rec_id: return
        net_task(self, "正在加载答题详情…",
                 lambda: self.net.call("recordDetail", recordId=rec_id, timeout=60),
                 on_ok=lambda r: self._show_detail_ok(r, rec_id, name))

    def _show_detail_ok(self, r, rec_id, name):
        try:
            detail = r.get("detail", [])

            def _refresh():
                """学生后台判分完成后，教师端重拉同一份明细。"""
                rr = self.net.call("recordDetail", recordId=rec_id, timeout=60)
                det = rr.get("detail", [])
                sc = round(sum(float(d.get("score") or 0) for d in det), 1) if det else None
                tot = round(sum(float(d.get("fullScore") or 0) for d in det), 1) if det else None
                return {"score": sc, "total": tot, "detail": det}

            GradeDetailDialog(score=None, total=None, detail=detail,
                              title=f"{name} 的答题详情", parent=self, refresh_cb=_refresh).exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

# ==================== 成绩分析 ====================
class AnalysisPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("成绩分析"))
        top = QHBoxLayout()
        self.hw_combo = ComboBox()
        self.hw_combo.setMinimumWidth(400)
        top.addWidget(self.hw_combo)
        self.analyze_btn = PrimaryPushButton("分析")
        self.analyze_btn.setFixedWidth(100)
        self.analyze_btn.clicked.connect(self.analyze)
        top.addWidget(self.analyze_btn)
        top.addStretch()
        layout.addLayout(top)
        self.info = BodyLabel("")
        layout.addWidget(self.info)
        self.bars_widget = QWidget()
        self.bars_layout = QVBoxLayout(self.bars_widget)
        self.bars_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.bars_widget)
        layout.addWidget(scroll, 1)
        self._load_homeworks()

    def _load_homeworks(self):
        self.hw_combo.clear()
        self.hws = []
        net_task(self, "正在加载练习列表…",
                 lambda: self.net.call("tchHomeworks", timeout=30),
                 on_ok=self._render_hws,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_hws(self, r):
        try:
            for h in r.get("homeworks", []):
                self.hw_combo.addItem(h.get("title", ""))
                self.hws.append(h)
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def analyze(self):
        idx = self.hw_combo.currentIndex()
        if idx < 0: return
        h = self.hws[idx]
        net_task(self, "正在分析成绩…",
                 lambda: self.net.call("analysis", homeworkId=h["id"], timeout=60),
                 on_ok=self._render_analysis)

    def _render_analysis(self, r):
        try:
            self.info.setText(f"参加人数: {r.get('n',0)}    平均分: {r.get('avg',0)}    最高: {r.get('max',0)}    最低: {r.get('min',0)}")
            while self.bars_layout.count():
                item = self.bars_layout.takeAt(0)
                w = item.widget()
                if w: w.deleteLater()
            for i, q in enumerate(r.get("perQuestion", []), 1):
                rate = q.get("rate", 0)
                row = QHBoxLayout()
                row.addWidget(CaptionLabel(f"题{i}"))
                bar = ProgressBar()
                bar.setRange(0, 100)
                bar.setValue(int(rate))
                bar.setCustomColor(QColor('#2ECC71' if rate>=60 else '#FF5252'))
                bar.setTextVisible(False)
                row.addWidget(bar, 1)
                row.addWidget(CaptionLabel(f"正确率 {rate}%"))
                w = QWidget()
                w.setLayout(row)
                self.bars_layout.addWidget(w)
            self.bars_layout.addStretch()
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

# ==================== 学生管理 ====================
class StudentsPage(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("学生管理"))

        # 筛选行
        filter_row = QHBoxLayout()
        filter_row.addWidget(BodyLabel("班级筛选:"))
        self.filter_combo = ComboBox()
        self.filter_combo.setFixedWidth(180)
        self.filter_combo.currentIndexChanged.connect(self.reload)
        filter_row.addWidget(self.filter_combo)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["选", "账号", "姓名", "班级"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 48)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        hh.sectionClicked.connect(self._header_click)
        # 表头“选”列的全选框：有可见勾选状态，支持一键全选/取消全选
        self._head_cb = CheckBox(self.table)
        self._head_cb.setFocusPolicy(Qt.NoFocus)
        self._head_cb.stateChanged.connect(self._head_cb_toggled)
        hh.sectionResized.connect(lambda *_: self._pos_head_cb())
        hh.geometriesChanged.connect(self._pos_head_cb)
        QTimer.singleShot(0, self._pos_head_cb)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.edit_btn = PrimaryPushButton("编辑选中")
        self.edit_btn.setFixedWidth(120)
        self.edit_btn.clicked.connect(self.edit_student)
        btn_row.addWidget(self.edit_btn)
        self.del_btn = PushButton("删除选中")
        self.del_btn.setFixedWidth(120)
        self.del_btn.setStyleSheet("background:#FF5252; color:white;")
        self.del_btn.clicked.connect(self.delete_student)
        btn_row.addWidget(self.del_btn)
        self.cls_btn = PushButton("班级管理")
        self.cls_btn.setFixedWidth(120)
        self.cls_btn.clicked.connect(self.manage_classes)
        btn_row.addWidget(self.cls_btn)
        self.import_btn = PushButton("导入账号(xlsx)")
        self.import_btn.setFixedWidth(150)
        self.import_btn.clicked.connect(self.import_students)
        btn_row.addWidget(self.import_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 添加学生
        add_group = QGroupBox("添加学生")
        add_layout = QHBoxLayout(add_group)
        add_layout.addWidget(BodyLabel("账号"))
        self.add_user = LineEdit(); self.add_user.setFixedWidth(120)
        add_layout.addWidget(self.add_user)
        add_layout.addWidget(BodyLabel("密码"))
        self.add_pwd = LineEdit(); self.add_pwd.setFixedWidth(120)
        add_layout.addWidget(self.add_pwd)
        add_layout.addWidget(BodyLabel("姓名"))
        self.add_name = LineEdit(); self.add_name.setFixedWidth(120)
        add_layout.addWidget(self.add_name)
        add_layout.addWidget(BodyLabel("班级"))
        self.add_cls = ComboBox(); self.add_cls.setFixedWidth(120)
        add_layout.addWidget(self.add_cls)
        self.add_btn = PushButton("添加")
        self.add_btn.setFixedWidth(100)
        self.add_btn.clicked.connect(self.add_student)
        add_layout.addWidget(self.add_btn)
        add_layout.addStretch()
        layout.addWidget(add_group)

        self._load_classes()
        self.reload()

    def _load_classes(self):
        net_task(self, "正在加载班级…",
                 lambda: self.net.call("listClasses", timeout=30),
                 on_ok=self._render_classes,
                 on_err=lambda e: InfoBar.error("班级加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_classes(self, r):
        try:
            self.classes = r.get("classes", [])
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItem("全部班级")
            self.add_cls.clear()
            for c in self.classes:
                self.filter_combo.addItem(c.get("name", ""))
                self.add_cls.addItem(c.get("name", ""))
            if self.add_cls.count() > 0:
                self.add_cls.setCurrentIndex(0)
            self.filter_combo.blockSignals(False)
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def _pos_head_cb(self):
        """把表头全选框定位到第0列中间"""
        try:
            hh = self.table.horizontalHeader()
            sec = hh.sectionViewportPosition(0)
            w = hh.sectionSize(0)
            self._head_cb.setGeometry(sec + (w - 26) // 2, 0, 26, 26)
        except Exception:
            pass

    def _all_checked(self):
        n = self.table.rowCount()
        if n == 0:
            return False
        for row in range(n):
            chk = self.table.cellWidget(row, 0)
            if not chk or not chk.isChecked():
                return False
        return True

    def _header_click(self, col):
        if col != 0:
            return
        self._head_cb.setChecked(not self._all_checked())

    def _head_cb_toggled(self, state):
        checked = state != 0
        for row in range(self.table.rowCount()):
            chk = self.table.cellWidget(row, 0)
            if chk:
                chk.blockSignals(True)
                chk.setChecked(checked)
                chk.blockSignals(False)
        self._sync_head_cb()

    def _sync_head_cb(self):
        """行勾选变化时同步表头全选框：全选=勾选、部分=半选、无=未勾"""
        try:
            self._head_cb.blockSignals(True)
            n = self.table.rowCount()
            if n == 0:
                self._head_cb.setTristate(False)
                self._head_cb.setChecked(False)
                return
            checked_n = 0
            for row in range(n):
                chk = self.table.cellWidget(row, 0)
                if chk and chk.isChecked():
                    checked_n += 1
            if checked_n == 0:
                self._head_cb.setTristate(False)
                self._head_cb.setChecked(False)
            elif checked_n == n:
                self._head_cb.setTristate(False)
                self._head_cb.setChecked(True)
            else:
                self._head_cb.setTristate(True)
                self._head_cb.setCheckState(Qt.PartiallyChecked)
        finally:
            self._head_cb.blockSignals(False)

    def reload(self):
        self.table.setRowCount(0)
        net_task(self, "正在加载学生列表…",
                 lambda: self.net.call("listStudents", timeout=60),
                 on_ok=self._render_students,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_students(self, r):
        try:
            filter_cls = ""
            if self.filter_combo.currentIndex() > 0:
                filter_cls = self.filter_combo.currentText()
            for s in r.get("students", []):
                if filter_cls and s.get("class", "") != filter_cls:
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                chk = CheckBox()
                chk.stateChanged.connect(self._sync_head_cb)
                self.table.setCellWidget(row, 0, chk)
                self.table.setItem(row, 1, QTableWidgetItem(s.get("username", "")))
                self.table.setItem(row, 2, QTableWidgetItem(s.get("name", "")))
                self.table.setItem(row, 3, QTableWidgetItem(s.get("class", "")))
                self.table.item(row, 1).setData(Qt.UserRole, s)
            self._sync_head_cb()
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)

    def _current_student(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先在表格里点选一个学生")
            return None
        return self.table.item(row, 1).data(Qt.UserRole)

    def _checked_students(self):
        """所有勾选的学生（按表格行序）"""
        out = []
        for row in range(self.table.rowCount()):
            chk = self.table.cellWidget(row, 0)
            if chk and chk.isChecked():
                it = self.table.item(row, 1)
                if it:
                    out.append(it.data(Qt.UserRole))
        return out

    def _target_students(self):
        """有勾选则操作全部勾选行，否则退回当前行单个"""
        checked = self._checked_students()
        if checked:
            return checked
        cur = self._current_student()
        return [cur] if cur else []

    def edit_student(self):
        stus = self._target_students()
        if not stus:
            QMessageBox.warning(self, "提示", "请先在表格里点选或勾选学生")
            return
        multi = len(stus) > 1
        stu = stus[0]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑学生 - {stu.get('username','')}" + (f" 等{len(stus)}人" if multi else ""))
        dlg.setFixedWidth(400)
        form = QFormLayout(dlg)
        pwd_edit = LineEdit()
        form.addRow("新密码(留空不改)", pwd_edit)
        name_edit = LineEdit(stu.get("name", ""))
        form.addRow("姓名", name_edit)
        cls_combo = ComboBox()
        for c in self.classes:
            cls_combo.addItem(c.get("name", ""))
        for i, c in enumerate(self.classes):
            if c.get("name") == stu.get("class"):
                cls_combo.setCurrentIndex(i); break
        form.addRow("班级", cls_combo)
        if multi:
            form.addRow(CaptionLabel(f"将同批更新 {len(stus)} 名学生（姓名/班级/密码）"))
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        if dlg.exec() == QDialog.Accepted:
            pars = []
            for s in stus:
                par = {"userId": s["id"], "displayName": name_edit.text().strip()}
                if pwd_edit.text():
                    par["password"] = pwd_edit.text()
                if cls_combo.currentIndex() >= 0:
                    par["classId"] = self.classes[cls_combo.currentIndex()]["id"]
                pars.append(par)

            def _work():
                for par in pars:
                    self.net.call("updateStudent", **par)
                return len(pars)

            def _ok(n):
                QMessageBox.information(self, "提示", f"已更新 {n} 名学生" if n > 1 else "已保存")
                self.reload()

            net_task(self, "正在保存…", _work, on_ok=_ok)

    def manage_classes(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("班级管理")
        dlg.setFixedSize(400, 350)
        vl = QVBoxLayout(dlg)
        listw = QListWidget()
        vl.addWidget(listw, 1)
        row = QHBoxLayout()
        name_edit = LineEdit()
        row.addWidget(name_edit)
        add_btn = PushButton("新增")
        row.addWidget(add_btn)
        vl.addLayout(row)
        del_btn = PushButton("删除选中")
        vl.addWidget(del_btn)

        def refresh_cls():
            listw.clear()
            self._load_classes()
            for c in self.classes:
                listw.addItem(c.get("name", ""))
        refresh_cls()

        def add_cls():
            if not name_edit.text().strip(): return
            def _work():
                r = self.net.call("addClass", name=name_edit.text().strip())
                if r.get("code") != 0:
                    raise RuntimeError(r.get("msg", "新增失败"))
                return r.get("msg", "")
            def _ok(msg):
                QMessageBox.information(self, "提示", msg)
                name_edit.clear(); refresh_cls(); self.reload()
            net_task(self, "正在新增班级…", _work, on_ok=_ok)
        add_btn.clicked.connect(add_cls)

        def del_cls():
            row = listw.currentRow()
            if row < 0:
                QMessageBox.warning(self, "提示", "请先选一个班级"); return
            def _work():
                r = self.net.call("deleteClass", classId=self.classes[row]["id"])
                if r.get("code") != 0:
                    raise RuntimeError(r.get("msg", "删除失败"))
                return r.get("msg", "")
            def _ok(msg):
                QMessageBox.information(self, "提示", msg)
                refresh_cls(); self.reload()
            net_task(self, "正在删除班级…", _work, on_ok=_ok)
        del_btn.clicked.connect(del_cls)
        dlg.exec()

    def import_students(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择学生账号表", "", "Excel文件 (*.xlsx)")
        if not path: return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取文件失败: {e}")
            return
        payload = base64.b64encode(data).decode()

        def _work():
            r = self.net.call("importStudents", data=payload, timeout=300)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", "导入失败"))
            return r

        def _ok(r):
            msg = r.get("msg", "")
            nc = r.get("newClasses", [])
            if nc:
                msg += "\n新建班级：" + "、".join(nc)
            QMessageBox.information(self, "导入结果", msg)
            self._load_classes(); self.reload()

        net_task(self, "正在导入学生账号（请稍等，批量导入可能需要几十秒）…", _work, on_ok=_ok)

    def add_student(self):
        def _work():
            cid = 1
            if self.add_cls.currentIndex() >= 0:
                cid = self.classes[self.add_cls.currentIndex()]["id"]
            r = self.net.call("addStudent", username=self.add_user.text().strip(),
                               password=self.add_pwd.text(),
                               displayName=self.add_name.text().strip() or self.add_user.text().strip(),
                               classId=cid)
            if r.get("code") != 0:
                raise RuntimeError(r.get("msg", "添加失败"))
            return r.get("msg", "")

        def _ok(msg):
            QMessageBox.information(self, "提示", msg)
            self.add_user.clear(); self.add_pwd.clear(); self.add_name.clear(); self.reload()

        net_task(self, "正在添加学生…", _work, on_ok=_ok)

    def delete_student(self):
        """删除学生账号（支持勾选多删；连同答题记录、错题本），二次确认后执行"""
        stus = self._target_students()
        if not stus:
            QMessageBox.warning(self, "提示", "请先在表格里点选或勾选学生")
            return
        if len(stus) == 1:
            stu = stus[0]
            msg = (f"确定删除学生「{stu.get('name','')}」\n账号：{stu.get('username','')}\n\n"
                   "该学生的答题记录、错题本数据将一并删除，且无法恢复！")
        else:
            names = "、".join((s.get('name', '') or s.get('username', '')) for s in stus[:5])
            msg = (f"确定删除勾选的 {len(stus)} 名学生吗？\n{names}{'…' if len(stus) > 5 else ''}\n\n"
                   "他们的答题记录、错题本数据将一并删除，且无法恢复！")
        ret = QMessageBox.question(self, "确认删除", msg,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        ids = [stu["id"] for stu in stus]

        def _work():
            for uid in ids:
                self.net.call("deleteStudent", userId=uid)
            return len(ids)

        def _ok(n):
            QMessageBox.information(self, "提示", f"已删除 {n} 名学生")
            self.reload()

        net_task(self, "正在删除学生…", _work, on_ok=_ok)


# ==================== 主窗口 ====================
# ==================== 练习和考试监控 ====================
class MonitorPage(QWidget):
    """实时监控：学生正在做哪题、哪题没做、错题。
    右侧题目方框与学生端一致：绿=答对、红=错、白=未答、蓝=正在做。"""
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self._stu_data = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._build_ui()
        QTimer.singleShot(0, self.refresh)
        self._timer.start(3000)

    def _build_ui(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 12, 16, 12)
        vl.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("练习和考试监控"))
        top.addStretch()
        self.auto_chk = CheckBox("自动刷新（3秒）")
        self.auto_chk.setChecked(True)
        self.auto_chk.stateChanged.connect(self._on_auto)
        top.addWidget(self.auto_chk)
        self.ref_btn = PrimaryPushButton("立即刷新")
        self.ref_btn.setFixedWidth(100)
        self.ref_btn.clicked.connect(self.refresh)
        top.addWidget(self.ref_btn)
        vl.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(8)
        left = QGroupBox("学生状态")
        lv = QVBoxLayout(left)
        self.stu_list = QListWidget()
        self.stu_list.currentRowChanged.connect(self._on_stu_selected)
        lv.addWidget(self.stu_list)
        mid.addWidget(left, 2)

        right = QGroupBox("答题进度（绿=答对 红=错 白=未答 蓝=正在做）")
        rv = QVBoxLayout(right)
        self.qscroll = QScrollArea()
        self.qscroll.setWidgetResizable(True)
        self.qscroll.setFrameShape(QFrame.NoFrame)
        self.qbody = QWidget()
        self.qv = QVBoxLayout(self.qbody)
        self.qv.setContentsMargins(8, 6, 8, 6)
        self.qv.setSpacing(8)
        self.qscroll.setWidget(self.qbody)
        rv.addWidget(self.qscroll)
        mid.addWidget(right, 5)
        vl.addLayout(mid, 3)

        log_g = QGroupBox("账号操作日志（最近 200 条）")
        lg = QVBoxLayout(log_g)
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(5)
        self.log_table.setHorizontalHeaderLabels(["时间", "账号", "姓名", "操作", "详情"])
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        lg.addWidget(self.log_table)
        vl.addWidget(log_g, 2)

    def _on_auto(self):
        if self.auto_chk.isChecked():
            self._timer.start(3000)
        else:
            self._timer.stop()

    def reload(self):
        self.refresh()

    def _on_stu_selected(self, row):
        item = self.stu_list.item(row)
        if item:
            self._draw_questions(item.data(Qt.UserRole))

    def refresh(self):
        # 轮询静默拉取（不弹等待框、不阻塞界面），结果回主线程刷新。
        # 上一轮还在路上就跳过本轮：慢网络下 3 秒一轮会把线程堆起来。
        if getattr(self, "_polling", False):
            return
        self._polling = True

        def _ok(r):
            self._polling = False
            self._render_monitor(r)

        def _err(_e):
            self._polling = False

        net_task(self, "正在刷新监控…",
                 lambda: self.net.call("monitorList", timeout=15),
                 on_ok=_ok, on_err=_err, show_wait=False)

    def _render_monitor(self, r):
        try:
            if r.get("code") != 0:
                return
            students = r.get("students", [])
            self._stu_data = {s["username"]: s for s in students}
            cur_user = None
            item = self.stu_list.currentItem()
            if item:
                cur_user = item.data(Qt.UserRole)
            self.stu_list.blockSignals(True)
            self.stu_list.clear()
            for s in students:
                if s.get("submitted"):
                    sttxt = "已交卷"
                elif s.get("online"):
                    sttxt = "做题中"
                else:
                    sttxt = "离线"
                dot = "●" if s.get("online") else "○"
                it = QListWidgetItem(f"{s.get('name') or s.get('username')}（{s['username']}） {dot} {sttxt}")
                it.setData(Qt.UserRole, s["username"])
                self.stu_list.addItem(it)
            if cur_user and cur_user in self._stu_data:
                for i in range(self.stu_list.count()):
                    if self.stu_list.item(i).data(Qt.UserRole) == cur_user:
                        self.stu_list.setCurrentRow(i)
                        break
            self.stu_list.blockSignals(False)
            if cur_user and cur_user in self._stu_data:
                self._draw_questions(cur_user)
            else:
                self._clear_questions()
            self._load_logs()
        except Exception as e:
            print("monitor refresh err:", e)

    def _clear_questions(self):
        while self.qv.count():
            item = self.qv.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _draw_questions(self, username):
        st = self._stu_data.get(username) or {}
        self._clear_questions()
        qids = st.get("qids") or []
        if not qids:
            self.qv.addWidget(BodyLabel("该学生尚未进入答题"))
            self.qv.addStretch()
            return
        answered = set(st.get("answered") or [])
        wrong = set(st.get("wrong") or [])
        cur = st.get("current")
        groups = []
        for item in qids:
            if isinstance(item, dict):
                qid = str(item.get("qid") or "")
                mod = item.get("module") or "题目"
            else:
                qid = str(item)
                mod = "题目"
            if not qid:
                continue
            if not groups or groups[-1][0] != mod:
                groups.append([mod, []])
            groups[-1][1].append(qid)
        total = sum(len(g[1]) for g in groups)
        info = CaptionLabel(f"共 {total} 题 · 已答 {len(answered)} · 错 {len(wrong)}")
        info.setStyleSheet("color:#4A5568; font-weight:bold;")
        self.qv.addWidget(info)
        for mod, ql in groups:
            lbl = CaptionLabel(mod)
            lbl.setStyleSheet("color:#4F86F8; font-weight:bold;")
            self.qv.addWidget(lbl)
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, qid in enumerate(ql):
                grid.addWidget(self._mk_box(i + 1, qid, answered, wrong, cur), i // 6, i % 6)
            self.qv.addLayout(grid)
        self.qv.addStretch()

    def _mk_box(self, no, qid, answered, wrong, cur):
        box = QFrame()
        box.setFixedSize(42, 34)
        lab = QLabel(str(no), box)
        lab.setAlignment(Qt.AlignCenter)
        lab.setGeometry(0, 0, 42, 34)
        lab.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        if qid == cur:
            box.setStyleSheet("QFrame{background:#4F86F8; border-radius:5px; border:2px solid #2B5FD9;}")
            lab.setStyleSheet("color:white;")
        elif qid in wrong:
            box.setStyleSheet("QFrame{background:#FF5252; border-radius:5px;}")
            lab.setStyleSheet("color:white;")
        elif qid in answered:
            box.setStyleSheet("QFrame{background:#34A853; border-radius:5px;}")
            lab.setStyleSheet("color:white;")
        else:
            box.setStyleSheet("QFrame{background:#FFFFFF; border:1px solid #CBD5E1; border-radius:5px;}")
            lab.setStyleSheet("color:#1F2937;")
        box.setToolTip(f"第 {no} 题")
        return box

    def _load_logs(self):
        # 监控页每 3 秒轮询调用，静默拉取（不弹等待框），结果回主线程刷新
        net_task(self, "正在加载操作日志…",
                 lambda: self.net.call("auditLogs", limit=200, timeout=20),
                 on_ok=self._render_logs, show_wait=False)

    def _render_logs(self, r):
        try:
            if r.get("code") != 0:
                return
            logs = r.get("logs", [])
            self.log_table.setRowCount(len(logs))
            for i, lg in enumerate(logs):
                for j, key in enumerate(["ts", "username", "name", "action", "detail"]):
                    self.log_table.setItem(i, j, QTableWidgetItem(str(lg.get(key, "") or "")))
        except Exception:
            pass


class PracticeScorePage(QWidget):
    """练习成绩：查看学生参加自主练习的次数与得分（练习随时可参加，只查记录不干预）"""
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(12)
        layout.addWidget(SubtitleLabel("练习成绩（学生自主练习次数与得分）"))
        top = QHBoxLayout()
        self.count_label = CaptionLabel("")
        top.addWidget(self.count_label)
        top.addStretch()
        ref = PrimaryPushButton("刷新")
        ref.setFixedWidth(100)
        ref.clicked.connect(self.reload)
        top.addWidget(ref)
        layout.addLayout(top)
        self.table = TableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["账号", "姓名", "练习次数", "最高分", "最近得分", "最近时间"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self.reload()

    def reload(self):
        net_task(self, "正在加载练习成绩…",
                 lambda: self.net.call("practiceHistory", timeout=20),
                 on_ok=self._render_practice,
                 on_err=lambda e: InfoBar.error("加载失败", str(e), duration=3000,
                                                position=InfoBarPosition.TOP, parent=self))

    def _render_practice(self, r):
        try:
            if r.get("code") != 0:
                InfoBar.error("加载失败", r.get("msg", ""), duration=3000, position=InfoBarPosition.TOP, parent=self)
                return
            recs = r.get("records", [])
            self.table.setRowCount(len(recs))
            for i, rec in enumerate(recs):
                for j, key in enumerate(["username", "name", "times", "best", "latest", "lastTime"]):
                    v = rec.get(key)
                    if key in ("best", "latest") and v is not None:
                        v = "%.1f" % v if isinstance(v, (int, float)) else v
                    self.table.setItem(i, j, QTableWidgetItem(str(v if v is not None else "—")))
            self.count_label.setText(f"共 {len(recs)} 名学生参加过练习")
        except Exception as e:
            InfoBar.error("加载失败", str(e), duration=3000, position=InfoBarPosition.TOP, parent=self)


class MainWindow(QWidget):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.setWindowTitle("教师端")
        self.resize(1100, 700)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = NavigationInterface(self, showMenuButton=True)
        self.nav.setFixedWidth(220)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.pages = [
            HomeworkPage(net), PapersPage(net), ResultsPage(net),
            AnalysisPage(net), StudentsPage(net), MonitorPage(net), PracticeScorePage(net)
        ]
        for p in self.pages:
            self.stack.addWidget(p)

        icons = [FIF.EDIT, FIF.DOCUMENT, FIF.SEARCH, FIF.PIE_SINGLE, FIF.PEOPLE, FIF.PLAY, FIF.HISTORY]
        texts = ["布置作业", "试卷管理", "成绩查询", "成绩分析", "学生管理", "监控中心", "练习成绩"]
        for i, (ic, tx) in enumerate(zip(icons, texts)):
            self.nav.addItem(routeKey=f"page{i}", icon=ic, text=tx, onClick=lambda *args, idx=i: self.switch(idx))
        self.nav.setCurrentItem("page0")

        name = net.user.get("displayName", "") if net.user else ""
        self.nav.addItem(routeKey="user", icon=FIF.PEOPLE, text=name, position=NavigationItemPosition.BOTTOM)

    def switch(self, idx):
        self.stack.setCurrentIndex(idx)
        p = self.pages[idx]
        if hasattr(p, "reload"):
            QTimer.singleShot(10, lambda: self._safe_reload(p))

    def _safe_reload(self, p):
        try:
            p.reload()
        except Exception as e:
            print(f"页面加载失败: {e}")
