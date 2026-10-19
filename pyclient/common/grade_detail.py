# -*- coding: utf-8 -*-
"""通用分层成绩明细对话框：
- 客观题整合为一个汇总卡片（答对X/Y、得分），点"查看详细"看每道对错
- 操作题逐题卡片显示题目内容+得分，点"查看详细"看命中/未命中得分点
学生端、教师端共用。
"""
import html as _html
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QDialog, QTextEdit,
                               QScrollArea, QFrame, QSizePolicy)
from qfluentwidgets import (PrimaryPushButton, PushButton, StrongBodyLabel, BodyLabel,
                            CaptionLabel, SubtitleLabel, InfoBar, InfoBarPosition)
from common.utils import format_operate_stem


def _esc(s):
    if s is None:
        return ""
    return _html.escape(str(s)).replace("\n", "<br>")


def is_pending(d):
    """该题是否处于后台判分中（服务端异步判分契约：pending=True 且 score=None）。

    仅当明细显式带 pending 或显式给出 score=None 的 operate 项才判定为判分中，
    避免把缺字段的历史答卷误判成 pending。
    """
    if not isinstance(d, dict):
        return False
    if d.get("pending"):
        return True
    return d.get("refType") == "operate" and "score" in d and d.get("score") is None


def _fmt_score(d, full_default=20):
    """返回 (得分文本, 颜色, 得分值or None)；判分中返回 ('判分中…', 橙色, None)"""
    if is_pending(d):
        return "判分中…", "#E67E22", None
    sc = d.get("score", 0) or 0
    full = d.get("fullScore", full_default) or full_default
    rate = (sc / full) if full else 0
    color = "#27AE60" if rate >= 0.6 else "#E67E22" if rate >= 0.3 else "#E74C3C"
    try:
        txt = f"{float(sc):g}/{float(full):g}"
    except Exception:
        txt = f"{sc}/{full}"
    return txt, color, sc


class _DetailWindow(QDialog):
    """纯文本/HTML 只读详情窗"""
    def __init__(self, title, html, parent=None, width=760, height=580):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)
        vl = QVBoxLayout(self)
        te = QTextEdit(); te.setReadOnly(True)
        te.setFont(QFont("Microsoft YaHei UI", 10.5))
        te.setHtml(html)
        vl.addWidget(te)
        hb = QHBoxLayout(); hb.addStretch()
        btn = PrimaryPushButton("关闭"); btn.setFixedWidth(100); btn.clicked.connect(self.accept)
        hb.addWidget(btn); vl.addLayout(hb)


def _objective_html(items):
    """客观题逐题对错 HTML（复用统一渲染）"""
    from common.utils import render_detail_html
    return render_detail_html(items)


