# -*- coding: utf-8 -*-
"""
服务端 GUI：一键启动/暂停 TCP 服务 + 实时日志。
零依赖（仅 Python 标准库 tkinter）。启动: pythonw server_gui.py
"""
import os, sys, queue, threading, asyncio, traceback, sqlite3, hashlib, secrets, re
import subprocess, json, urllib.request

# pythonw 下 stdout 为 None，先兜底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as srv

import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox

PORT_DEFAULT = 5943

class StdoutToQueue:
    """把 print 输出转发到队列，供 GUI 轮询显示"""
    def __init__(self, q): self.q = q
    def write(self, text):
        if text and text.strip("\r\n\t "):
            self.q.put(text.rstrip("\n"))
    def flush(self): pass
    def isatty(self): return False

class ServerGUI:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.loop = None
        self.srv = None
        self.worker = None
        self.running = False

        root.title("天天乐学教学系统 · 服务端控制台")
        root.geometry("900x680")
        root.minsize(700, 560)
        root.configure(bg="#F5F7FA")

        # 顶部控制区
        top = tk.Frame(root, bg="#2D3748", height=70)
        top.pack(fill=tk.X, side=tk.TOP)
        tk.Label(top, text="教学服务端", bg="#2D3748", fg="white",
                 font=("Microsoft YaHei UI", 14, "bold")).place(x=20, y=18)

        tk.Label(top, text="端口:", bg="#2D3748", fg="#CBD5E0",
                 font=("Microsoft YaHei UI", 10)).place(x=220, y=24)
        self.port_var = tk.StringVar(value=str(PORT_DEFAULT))
        tk.Entry(top, textvariable=self.port_var, width=7,
                 font=("Consolas", 11)).place(x=260, y=22)

        self.btn_start = tk.Button(top, text="▶ 启动服务", command=self.start,
                                   bg="#2ECC71", fg="white", font=("Microsoft YaHei UI", 10, "bold"),
                                   relief=tk.FLAT, width=12, cursor="hand2")
        self.btn_start.place(x=360, y=20)
        self.btn_stop = tk.Button(top, text="■ 暂停", command=self.stop, state=tk.DISABLED,
                                  bg="#E74C3C", fg="white", font=("Microsoft YaHei UI", 10, "bold"),
                                  relief=tk.FLAT, width=10, cursor="hand2")
        self.btn_stop.place(x=480, y=20)

        self.status = tk.Label(top, text="● 已停止", bg="#2D3748", fg="#A0AEC0",
                               font=("Microsoft YaHei UI", 10, "bold"))
        self.status.place(x=600, y=24)

        # 可滚动配置区（教师账号 + 内网穿透）：小窗口下也能滚动查看全部控件
        cfg_wrap = tk.Frame(root, bg="#F5F7FA")
        cfg_wrap.pack(fill=tk.X, side=tk.TOP)
        self.cfg_vsb = tk.Scrollbar(cfg_wrap, orient=tk.VERTICAL)
        self.cfg_canvas = tk.Canvas(cfg_wrap, bg="#F5F7FA", highlightthickness=0,
                                   yscrollcommand=self.cfg_vsb.set)
        self.cfg_vsb.config(command=self.cfg_canvas.yview)
        self.cfg_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cfg_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cfg_inner = tk.Frame(self.cfg_canvas, bg="#F5F7FA")
        self.cfg_win = self.cfg_canvas.create_window((0, 0), window=self.cfg_inner, anchor="nw")
        self.cfg_inner.bind("<Configure>",
                            lambda e: self.cfg_canvas.configure(scrollregion=self.cfg_canvas.bbox("all")))
        self.cfg_canvas.bind("<Configure>",
                             lambda e: self.cfg_canvas.itemconfigure(self.cfg_win, width=e.width))

        # 教师账号管理区
        acc = tk.Frame(self.cfg_inner, bg="#F5F7FA")
        acc.pack(fill=tk.X, padx=16, pady=(10, 0))
        acc_top = tk.Frame(acc, bg="#F5F7FA")
        acc_top.pack(fill=tk.X)
        tk.Label(acc_top, text="教师账号管理", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(acc_top, text="刷新", command=self.refresh_teachers, relief=tk.FLAT, bg="#E2E8F0",
                  fg="#2D3748", font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side=tk.RIGHT)
        inner = tk.Frame(acc, bg="#F5F7FA")
        inner.pack(fill=tk.X, pady=(4, 0))
        self.teacher_list = tk.Listbox(inner, height=4, font=("Microsoft YaHei UI", 9),
                                       selectbackground="#4F86F8", selectforeground="white")
        self.teacher_list.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        side = tk.Frame(inner, bg="#F5F7FA")
        side.pack(side=tk.LEFT, anchor="n")
        tk.Label(side, text="账号", bg="#F5F7FA", fg="#4A5568", font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="e")
        self.tea_user = tk.Entry(side, width=14, font=("Consolas", 9)); self.tea_user.grid(row=0, column=1, pady=1)
        tk.Label(side, text="密码", bg="#F5F7FA", fg="#4A5568", font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="e")
        self.tea_pwd = tk.Entry(side, show="*", width=14, font=("Consolas", 9)); self.tea_pwd.grid(row=1, column=1, pady=1)
        tk.Label(side, text="姓名", bg="#F5F7FA", fg="#4A5568", font=("Microsoft YaHei UI", 9)).grid(row=2, column=0, sticky="e")
        self.tea_name = tk.Entry(side, width=14, font=("Microsoft YaHei UI", 9)); self.tea_name.grid(row=2, column=1, pady=1)
        btns = tk.Frame(side, bg="#F5F7FA"); btns.grid(row=3, column=0, columnspan=2, pady=(4, 0))
        tk.Button(btns, text="添加教师", command=self.add_teacher, bg="#2ECC71", fg="white", relief=tk.FLAT,
                  font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btns, text="删除选中", command=self.delete_teacher, bg="#E74C3C", fg="white", relief=tk.FLAT,
                  font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").pack(side=tk.LEFT)
        self._teacher_rows = []

        self._build_frpc_ui(self.cfg_inner)

        # 日志区
        logwrap = tk.Frame(root, bg="#F5F7FA")
        logwrap.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)
        tk.Label(logwrap, text="运行日志", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.log = scrolledtext.ScrolledText(logwrap, wrap=tk.WORD, state=tk.DISABLED,
                                              bg="#1E1E2E", fg="#CDD6F4",
                                              font=("Consolas", 10), relief=tk.FLAT, padx=10, pady=8)
        self.log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # 底部信息
        bottom = tk.Frame(root, bg="#F5F7FA")
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=16, pady=(0, 10))
        tk.Label(bottom, text="数据库: server/exam.db    协议: JSON over TCP    客户端: 学生端/教师端/手机端均可连接",
                 bg="#F5F7FA", fg="#718096", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        self._append("服务端控制台就绪。点击「启动服务」开始监听端口。")
        self.refresh_teachers()
        self.root.after(120, self.poll)

    # ---------------- 内网穿透（OpenFrp / MeFrp / frpc） ----------------
    FRPC_URL = "https://staticassets.naids.com/client/OF_0.68.0_37f78258_260326/frpc_windows_amd64.zip"
    MEFRP_URL = "https://www.mefrp.com/"
    # OpenFrp 节点/隧道查询 API（面板私有接口，多端点尝试；不可用时引导打开面板手动操作）
    OFAPI_ENDPOINTS = [
        "https://ofapi.naids.com/api/getNodeList",
        "https://ofapi.naids.com/apidist/getNodeList",
        "https://api.openfrp.net/api/getNodeList",
    ]
    FRP_PLATFORMS = ["OpenFrp", "MeFrp"]

    def _build_frpc_ui(self, parent):
        fr = tk.Frame(parent, bg="#F5F7FA")
        fr.pack(fill=tk.X, padx=16, pady=(10, 0))
        top = tk.Frame(fr, bg="#F5F7FA")
        top.pack(fill=tk.X)
        tk.Label(top, text="内网穿透", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(top, text="穿透平台", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(20, 4))
        self.frpc_platform = ttk.Combobox(top, values=self.FRP_PLATFORMS, width=10,
                                          state="readonly", font=("Microsoft YaHei UI", 9))
        self.frpc_platform.pack(side=tk.LEFT)
        self.frpc_platform.bind("<<ComboboxSelected>>", self._frpc_platform_changed)
        self.frpc_state = tk.Label(top, text="● 未运行", bg="#F5F7FA", fg="#A0AEC0",
                                   font=("Microsoft YaHei UI", 9))
        self.frpc_state.pack(side=tk.RIGHT)

        row1 = tk.Frame(fr, bg="#F5F7FA")
        row1.pack(fill=tk.X, pady=(4, 0))
        tk.Label(row1, text="访问密钥", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        self.frpc_token = tk.Entry(row1, show="*", width=32, font=("Consolas", 9))
        self.frpc_token.pack(side=tk.LEFT, padx=(6, 6))
        self.frpc_pick = tk.Button(row1, text="选择节点", command=self._frpc_pick_node, relief=tk.FLAT,
                                   bg="#4F86F8", fg="white", font=("Microsoft YaHei UI", 9, "bold"),
                                   cursor="hand2")
        self.frpc_pick.pack(side=tk.LEFT)
        tk.Label(row1, text="隧道ID（多个用英文逗号分隔）", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(12, 6))
        self.frpc_ids = tk.Entry(row1, width=20, font=("Consolas", 9))
        self.frpc_ids.pack(side=tk.LEFT, padx=6)
        self.frpc_panel = tk.Button(row1, text="打开OpenFrp面板", command=self._frpc_open_panel,
                                    relief=tk.FLAT, bg="#E2E8F0", fg="#2D3748",
                                    font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.frpc_panel.pack(side=tk.LEFT, padx=6)
        self.frpc_nodes_btn = tk.Button(row1, text="管理节点", command=self._frpc_manage_nodes,
                                        relief=tk.FLAT, bg="#E2E8F0", fg="#2D3748",
                                        font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.frpc_nodes_btn.pack(side=tk.LEFT, padx=6)

        row2 = tk.Frame(fr, bg="#F5F7FA")
        row2.pack(fill=tk.X, pady=(6, 0))
        self.frpc_tip = tk.Label(row2,
                 text="使用说明：① 在 OpenFrp 面板「创建隧道」时，隧道类型必须选「TCP」（选 HTTP/HTTPS 会改写数据，"
                      "客户端必然 Time Out），本地端口填服务端端口（默认 5943）；"
                      "② 在面板「首页」复制访问密钥粘贴到上方；③ 在「隧道管理」复制隧道ID填入上方；④ 启动穿透后下方显示公网地址，"
                      "客户端「服务器地址」填该地址即可远程连接（会自动做一次隧道自检）。",
                 bg="#F5F7FA", fg="#718096", font=("Microsoft YaHei UI", 8), justify="left",
                 wraplength=820)
        self.frpc_tip.pack(anchor="w")

        row3 = tk.Frame(fr, bg="#F5F7FA")
        row3.pack(fill=tk.X, pady=(6, 0))
        self.frpc_addr = tk.Label(row3, text="公网地址：尚未连接", bg="#F5F7FA", fg="#2D3748",
                                  font=("Microsoft YaHei UI", 9, "bold"))
        self.frpc_addr.pack(side=tk.LEFT)
        self.frpc_copy = tk.Button(row3, text="复制地址", command=self._frpc_copy_addr, state=tk.DISABLED,
                                   relief=tk.FLAT, bg="#E2E8F0", fg="#2D3748",
                                   font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.frpc_copy.pack(side=tk.LEFT, padx=(8, 0))
        self.frpc_dl = tk.Button(row3, text="下载 frpc", command=self._frpc_download, relief=tk.FLAT,
                                 bg="#E2E8F0", fg="#2D3748", font=("Microsoft YaHei UI", 9), cursor="hand2")
        self.frpc_dl.pack(side=tk.RIGHT)
        self.frpc_start = tk.Button(row3, text="▶ 启动穿透", command=self._frpc_start, bg="#2ECC71",
                                    fg="white", relief=tk.FLAT, font=("Microsoft YaHei UI", 9, "bold"),
                                    cursor="hand2")
        self.frpc_start.pack(side=tk.RIGHT, padx=(0, 6))
        self.frpc_stop = tk.Button(row3, text="■ 停止穿透", command=self._frpc_stop, state=tk.DISABLED,
                                   bg="#E74C3C", fg="white", relief=tk.FLAT,
                                   font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2")
        self.frpc_stop.pack(side=tk.RIGHT, padx=(0, 6))

        self._frpc_proc = None
        self._frpc_remote = ""
        self._frpc_cfg = os.path.join(srv.BASE_DIR, "frpc_config.json")
        self._frpc_platform = "OpenFrp"
        self.frpc_platform.set(self._frpc_platform)
        try:
            if os.path.isfile(self._frpc_cfg):
                with open(self._frpc_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.frpc_token.delete(0, tk.END); self.frpc_token.insert(0, cfg.get("token", ""))
                self.frpc_ids.delete(0, tk.END); self.frpc_ids.insert(0, cfg.get("ids", ""))
                pl = cfg.get("platform", "OpenFrp")
                if pl in self.FRP_PLATFORMS:
                    self.frpc_platform.set(pl)
                    self._frpc_platform = pl
                    self._frpc_platform_changed()
        except Exception:
            pass

    def _frpc_platform_changed(self, event=None):
        """切换穿透平台：更新说明文字、面板按钮、隧道ID 提示"""
        self._frpc_platform = self.frpc_platform.get()
        self._save_frpc_cfg()
        if self._frpc_platform == "MeFrp":
            self.frpc_panel.config(text="打开MeFrp面板")
            self.frpc_dl.config(text="下载 MeFrp 客户端")
            self.frpc_tip.config(
                text="使用说明：① 打开 MeFrp 面板（www.mefrp.com）→「隧道管理」→ 找到你要用的 TCP 隧道"
                     "（本地端口必须等于服务端端口，默认 5943）→ 点「更多」→「生成启动配置」，"
                     "会得到形如  mefrpc -t <访问密钥> -p <隧道ID>  的命令；"
                     "② 把命令里的「访问密钥」粘贴到上方（mefrpc -t 后面那串）；"
                     "③ 把「-p 后面的隧道ID」填入上方；④ 客户端 mefrpc.exe 请先下载放到程序目录 frpc 文件夹，"
                     "点「下载客户端」会打开 MeFrp 文件下载页；⑤ 启动穿透后下方显示公网地址，客户端填该地址即可。")
        else:
            self.frpc_panel.config(text="打开OpenFrp面板")
            self.frpc_dl.config(text="下载 frpc")
            self.frpc_tip.config(
                text="使用说明：① 在 OpenFrp 面板「创建隧道」时，隧道类型必须选「TCP」（选 HTTP/HTTPS 会改写数据，"
                     "客户端必然 Time Out），本地端口填服务端端口（默认 5943）；"
                     "② 在面板「首页」复制访问密钥粘贴到上方；③ 在「隧道管理」复制隧道ID填入上方；④ 启动穿透后下方显示公网地址，"
                     "客户端「服务器地址」填该地址即可远程连接（会自动做一次隧道自检）。")

    def _frpc_open_panel(self):
        import webbrowser
        if self._frpc_platform == "MeFrp":
            webbrowser.open(self.MEFRP_URL)
        else:
            webbrowser.open("https://www.openfrp.net/?page=panel")

    def _frpc_pick_node(self):
        """输入访问密钥后弹窗选择节点：尝试从 OpenFrp 查询节点列表，失败则引导打开面板"""
        if self._frpc_platform == "MeFrp":
            import webbrowser
            self.q.put("[frpc] MeFrp 节点请在面板「隧道管理」里为隧道直接选择/更换节点，"
                       "然后点「更多 → 生成启动配置」复制启动参数；本机无法直接列出 MeFrp 节点。")
            webbrowser.open(self.MEFRP_URL)
            return
        import urllib.request, json as _json
        token = self.frpc_token.get().strip()
        win = tk.Toplevel(self.root)
        win.title("选择节点")
        win.geometry("520x420")
        win.configure(bg="#F5F7FA")
        tk.Label(win, text="OpenFrp 节点列表", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        status = tk.Label(win, text="正在获取节点列表...", bg="#F5F7FA", fg="#718096",
                          font=("Microsoft YaHei UI", 9))
        status.pack(anchor="w", padx=14)
        box = tk.Listbox(win, font=("Microsoft YaHei UI", 9), selectbackground="#4F86F8",
                         selectforeground="white")
        box.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        tip = tk.Label(win, text="", bg="#F5F7FA", fg="#4A5568", font=("Microsoft YaHei UI", 9),
                       justify="left", wraplength=480)
        tip.pack(anchor="w", padx=14)

        nodes = []
        got = False
        for ep in self.OFAPI_ENDPOINTS:
            try:
                req = urllib.request.Request(ep, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    raw = r.read()
                data = _json.loads(raw.decode("utf-8", "replace"))
                # 兼容 {code,data:{list:[...]}} / {data:[...]} / [..]
                lst = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(lst, dict):
                    lst = lst.get("list", lst.get("List", []))
                for it in lst or []:
                    name = (it.get("name") or it.get("NodeName") or it.get("nodeName") or "").strip()
                    if name:
                        nodes.append(name)
                if nodes:
                    got = True
                    break
            except Exception:
                continue
        if got:
            status.config(text="已获取 %d 个节点，双击选择（隧道需在对应节点创建）" % len(nodes), fg="#48BB78")
            for n in nodes:
                box.insert(tk.END, n)

            def on_pick(event):
                sel = box.curselection()
                if not sel:
                    return
                node = box.get(sel[0])
                tip.config(text="已选择节点：%s\n提示：隧道 ID 需在 OpenFrp 面板「隧道管理」中查看，"
                                 "确认该隧道创建在所选节点上后，填入上方隧道ID输入框。" % node)
            box.bind("<Double-Button-1>", on_pick)
        else:
            status.config(text="无法获取节点列表（OpenFrp 接口暂不可用）", fg="#E74C3C")
            tip.config(text="请点击下方按钮打开 OpenFrp 面板：在「创建隧道」页选择节点并创建隧道，"
                            "然后在「隧道管理」复制隧道ID、在「首页」复制访问密钥，填回服务端窗口。")
            bt = tk.Button(win, text="打开 OpenFrp 面板", command=self._frpc_open_panel,
                           bg="#4F86F8", fg="white", relief=tk.FLAT, cursor="hand2")
            bt.pack(pady=6)

    def _frpc_copy_addr(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._frpc_remote)
            self.q.put("[frpc] 公网地址已复制到剪贴板: %s" % self._frpc_remote)
        except Exception:
            pass

    def _frpc_load_nodes(self):
        """读取节点列表（frpc_config.json 的 nodes 字段）"""
        try:
            if os.path.isfile(self._frpc_cfg):
                with open(self._frpc_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return [n for n in (cfg.get("nodes") or []) if isinstance(n, str) and n.strip()]
        except Exception:
            pass
        return []

    def _frpc_save_nodes(self, nodes):
        """保存节点列表到 frpc_config.json（保留 token/ids/platform）"""
        try:
            cfg = {}
            if os.path.isfile(self._frpc_cfg):
                with open(self._frpc_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["nodes"] = [n for n in nodes if isinstance(n, str) and n.strip()]
            with open(self._frpc_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.q.put("[frpc] 节点保存失败: %s" % e)

    def _frpc_add_node(self, addr):
        """拿到新公网地址时自动加入节点列表（去重）"""
        nodes = self._frpc_load_nodes()
        if addr and addr not in nodes:
            nodes.append(addr)
            self._frpc_save_nodes(nodes)
            self.q.put("[frpc] 已把 %s 加入节点列表（教师端/学生端登录超时时可切换）" % addr)

    def _frpc_manage_nodes(self):
        """弹窗管理候选节点列表：每行一个 host:port，客户端登录超时时可切换"""
        win = tk.Toplevel(self.root)
        win.title("管理节点（客户端登录超时后可切换）")
        win.geometry("560x420")
        win.configure(bg="#F5F7FA")
        tk.Label(win, text="候选节点列表（每行一个 地址:端口）", bg="#F5F7FA", fg="#4A5568",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(win, text="客户端在登录/连接超时后会弹出「更换节点」窗口，从这里选择可用的地址重试。\n"
                           "启动穿透成功时公网地址会自动加入；也可手动粘贴 局域网IP:5943 或 公网地址。",
                 bg="#F5F7FA", fg="#718096", font=("Microsoft YaHei UI", 9), justify="left",
                 wraplength=520).pack(anchor="w", padx=16)
        txt = tk.Text(win, font=("Consolas", 10), bg="white", fg="#2D3748",
                      relief=tk.SOLID, bd=1)
        txt.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        txt.insert("1.0", "\n".join(self._frpc_load_nodes()))
        row = tk.Frame(win, bg="#F5F7FA")
        row.pack(fill=tk.X, padx=16, pady=(0, 12))
        tk.Button(row, text="保存", command=lambda: self._save_nodes_win(txt, win),
                  bg="#4F86F8", fg="white", relief=tk.FLAT,
                  font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.RIGHT)
        tk.Button(row, text="取消", command=win.destroy,
                  bg="#E2E8F0", fg="#2D3748", relief=tk.FLAT,
                  font=("Microsoft YaHei UI", 9)).pack(side=tk.RIGHT, padx=8)

    def _save_nodes_win(self, txt, win):
        lines = [l.strip() for l in txt.get("1.0", "end").splitlines() if l.strip()]
        # 过滤明显非地址的行
        import re as _re
        lines = [l for l in lines if _re.match(r"^[a-zA-Z0-9.\-]+(?::\d+)?$", l)]
        self._frpc_save_nodes(lines)
        self.q.put("[frpc] 节点列表已保存：%d 个节点" % len(lines))
        win.destroy()

    def _frpc_exe(self):
        target_dir = os.path.join(srv.BASE_DIR, "frpc")
        if self._frpc_platform == "MeFrp":
            return os.path.join(target_dir, "mefrpc.exe")
        exe = os.path.join(target_dir, "frpc.exe")
        if os.path.isfile(exe):
            return exe
        # PyInstaller 打包后 frpc 位于 _MEIPASS 临时目录：
        # 首次使用时复制到 exe 同级（持久化，之后可手动覆盖更新）
        meip = getattr(sys, "_MEIPASS", "")
        if meip:
            bundled = os.path.join(meip, "frpc", "frpc.exe")
            if os.path.isfile(bundled):
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    import shutil
                    shutil.copy2(bundled, exe)
                    return exe
                except Exception:
                    return bundled
        return ""

    def _frpc_download(self):
        if self._frpc_platform == "MeFrp":
            # MeFrp 客户端在蓝奏云，无法程序化下载；打开官网引导用户下载后放到 frpc 目录
            import webbrowser
            target = os.path.join(srv.BASE_DIR, "frpc")
            os.makedirs(target, exist_ok=True)
            self.q.put("[frpc] MeFrp 客户端需从官网下载（蓝奏云，无法自动下载）。"
                       "已为你打开 MeFrp 官网，请在「文件下载」页下载 Windows amd64 版客户端，"
                       "解压后把 mefrpc.exe 放到：%s" % target)
            webbrowser.open(self.MEFRP_URL)
            return
        import zipfile, io
        target = os.path.join(srv.BASE_DIR, "frpc")
        os.makedirs(target, exist_ok=True)
        exe = os.path.join(target, "frpc.exe")
        self.q.put("[frpc] 开始下载 openfrp frpc ...")
        try:
            with urllib.request.urlopen(self.FRPC_URL, timeout=120) as resp:
                data = resp.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                member = next((n for n in names if n.lower().endswith(".exe")), names[0])
                with z.open(member) as src, open(exe, "wb") as dst:
                    dst.write(src.read())
            self.q.put("[frpc] 下载完成: %s" % exe)
        except Exception as e:
            self.q.put("[frpc] 下载失败: %s" % e)

    def _save_frpc_cfg(self):
        try:
            with open(self._frpc_cfg, "w", encoding="utf-8") as f:
                json.dump({"platform": self._frpc_platform,
                           "token": self.frpc_token.get().strip(),
                           "ids": self.frpc_ids.get().strip()}, f, ensure_ascii=False)
        except Exception as e:
            self.q.put("[frpc] 配置保存失败: %s" % e)

    def _frpc_start(self):
        if self._frpc_proc and self._frpc_proc.poll() is None:
            return
        exe = self._frpc_exe()
        if not exe or not os.path.isfile(exe):
            if self._frpc_platform == "MeFrp":
                self.q.put("[frpc] 未找到 mefrpc.exe，请先点「下载 frpc/客户端」到 MeFrp 官网下载"
                           "（Windows amd64），解压后把 mefrpc.exe 放进程序目录的 frpc 文件夹")
                self._frpc_download()
            else:
                self.q.put("[frpc] 未找到 frpc.exe，请先点击「下载 frpc」")
            return
        token = self.frpc_token.get().strip()
        ids = self.frpc_ids.get().strip()
        if not token:
            if self._frpc_platform == "MeFrp":
                self.q.put("[frpc] 请先在 MeFrp 面板「隧道管理 → 生成启动配置」里复制启动参数，"
                           "把 mefrpc -t 后面的访问密钥粘贴到「访问密钥」")
            else:
                self.q.put("[frpc] 请先在 OpenFrp 面板「首页」复制访问密钥并粘贴到「访问密钥」")
            return
        if not ids:
            self.q.put("[frpc] 请填写隧道ID（%s 面板「隧道管理」获得）" % self._frpc_platform)
            return
        self._save_frpc_cfg()
        # 残留 frpc 会与本次争抢同一条隧道，节点反复踢线 => 客户端大面积 Time Out
        try:
            exe_name = "mefrpc.exe" if self._frpc_platform == "MeFrp" else "frpc.exe"
            tl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % exe_name],
                                capture_output=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if exe_name.encode("ascii", "replace") in (tl.stdout or b""):
                self.q.put("[frpc] 警告：已有一个 %s 在运行。同一条隧道被两个客户端登录会被节点踢线，"
                           "表现为客户端频繁 Time Out；请先点「■ 停止穿透」或结束旧的进程。" % exe_name)
        except Exception:
            pass
        if self._frpc_platform == "MeFrp":
            # MeFrp 快捷启动：mefrpc -t <访问密钥> -p <隧道ID>
            cmd = [exe, "-t", token, "-p", ids]
            self.q.put("[frpc] 启动: mefrpc -t *** -p %s" % ids)
        else:
            cmd = [exe, "-u", token, "-p", ids, "--noupdate"]
            self.q.put("[frpc] 启动: frpc -u *** -p %s" % ids)
        try:
            self._frpc_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.q.put("[frpc] 启动失败: %s" % e); return
        threading.Thread(target=self._frpc_reader, args=(self._frpc_proc,), daemon=True).start()
        self.frpc_start.config(state=tk.DISABLED)
        self.frpc_stop.config(state=tk.NORMAL)
        self.frpc_state.config(text="● 连接节点中...", fg="#ED8936")

    def _frpc_reader(self, proc):
        import time as _t, re as _re
        try:
            for line in iter(proc.stdout.readline, b""):
                try:
                    txt = line.decode("utf-8").rstrip()
                except UnicodeDecodeError:
                    txt = line.decode("gbk", "replace").rstrip()   # frpc 中文输出可能是 GBK
                if txt:
                    self.q.put("[frpc] " + txt)
                    # 公网地址【只】从权威行提取：Use [host:port] / 请使用 [host:port]。
                    # frpc 日志里还有 root_ext.go:50 / service.go:390 等源码位置，
                    # 长得像 host:port，绝不能当公网地址（之前就出过这个 bug）。
                    m = (_re.search(r"(?:Use|使用)\s*\[?([a-zA-Z0-9.\-]+:\d+)\]?", txt)
                         or _re.search(r"启动成功[^\]]{0,40}\[([a-zA-Z0-9.\-]+:\d+)\]", txt)
                         or _re.search(r"您可以使用\s*\[?([a-zA-Z0-9.\-]+:\d+)\]?", txt)
                         or _re.search(r"访问您的服务\s*[:\[]?\s*([a-zA-Z0-9.\-]+:\d+)", txt))
                    if m:
                        host, _, _port = m.group(1).rpartition(":")
                        if self._is_public_host(host) and m.group(1) != self._frpc_remote:
                            addr = m.group(1)
                            self._frpc_remote = addr
                            self.frpc_addr.config(
                                text="公网地址：%s  （客户端「服务器地址」填这个）" % addr, fg="#2ECC71")
                            self.frpc_copy.config(state=tk.NORMAL)
                            self.frpc_state.config(text="● 运行中", fg="#48BB78")
                            self.q.put("[frpc] 已获取公网地址: %s" % addr)
                            self._frpc_add_node(addr)
                            threading.Thread(target=self._frpc_check_thread, args=(addr,), daemon=True).start()
                    if "proxy is available" in txt or "启动成功" in txt:
                        self.frpc_state.config(text="● 运行中", fg="#48BB78")
                    # 节点侧故障提示：502 / 启动失败 / 无法连接到节点
                    if "502" in txt or "无法连接到节点" in txt or "启动失败" in txt:
                        self.frpc_state.config(text="● 节点连接失败", fg="#E74C3C")
                        pname = "MeFrp" if self._frpc_platform == "MeFrp" else "OpenFrp"
                        self.q.put("[frpc] ⚠ 穿透节点当前连不上（节点故障/过载或本机网络问题）。"
                                   "请到 %s 面板「隧道管理」把该隧道的节点改成其它节点，"
                                   "或等几分钟后重试；这不是服务端/客户端的问题。" % pname)
        except Exception:
            pass
        rc = proc.poll()
        self.q.put("[frpc] 进程退出，代码=%s" % rc)
        self.q.put("FRPC_STATE_STOPPED")
        try:
            self.frpc_start.config(state=tk.NORMAL)
            self.frpc_stop.config(state=tk.DISABLED)
            self.frpc_state.config(text="● 已停止", fg="#A0AEC0")
        except Exception:
            pass

    def _frpc_stop(self):
        proc = self._frpc_proc
        self._frpc_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            # frpc 有时忽略 terminate：残留进程会占着隧道，
            # 下次启动会被节点踢掉（客户端表现为大面积 Time Out），必须强杀干净
            if proc.poll() is None:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
        self.frpc_start.config(state=tk.NORMAL)
        self.frpc_stop.config(state=tk.DISABLED)
        self.frpc_state.config(text="● 未运行", fg="#A0AEC0")
        self.q.put("[frpc] 已停止")

    # ---------------- frpc 隧道自检 ----------------
    # 客户端（教师端/学生端）经 frp 报 Time Out 时，用这里一眼区分三种情况：
    # ① 本机服务没开  ② 隧道没通（本地端口填错/隧道未生效）  ③ 隧道类型不是纯 TCP
    # 源码文件后缀：日志里 "root_ext.go:50" 之类是源码位置，不是域名
    _BAD_TLD = {"go", "py", "js", "ts", "java", "cpp", "cc", "c", "h", "log", "txt",
                "ini", "conf", "toml", "json", "yaml", "yml", "md", "bat", "sh",
                "db", "zip", "exe", "dll", "sys", "ps1", "csv"}

    def _is_public_host(self, host):
        """过滤日志里的本机/内网地址和源码文件名，只保留穿透节点给的公网地址"""
        h = (host or "").strip().lower()
        if not h or h in ("localhost", "0.0.0.0", "::1"):
            return False
        if "." in h and h.rsplit(".", 1)[-1] in self._BAD_TLD:
            return False                      # root_ext.go 之类源码位置
        m = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", h)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a in (0, 10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) \
               or (a == 169 and b == 254) or a >= 224:
                return False
            return True
        return "." in h

    def _frpc_local_port(self):
        try:
            return int(self.port_var.get())
        except Exception:
            return 5943

    def _probe_tcp(self, host, port, timeout=8):
        """裸 TCP 握手：判断端口是否可达"""
        import socket as _sk
        try:
            s = _sk.create_connection((host, int(port)), timeout=timeout)
            s.close()
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _probe_protocol(self, host, port, timeout=10):
        """用真实加密帧探测：能连上但协议不通 => 隧道多半不是纯 TCP 类型"""
        import socket as _sk, struct as _st, json as _js
        s = None
        try:
            s = _sk.create_connection((host, int(port)), timeout=timeout)
            s.settimeout(timeout)
            raw = srv._encrypt(_js.dumps({"cmd": "ping"}).encode("utf-8"))
            s.sendall(_st.pack(">I", len(raw)) + raw)
            hdr = b""
            while len(hdr) < 4:
                ch = s.recv(4 - len(hdr))
                if not ch:
                    return False, "连接被关闭，未收到数据"
                hdr += ch
            n = _st.unpack(">I", hdr)[0]
            buf = b""
            while len(buf) < n:
                ch = s.recv(n - len(buf))
                if not ch:
                    return False, "响应不完整"
                buf += ch
            out = _js.loads(srv._decrypt(buf).decode("utf-8"))
            return (out.get("code") == 0), str(out.get("msg", "pong"))
        except Exception as e:
            return False, "协议探测失败：%s" % e
        finally:
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass

    def _frpc_check_thread(self, addr):
        import time as _t
        host, _, port = addr.rpartition(":")
        lp = self._frpc_local_port()

        lok, lmsg = self._probe_protocol("127.0.0.1", lp, timeout=5)
        if lok:
            self.q.put("[frpc自检] ① 本机服务正常（127.0.0.1:%d）" % lp)
        else:
            self.q.put("[frpc自检] ① 本机服务未就绪（127.0.0.1:%d）：%s" % (lp, lmsg))
            self.q.put("[frpc自检]    → 先点上方「▶ 启动服务」；服务端没开时外网连进来一定 Time Out")

        # 实测：隧道刚启动时节点可能重置新连接(10054)，首帧握手要 15~32s。
        # 自检最多试 2 轮、每轮给足超时，不做更多重试。
        _t.sleep(5)
        pname = "MeFrp" if self._frpc_platform == "MeFrp" else "OpenFrp"
        tip = "② 公网地址 %s 暂时不可达：请确认 %s 隧道的「本地端口」= %d，且隧道处于启用状态" % (addr, pname, lp)
        for i in range(2):
            if i:
                _t.sleep(10)
                self.q.put("[frpc自检]   再试一次（隧道刚启动约需 1 分钟才稳定）...")
            pok, pmsg = self._probe_protocol(host, port, timeout=35)
            if pok:
                self.q.put("[frpc自检] ② 隧道可用：%s 协议握手成功，客户端「服务器地址」填这个即可" % addr)
                self.q.put("[frpc自检]    客户端首次连接约需 15~40 秒，属正常现象，请勿反复点登录")
                return
            tip = "② 协议握手失败：%s\n   → 若隧道类型不是「TCP」请到面板改成 TCP；\n   → 若是刚启动，等 1 分钟后再让客户端连接" % pmsg
        self.q.put("[frpc自检] " + tip)

    def on_close(self):
        """关闭窗口：先停服务再杀 frpc，避免残留进程占着隧道"""
        try:
            if self.running:
                self.stop()
        except Exception:
            pass
        try:
            self._frpc_stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _append(self, text):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "FRPC_STATE_STOPPED":
                    self.frpc_start.config(state=tk.NORMAL)
                    self.frpc_stop.config(state=tk.DISABLED)
                    self.frpc_state.config(text="● 未运行", fg="#A0AEC0")
                    continue
                self._append(line)
        except queue.Empty:
            pass
        self.root.after(120, self.poll)

    def start(self):
        if self.running: return
        try:
            port = int(self.port_var.get())
        except:
            self._append("[错误] 端口号无效"); return
        self.loop = asyncio.new_event_loop()
        def run():
            try:
                asyncio.set_event_loop(self.loop)
                self.srv = self.loop.run_until_complete(
                    asyncio.start_server(srv.handle, "0.0.0.0", port))
                self.loop.run_forever()
            except Exception as e:
                self.q.put("[异常] 服务启动失败: " + str(e))
                self.q.put(traceback.format_exc())
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        self.running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.status.config(text="● 运行中", fg="#48BB78")
        self.q.put("服务已启动，监听 0.0.0.0:%d" % port)

    def stop(self):
        if not self.running: return
        try:
            if self.srv is not None:
                self.srv.close()
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception as e:
            self.q.put("[错误] 停止异常: " + str(e))
        self.running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.status.config(text="● 已停止", fg="#A0AEC0")
        self.q.put("服务已暂停，端口已释放。")

    # ---------------- 教师账号管理 ----------------
    def _teacher_db(self):
        c = sqlite3.connect(srv.DB, timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def refresh_teachers(self):
        try:
            c = self._teacher_db()
            rows = c.execute("SELECT id,username,display_name FROM users WHERE role='teacher' ORDER BY id").fetchall()
            c.close()
            self.teacher_list.delete(0, tk.END)
            self._teacher_rows = rows
            for r in rows:
                name = r["display_name"] or r["username"]
                self.teacher_list.insert(tk.END, "%s（%s）" % (name, r["username"]))
        except Exception as e:
            self._append("[错误] 读取教师账号失败: " + str(e))

    def add_teacher(self):
        un = self.tea_user.get().strip()
        pwd = self.tea_pwd.get()
        name = self.tea_name.get().strip() or un
        if not un or not pwd:
            self._append("[错误] 教师账号和密码不能为空"); return
        if not messagebox.askyesno("确认添加", "添加教师账号：%s（姓名：%s）？" % (un, name)):
            return
        try:
            salt = secrets.token_hex(8)
            h = hashlib.sha256((salt + pwd).encode()).hexdigest()
            c = self._teacher_db()
            c.execute("INSERT INTO users(username,salt,pwd_hash,display_name,role) VALUES(?,?,?,?, 'teacher')",
                      (un, salt, h, name))
            c.commit(); c.close()
            self._append("[教师账号] 已添加：%s（%s）" % (name, un))
            self.tea_user.delete(0, tk.END); self.tea_pwd.delete(0, tk.END); self.tea_name.delete(0, tk.END)
            self.refresh_teachers()
        except sqlite3.IntegrityError:
            self._append("[错误] 账号已存在：%s" % un)
        except Exception as e:
            self._append("[错误] 添加教师失败: " + str(e))

    def delete_teacher(self):
        sel = self.teacher_list.curselection()
        if not sel:
            self._append("[提示] 请先在列表中选中要删除的教师账号"); return
        r = self._teacher_rows[sel[0]]
        if r["username"] == "admin":
            self._append("[错误] 内置管理员账号 admin 不允许删除"); return
        name = r["display_name"] or r["username"]
        if not messagebox.askyesno("确认删除", "确定删除教师账号「%s（%s）」吗？删除后不可恢复。" % (name, r["username"])):
            return
        try:
            c = self._teacher_db()
            c.execute("DELETE FROM users WHERE id=? AND role='teacher'", (r["id"],))
            c.commit(); c.close()
            self._append("[教师账号] 已删除：%s" % r["username"])
            self.refresh_teachers()
        except Exception as e:
            self._append("[错误] 删除教师失败: " + str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = ServerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)   # 关窗自动停服务 + 杀 frpc
    # print 输出全部进队列
    sys.stdout = StdoutToQueue(app.q)
    sys.stderr = StdoutToQueue(app.q)
    if len(sys.argv) > 1 and sys.argv[1] == "--auto-start":
        root.after(500, app.start)
    root.mainloop()
