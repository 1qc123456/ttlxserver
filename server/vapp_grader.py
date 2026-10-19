# -*- coding: utf-8 -*-
"""
虚拟程序（Edge / Win10 / Win10_Email）操作题自动判分。

判分文件：虚拟程序完成操作后在 exe 同目录生成的无后缀文件（Edge / Win10 / Win10_Email）。
编码三层：文件文本 = base64( gzip( base64( .NET BinaryFormatter 序列化的 List<string> ) ) )
每条 List 元素形如：中文菜单路径（用 > 分隔） + 英文方法名，例如
    Edge 属性 > 隐私搜索和服务 > 防止跟踪  TrackProtect
    写信 > 收件人  writeMail_Address

判分思路：英文方法名是稳定锚点。建立「题干中文关键词 -> 方法名」映射，
题干出现某要求、且学生日志方法集合里出现对应方法名，即该得分点命中。
"""
import base64
import gzip
import io
import re

# 题干关键词 -> 该操作在日志里的方法名（任一出现即命中）
RULES = [
    # ---------------- Edge 浏览器 ----------------
    (["跟踪防护", "防止跟踪", "跟踪"], ["TrackProtect", "ProtectType", "UseStrictLevel"]),
    (["清除浏览数据", "清除浏览记录", "清除数据", "清理浏览"], ["ClearData", "ClearDate"]),
    (["请勿跟踪", "发送.*跟踪", "不跟踪"], ["SendRequest"]),
    (["付款", "支付", "缴费"], ["AllowPayway"]),
    (["SmartScreen", "智能屏幕", "Defender", "安全防护"], ["Smartscreen", "Defender", "DeterApp"]),
    (["使用安全 DNS", "安全DNS", "DNS"], ["UseDNS"]),
    (["使用安全的当前服务", "当前服务"], ["UseCurrentService"]),
    (["自定义.*服务", "自定义DNS", "其他服务"], ["UseCustomService", "CustomService"]),
    (["Web 服务", "Web服务", "网络服务"], ["UseWebServices"]),
    (["建议", "推荐网站", "推荐"], ["UseSuggest"]),
    (["帮助"], ["HelpMicrosoft", "HelpWeb"]),
    (["主题", "外观"], ["Theme", "OverallAppearance"]),
    (["缩放"], ["ZoomLevel"]),
    (["字体大小"], ["FontSize"]),
    (["垂直选项卡", "垂直标签"], ["ShowVerticalLabel"]),
    (["隐藏标题栏", "标题栏"], ["HideTitle"]),
    (["收藏夹按钮", "收藏夹栏按钮"], ["ShowFavoritesButton", "ShowFavorites"]),
    (["显示收藏夹", "收藏夹栏", "添加.*收藏夹", "添加至.*收藏夹", "加入收藏夹", "收藏夹"], ["ShowFavorites", "ShowFavoritesButton"]),
    (["开始按钮", "开始主页按钮"], ["ShowStartButton"]),
    (["主页按钮", "主按钮"], ["ShowHomeButton"]),
    (["扩展按钮"], ["ShowExtendButton"]),
    (["集合按钮", "集锦按钮"], ["ShowCollectionsButton"]),
    (["历史记录按钮"], ["ShowHistoryButton"]),
    (["下载按钮"], ["ShowDownloadsButton"]),
    (["数学", "Math按钮"], ["ShowMathButton"]),
    (["IE 模式", "IE模式", "Internet Explorer 模式", "兼容"], ["ShowIEButton", "IEcompatible", "AllowLoadWebInIE"]),
    (["截图按钮", "网页捕获"], ["ShowCaptureButton"]),
    (["共享按钮"], ["ShowShareButton"]),
    (["反馈按钮"], ["ShowFeedbackButton"]),
    (["浮动菜单", "悬浮菜单"], ["ShowFloatingMenu"]),
    (["关闭.*确认", "关闭时询问", "关闭多个标签"], ["AskWhenClose"]),
    (["启动.*主页", "打开 Microsoft Edge 时", "启动时"], ["WhenEdgeRun"]),
    (["主页.*网址", "主页 URL", "主页地址", "新标签页网址"], ["PageUrl", "NewTabUrl"]),
    (["新标签页"], ["IsNewTab", "NewTabUrl"]),
    (["预加载", "预载入"], ["Preload"]),
    (["默认浏览器", "设为默认"], ["IsDefaultBrowser"]),
    # ---------------- QQ 邮箱 ----------------
    (["收件人"], ["writeMail_Address"]),
    (["抄送"], ["writeMail_Address2"]),
    (["密送", "密件抄送"], ["writeMail_Address3"]),
    (["主题"], ["writeMail_Topic"]),
    (["附件名", "附件名称"], ["writeMail_FileName"]),
    (["附件大小"], ["writeMail_FileSize"]),
    (["附件内容"], ["writeMail_FileContent"]),
    (["添加附件", "插入附件", "附件"], ["writeMail_FileName", "writeMail_FileSize", "writeMail_FileContent"]),
    (["正文"], ["writeMail_Content"]),
    (["写信.*文字大小", "写信.*字号", "写信.*字体大小"], ["general_Display_FontSize"]),
    (["每页显示.*邮件", "每页.*封"], ["general_Display_MailList_PageSize"]),
    (["邮件摘要"], ["general_Display_MailList_ShowMailSummary"]),
    (["显示邮件大小", "邮件大小"], ["general_Display_MailList_ShowMailSize"]),
    (["写信默认字体"], ["general_Display_WriteDefaultFont_Font", "general_Display_WriteDefaultFont_Size"]),
    (["个性签名", "签名"], ["general_Sign"]),
    (["未填主题", "没有主题"], ["general_Send_FirstTopic"]),
    (["收件人.*保存.*联系人", "自动保存.*联系人", "历史联系人"], ["general_Send_SaveContacts"]),
    (["邮件自动保存", "自动保存邮件"], ["general_Send_SaveSend"]),
    (["自动转发", "邮件转发"], ["general_MailAutoForward"]),
    (["默认帐户昵称", "账户昵称", "昵称"], ["account_Info_DefaultName"]),
    (["默认发信", "默认发信账号", "默认发信帐号"], ["account_DefaultSendAccount"]),
    (["独立密码"], ["account_Safe_Password"]),
    (["POP3"], ["account_POP3_Service_POP3"]),
    (["IMAP"], ["account_POP3_Service_IMAP"]),
    (["开启服务", "CalDAV", "CardDAV", "Exchange"], ["account_POP3_Service_POP3", "account_POP3_Service_IMAP"]),
    # ---------------- Win10 控制面板 / 设置 ----------------
    (["安装打印机", "添加打印机", "安装.*打印机"], ["installationPrintType", "defaultPrinter"]),
    (["选择.*端口", "打印端口", "打印机端口"], ["selectPrintPort"]),
    (["驱动程序", "打印机驱动"], ["installationPrintDriver"]),
    (["打印机名称", "打印机命名", "命名打印机"], ["printNameSetup"]),
    (["共享打印机", "共享这台打印机", "打印机共享"], ["printShareSetup"]),
    (["设为默认打印机", "默认打印机"], ["defaultPrint", "defaultPrinter"]),
    (["打印测试页", "测试页"], ["testPage"]),
    (["网络打印机.*地址", "打印机.*IP", "网络地址"], ["netWorkPrintAddress"]),
    (["网络打印机.*名", "网络打印机名称"], ["netWorkPrintName"]),
    (["打印方向", "横向", "纵向"], ["direction"]),
    (["纸张", "页面大小", "纸张大小"], ["printerPage"]),
    (["规格"], ["specification"]),
    (["打印份数", "份数"], ["copies"]),
    (["打印质量", "分辨率", "打印分辨率"], ["quality"]),
    (["鼠标.*主按钮", "切换主.*次按钮", "鼠标主键", "主按钮"], ["primaryMouse", "mainButton"]),
    (["双击速度"], ["doubleSpeed"]),
    (["单击锁定", "启用单击锁定", "点击锁定"], ["clickLock"]),
    (["指针方案", "鼠标指针方案", "方案"], ["schemePointer"]),
    (["指针阴影", "鼠标指针阴影", "显示阴影"], ["shadowPointer"]),
    (["指针移动速度", "鼠标移动速度", "移动速度"], ["moveSpeed", "cursorSpeed"]),
    (["提高指针精确度", "指针精确度", "精确度"], ["precisionPointer"]),
    (["默认按钮.*指针", "指针.*默认按钮"], ["defaultBtnPointer"]),
    (["指针轨迹", "显示指针轨迹", "鼠标轨迹"], ["trackPointer", "trackSpeed"]),
    (["打字时隐藏指针", "输入时隐藏指针", "隐藏指针"], ["hidePointer"]),
    (["Ctrl.*指针位置", "按 Ctrl.*位置", "显示指针位置"], ["ctrlShowPointer"]),
    (["滚轮", "滑轮", "鼠标轮"], ["mouseWheel", "rollRowPulley"]),
    (["滚动.*行", "一次滚动.*行", "滚动行数"], ["rollingLines", "rollRowPulleyNum"]),
    (["非活动窗口", "不活动窗口", "悬停滚动"], ["inactiveWindow"]),
    (["输入语言", "默认输入语言", "输入法"], ["inputLanguage", "desktopLanguage", "differentInput", "cutInput"]),
    (["语言栏", "浮动语言栏", "透明语言栏"], ["languageBar", "lucencyLanguageBar", "elseLanguageBar"]),
    (["Caps Lock", "大写锁定"], ["capsLock"]),
    (["键盘布局", "切换键盘", "按键顺序"], ["keyboardLayout", "keysequence_1"]),
    (["自动播放"], ["autoplay"]),
    (["蓝牙"], ["bluetooth", "linkDownload_bluetooth"]),
    (["自动更正", "拼写", "键入时.*更正", "文字建议", "输入建议"], ["autoCorrect", "cacography", "textSuggest", "highlighted"]),
    (["手写面板", "手写"], ["handwrittenPanel"]),
]