def _operate_html(d):
    """单道操作题的完整要求 + 判分得分点明细"""
    hits = d.get("hits") or []
    miss = d.get("miss") or []
    details = d.get("details") or []
    note = d.get("note") or ""
    stem_html = f"""
      <p style="font-weight:bold;margin:10px 0 4px;">📋 题目要求</p>
      <div style="background:#f7f7f9;border-radius:6px;padding:10px;line-height:1.7;color:#333;
                  white-space:pre-wrap;">{_esc(format_operate_stem(d.get("stem", "")))}</div>
    """
    # ---- 后台判分中：只展示状态与题目要求，不渲染（也不臆造）判分点 ----
    if is_pending(d):
        return f"""
    <div style="font-family:'Microsoft YaHei UI';">
      <p style="font-size:15px;font-weight:bold;color:#E67E22;margin:4px 0;">⏳ 本题判分中（后台自动判分）…</p>
      <p style="color:#666;margin:2px 0 0;">交卷已受理，系统正在自动判分（Office / PSD 类文件题较慢，通常数十秒内完成）。
         请稍后刷新成绩查看逐点得分。</p>
      {stem_html}
    </div>
    """
    score = d.get("score", 0) or 0
    full = d.get("fullScore", 20) or 20
    rate = (score / full) if full else 0
    color = "#27AE60" if rate >= 0.6 else "#E67E22" if rate >= 0.3 else "#E74C3C"
    h = f"""
    <div style="font-family:'Microsoft YaHei UI';">
      <p style="font-size:15px;font-weight:bold;color:{color};margin:4px 0;">
        本题得分：{score} / {full}（{int(rate*100)}%）</p>
      {stem_html}
    """
    if details:
        # 原版引擎逐点判分明细：✔/✗ + 分值 + 预期值 + 学生实际值
        rows = []
        for x in details:
            ok = bool(x.get("correct"))
            color = "#27AE60" if ok else "#E74C3C"
            mark = "✔" if ok else "✗"
            desc = _esc(x.get("desc") or x.get("timuDesc") or "判分点")
            got = x.get("get", 0) or 0
            full = x.get("full", 0) or 0
            expect = str(x.get("expect") or x.get("standard") or "")
            student = str(x.get("student") or "")
            if not student:
                student = "—" if ok else "（空）"
            if len(student) > 200:
                student = student[:200] + "…"
            if len(expect) > 200:
                expect = expect[:200] + "…"
            rows.append(
                f'<tr style="border-bottom:1px solid #eee;">'
                f'<td style="color:{color};font-weight:bold;text-align:center;padding:5px 6px;">{mark}</td>'
                f'<td style="padding:5px 6px;line-height:1.55;">{desc}</td>'
                f'<td style="text-align:center;color:{color};padding:5px 6px;white-space:nowrap;">{got:g}/{full:g}</td>'
                f'<td style="padding:5px 6px;color:#555;">{_esc(expect)}</td>'
                f'<td style="padding:5px 6px;color:{color};">{_esc(student)}</td>'
                f'</tr>')
        h += ('<p style="font-weight:bold;margin:12px 0 4px;">📊 判分明细'
              f'（命中 <b style="color:#27AE60;">{len(hits)}</b> / 未达标 '
              f'<b style="color:#E74C3C;">{len(miss)}</b>，共 {len(details)} 个判分点）</p>'
              '<table cellspacing="0" cellpadding="0" width="100%" '
              'style="border-collapse:collapse;background:#fbfbfd;border:1px solid #e6e9f2;border-radius:6px;">'
              '<tr style="background:#eef2fa;">'
              '<th style="padding:6px;font-size:12px;">结果</th>'
              '<th style="padding:6px;font-size:12px;text-align:left;">判分点</th>'
              '<th style="padding:6px;font-size:12px;">得分</th>'
              '<th style="padding:6px;font-size:12px;text-align:left;">预期值</th>'
              '<th style="padding:6px;font-size:12px;text-align:left;">学生值</th>'
              '</tr>' + "".join(rows) + '</table>')
    elif hits or miss:
        h += '<p style="font-weight:bold;margin:12px 0 4px;">📊 判分明细</p>'
        if hits:
            li = "".join(f'<div style="color:#27AE60;line-height:1.7;">✓ {_esc(x)}</div>' for x in hits)
            h += f'<div style="background:#E8F8F0;border-radius:6px;padding:8px;margin-bottom:6px;">' \
                 f'<b style="color:#27AE60;">已掌握（{len(hits)}项）</b><br>{li}</div>'
        if miss:
            li = "".join(f'<div style="color:#E74C3C;line-height:1.7;">✗ {_esc(x)}</div>' for x in miss)
            h += f'<div style="background:#FDEDEC;border-radius:6px;padding:8px;">' \
                 f'<b style="color:#E74C3C;">未达标（{len(miss)}项）</b><br>{li}</div>'
    if note:
        h += f'<p style="color:#888;font-size:12px;margin-top:10px;">判分说明：{_esc(note)}</p>'
    if not hits and not miss:
        h += '<p style="color:#999;margin-top:10px;">本题无结构化判分点（可能未上传作答文件或该题型暂不支持逐点判分）。</p>'
    h += "</div>"
    return h


