# -*- coding: utf-8 -*-
"""操作题自动判分：解析 OOXML 格式属性，对照题干要求给分。
只读 docx/xlsx/pptx 的 XML，不依赖 Office。"""
import zipfile, re, io

FONT_MAP = {"楷体":"楷体","黑体":"黑体","宋体":"宋体","微软雅黑":"微软雅黑","新宋体":"新宋体",
            "仿宋":"仿宋","幼圆":"幼圆","隶书":"隶书","华文行楷":"华文行楷","Arial":"Arial"}
# 字号中文名 -> 半磅(w:sz val)
SIZE_MAP = {"初号":84,"小初":72,"一号":52,"小一":48,"二号":44,"小二":36,
            "三号":32,"小三":30,"四号":28,"小四":24,"五号":21,"小五":18,
            "六号":15,"小六":13,"七号":11,"八号":10}
COLOR_MAP = {"红色":"FF0000","蓝色":"0000FF","绿色":("00B050","008000","00B050"),"黄色":"FFFF00",
             "紫色":"800080","橙色":("FF6600","FFC000","ED7D31"),"黑色":"000000","白色":"FFFFFF",
             "灰色":"808080","粉红":"FFC0CB","深红":"C00000","深灰":"595959"}

def docx_features(data):
    """从 docx 字节提取格式特征"""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    allxml = ""
    for n in names:
        if n.endswith('.xml') and ('word/' in n):
            allxml += z.read(n).decode('utf-8','ignore') + "\n"
    feats = {
        "fonts": set(re.findall(r'w:(?:ascii|eastAsia|hAnsi)="([^"]+)"', allxml)),
        "sizes": set(re.findall(r'<w:sz w:val="(\d+)"', allxml)),
        "colors": set(re.findall(r'<w:color w:val="([0-9A-Fa-f]{6})"', allxml)),
        "before_lines": set(re.findall(r'w:beforeLines="(\d+)"', allxml)),
        "after_lines": set(re.findall(r'w:afterLines="(\d+)"', allxml)),
        "line_rules": set(re.findall(r'<w:spacing[^/]*w:line="(\d+)"', allxml)),
        "first_line_chars": set(re.findall(r'w:firstLineChars="(\d+)"', allxml)),
        "left_chars": set(re.findall(r'w:leftChars="(\d+)"', allxml)),
        "right_chars": set(re.findall(r'w:rightChars="(\d+)"', allxml)),
        "images": re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"', allxml),
        "hyperlinks": set(re.findall(r'r:id', allxml)),
        "urls": set(re.findall(r'(https?://[^\s"<]+)', allxml)),
        "tables": len(re.findall(r'<w:tbl>', allxml)),
        "bold": '<w:b/>' in allxml or '<w:b ' in allxml,
        "italic": '<w:i/>' in allxml or '<w:i ' in allxml,
        "underline": '<w:u ' in allxml,
        "strike": '<w:strike' in allxml,
        "jc_center": 'w:val="center"' in allxml,
        "jc_right": 'w:val="right"' in allxml,
        "jc_justify": 'w:val="both"' in allxml,
        "pgMar": re.search(r'<w:pgMar[^/]*/>', allxml),
        "cols": re.findall(r'<w:cols[^/]*w:num="(\d+)"', allxml),
        "dropCap": 'w:dropCap' in allxml,
        "shd": len(re.findall(r'<w:shd ', allxml)),
        "pageBorders": '<w:pgBorders' in allxml,
        "page_num": bool(re.search(r'instrText[^>]*>\s*PAGE', allxml)) or
                    bool(re.search(r'fldSimple[^>]*\bPAGE\b', allxml)),
        "full_text": re.sub(r'<[^>]+>','', allxml),
    }
    # 页眉页脚
    header_text = ""; footer_text = ""
    for n in names:
        if 'header' in n and n.endswith('.xml'):
            header_text += re.sub(r'<[^>]+>','', z.read(n).decode('utf-8','ignore'))
        if 'footer' in n and n.endswith('.xml'):
            footer_text += re.sub(r'<[^>]+>','', z.read(n).decode('utf-8','ignore'))
    feats["header"] = header_text
    feats["footer"] = footer_text
    return feats

