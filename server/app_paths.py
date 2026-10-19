# -*- coding: utf-8 -*-
"""统一资源基目录：
- 源码运行：本文件所在目录（app/server）；
- PyInstaller 打包后：可执行文件所在目录（exam.db / judge / runtime / operate_files 等
  可变或外部资源一律放在 exe 同级，保证成绩持久化、不依赖会被删除的临时解压目录）。
"""
import os
import sys

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