class _OperateCard(QFrame):
    """单道操作题卡片"""
    def __init__(self, idx, d, parent=None):
        super().__init__(parent)
        self.d = d
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(".QFrame{background:#fafbff;border:1px solid #e6e9f2;border-radius:8px;}")
        sc_txt, sc_color, _sc = _fmt_score(d)
        pending = is_pending(d)
        fmt = format_operate_stem(d.get("stem", ""))
        segs = fmt.split("\n\n", 1)
        body = segs[1] if len(segs) > 1 else fmt
        stem = body.replace("\n", " ")
        brief = stem[:70] + ("…" if len(stem) > 70 else "")
        hl = QHBoxLayout(self); hl.setContentsMargins(14, 10, 14, 10)
        col = QVBoxLayout()
        title = BodyLabel(("⏳ " if pending else "") + f"{idx}. {brief}")
        title.setWordWrap(True)
        col.addWidget(title)
        if pending:
            sub = CaptionLabel(f"操作题 ｜ <b style='color:{sc_color};font-size:13px;'>判分中…</b>")
        else:
            sub = CaptionLabel(f"操作题 ｜ 得分 <b style='color:{sc_color};font-size:13px;'>{sc_txt}</b>")
        sub.setTextFormat(Qt.RichText)
        col.addWidget(sub)
        hl.addLayout(col, 1)
        btn = PushButton("查看详细"); btn.setFixedWidth(96)
        btn.clicked.connect(self._show)
        hl.addWidget(btn)

    def _show(self):
        _DetailWindow(f"操作题详细", _operate_html(self.d), self).exec()


