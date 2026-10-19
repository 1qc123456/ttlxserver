# -*- coding: utf-8 -*-
"""
judge_bridge.py  ——  原版天天乐学判分引擎桥接层

通过子进程调用 judge/JudgeHost.exe（x86 .NET Framework 4.x），反射调用原版
TTLX.OperatorJudgeFunction.StartJudgeFunction.StartGrade，对操作题做与原版完全
一致的全自动判分，返回总分 + 每个判分点(DFD)的得分与学生值/标准值明细。

覆盖题型（题库 dfdList 非空时）：
  31 Win10设置 / 32 Edge·网络·邮箱（虚拟程序判分文件，无 Office 依赖）
  43 Word(WPS) / 44 Excel(WPS) / 45 PPT(WPS)（COM 自动化，需 Office 或 WPS；
     仅装 Office 时由 setup_office_alias.ps1 写入 HKCU 的 WPS ProgID 别名）
  18 Photoshop（Aspose.PSD，无外部依赖）
  17/37 Access、24 MySQL、25 Python 等：dfdList 为空或另走专用判分器时回退。

判分文件（虚拟程序）三层编码：Base64(GZip(Base64(BinaryFormatter(List<string>))))，
JudgeHost 内部由原版 DLL 自行反序列化，本层只需把学生交上来的无后缀判分文件按固定名
放入 paperFolder 即可，无需在 Python 侧解码。
"""
import os
import re
import json
import shutil
import tempfile
import threading
import subprocess
import collections
from app_paths import BASE_DIR as _HERE

JUDGE_DIR = os.path.join(_HERE, "judge")
JUDGE_EXE = os.path.join(JUDGE_DIR, "JudgeHost.exe")
BIN_DIR = os.path.join(JUDGE_DIR, "bin")

# 原版判分内部超时固定 600000ms；子进程外层超时（秒）略大于它
_ENGINE_TIMEOUT = 630

# Office COM 互斥是按“应用”粒度：同一应用(WinWord/Excel/PowerPnt)的判分会
# KillProcess 全局该应用的进程，必须互斥；不同应用之间互不影响，可并行。
# PSD(type18) 走 Aspose.PSD 纯库调用，完全不碰 Office COM，不需要任何锁。
# 因此把原来的全局 _office_lock 拆成按应用分锁，让 Word/Excel/PPT 可并行判分，
# PSD 完全自由并行。
_office_locks = collections.defaultdict(threading.Lock)
# 向后兼容别名：旧代码若引用 _global_lock / _office_lock，指向一个占位锁（不再用于实际加锁）
_office_lock = threading.Lock()
_global_lock = _office_lock
# 不需要 Office COM 互斥锁的题型（走纯库判分）
_NO_LOCK_TYPES = {18}  # PSD → Aspose.PSD

# 走虚拟程序判分文件的题型
VAPP_TYPES = {11, 12, 31, 32}
# timuType=11/12（Win10设置/网络）的判分方法表在原版DLL里不完整（haveFuction 3664等找不到method），
# 用timuType=31调用JudgeHost即可（判分文件名vapp_log_name和DFD都不变，已验证满分）。
_ENGINE_TYPE_MAP = {11: 31, 12: 31,
                    # 旧版 Office / 2016 题的判分方法在当前 DLL 中静默跳过(records空)，
                    # 映射到 WPS 判分题型即恢复逐点判分（已端到端验证 hf106/107 命中）。
                    13: 43, 33: 43,
                    14: 44,
                    15: 45}