# 文件系统类操作（保存图片 / 另存网页等）虚拟程序日志不记录，判分时提示但不扣设置分
FILE_HINT = ["保存到", "另存为", "保存至", "文件夹", ".jpg", ".html", ".htm", "下载", "新建文件", "重命名"]


def parse_vapp_log(raw):
    """raw 为无后缀判分文件的原始字节（也兼容已 base64 的文本）。
    返回 (methods:set, paths:list[str])。"""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    bf = None
    # 三层解码：文件 -> b64 -> gzip -> b64文本 -> BinaryFormatter
    try:
        txt = raw.decode("utf-8-sig", "ignore").strip().lstrip("﻿")
        inner = gzip.decompress(base64.b64decode(txt))
        bf = base64.b64decode(inner)
    except Exception:
        # 也许已经是 BinaryFormatter 字节
        bf = raw
    methods = set()
    for m in re.findall(rb"[A-Za-z_][A-Za-z0-9_]{4,}", bf):
        token = m.decode("ascii", "ignore")
        if token in ("String", "System", "mscorlib", "Version", "Culture", "neutral",
                     "PublicKeyToken", "Collections", "Generic", "List", "_items", "_size", "_version"):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{16}", token):
            continue
        methods.add(token)
    paths = re.findall(r"[一-龥A-Za-z0-9（）()_. >\-]{4,}", bf.decode("utf-8", "ignore"))
    paths = [p.strip() for p in paths if re.search(r"[一-龥]", p)]
    return methods, paths