def xlsx_features(data):
    z=zipfile.ZipFile(io.BytesIO(data)); names=z.namelist()
    ss=[]
    if 'xl/sharedStrings.xml' in names:
        sx=z.read('xl/sharedStrings.xml').decode('utf-8','ignore')
        ss=re.findall(r'<t[^>]*>([^<]*)</t>', sx)
    all_text=" ".join(ss)
    fills=set(); has_formula=False; sheet_count=0; colors=set()
    charts=0; merged=0
    for n in names:
        if re.match(r'xl/worksheets/sheet\d+\.xml$', n):
            sheet_count+=1
            x=z.read(n).decode('utf-8','ignore')
            all_text += " " + " ".join(re.findall(r'<v>([^<]*)</v>', x))
            if '<f>' in x or '<f ' in x: has_formula=True
            fills |= set(re.findall(r'fgColor rgb="([0-9A-Fa-f]{8})"', x))
            colors |= set(re.findall(r'color rgb="([0-9A-Fa-f]{8})"', x))
            merged += len(re.findall(r'<mergeCell ', x))
        if 'charts/chart' in n and n.endswith('.xml'):
            charts+=1
    return {"text":all_text, "fills":fills, "colors":colors,
            "has_formula":has_formula, "sheets":sheet_count, "full_text":all_text,
            "charts":charts, "merged":merged,
            "fonts":set(), "sizes":set(), "before_lines":set(), "after_lines":set(),
            "line_rules":set(), "first_line_chars":set(), "left_chars":set(), "right_chars":set(),
            "images":[], "urls":set(), "tables":0, "header":""}