# type11/12/31/32 的DFD在服务器SDFDTable里keyword字段常为空，
# 但判分引擎按keyword在判分文件里匹配记录。keyword空→引擎找不到→0分。
#
# 【重要】keyword 必须等于「判分文件记录条里的字段ID」，格式 nav路径□字段ID□值，
# 字段ID 来自 Win10.exe / Win10_Email.exe 的 StreamWriteFile 反编译结果
# （见 oder/probes/outputs/_win10_nav_field.json 466 条、_email_nav_field.json 24 条）。
# 早期版本误用 WinForms 控件名（rtxPPPoEUserName / txtPPPoEPassword / chkPPPoEPpwd /
# vpnName / vpnAddress / vpnType / vpnLoginType / vpnUser / vpnPwd / vpnRemember），
# 且打印机段整体错位一位 —— 结果就是答案值为英文的判分点（打印机名 stu01、
# 端口 LPT3:(打印机端口)、驱动 Sony(索尼)■Sony DPP-FP60、宽带用户名 user06、
# VPN 用户名 user14、VPN 连接名 stu12 等）一律判 0 分。
# 现按 nav 表全部修正为真实字段ID（pPPoEUserName / pPPoEPassword / pPPoEPpwd /
# pPPoEConnName / connectname / servername / vpntype / vpnname / vpnpwd …）。
# 推导脚本与原始数据：_derive_hf.py / _hf_derived.json。
_HF_KEYWORD_MAP = {
    1001: "wz_wz",
    1004: "sj_csj",
    1009: "installationPrintType",  # 添加/安装打印机类型
    1010: "netWorkPrintAddress",  # 按名称查找网络打印机(共享路径)
    1011: "printNameSetup",  # 打印机名称设置
    1012: "defaultPrint",  # 设置为默认打印机
    1019: "sz_fsgs",
    1020: "sz_dlh",
    1023: "sz_xsw",
    1024: "sz_ddsz",
    1026: "locktaskbar",
    1029: "autohide",
    1032: "testPage",  # 是否打印测试页
    1033: "selectPrintPort",  # 选择打印机端口
    1036: "installationPrintDriver",  # 安装打印机驱动程序
    1037: "netWorkPrintName",  # 网络打印机名称
    1038: "printShareSetup",  # 打印机共享设置(名称/注释/位置)
    1042: "gs_dyt",
    1043: "sz_fz",
    1044: "sj_am",
    1045: "sj_pm",
    1051: "color",
    1052: "sz_lqs",
    1054: "gs_dsj",
    1055: "px_px",
    1057: "resumeDisplay",
    1100: "writeMail_Address",  # 写信 收件人
    1101: "writeMail_Topic",  # 写信 主题
    1102: "writeMail_Content",  # 写信 正文
    1103: "writeMail_FileName",  # 写信 附件名
    1104: "pPPoEUserName",  # 宽带PPPoE 用户名
    1105: "pPPoEPassword",  # 宽带PPPoE 密码
    1106: "pPPoEPpwd",  # 宽带PPPoE 记住密码
    1107: "pPPoEConnName",  # 宽带PPPoE 连接名称
    1108: "dialUserName",  # 拨号用户名
    1109: "dialPassword",  # 拨号密码
    1110: "dialpwd",  # 拨号记住密码
    1111: "dialPhoneOther",  # 拨号 任何使用此计算机的人(允许其他人使用此连接)
    1113: "homepage",  # Internet选项>常规>主页
    1114: "keepwebsite",  # Internet选项>常规>删除浏览历史记录(勾选删除项:密码/历史记录)
    1132: "aq_intranetevel",  # Internet选项>安全>本地Intranet 安全级别
    1217: "gj_http",  # Internet选项>高级>HTTP1.1>通过代理连接使用 HTTP1.1
    1117: "writeMail_Address2",  # 写信 抄送
    1118: "general_Send_SaveContacts",  # 发信 收件人自动保存历史联系人
    1119: "general_Display_FontSize",  # 显示 文字大小
    1120: "account_Info_DefaultName",  # 默认帐户昵称
    1121: "account_DefaultSendAccount",  # 默认发信帐号
    1122: "cx_prompt",
    1123: "tab_other",
    1126: "general_Display_MailList_ShowMailSummary",  # 显示邮件摘要
    1127: "pPPoEString",  # 宽带PPPoE 显示字符
    1131: "general_Send_SaveSend",  # 邮件自动保存到已发送
    1164: "gs_drq",
    1165: "gs_csj",
    1201: "ipv4_IpAddress",  # IPv4 IP地址
    1202: "ipv4_Mask",  # IPv4 子网掩码
    1203: "ipv4_Gateway",  # IPv4 默认网关
    1204: "ipv4_MainDNS",  # IPv4 首选DNS
    1205: "ipv4_SubDNS",  # IPv4 备用DNS
    1211: "pPPoEOther",  # 宽带允许其他人使用
    1212: "pPPoEUserName",  # 宽带属性>常规>服务名
    1216: "general_Display_MailList_PageSize",  # 每页显示几封邮件
    1218: "general_Sign",  # 个性签名
    3030: "gs_crq",
    3033: "hb_fz",
    3034: "sj_dsj",
    3086: "ipv4_VerificationExit",  # IPv4 退出时验证
    3087: "ipv4_BackIpAddress",  # 备用配置 IP
    3088: "ipv4_BackMask",  # 备用配置 掩码
    3089: "ipv4_BackGateway",  # 备用配置 网关
    3090: "ipv4_BackMainDNS",  # 备用配置 首选DNS
    3091: "ipv4_BackSubDNS",  # 备用配置 备用DNS
    3092: "ipv4_BackMainWINS",  # 备用配置 首选WINS
    3093: "ipv4_BackSubWINS",  # 备用配置 备选WINS
    3094: "ipv4_BackVerificationExit",  # 备用配置 退出验证
    3102: "writeMail_Address3",  # 写信 密送
    3103: "general_Display_MailList_ShowMailSize",  # 显示邮件大小
    3104: "general_Display_WriteDefaultFont_Font",  # 写信默认字体
    3105: "general_Display_WriteDefaultFont_Size",  # 写信默认字体大小
    3106: "general_Send_FirstTopic",  # 未填主题用正文首句
    3108: "account_Safe_Password",  # 独立密码
    3381: "favorites",
    3479: "connectname",  # VPN 连接名称
    3480: "servername",  # VPN 服务器名称或地址
    3481: "vpntype",  # VPN 类型
    3482: "vpnsecretkey",  # VPN 预共享秘钥
    3483: "logintype",  # VPN 登录信息类型
    3484: "vpnname",  # VPN 用户名
    3485: "vpnpwd",  # VPN 密码
    3486: "vpnremember",  # VPN 记住登录信息
    3506: "direction",  # 打印机方向
    3507: "printerPage",  # 每张纸打印的页数
    3508: "specification",  # 高级选项 > 纸张规格
    3509: "copies",  # 高级选项 > 纸张份数
    3510: "quality",  # 高级选项 > 打印质量
    3511: "primaryMouse",  # 鼠标键 > 切换主要和次要的按钮
}
# 走 Office / PSD / Access 等本地文件 COM/OpenXML 判分的题型
FILE_TYPES = {13, 14, 15, 17, 18, 33, 34, 35, 37, 43, 44, 45}

