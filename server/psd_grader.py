# -*- coding: utf-8 -*-
"""Photoshop(.psd) 操作题自动判分（结构检测）。
用 psd-tools 读取：画布尺寸、颜色模式、图层数量/顺序/名称、文字图层、
图层样式（投影/内阴影/内外发光/斜面浮雕/描边/叠加/光泽）、文件大小。
题干常见“文件大小超过5M不得分”作为硬门槛；“保持图层数量与顺序”与基线对比。
滤镜渲染、像素级视觉效果无法从 PSD 结构可靠判定，note 中提示以样张为准。
任何读取异常返回 score=None 交由上层兜底。"""
import os
import re
from io import BytesIO

# 题干中文效果词 -> psd-tools 效果类名（小写）
EFFECT_MAP = [
    ("投影", "dropshadow"), ("内阴影", "innershadow"),
    ("外发光", "outerglow"), ("内发光", "innerglow"),
    ("斜面和浮雕", "bevelemboss"), ("斜面浮雕", "bevelemboss"),
    ("浮雕效果", "bevelemboss"), ("斜面", "bevelemboss"),
    ("颜色叠加", "coloroverlay"), ("渐变叠加", "gradientoverlay"),
    ("图案叠加", "patternoverlay"), ("光泽效果", "satin"),
    ("描边", "stroke"),
]
MODE_WORDS = {"RGB": "RGB", "CMYK": "CMYK", "灰度": "GRAYSCALE", "索引": "INDEXED", "位图": "BITMAP"}


def psd_features(data):
    from psd_tools import PSDImage
    psd = PSDImage.open(BytesIO(data))
    layers = list(psd.descendants())
    feats = {
        "width": psd.width, "height": psd.height,
        "mode": (psd.color_mode.name if hasattr(psd.color_mode, "name") else str(psd.color_mode)).upper(),
        "size": len(data),
        "layer_count": len(layers),
        "layer_names": [],
        "texts": [],
        "effects": set(),
    }
    for lyr in layers:
        try:
            feats["layer_names"].append(lyr.name)
        except Exception:
            feats["layer_names"].append("")
        if getattr(lyr, "kind", "") == "type":
            try:
                if lyr.text:
                    feats["texts"].append(lyr.text)
            except Exception:
                pass
        try:
            if lyr.has_effects():
                for it in lyr.effects.items:
                    try:
                        if getattr(it, "enabled", True):
                            feats["effects"].add(type(it).__name__.lower())
                    except Exception:
                        pass
        except Exception:
            pass
    feats["all_text"] = " ".join(feats["texts"])
    return feats


def grade_psd(data, stem, full_score=20.0, base_data=None):
    """data/base_data 为 .psd 字节。返回 (score,total,hits,miss,note)。"""
    try:
        feats = psd_features(data)
    except Exception as e:
        return None, 0, [], [], "PSD读取失败: %s" % e
    base = None
    if base_data:
        try:
            base = psd_features(base_data)
        except Exception:
            base = None

    s = stem or ""
    # 只在“操作/要求”正文里找效果，避免样张描述干扰；无明显分隔则用全文
    body = s
    m = re.search(r"操作[：:]", s)
    if m:
        body = s[m.start():]
    checks = []

    # 1) 文件大小硬门槛：超过 X M 不得分
    size_lim = None
    mm = re.search(r"超过\s*(\d+(?:\.\d+)?)\s*M(?:B)?\s*不得分", s)
    if mm:
        size_lim = float(mm.group(1)) * 1024 * 1024
    if size_lim:
        ok = feats["size"] <= size_lim
        checks.append(("文件大小不超过 %.0fM" % (size_lim / 1024 / 1024), ok))
        if not ok:
            return 0, 1, [], ["文件大小 %.1fM 超过 %.0fM，按题目要求不得分" % (
                feats["size"] / 1024 / 1024, size_lim / 1024 / 1024)], \
                "PSD判分：文件超过大小限制，本题 0 分（结构未细判）"

    # 2) 图层样式效果
    added = set()
    for zh, en in EFFECT_MAP:
        if zh in body and en not in added:
            want = en in feats["effects"]
            checks.append(("添加图层样式“%s”" % zh, want))
            added.add(en)

    # 3) 文字内容（题干要求输入的文字应存在于文字图层）
    text_tokens = set()
    if re.search(r"文字|文本|字工具|输入字", s):
        for t in re.findall(r"[“\"'‘]([^”\"'’\n]{1,20})[”\"'’]", s):
            # 仅保留像文字内容的（含中文/字母/数字），排除文件名与纯术语
            if re.search(r"[一-龥A-Za-z0-9]", t) and not re.search(r"\.(PSD|psd|JPG|jpg|PNG|png)$", t):
                text_tokens.add(t)
        for t in re.finditer(r"(?:输入|添加|写上|打出)\s*文字?\s*[“\"'‘]?([^”\"'’\n。，；]{1,20})", s):
            text_tokens.add(t.group(1).strip())
    for t in text_tokens:
        checks.append(("包含文字“%s”" % t, t in feats["all_text"]))

    # 4) 图层数量/顺序保持
    if re.search(r"不能新建|保持\s*题目中?\s*图层|图层的数量与顺序|图层数量", s) and base is not None:
        same_count = feats["layer_count"] == base["layer_count"]
        same_order = feats["layer_names"] == base["layer_names"]
        checks.append(("保持图层数量与顺序", same_count and same_order))

    # 5) 画布尺寸（题干要求设置为某尺寸）
    ms = re.search(r"(?:设置|调整|改为|修改为|图像大小为|画布大小为|大小为)\s*[^。\n]{0,10}?(\d{2,5})\s*[×x*X]\s*(\d{2,5})", s)
    if ms:
        w, h = int(ms.group(1)), int(ms.group(2))
        ok = (feats["width"] == w and feats["height"] == h) or (feats["width"] == h and feats["height"] == w)
        checks.append(("画布尺寸 %d×%d" % (w, h), ok))
    elif re.search(r"不要修改[^。\n]*图像?\s*尺寸|保持[^。\n]*尺寸", s) and base is not None:
        checks.append(("保持原图像尺寸", feats["width"] == base["width"] and feats["height"] == base["height"]))

    # 6) 颜色模式（要求设置/转换）
    for zh, en in MODE_WORDS.items():
        if re.search(r"(?:设置|转换为|改为|调整为|图像模式为|模式为)\s*[“\"'’]?" + zh + r"(?:模式)?", s):
            checks.append(("颜色模式为 %s" % zh, feats["mode"] == en))
            break
    if re.search(r"不要修改[^。\n]*颜色模式|保持[^。\n]*颜色模式", s) and base is not None:
        checks.append(("保持原颜色模式", feats["mode"] == base["mode"]))

    uniq, seen = [], set()
    for desc, hit in checks:
        if desc not in seen:
            seen.add(desc)
            uniq.append((desc, hit))
    checks = uniq
    if not checks:
        return None, 0, [], [], "PSD题：未能从题干提取可结构核验的要求（视觉效果以样张为准）"

    hits = [d for d, ok in checks if ok]
    miss = [d for d, ok in checks if not ok]
    total = len(checks)
    score = round(full_score * len(hits) / total, 1)
    note = "PSD结构判分 命中%d/%d 项；滤镜与像素效果以样张为准" % (len(hits), total)
    return score, total, hits, miss, note
