# -*- coding: utf-8 -*-
"""网页收藏/另存类操作题（subType=6）判分：
- hf251 文件另存：检查考生文件夹(paperZip)内是否存在题目指定文件名；
- hf3381 添加收藏：检查真实 Edge 收藏夹中是否存在题目指定名称（精确，要求改名）。
"""
import io
import os
import re
import zipfile


def _norm(s):
    s = str(s or "").lower()
    tbl = {"，": ",", "。": ".", "；": ";", "：": ":", "“": '"', "”": '"',
           "（": "(", "）": ")"}
    for k, v in tbl.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", "", s)


def _target(yuqizhi):
    s = str(yuqizhi or "")
    if "//" in s:
        s = s.split("//", 1)[1]
    return s.strip()


def grade_sub6(dfdlist, paper_zip_bytes, favorites, full_score):
    names = set()
    try:
        z = zipfile.ZipFile(io.BytesIO(paper_zip_bytes))
        for n in z.namelist():
            if n and not n.endswith("/"):
                names.add(_norm(os.path.basename(n)))
    except Exception:
        pass
    favs = [_norm(f.get("name")) for f in (favorites or [])]
    details = []
    raw_sum = raw_get = 0.0
    hits, miss = [], []
    for d in dfdlist:
        hf = int(d.get("haveFuction") or 0)
        pt = float(d.get("score") or 0)
        raw_sum += pt
        tgt = _norm(_target(d.get("yuqizhi")))
        expect = _target(d.get("yuqizhi"))
        if hf == 251:
            ok = tgt in names
            student = expect if ok else "未在试题文件夹找到另存文件"
        elif hf == 3381:
            ok = tgt in favs
            student = expect if ok else "未在 Edge 收藏夹找到指定名称"
        else:
            ok = False
            student = "未知判分点"
        if ok:
            raw_get += pt
            hits.append(d.get("timuDesc"))
            get = pt
        else:
            miss.append(d.get("timuDesc"))
            get = 0.0
        details.append({"dfdNo": d.get("dfdId"), "code": hf, "point": d.get("timuDesc"),
                        "get": get, "full": pt, "correct": ok,
                        "student": student, "expect": expect})
    score = round(full_score * raw_get / raw_sum, 1) if raw_sum else 0
    return {"auto": True, "score": score, "total": len(dfdlist),
            "hits": hits, "miss": miss,
            "note": "网页收藏/另存判分：命中 %d/%d" % (len(hits), len(dfdlist)),
            "details": details}
