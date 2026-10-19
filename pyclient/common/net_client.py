# -*- coding: utf-8 -*-
"""共享 TCP 客户端：4字节大端长度 + AES-256-GCM 认证加密帧，与 server.py 协议一致。
帧 = 4字节长度 + nonce(12) + GCM tag(16) + 密文；密钥由共享口令经 PBKDF2 派生。

【经 frpc / 公网连接的关键适配】
1. 超时隔离：每次 call 明确设置"本次超时"，调用结束无论成败都恢复默认。
   旧实现里 settimeout 会永久留在 socket 上——教师端监控页每 3 秒用 timeout=5
   调 monitorList，之后该连接上所有请求都被压成 5 秒超时，经 frp 必然 Time Out。
2. 默认超时 30s；新建连接上的**第一个请求**放宽到 60s——
   实测 frp 隧道 TCP 握手 0.03s，但第一个请求要等 15~32 秒（节点↔frpc 后端
   握手完成后才转发数据），之后同连接都是毫秒级。不做任何自动重连/换连接。
3. 建连用 getaddrinfo 逐个尝试（IPv4 优先）；本机 DNS 解析不到穿透域名时，
   自动向公共 DNS 兜底查询 A 记录后直连。
4. TCP_NODELAY + 1MB 收发缓冲 + TCP keepalive(10s 探测)。
"""
import socket, json, struct, threading, re, os, sys, time, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_CRYPTO_KEY = hashlib.pbkdf2_hmac("sha256", b"ttlx-secure-transport-2026", b"ttlx-transport", 120000, 32)
_aesgcm = AESGCM(_CRYPTO_KEY)

def _enc(body):
    nonce = os.urandom(12)
    return nonce + _aesgcm.encrypt(nonce, body, None)

def _dec(raw):
    return _aesgcm.decrypt(raw[:12], raw[12:], None)

class NetError(Exception): pass

DEFAULT_PORT = 5943
DEFAULT_TIMEOUT = 30.0    # 常规请求（公网/frp 场景）
CONNECT_TIMEOUT = 20.0    # 建连与重连登录
# 【实测】frp 隧道第一个请求要 15~32 秒（TCP 握手本身只要 0.03s），
# 所以新建连接上的首个请求放宽到 60 秒；此后同连接都是毫秒级。
FIRST_CALL_TIMEOUT = 60.0
MAX_FRAME = 64 * 1024 * 1024

def parse_host_port(text, default_port=DEFAULT_PORT):
    """解析登录框地址：支持「IP[:端口]」「域名[:端口]」「[IPv6]:端口」。
    额外容错：从穿透面板复制来的 http:// 前缀、全角冒号、尾部斜杠/空格。
    没写端口时返回默认端口；端口非法时按没写处理。"""
    s = (text or "").strip()
    s = re.sub(r"^\s*[a-zA-Z][a-zA-Z0-9+.\-]*://", "", s)
    s = s.replace("：", ":").strip().rstrip("/").strip()
    m = re.match(r"^\[(.+)\]:(\d{1,5})$", s)            # [::1]:5943
    if m:
        p = int(m.group(2))
        return m.group(1).strip(), (p if 0 < p <= 65535 else default_port)
    if s.count(":") == 1:                               # 192.168.1.10:5943 / xxx.frp.one:12345
        head, _, tail = s.rpartition(":")
        if tail.isdigit() and 0 < int(tail) <= 65535:
            return head.strip(), int(tail)
        if head.strip():                                # 端口非法/缺失：按没写处理
            return head.strip(), default_port
    return s, default_port

def _dns_lookup_public(host, servers=("223.5.5.5", "119.29.29.29", "114.114.114.114")):
    """getaddrinfo 失败时的兜底：直接向公共 DNS 发 A 记录查询（纯标准库）。

    适用场景：客户端所在网络的 DNS 解析不到穿透域名（内网 DNS 限制 /
    缓存了隧道离线期的 NXDOMAIN）。frp 是纯 TCP 转发，拿到 IP 直连完全等效。
    """
    import random
    if not host or all(ch.isdigit() or ch == "." for ch in host):
        return []                       # 已经是 IP，无需查询
    try:
        tid = random.randint(0, 65535)
        q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
        for label in host.split("."):
            if not label:
                return []
            b = label.encode("ascii")
            q += bytes([len(b)]) + b
        q += b"\x00" + struct.pack(">HH", 1, 1)      # type A, class IN
    except Exception:
        return []
    for srv in servers:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(3)
            s.sendto(q, (srv, 53))
            data, _ = s.recvfrom(2048)
            if len(data) < 12:
                continue
            rid, _flags, _qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
            if rid != tid or an == 0:
                continue
            i = 12                                    # 跳过 question
            while data[i] != 0:
                i += data[i] + 1
            i += 1 + 4
            ips = []
            for _ in range(an):
                if data[i] & 0xC0:
                    i += 2
                else:
                    while data[i] != 0:
                        i += data[i] + 1
                    i += 1
                rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
                i += 10
                if rtype == 1 and rdlen == 4:
                    ips.append(".".join(str(b) for b in data[i:i + 4]))
                i += rdlen
            if ips:
                return ips
        except Exception:
            continue
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass
    return []