def grade_vapp(stem, raw, full_score=20.0):
    """返回 dict: score/auto/note/hit/miss"""
    methods, paths = parse_vapp_log(raw)
    hay = " ".join(paths) + " " + " ".join(methods)
    hit, miss = [], []
    for kws, targets in RULES:
        required = False
        for kw in kws:
            if re.search(kw, stem):
                required = True
                break
        if not required:
            continue
        ok = any(t in methods for t in targets)
        label = kws[0].replace(".*", "")
        (hit if ok else miss).append(label)
    required_n = len(hit) + len(miss)
    file_steps = sum(1 for h in FILE_HINT if h in stem)
    note_parts = []
    if hit:
        note_parts.append("已完成: " + "、".join(hit))
    if miss:
        note_parts.append("未检测到: " + "、".join(miss))
    if file_steps:
        note_parts.append("另含文件保存类操作 %d 处，需在试题文件夹人工确认，未计入自动分" % file_steps)
    if required_n == 0:
        # 题干没匹配到任何可自动检测的设置点：只要产生了操作日志就给基础分
        score = full_score if methods else 0.0
        note = "题干未匹配到可自动检测项，按是否产生操作记录判分"
        if methods:
            note += "（检测到 %d 条操作）" % len(methods)
        return {"score": round(score, 1), "auto": True, "note": note,
                "hit": hit, "miss": miss, "ops": sorted(methods)}
    score = full_score * len(hit) / required_n
    return {"score": round(score, 1), "auto": True,
            "note": "；".join(note_parts), "hit": hit, "miss": miss, "ops": sorted(methods)}


if __name__ == "__main__":
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    for f in ("Edge", "Win10", "Win10_Email"):
        p = r"D:\天天乐学学生端\document\exe\\" + f
        raw = open(p, "rb").read()
        methods, paths = parse_vapp_log(raw)
        print(f, "方法数", len(methods), "路径片段", len(paths))
