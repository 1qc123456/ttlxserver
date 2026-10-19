# -*- coding: utf-8 -*-
"""客户端共享工具：题目/答题明细的 HTML 渲染（完整不截断、自动转义）"""
import html as _html
import os
import re
import sys


def resource_path(rel):
    """资源文件绝对路径：PyInstaller 单 exe 下用 sys._MEIPASS，开发时相对 common/。"""
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, rel)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def make_light_palette():
    """构造标准浅色调色板，屏蔽系统深色模式对普通 QWidget 的影响。
    客户端界面按浅色设计（含大量硬编码浅色背景/深色文字），
    任何系统主题下都应显示为浅色，保证文字清晰可读。"""
    from PySide6.QtGui import QPalette, QColor
    p = QPalette()
    white = QColor("#ffffff")
    light = QColor("#f0f0f0")
    ink = QColor("#000000")
    p.setColor(QPalette.Window, light)
    p.setColor(QPalette.WindowText, ink)
    p.setColor(QPalette.Base, white)
    p.setColor(QPalette.AlternateBase, QColor("#f7f7f7"))
    p.setColor(QPalette.Text, ink)
    p.setColor(QPalette.Button, light)
    p.setColor(QPalette.ButtonText, ink)
    p.setColor(QPalette.BrightText, QColor("#ff0000"))
    p.setColor(QPalette.Highlight, QColor("#4F86F8"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor("#2f80ed"))
    p.setColor(QPalette.LinkVisited, QColor("#7a4fd8"))
    p.setColor(QPalette.ToolTipBase, QColor("#ffffdc"))
    p.setColor(QPalette.ToolTipText, ink)
    p.setColor(QPalette.PlaceholderText, QColor("#999999"))
    return p


def esc(s):
    """HTML 转义，防止题干里的 < > & 被当成标签吞掉"""
    if s is None:
        return ""
    return _html.escape(str(s)).replace("\n", "<br>")


# 操作题步骤编号：（1）(1) / 1、 / 1. 1． / ①⑳ 。
# 编号数字前不得紧邻字母/数字/点（防 F13、c5、192.168），点号编号后不得紧跟字母数字（防 1.jpg、5.txt）
_NUM_PAT = (
    r'(?<![A-Za-z0-9.])'
    r'(?:[（(]\s*\d{1,2}\s*[)）]'
    r'|\d{1,2}\s*、'
    r'|\d{1,2}\s*[.．](?![0-9A-Za-z])'
    r'|[①-⑳])'
)
_BAR_CHARS = "-=*—_·═─│┃┄┅┈┉╌╍━┄┅"


def format_operate_stem(raw):
    """把操作题原始题干（横线框引导语 + 编号步骤）整理成：
    引导语（空一行）步骤1\\n步骤2...；去掉装饰横线，兼容 1、 1. （1）① 编号，
    且不误伤小数、IP、负数格式、单元格地址、文件名。"""
    if not raw:
        return ""
    s = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    # 去装饰横线行
    kept = []
    for line in s.split("\n"):
        t = line.strip()
        if t and t.strip(_BAR_CHARS).strip() == "":
            continue
        kept.append(t)
    s = "\n".join(kept)
    # 折叠空白，保留换行语义
    s = re.sub(r"[ \t　]+", " ", s)
    matches = list(re.finditer(_NUM_PAT, s))
    if not matches:
        out = re.sub(r"\s*\n\s*", " ", s).strip()
        return out
    intro = s[:matches[0].start()]
    intro = re.sub(r"[%s]{3,}" % re.escape(_BAR_CHARS), " ", intro)  # 行内残留横线
    intro = re.sub(r"\s*\n\s*", " ", intro).strip(" 。；;\n\t")
    steps = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        chunk = s[m.start():end]
        chunk = re.sub(r"\s*\n\s*", " ", chunk).strip(" ；;\n\t")
        if chunk:
            steps.append(chunk)
    parts = []
    if intro:
        parts.append(intro + "。")
    if steps:
        parts.append("\n".join(steps))
    return "\n\n".join(parts)



def render_detail_html(detail):
    """把答题明细列表渲染成完整 HTML。
    兼容三种数据：
      - 即时判分 grade_answers: {qid,right,your,correct,tip,stem}
      - 历史详情 recordDetail:   {refType,stem,options,your,right,correct}
      - 交卷结果:                上述任一
    不做任何长度截断，QTextEdit 自身可滚动。
    """
    if not detail:
        return "<p style='color:#999;'>暂无答题明细</p>"
    parts = []
    for i, d in enumerate(detail, 1):
        ok = bool(d.get("correct"))
        color = "#3498DB" if ok else "#FF5252"
        bg = "#E8F5E9" if ok else "#FFEBEE"
        mark = "✓ 正确" if ok else "✗ 错误"
        is_op = d.get("refType") == "operate"
        block = (
            f'<p style="margin:8px 0; padding:10px; background:{bg}; border-radius:6px;">'
            f'<b style="color:{color};">[{i}] {mark}</b><br>'
            f'<span style="color:#333; line-height:1.6;">{esc(d.get("stem", ""))}</span>'
        )
        # 客观题：列出选项，正确答案绿色加粗、错选红色删除线
        opts = d.get("options") or []
        right = (d.get("right") or "").strip()
        your = (d.get("your") or "").strip()
        opt_html = []
        for o in opts:
            k = (o.get("k") or "").strip()
            t = o.get("t")
            if not t:
                continue
            style = ""
            if k and k == right:
                style = 'color:#27AE60;font-weight:bold;'
            elif k and your and k == your and k != right:
                style = 'color:#E74C3C;text-decoration:line-through;'
            opt_html.append(f'<span style="{style}">{_html.escape(k)}. {_html.escape(str(t))}</span>')
        if opt_html:
            block += '<br><span style="font-size:12px;color:#555;line-height:1.7;">' + "<br>".join(opt_html) + "</span>"
        # 答案行
        if is_op:
            block += '<br><span style="color:#888;font-size:12px;">操作题·已自动判分</span>'
        else:
            ya = d.get("your", "")
            block += (
                f'<br><span style="color:#888;font-size:12px;">'
                f'你的答案: {_html.escape(str(ya) if ya not in (None, "") else "(未答)")}'
                f' ｜ 正确答案: {_html.escape(str(d.get("right", "")))}</span>'
            )
        # 解析提示（即时练习可能有）
        tip = d.get("tip")
        if tip and not ok:
            block += f'<br><span style="color:#E67E22;font-size:12px;">解析: {_html.escape(str(tip))}</span>'
        block += "</p>"
        parts.append(block)
    return "".join(parts)


def render_wrong_html(w):
    """渲染单道错题的完整内容（错题本双击查看）"""
    stem = esc(w.get("stem", ""))
    html = f'<p style="line-height:1.8;color:#333;">{stem}</p>'
    opts = w.get("options") or []
    right = (w.get("right_answer") or w.get("right") or "").strip()
    your = (w.get("my_answer") or w.get("your") or "").strip()
    opt_html = []
    for o in opts:
        k = (o.get("k") or "").strip()
        t = o.get("t")
        if not t:
            continue
        style = ""
        if k and k == right:
            style = 'color:#27AE60;font-weight:bold;'
        elif k and your and k == your and k != right:
            style = 'color:#E74C3C;text-decoration:line-through;'
        opt_html.append(f'<span style="{style}">{_html.escape(k)}. {_html.escape(str(t))}</span>')
    if opt_html:
        html += '<p style="line-height:1.7;font-size:13px;">' + "<br>".join(opt_html) + "</p>"
    html += (
        f'<p style="font-size:13px;color:#888;">'
        f'我的答案: <span style="color:#E74C3C;">{_html.escape(your or "(未答)")}</span>'
        f' ｜ 正确答案: <span style="color:#27AE60;">{_html.escape(right)}</span></p>'
    )
    return html