# 文件扩展名映射
_EXT = {13: ".docx", 33: ".docx", 43: ".docx",
        14: ".xlsx", 34: ".xlsx", 44: ".xlsx",
        15: ".pptx", 35: ".pptx", 45: ".pptx",
        17: ".accdb", 37: ".accdb", 18: ".psd"}

# Office COM 判分前需清理的对应残留进程（判分器内部也会 KillProcess，
# 这里再兜一层：上次判分超时强杀或机器上开着 Word 会让首个判分点 COM 异常）
_OFFICE_PROC = {".docx": "WINWORD.EXE", ".doc": "WINWORD.EXE",
                ".xlsx": "EXCEL.EXE", ".xls": "EXCEL.EXE",
                ".pptx": "POWERPNT.EXE", ".ppt": "POWERPNT.EXE"}


def _prekill_office(timu_type, file_ext):
    proc = _OFFICE_PROC.get((file_ext or _EXT.get(timu_type) or "").lower())
    if proc:
        try:
            subprocess.run(["taskkill", "/F", "/IM", proc],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            pass


def vapp_log_name(timu_type, sub_type):
    """虚拟程序判分文件固定名（与 exe 同名、无后缀）。
    实证 GetExeNameByQueSubtype：sub7=Edge，sub11=Win10_Email，其余=Win10。"""
    st = int(sub_type or 0)
    if st == 7:
        return "Edge"
    if st == 11:
        return "Win10_Email"
    return "Win10"


def engine_available():
    return os.path.isfile(JUDGE_EXE) and os.path.isdir(BIN_DIR)


def _norm(v):
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("；", ";").replace("，", ",").replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    return s


def _eq(a, b):
    return _norm(a) == _norm(b)


_OFFICE_ENUM = None


def office_enum():
    """加载标准 Office 枚举名→数值表（来源 NetOffice，与微软 PIA 一致）。"""
    global _OFFICE_ENUM
    if _OFFICE_ENUM is None:
        m = {}
        p = os.path.join(_HERE, "pia_enum.txt")
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if "=" in line:
                    k, v = line.rsplit("=", 1)
                    try:
                        m[k] = int(v)
                    except ValueError:
                        pass
        except Exception:
            pass
        _OFFICE_ENUM = m
    return _OFFICE_ENUM


def _num(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return None


def office_point_match(student, expect):
    """判定一个 Office 判分点是否达标。

    引擎（映射 WPS 43/44/45 后）把枚举类标准答案转成大写枚举名字符串，
    与学生数值直接比较导致类型不匹配（如居中学生值 1 vs 'WDALIGNPARAGRAPHCENTER'）误判。
    此处用权威枚举表把期望解析为数值后与学生值比较；文字类按归一化字符串比较。"""
    if expect is None:
        return False
    s = str(student or "").strip()
    if s.startswith("异常"):
        return False
    enum = office_enum()
    tokens = [t.strip() for t in str(expect).split(",") if t.strip()]
    enumvals, nums, texts = [], [], []
    for t in tokens:
        if t in enum:
            enumvals.append(enum[t])
        else:
            n = _num(t)
            if n is not None:
                nums.append(n)
            else:
                texts.append(t)
    sn = _num(s)
    if texts and not enumvals and not nums:
        return _eq(s, " ".join(texts))
    targets = enumvals + nums
    if targets:
        if sn is not None and any(abs(sn - v) < 1e-6 for v in targets):
            return True
        if any(_eq(s, t) for t in tokens if t in enum):
            return True
        if texts and _eq(s, " ".join(texts)):
            return True
        return False
    return _eq(s, expect)


def _build_detail(dfd, rec, scale=1.0):
    """把一个判分点(DFD)与引擎返回记录对齐成明细 dict。

    scale: 题库 DFD 原始分 → 试卷本题满分的缩放系数（引擎按 timuScore 等比缩放得分，
           明细里的 full 必须用同一系数换算，否则出现 get=3.333 / full=10 的错位）。
    """
    students, standards = [], []
    if rec:
        for sd in rec.get("scoreDetails") or []:
            sa = sd.get("studentAnswer")
            ta = sd.get("standardAnswer")
            if sa is not None and sa != "":
                students.append(str(sa))
            if ta is not None and ta != "":
                standards.append(str(ta))
    get = float(rec["dfdGetScore"]) if rec else 0.0
    full = float(dfd.get("score") or 0.0) * scale
    desc = dfd.get("timuDesc") or dfd.get("keyword") or ("动作码%s" % dfd.get("haveFuction"))
    expect = dfd.get("yuqizhi")
    student = " ; ".join(students)
    standard = " ; ".join(standards)
    if not standard and expect not in (None, ""):
        standard = str(expect)
    return {
        "dfdId": dfd.get("dfdId"),
        "code": dfd.get("haveFuction"),
        "desc": desc,
        "expect": ("" if expect is None else str(expect)),
        "keyword": dfd.get("keyword"),
        "startMark": dfd.get("startMark"),
        "full": full,
        "get": round(get, 3),
        "correct": get > 0.0001,
        "student": student,
        "standard": standard,
    }


def decode_vapp_entries(raw):
    """三层解码虚拟程序判分文件，完整还原 List<string> 条目。
    返回 list[str]，每条形如 '导航路径□字段ID□值'（值可能含 ■ 多值分隔）。
    判分引擎给分但不回填 studentAnswer，故由桥接层自行解码用于成绩明细展示。"""
    import base64, gzip
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    bf = None
    try:
        txt = raw.decode("utf-8-sig", "ignore").strip().lstrip("﻿")
        bf = base64.b64decode(gzip.decompress(base64.b64decode(txt)))
    except Exception:
        bf = raw  # 也许已是 BinaryFormatter 字节
    text = bf.decode("utf-8", "ignore")
    out = []
    # BinaryFormatter 字符串为可见字符（控制字符是序列化元数据）；条目恰好含 2 个 □
    for chunk in re.findall(r"[^\x00-\x1f\x7f]+", text):
        chunk = chunk.strip()
        if chunk.count("□") == 2 and chunk not in out:
            out.append(chunk)
    return out

def vapp_field_map(raw):
    """判分条目 -> {字段ID: 原始值(可能 ■ 多值)}。"""
    fm = {}
    for e in decode_vapp_entries(raw):
        nav, fld, val = e.split("□")
        fm.setdefault(fld.strip(), val)
    return fm

def _extract_paper_zip(zip_bytes, dest):
    """把学生交上来的考生文件夹 zip 解到 dest。文件名 UTF-8 优先，失败再试 GBK；
    并通过 zip external_attr(DOS 属性位) 恢复 Windows 只读/隐藏属性。"""
    import zipfile
    import io
    import ctypes

    def _apply_attr(path, info):
        # zip DOS 属性位与 Windows FILE_ATTRIBUTE 低位定义一致：bit0=只读 bit1=隐藏
        try:
            dos = (int(getattr(info, "external_attr", 0) or 0) >> 16) & 0xFFFF
            attr = dos & 0x03
            if attr and os.path.abspath(str(path)) != os.path.abspath(dest):
                ctypes.windll.kernel32.SetFileAttributesW(str(path), attr)
        except Exception:
            pass

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            name = info.filename
            # 客户端打包时已用正确 Unicode 名；若仍是 CP437 乱码则按 GBK 还原
            if info.flag_bits & 0x800 == 0:
                try:
                    name = name.encode("cp437").decode("gbk")
                except Exception:
                    pass
            # 规范化路径，拒绝跳出 dest
            name = name.replace("\\", "/").lstrip("/")
            if not name or name.endswith("/"):
                target = os.path.join(dest, name.replace("/", os.sep)) if name else dest
                os.makedirs(target, exist_ok=True)
                _apply_attr(target, info)
                continue
            if ".." in name.split("/"):
                continue
            target = os.path.join(dest, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            _apply_attr(target, info)


def grade_by_engine(*, timu_type, sub_type, dfdlist, full_score=20.0,
                    vapp_log=None, student_file=None, file_ext=None,
                    paper_zip=None,
                    timu_id="", timu_title="", timu_desc="",
                    stand_answer=None, arg1=None, arg2=None, arg3=None,
                    timeout=_ENGINE_TIMEOUT):
    """同步执行一次引擎判分（调用方应在线程池中调用，避免阻塞事件循环）。

    参数:
      timu_type/sub_type : 题型/子题型(int)
      dfdlist            : 判分点列表（题库 dfdList 反序列化后的 camelCase dict 列表）
      vapp_log           : 虚拟程序判分文件原始字节（type 11/12/31/32）
      student_file       : 学生作答文件字节（Office/PSD/Access）
      file_ext           : 学生文件扩展名（含点，如 .docx）；缺省按题型推断
      paper_zip          : 考生文件夹 zip（文件管理题 subType=2：引擎按 paperFolder 文件系统判分）
    返回 dict:
      {auto:bool, score:float, total:int, hits:[...], miss:[...], note:str,
       details:[...], error:str}
      auto=False 表示引擎无法判（无判分点/缺文件/异常），调用方应回退旧判分器。
    """
    result = {"auto": False, "score": 0.0, "total": 0, "hits": [], "miss": [],
              "note": "", "details": [], "error": ""}
    tt = int(timu_type)
    if not engine_available():
        result["error"] = "判分引擎未部署(judge/JudgeHost.exe)"
        return result
    if not dfdlist:
        result["error"] = "该题无判分点(DFD)，引擎跳过"
        return result

    # type11/12/32 的 DFD keyword 在服务器端常为空，判分引擎按 keyword 匹配判分文件记录，
    # keyword 空→0分（打印机/网络/宽带题期望值多为 stu*/user* 英文）。用 haveFuction→keyword 补全。
    # type11 经 _ENGINE_TYPE_MAP 映射到 31 调引擎，但 keyword 补全必须在映射前按原始 tt 处理。
    if tt in (11, 12, 31, 32):
        for d in dfdlist:
            if not d.get("keyword"):
                hf = d.get("haveFuction")
                if hf in _HF_KEYWORD_MAP:
                    d["keyword"] = _HF_KEYWORD_MAP[hf]

    work = tempfile.mkdtemp(prefix="judge_")
    try:
        paper_folder = work
        local_file = ""
        if tt in (17, 37):
            # 原版判分 DLL 不含 Access 逐点判分（StartGrade 对 Access 直接跳过），
            # 走自建 Python DAO 判分器（表/字段/属性/关系/查询/记录；窗体/报表控件点需完整 Access）。
            if not student_file:
                result["error"] = "缺少学生作答文件"
                return result
            apath = os.path.join(work, "answer.accdb")
            with open(apath, "wb") as f:
                f.write(student_file)
            try:
                import accdb_grader
                agr = accdb_grader.grade(dfdlist, apath)
            except Exception as e:
                result["error"] = "Access判分异常: %s: %s" % (type(e).__name__, e)
                return result
            raw_sum = sum(float(x.get("score") or 0) for x in dfdlist)
            scale = (float(full_score) / raw_sum) if raw_sum > 0 else 1.0
            details, hits, miss = [], [], []
            pend = 0
            for dfd, ad in zip(dfdlist, agr["details"]):
                stt = ad.get("status")
                if stt in ("pending", "unsupported"):
                    pend += 1
                get = float(ad.get("get") or 0) * scale
                full = float(dfd.get("score") or 0) * scale
                exp = dfd.get("yuqizhi")
                det = {
                    "dfdId": dfd.get("dfdId"), "code": dfd.get("haveFuction"),
                    "desc": dfd.get("timuDesc") or ("动作码%s" % dfd.get("haveFuction")),
                    "expect": ("" if exp is None else str(exp)),
                    "keyword": dfd.get("keyword"), "startMark": dfd.get("startMark"),
                    "full": round(full, 3), "get": round(get, 3), "correct": get > 0.0001,
                    "student": ad.get("student") or "", "standard": ad.get("standard") or ""}
                details.append(det)
                if det["correct"]:
                    hits.append(det["desc"])
                elif stt not in ("pending", "unsupported"):
                    miss.append(det["desc"])
            score = round(float(agr["score"]) * scale, 1)
            note = "Access Python判分：%d/%d 点达标，得分 %.1f/%.0f" % (
                len(hits), len(dfdlist), score, float(full_score))
            if pend:
                note += "；%d 个窗体/报表/控件点需完整 Microsoft Access，未自动判分" % pend
            result.update(auto=True, score=score, total=len(dfdlist),
                          hits=hits, miss=miss, details=details, note=note)
            return result
        if tt in VAPP_TYPES:
            # 文件管理题（subType=2）：以考生文件夹内容为判分依据；
            # 可同时附带 vapp_log（无则写空文件占位，引擎主要看 paperFolder 文件）。
            if paper_zip:
                try:
                    _extract_paper_zip(paper_zip, work)
                except Exception as e:
                    result["error"] = "考生文件夹解压失败: %s" % e
                    return result
            if vapp_log:
                name = vapp_log_name(tt, sub_type)
                with open(os.path.join(work, name), "wb") as f:
                    f.write(vapp_log)
            elif not paper_zip:
                result["error"] = "缺少虚拟程序判分文件"
                return result
            else:
                # 仅有考生文件夹：写空占位 log，避免引擎因缺文件名报错
                name = vapp_log_name(tt, sub_type)
                open(os.path.join(work, name), "wb").close()
        elif tt in FILE_TYPES:
            if not student_file:
                result["error"] = "缺少学生作答文件"
                return result
            ext = (file_ext or _EXT.get(tt) or ".bin").lower()
            local_file = os.path.join(work, "answer" + ext)
            with open(local_file, "wb") as f:
                f.write(student_file)
        else:
            # 其它题型（Python/C/MySQL）不在此引擎桥接处理
            result["error"] = "题型 %d 不走文件引擎" % tt
            return result

        inp = {
            "dllDir": BIN_DIR,
            "timuType": _ENGINE_TYPE_MAP.get(tt, tt),
            "subType": int(sub_type or 1),
            "timuScore": float(full_score),
            "timuID": str(timu_id or ""),
            "timuTitle": timu_title or "",
            "timuDesc": timu_desc or "",
            "standAnswer": stand_answer,
            "paperFolder": paper_folder,
            "localFilePath": local_file,
            "programArg1": arg1,
            "programArg2": arg2,
            "programArg3": arg3,
            "dfdList": dfdlist,
        }
        ipath = os.path.join(work, "input.json")
        with open(ipath, "w", encoding="utf-8") as f:
            json.dump(inp, f, ensure_ascii=False)

        # Office COM 互斥按“应用”粒度：同一应用(WinWord/Excel/PowerPnt)判分会
        # KillProcess 全局该应用进程，必须互斥；不同应用之间互不影响，可并行。
        # PSD(Aspose.PSD)/vapp 不碰 Office COM，完全自由并行，不加任何锁。
        app_proc = _OFFICE_PROC.get((file_ext or _EXT.get(tt) or "").lower())
        if tt in FILE_TYPES and tt not in _NO_LOCK_TYPES and app_proc:
            with _office_locks[app_proc]:
                _prekill_office(tt, file_ext)
                proc = subprocess.run([JUDGE_EXE, ipath],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=timeout)
        else:
            # vapp / PSD / 无 Office COM 依赖的文件题：不加锁，可与 Office 题并行
            proc = subprocess.run([JUDGE_EXE, ipath],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout)
        out_text = proc.stdout.decode("utf-8", "ignore").strip()
        try:
            out = json.loads(out_text)
        except Exception as e:
            result["error"] = "引擎输出解析失败: %s; stderr=%s; out=%s" % (
                e, proc.stderr.decode("utf-8", "ignore")[:300], out_text[:300])
            return result

        if not out.get("ok"):
            result["error"] = out.get("error") or "引擎判分返回失败"
            return result

        rec_by_id = {}
        for rec in out.get("records") or []:
            rec_by_id[str(rec.get("dfdNo"))] = rec

        # DFD 原始分合计 → 本题满分的缩放系数（引擎按 timuScore 等比缩放）
        raw_sum = sum(float(x.get("score") or 0) for x in dfdlist)
        scale = (float(full_score) / raw_sum) if raw_sum > 0 else 1.0

        OFFICE_RETYPE = {13, 14, 15, 33, 43, 44, 45}
        details, hits, miss = [], [], []
        office_score = 0.0
        for dfd in dfdlist:
            rec = rec_by_id.get(str(dfd.get("dfdId")))
            det = _build_detail(dfd, rec, scale)
            if tt in OFFICE_RETYPE:
                # 引擎枚举比较有类型 bug：正确性与得分以 Python 等价判定为准
                ok = office_point_match(det["student"], dfd.get("yuqizhi"))
                det["correct"] = ok
                det["get"] = round(det["full"], 3) if ok else 0.0
                if ok:
                    office_score += det["full"]
            details.append(det)
            if det["correct"]:
                hits.append(det["desc"])
            else:
                extra = det["expect"]
                miss.append(det["desc"] + ("（预期:%s，学生:%s）" % (
                    det["expect"], det["student"] or "空") if extra not in (None, "") else ""))

        # vapp 题：引擎给分但不回填 studentAnswer，自行解码判分文件补齐，
        # 让成绩明细能看到学生实际填的值（宽带/VPN 用户名、共享位置 C:\Windows 等）。
        if tt in VAPP_TYPES and vapp_log:
            try:
                fmap = vapp_field_map(vapp_log)
                for det in details:
                    if det["student"]:
                        continue
                    kw = det["keyword"]
                    if not kw or kw not in fmap:
                        continue
                    val = fmap[kw]
                    if "■" in val:
                        # printShareSetup = 共享名■位置■注释：按本点期望值定位对应段
                        segs = val.split("■")
                        exp = det["expect"]
                        pick = [s for s in segs if s and exp and (exp in s or s in exp)]
                        det["student"] = pick[0] if pick else val
                    else:
                        det["student"] = val
            except Exception:
                pass

        if tt in OFFICE_RETYPE:
            score = round(office_score, 1)
            note = "Office判分：%d/%d 个判分点达标，得分 %.1f/%.0f" % (
                len(hits), len(dfdlist), score, float(full_score))
        else:
            score = round(float(out.get("score") or 0.0), 1)
            note = "原版引擎判分：%d/%d 个判分点达标，得分 %.1f/%.0f" % (
                len(hits), len(dfdlist), score, float(full_score))
        result.update({
            "auto": True,
            "score": score,
            "total": len(dfdlist),
            "hits": hits,
            "miss": miss,
            "details": details,
            "note": note,
        })
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "判分引擎超时(%ds)" % timeout
        return result
    except Exception as e:
        result["error"] = "判分桥接异常: %s: %s" % (type(e).__name__, e)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
