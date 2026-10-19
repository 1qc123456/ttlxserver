package com.ttlx.student;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

import javax.crypto.Cipher;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * 学生端网络层（原生版）——与桌面端 common/net_client.py 完全同一套协议：
 *   · TCP 5943，AES-256-GCM 帧：4 字节大端长度 + 12 字节 nonce + 密文(含16字节 tag)
 *   · 密钥：PBKDF2-HMAC-SHA256("ttlx-secure-transport-2026", salt="ttlx-transport",
 *           120000 轮, 32 字节)
 * 超时策略与桌面端一致：常规 30s；新建连接的首个请求放宽到 60s（穿透首帧慢）。
 */
public class NetClient {

    private static final String PASS = "ttlx-secure-transport-2026";
    private static final byte[] SALT = "ttlx-transport".getBytes(StandardCharsets.UTF_8);
    private static final int ITER = 120000;
    private static final int DEFAULT_TIMEOUT = 30000;
    private static final int FIRST_CALL_TIMEOUT = 60000;
    private static final int CONNECT_TIMEOUT = 20000;

    private static SecretKeySpec KEY;                 // 全局算一次（120000 轮较慢）
    private static synchronized SecretKeySpec key() throws Exception {
        if (KEY == null) {
            PBEKeySpec spec = new PBEKeySpec(PASS.toCharArray(), SALT, ITER, 256);
            SecretKeyFactory f = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
            KEY = new SecretKeySpec(f.generateSecret(spec).getEncoded(), "AES");
        }
        return KEY;
    }

    public final String host;
    public final int port;
    private Socket sock;
    private boolean firstCall = true;
    private String user = "", pwd = "";
    public JSONObject userInfo;                        // 登录后的用户信息

    public NetClient(String hostport) {
        String h = (hostport == null ? "" : hostport.trim());
        if (h.startsWith("http://")) h = h.substring(7);
        if (h.startsWith("https://")) h = h.substring(8);
        h = h.replace("：", ":").replaceAll("/+$", "");
        int p = 5943;
        int idx = h.lastIndexOf(':');
        if (idx > 0 && idx < h.length() - 1) {
            try { p = Integer.parseInt(h.substring(idx + 1)); h = h.substring(0, idx); }
            catch (NumberFormatException ignore) { }
        }
        if (h.isEmpty()) h = "127.0.0.1";
        this.host = h;
        this.port = p;
    }

    public String addr() { return host + ":" + port; }

    // ---------------- 连接 ----------------
    public void connect() throws Exception {
        close();
        sock = new Socket();
        sock.connect(new InetSocketAddress(host, port), CONNECT_TIMEOUT);
        sock.setTcpNoDelay(true);
        sock.setSoTimeout(FIRST_CALL_TIMEOUT);
        sock.setKeepAlive(true);
        firstCall = true;
    }

    public boolean isConnected() {
        return sock != null && sock.isConnected() && !sock.isClosed();
    }

    public void close() {
        synchronized (lock) {
            try { if (sock != null) sock.close(); } catch (Exception ignore) { }
            sock = null;
        }
    }

    // ---------------- 登录 ----------------
    public JSONObject login(String u, String p) throws Exception {
        this.user = u; this.pwd = p;
        JSONObject r = call("login", new JSONObject()
                .put("username", u).put("password", p));
        if (r.optInt("code") == 0) userInfo = r;
        return r;
    }

    // ---------------- 调用（线程安全 + 断线重连 + 会话失效自动重登） ----------------
    // ⚠ 教训：AES-GCM 帧协议不支持并发复用同一个 socket（多线程同时写帧会交叉错乱，
    //   服务端会断连/串包 → 出现「未登录」等灵异现象）。桌面端 Python 客户端用
    //   threading.RLock 串行化所有调用——这里保持一致，整个 call 全程持锁。
    private final Object lock = new Object();

    public JSONObject call(String cmd) throws Exception {
        return call(cmd, new JSONObject());
    }

    public JSONObject call(String cmd, JSONObject args) throws Exception {
        synchronized (lock) {
            Exception last = null;
            for (int attempt = 0; attempt < 3; attempt++) {
                try {
                    if (!isConnected()) connect();
                    JSONObject req = new JSONObject();
                    req.put("cmd", cmd);
                    if (args != null) {
                        for (java.util.Iterator<String> it = args.keys(); it.hasNext(); ) {
                            String k = it.next();
                            req.put(k, args.get(k));
                        }
                    }
                    JSONObject resp = roundTrip(req);
                    firstCall = false;
                    sock.setSoTimeout(DEFAULT_TIMEOUT);     // 首帧后恢复常规超时
                    // 会话失效（401）：重连并重新登录后重试
                    if (resp.optInt("code") == 401 && !user.isEmpty()
                            && !"login".equals(cmd) && attempt < 2) {
                        AppLog.e("net", cmd + " 返回401（会话失效），自动重登重试");
                        close();
                        connect();
                        JSONObject lr = roundTrip(new JSONObject()
                                .put("cmd", "login")
                                .put("username", user).put("password", pwd));
                        if (lr.optInt("code") != 0) throw new Exception("自动重登失败");
                        firstCall = false;
                        continue;
                    }
                    return resp;
                } catch (Exception e) {
                    last = e;
                    AppLog.e("net", "call " + cmd + " 第" + (attempt + 1) + "次失败: " + e);
                    close();
                    if (attempt < 2) {
                        try { Thread.sleep(300); } catch (InterruptedException ignore) { }
                        try {
                            connect();
                            if (!user.isEmpty() && !"login".equals(cmd)) {
                                JSONObject lr = roundTrip(new JSONObject()
                                        .put("cmd", "login")
                                        .put("username", user).put("password", pwd));
                                if (lr.optInt("code") != 0) throw new Exception("重新登录失败");
                                firstCall = false;
                            }
                        } catch (Exception e2) {
                            last = e2;
                        }
                    }
                }
            }
            throw new Exception("请求「" + cmd + "」失败："
                    + (last == null ? "未知错误" : last.getMessage()));
        }
    }