def pptx_features(data):
    z=zipfile.ZipFile(io.BytesIO(data)); names=z.namelist()
    slides=[n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    text=""; pics=0; colors=set(); anim_filters=[]; transitions=set(); links=0; media=0
    for n in slides:
        x=z.read(n).decode('utf-8','ignore')
        text += " " + " ".join(re.findall(r'<a:t>([^<]*)</a:t>', x))
        pics += len(re.findall(r'<p:pic>', x))
        colors |= set(re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', x))
        anim_filters += re.findall(r'<p:animEffect[^>]*filter="([^"]+)"', x)
        transitions |= set(re.findall(r'<p:transition[^>]*>\s*<p:(\w+)/?>', x))
        transitions |= set(re.findall(r'<p:transition[^>]*/>', x))
        links += len(re.findall(r'<a:hlinkClick', x))
    for n in names:
        if 'media/' in n and re.search(r'\.(mp3|wav|mp4|wmv|avi|m4a)$', n, re.I):
            media+=1
    return {"text":text, "slides":len(slides), "pics":pics, "full_text":text,
            "anim_filters":anim_filters, "transitions":transitions,
            "links":links, "media":media,
            "fills":set(), "colors":colors, "has_formula":False, "sheets":len(slides),
            "fonts":set(), "sizes":set(), "before_lines":set(), "after_lines":set(),
            "line_rules":set(), "first_line_chars":set(), "left_chars":set(), "right_chars":set(),
            "images":[], "urls":set(), "tables":0, "header":""}

def grade_xlsx_pptx(feats, stem, full_score=20.0):
    """表格/PPT 的简化判分：文本内容 + 颜色 + 公式/图片/幻灯片数"""
    checks=[]; text=feats["full_text"]
    for q in re.findall(r'[“"]([^"”]{3,40})[”"]', stem):
        checks.append((f"含「{q[:18]}」", q in text))
    for cn,vals in COLOR_MAP.items():
        if cn+"色" in stem:
            if isinstance(vals,str): vals=(vals,)
            ok=any(v.upper() in {c.upper() for c in feats["colors"]} for v in vals)
            checks.append((f"颜色「{cn}」", ok))
    for kw,label in [("求和","有求和/公式"),("计算","有公式"),("平均","有平均"),("排序","有公式/操作")]:
        if kw in stem and "has_formula" in feats:
            checks.append((label, feats["has_formula"]))
    if "图表" in stem or "柱形图" in stem or "饼图" in stem or "折线图" in stem:
        checks.append(("已插入图表", feats.get("charts",0)>0))
    if "合并" in stem:
        checks.append(("有合并单元格", feats.get("merged",0)>0))
    m=re.search(r'(\d+)\s*张幻灯片', stem)
    if m and "slides" in feats:
        need=int(m.group(1)); ok=abs(feats["slides"]-need)<=1
        checks.append((f"{need}张幻灯片", ok))
    if "插入图片" in stem or "图片" in stem and "pics" in feats:
        checks.append(("已插入图片", feats["pics"]>0))
    ANIM_MAP = [
        ("飞入","fly"),("擦除","wipe"),("百叶窗","blinds"),("棋盘","checker"),
        ("溶解","dissolve"),("缩放","zoom"),("弹跳","bounce"),("旋转","spin"),
        ("淡出","fade"),("淡入","fade"),("劈裂","split"),("伸展","stretch"),
        ("阶梯","stairs"),("出现","appear"),("随机","random"),("折叠","wheel"),
    ]
    all_filters = " ".join(feats.get("anim_filters",[]))
    for cn, key in ANIM_MAP:
        if cn in stem and "anim_filters" in feats:
            checks.append((f"动画「{cn}」", key in all_filters))
    if "切换" in stem and "transitions" in feats:
        checks.append(("已设置切换效果", len(feats.get("transitions",set()))>0))
    if "超链接" in stem or "链接" in stem:
        checks.append(("有超链接", feats.get("links",0)>0))
    if "音频" in stem or "视频" in stem or "媒体" in stem:
        checks.append(("已插入媒体", feats.get("media",0)>0))
    if not checks:
        return None,0,[],["无可识别要求"]
    hits=[c[0] for c in checks if c[1]]; miss=[c[0] for c in checks if not c[1]]
    return round(full_score*len(hits)/len(checks),1), len(checks), hits, miss

def grade_against_requirements(feats, stem, full_score=20.0):
    """根据题干要求逐项检查，返回 (score, total, hit_list, miss_list)"""
    checks = []  # (描述, 是否命中)
    text = feats["full_text"]
    # 1. 字体
    for cn, en in FONT_MAP.items():
        if cn in stem:
            ok = any(en in f for f in feats["fonts"])
            checks.append((f"字体含「{cn}」", ok))
    # 2. 字号（先匹配长词，匹配到后从题干扣除，避免"小四号"同时触发"四号"）
    stem_rem = stem
    for cn in sorted(SIZE_MAP.keys(), key=len, reverse=True):
        halfpt = SIZE_MAP[cn]
        if cn in stem_rem:
            ok = str(halfpt) in feats["sizes"]
            checks.append((f"字号含「{cn}」({halfpt/2}pt)", ok))
            stem_rem = stem_rem.replace(cn, "", 1)
    # 3. 颜色
    for cn, vals in COLOR_MAP.items():
        if cn+"色" in stem or (cn in stem and cn in ("红色","蓝色","黑色","白色","黄色","紫色","灰色")):
            if isinstance(vals, str): vals=(vals,)
            ok = any(v.upper() in {c.upper() for c in feats["colors"]} for v in vals)
            checks.append((f"颜色含「{cn}」", ok))
    # 4. 行距
    line_map = {"单倍":240,"1.5倍":360,"2倍":480,"0.5倍":120}
    for cn, val in line_map.items():
        if cn+"行距" in stem or (cn in stem and "倍" in cn):
            ok = str(val) in feats["line_rules"]
            checks.append((f"行距「{cn}」", ok))
    # 5. 段前/段后 (beforeLines/afterLines, 单位1/100行)
    m = re.search(r'段前(\d+\.?\d*)行', stem)
    if m:
        need = int(float(m.group(1))*100)
        ok = str(need) in feats["before_lines"]
        checks.append((f"段前{m.group(1)}行", ok))
    m = re.search(r'段后(\d+\.?\d*)行', stem)
    if m:
        need = int(float(m.group(1))*100)
        ok = str(need) in feats["after_lines"]
        checks.append((f"段后{m.group(1)}行", ok))
    # 6. 首行缩进
    m = re.search(r'首行缩进(\d+\.?\d*)字符', stem)
    if m:
        need = int(float(m.group(1))*100)
        ok = str(need) in feats["first_line_chars"]
        checks.append((f"首行缩进{m.group(1)}字符", ok))
    # 7. 左右缩进
    m = re.search(r'左右缩进(\d+\.?\d*)字符', stem)
    if m:
        need = int(float(m.group(1))*100)
        ok = str(need) in feats["left_chars"] and str(need) in feats["right_chars"]
        checks.append((f"左右缩进{m.group(1)}字符", ok))
    # 8. 图片尺寸 (1cm=360000 EMU)
    m = re.search(r'高\s*(\d+\.?\d*)\s*厘米.*?宽\s*(\d+\.?\d*)\s*厘米', stem) or \
        re.search(r'宽\s*(\d+\.?\d*)\s*厘米.*?高\s*(\d+\.?\d*)\s*厘米', stem)
    if m:
        h_cm = float(m.group(1)); w_cm = float(m.group(2))
        need_cy = int(h_cm*360000); need_cx = int(w_cm*360000)
        ok = any(abs(int(cx)-need_cx)<36000 and abs(int(cy)-need_cy)<36000 for cx,cy in feats["images"])
        checks.append((f"图片{w_cm}x{h_cm}cm", ok))
    # 9. 页眉文字
    for q in re.findall(r'页眉[为是]?[“"]([^"”]{2,30})[”"]', stem):
        ok = q in feats["header"]
        checks.append((f"页眉含「{q}」", ok))
    # 10. 超链接 URL
    for url in re.findall(r'(https?://[^\s，。）)"]+)', stem):
        ok = any(url.split("//")[-1].split("/")[0] in u for u in feats["urls"]) or url in text
        checks.append((f"超链接{url[:30]}", ok))
    # 11. 加粗/倾斜/下划线/删除线
    if "加粗" in stem or "B" in stem and "加粗" in stem:
        checks.append(("有加粗", feats["bold"]))
    if "倾斜" in stem or "斜体" in stem:
        checks.append(("有倾斜", feats["italic"]))
    if "下划线" in stem:
        checks.append(("有下划线", feats["underline"]))
    if "删除线" in stem:
        checks.append(("有删除线", feats["strike"]))
    # 12. 对齐
    if "居中" in stem:
        checks.append(("居中对齐", feats["jc_center"]))
    if "右对齐" in stem:
        checks.append(("右对齐", feats["jc_right"]))
    if "两端对齐" in stem:
        checks.append(("两端对齐", feats["jc_justify"]))
    # 13. 表格/页边距/分栏/首字下沉/底纹/页面边框
    m=re.search(r'(\d+)\s*[列个].{0,4}表格|(\d+)\s*行', stem)
    if "表格" in stem:
        checks.append(("有表格", feats["tables"]>0))
    if "页边距" in stem:
        checks.append(("设置了页边距", feats["pgMar"] is not None))
    if "分栏" in stem:
        checks.append(("有分栏", len(feats["cols"])>0))
    if "首字下沉" in stem:
        checks.append(("首字下沉", feats["dropCap"]))
    if "底纹" in stem or "底纹" in stem:
        checks.append(("有底纹", feats["shd"]>0))
    if "页面边框" in stem:
        checks.append(("有页面边框", feats["pageBorders"]))
    # 14. 页脚/页码
    for q in re.findall(r'页脚[为是]?[“"]([^"”]{2,30})[”"]', stem):
        checks.append((f"页脚含「{q}」", q in feats["footer"]))
    if "页码" in stem:
        checks.append(("插入了页码", feats.get("page_num", False) or len(feats["footer"])>0))
    # 15. 引号正文是否出现在文档
    for q in re.findall(r'[“"]([^"”]{6,40})[”"]', stem):
        if q not in ("楷体","黑体") and not q.startswith("http"):
            ok = q in text
            checks.append((f"正文含「{q[:20]}」", ok))
    if not checks:
        return None, 0, [], ["题干无可识别格式要求"]
    hits = [c[0] for c in checks if c[1]]
    miss = [c[0] for c in checks if not c[1]]
    rate = len(hits)/len(checks)
    score = round(full_score*rate, 1)
    return score, len(checks), hits, miss