class GradeDetailDialog(QDialog):
    """分层成绩明细对话框。
    detail: 服务端 recordDetail / submit 返回的题目列表（每题含 refType）。
    refresh_cb: 可选回调，点击"刷新"时重新拉取，返回
                {"score":float|None, "total":float|None, "detail":[...]}。
    """
    def __init__(self, score=None, total=None, detail=None, title="成绩明细", parent=None,
                 refresh_cb=None):
        super().__init__(parent)
        self.detail = detail or []
        self.refresh_cb = refresh_cb
        self.pending_n = sum(1 for d in self.detail if is_pending(d))
        self.setWindowTitle(title)
        self.resize(820, 660)
        outer = QVBoxLayout(self)
        # 顶部总分（未显式传入时，从明细汇总；判分中的题按 0 分计入已得分、满分计入总分）
        if score is None and self.detail and any("score" in d for d in self.detail):
            score = round(sum(d.get("score", 0) or 0 for d in self.detail), 1)
            total = round(sum(d.get("fullScore", 0) or 0 for d in self.detail), 1)
        if score is not None:
            if total:
                rate = (score / total) if total else 0
                color = "#27AE60" if rate >= 0.6 else "#E67E22" if rate >= 0.3 else "#E74C3C"
                head = StrongBodyLabel(f"总得分：{score:g} / {total:g}　（{int(rate*100)}%）")
            else:
                color = "#2E6FF2"
                head = StrongBodyLabel(f"总得分：{score:g}")
            head.setStyleSheet(f"font-size:20px;color:{color};")
            outer.addWidget(head)
        # 判分中提示条
        if self.pending_n:
            tip = BodyLabel(f"⏳ 有 <b style='color:#E67E22;'>{self.pending_n}</b> 道操作题正在后台判分，"
                            f"当前总分为已判完部分；判分完成后点击「刷新成绩」查看最终得分与明细。")
            tip.setTextFormat(Qt.RichText)
            tip.setWordWrap(True)
            tip.setStyleSheet("background:#FFF7E6;border:1px solid #FFE0A3;border-radius:6px;padding:8px;color:#8a5a00;")
            outer.addWidget(tip)
        # 滚动区
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(); sv = QVBoxLayout(host); sv.setContentsMargins(2, 6, 10, 6); sv.setSpacing(12)
        obj = [d for d in self.detail if d.get("refType") != "operate"]
        op = [d for d in self.detail if d.get("refType") == "operate"]

        # ---- 客观题汇总卡片 ----
        if obj:
            card = QFrame(); card.setFrameShape(QFrame.StyledPanel)
            card.setStyleSheet(".QFrame{background:#f4f8ff;border:1px solid #d6e4ff;border-radius:8px;}")
            cl = QVBoxLayout(card); cl.setContentsMargins(16, 12, 16, 12)
            right_n = sum(1 for d in obj if d.get("correct"))
            # 得分（有 score 字段时统计）
            has_score = any("score" in d for d in obj)
            if has_score:
                got = sum(d.get("score", 0) or 0 for d in obj)
                full = sum(d.get("fullScore", 0) or 0 for d in obj)
                stat = SubtitleLabel(f"📝 客观题（选择题）")
                cl.addWidget(stat)
                cl.addWidget(BodyLabel(f"答对 <b>{right_n}</b> / {len(obj)} 题　得分 <b>{got:g}</b> / {full:g}"))
            else:
                cl.addWidget(SubtitleLabel("📝 客观题（选择题）"))
                cl.addWidget(BodyLabel(f"答对 <b style='color:#27AE60;'>{right_n}</b> 题，"
                                       f"答错 <b style='color:#E74C3C;'>{len(obj)-right_n}</b> 题，共 {len(obj)} 题"))
            row = QHBoxLayout(); row.addStretch()
            btn = PrimaryPushButton(f"查看客观题详细（{len(obj)}题）"); btn.setFixedWidth(200)
            btn.clicked.connect(lambda: _DetailWindow("客观题详细（题干/选项/对错）", _objective_html(obj), self).exec())
            row.addWidget(btn); cl.addLayout(row)
            sv.addWidget(card)

        # ---- 操作题逐题卡片 ----
        if op:
            head = SubtitleLabel(f"🛠 操作题（{len(op)}题，点击查看具体对错）")
            sv.addWidget(head)
            for i, d in enumerate(op, 1):
                sv.addWidget(_OperateCard(i, d, self))

        if not self.detail:
            sv.addWidget(BodyLabel("暂无答题明细"))
        sv.addStretch()
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)
        # 底部按钮
        br = QHBoxLayout(); br.addStretch()
        if self.refresh_cb and self.pending_n:
            rb = PrimaryPushButton("🔄 刷新成绩"); rb.setFixedWidth(130)
            rb.clicked.connect(self._do_refresh)
            br.addWidget(rb)
        cb = PrimaryPushButton("关闭"); cb.setFixedWidth(120); cb.clicked.connect(self.accept)
        br.addWidget(cb); outer.addLayout(br)

    def _do_refresh(self):
        """重新拉取明细并重建对话框（判分中的题刷新后即可看到最终得分）。"""
        if not self.refresh_cb:
            return
        try:
            new = self.refresh_cb() or {}
        except Exception as e:
            InfoBar.error(title="刷新失败", content=str(e), parent=self,
                          duration=3000, position=InfoBarPosition.TOP)
            return
        detail = new.get("detail") or []
        still = sum(1 for d in detail if is_pending(d))
        dlg = GradeDetailDialog(score=new.get("score"), total=new.get("total"), detail=detail,
                               title=self.windowTitle(), parent=self.parent(),
                               refresh_cb=self.refresh_cb)
        if still:
            InfoBar.warning(title="部分题目仍在判分",
                            content=f"还有 {still} 道操作题未判完，可稍后再次刷新。",
                            parent=dlg, duration=4000, position=InfoBarPosition.TOP)
        dlg.exec()
        self.accept()