def _tune_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass
    for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, 1 << 20)
        except Exception:
            pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            # (onoff, keepalivetime_ms, keepaliveinterval_ms)：10s 空闲后每 3s 探测
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        else:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    except Exception:
        pass

class NetClient:
    def __init__(self, host="127.0.0.1", port=DEFAULT_PORT, progress_cb=None):
        # 允许在地址栏直接写端口："192.168.1.10:1234" 或 "[::1]:1234"
        if isinstance(host, str):
            host, port = parse_host_port(host, port)
        self.host = host
        self.port = port
        self.progress_cb = progress_cb     # 进度回调（供 UI 显示，可空）
        self.sock = None
        self.user = None
        self._lock = threading.RLock()
        self._first_call = True            # 新连接上的第一个请求放宽超时（frp 首帧慢）

    def _report(self, text):
        if self.progress_cb:
            try:
                self.progress_cb(text)
            except Exception:
                pass

    # ---------------- 连接（单次，不重连不换连接） ----------------
    def connect(self):
        self._report("正在连接服务器 %s:%s …" % (self.host, self.port))
        try:
            infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        except Exception as e:
            # 本机 DNS 解析不到（内网 DNS 限制 / 缓存了 NXDOMAIN）：
            # 自动向公共 DNS 兜底查询，拿到 IP 直连（frp 是纯 TCP 转发，等效）
            ips = _dns_lookup_public(self.host)
            if ips:
                self._report("本机 DNS 解析不到，已改用公共 DNS 解析成功")
                infos = [(socket.AF_INET, None, None, "", (ip, self.port)) for ip in ips]
            else:
                raise NetError(
                    "地址解析失败（%s）：%s\n"
                    "① 确认服务端穿透已启动（部分穿透域名只在隧道在线时才解析得到）；\n"
                    "② 客户端所在网络的 DNS 可能解析不了该域名，可在客户端机器执行\n"
                    "   ipconfig /flushdns 后重试，或把 DNS 改为 223.5.5.5 / 119.29.29.29；\n"
                    "③ 也可以直接在地址栏填「公网IP:端口」。" % (self.host, e))
        # IPv4 优先（多数穿透节点只给 IPv4），IPv6 兜底
        infos.sort(key=lambda x: 0 if x[0] == socket.AF_INET else 1)
        last = None
        for af, _st, _proto, _cn, sa in infos:
            s = None
            try:
                s = socket.socket(af, socket.SOCK_STREAM)
                s.settimeout(CONNECT_TIMEOUT)
                _tune_socket(s)
                s.connect(sa)
                s.settimeout(DEFAULT_TIMEOUT)
                self.sock = s
                self._first_call = True
                return
            except Exception as e:
                last = e
                try:
                    if s: s.close()
                except Exception:
                    pass
        self.sock = None
        raise NetError("无法连接服务器 %s:%s（%s）" % (self.host, self.port, last))

    def close(self):
        if self.sock:
            try: self.sock.close()
            except: pass
            self.sock = None
        self._first_call = True

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise NetError("连接已断开")
            buf += chunk
        return buf

    def _reset_timeout(self):
        """调用结束后把 socket 超时恢复默认，杜绝短超时调用污染后续请求"""
        if self.sock:
            try:
                self.sock.settimeout(DEFAULT_TIMEOUT)
            except Exception:
                pass

    def call(self, cmd, timeout=None, **kwargs):
        """同步调用。连接断开时自动重连一次并重新登录（仅一次，不反复重试）。

        timeout: 本次调用 recv 等待秒数（None=默认 30s）。
                 心跳等轻量调用可传小值（如 8s），Office/判分等应传 120~300。
                 新连接上的第一个请求自动放宽到 60s（frp 首帧握手 15~32s）。
                 无论成败，调用结束都会把 socket 超时恢复为默认值。
        """
        kwargs["cmd"] = cmd
        body = json.dumps(kwargs, ensure_ascii=False).encode("utf-8")
        last = None
        with self._lock:
            for attempt in range(2):          # 与旧版一致：断线后重连一次
                try:
                    if not self.sock:
                        self.connect()
                        if self.user:
                            self.call_raw("login", timeout=CONNECT_TIMEOUT,
                                          username=self.user.get("username"),
                                          password=self.user.get("password"))
                    want = timeout or DEFAULT_TIMEOUT
                    if self._first_call:
                        want = max(want, FIRST_CALL_TIMEOUT)
                        self._report("已连接，等待服务器响应…（穿透首次约需 15~40 秒）")
                    self.sock.settimeout(want)
                    raw = _enc(body)
                    self.sock.sendall(struct.pack(">I", len(raw)) + raw)
                    hdr = self._recv_exact(4)
                    n = struct.unpack(">I", hdr)[0]
                    if n <= 0 or n > MAX_FRAME:
                        raise NetError("响应长度异常(%d)" % n)
                    data = self._recv_exact(n)
                    self._first_call = False   # 首帧已通，后续按正常超时
                    try:
                        return json.loads(_dec(data).decode("utf-8"))
                    except Exception as e:
                        raise NetError("解密失败或数据被篡改: %s" % e)
                except (NetError, OSError) as e:
                    last = e
                    self.close()
                    if attempt >= 1:
                        break
                    time.sleep(0.3)
                finally:
                    self._reset_timeout()
            if isinstance(last, NetError) and ("无法连接服务器" in str(last) or "地址解析" in str(last)):
                raise last
            raise NetError("请求「%s」失败：%s" % (cmd, last))

    def call_raw(self, cmd, timeout=None, **kwargs):
        """不重连的原始调用"""
        kwargs["cmd"] = cmd
        body = json.dumps(kwargs, ensure_ascii=False).encode("utf-8")
        raw = _enc(body)
        try:
            if self.sock and timeout:
                self.sock.settimeout(timeout)
            self.sock.sendall(struct.pack(">I", len(raw)) + raw)
            hdr = self._recv_exact(4)
            n = struct.unpack(">I", hdr)[0]
            if n <= 0 or n > MAX_FRAME:
                raise NetError("响应长度异常(%d)" % n)
            data = self._recv_exact(n)
            try:
                return json.loads(_dec(data).decode("utf-8"))
            except Exception as e:
                raise NetError("解密失败或数据被篡改: %s" % e)
        finally:
            self._reset_timeout()

    def login(self, username, password):
        r = self.call("login", timeout=CONNECT_TIMEOUT, username=username, password=password)
        if r.get("code") == 0:
            self.user = {"username": username, "password": password, **r}
        return r

    def ping(self, timeout=8):
        """连通性自检：服务端 ping 命令（免登录），用于穿透隧道排障"""
        r = self.call("ping", timeout=timeout)
        return r.get("code") == 0