    private JSONObject roundTrip(JSONObject req) throws Exception {
        byte[] body = req.toString().getBytes(StandardCharsets.UTF_8);
        byte[] nonce = new byte[12];
        new SecureRandom().nextBytes(nonce);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, key(), new GCMParameterSpec(128, nonce));
        byte[] ct = c.doFinal(body);
        byte[] raw = new byte[12 + ct.length];
        System.arraycopy(nonce, 0, raw, 0, 12);
        System.arraycopy(ct, 0, raw, 12, ct.length);

        OutputStream out = sock.getOutputStream();
        ByteBuffer hdr = ByteBuffer.allocate(4);
        hdr.putInt(raw.length);
        out.write(hdr.array());
        out.write(raw);
        out.flush();

        InputStream in = sock.getInputStream();
        byte[] h = readN(in, 4);
        int n = ByteBuffer.wrap(h).getInt();
        if (n <= 0 || n > 64 * 1024 * 1024) throw new Exception("帧长度异常");
        byte[] frame = readN(in, n);
        byte[] nn = new byte[12];
        System.arraycopy(frame, 0, nn, 0, 12);
        Cipher d = Cipher.getInstance("AES/GCM/NoPadding");
        d.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, nn));
        byte[] plain = d.doFinal(frame, 12, frame.length - 12);
        return new JSONObject(new String(plain, StandardCharsets.UTF_8));
    }

    private static byte[] readN(InputStream in, int n) throws Exception {
        byte[] buf = new byte[n];
        int off = 0;
        while (off < n) {
            int r = in.read(buf, off, n - off);
            if (r < 0) throw new Exception("连接被关闭");
            off += r;
        }
        return buf;
    }

    // ---------------- 常用业务封装 ----------------
    public JSONObject stuHomeworks() throws Exception {
        return call("stuHomeworks");
    }

    public JSONObject startHomework(long hwId) throws Exception {
        return call("startHomework", new JSONObject().put("homeworkId", hwId));
    }

    public JSONObject startPractice(JSONArray parts) throws Exception {
        return call("startPractice", new JSONObject().put("parts", parts));
    }

    public JSONObject submit(long recordId, JSONArray answers) throws Exception {
        return call("submit", new JSONObject().put("recordId", recordId).put("answers", answers));
    }

    public JSONObject submitPractice(JSONArray answers) throws Exception {
        return call("submitPractice", new JSONObject().put("answers", answers));
    }

    public JSONObject myResult(long hwId) throws Exception {
        return call("myResult", new JSONObject().put("homeworkId", hwId));
    }

    public JSONObject recordDetail(long recId) throws Exception {
        return call("recordDetail", new JSONObject().put("recordId", recId));
    }

    public JSONObject wrongList() throws Exception { return call("wrongList"); }

    public JSONObject myHistory() throws Exception { return call("myHistory"); }

    public JSONObject getNodes() throws Exception { return call("getNodes"); }

    public JSONObject getMaterialZip(String qid) throws Exception {
        return call("getMaterialZip", new JSONObject().put("timuID", qid));
    }

    public JSONObject heartbeat(String mode, long id, String current, JSONArray qids, JSONArray answered)
            throws Exception {
        return call("heartbeat", new JSONObject()
                .put("mode", mode).put("id", id).put("current", current)
                .put("qids", qids).put("answered", answered));
    }

    /** 上传操作题（paperZip / code / mysqlZip / vapp+log 由 args 传入） */
    public JSONObject uploadOperate(boolean practice, long recordId, String qid, JSONObject args)
            throws Exception {
        JSONObject a = args == null ? new JSONObject() : args;
        if (practice) a.put("timuID", qid);
        else a.put("recordId", recordId).put("qid", qid);
        return call(practice ? "practiceGradeOperate" : "uploadOperate", a);
    }

    /** 连接失败时的可执行排查清单（与桌面端 friendly_conn_error 对齐） */
    public String friendlyError(Exception e) {
        return "连接 " + addr() + " 失败：\n" + e.getMessage() + "\n\n"
                + "排查清单：\n"
                + "1) 服务端是否已点「启动服务」；\n"
                + "2) 远程连接：穿透是否已开，地址是否是界面显示的公网地址（含端口）；\n"
                + "   穿透首次连接约需 15~40 秒属正常，请耐心等待；\n"
                + "3) 隧道类型必须是 TCP，本地端口必须等于服务端端口（默认 5943）；\n"
                + "4) 仍失败可等 1~2 分钟后重试，或在「更换节点」里换地址；\n"
                + "5) 服务端/教师端/学生端三端版本必须一致。";
    }

    /** 未使用的占位（保留 API 兼容） */
    public static byte[] empty() { return new byte[0]; }
    private static ByteArrayOutputStream unused() { return new ByteArrayOutputStream(); }
}