def friendly_conn_error(addr, e):
    """把底层异常翻译成可直接排查的中文提示（远程/穿透连接时尤其重要）"""
    s = str(e)
    low = s.lower()
    if "timed out" in low or "超时" in s:
        head = "连接超时（Time Out）：\n%s\n" % s
    elif "拒绝" in s or "refused" in low or "无法连接" in s:
        head = "连不上服务器：\n%s\n" % s
    elif "解析" in s or "getaddrinfo" in low or "11001" in s or "11004" in s:
        head = ("地址无法解析（域名解析不到）：\n%s\n" % s
                + "程序已自动尝试公共 DNS（223.5.5.5 / 119.29.29.29）兜底。\n"
                  "若仍失败：客户端机器执行 ipconfig /flushdns 后重试，\n"
                  "或把该机器 DNS 改为 223.5.5.5，或直接填「公网IP:端口」。\n")
    else:
        head = "连接失败：\n%s\n" % s
    tip = ("\n请按顺序排查：\n"
           "1) 服务端是否已点「▶ 启动服务」（本机 127.0.0.1 连接也要先启动）；\n"
           "2) 远程连接：服务端「内网穿透」是否已启动，地址是否填界面显示的公网地址（含端口）；\n"
           "   穿透首次连接约需 15~40 秒属正常，请耐心等待，不要反复点登录；\n"
           "3) OpenFrp 隧道类型必须是「TCP」，隧道本地端口必须等于服务端端口（默认 5943）；\n"
           "4) 仍失败可稍等 1~2 分钟后重试（免费节点会冷却），或换节点 / 用局域网直连；\n"
           "5) 服务端 / 教师端 / 学生端三端版本必须一致，新旧版本协议不兼容。\n"
           "当前填写的地址：%s" % addr)
    return head + tip

def ping(host, port=None, timeout=8):
    """独立自检：不登录，仅验证 TCP+协议是否通（供穿透排障用）"""
    nc = NetClient(host, port or DEFAULT_PORT)
    nc.connect()
    r = nc.call("ping", timeout=timeout)
    nc.close()
    return r.get("code") == 0, r
