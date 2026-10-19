package com.ttlx.student;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.net.LocalSocket;
import android.net.LocalSocketAddress;
import android.os.Handler;
import android.os.Looper;
import android.util.AttributeSet;
import android.view.MotionEvent;
import android.view.View;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * 内置原生 VNC 客户端（RFB 3.3 / 3.8，直连容器内 Xvnc:5901）。
 *
 * 实现：None 认证 → 自选像素格式（LE 32bpp，R16/G8/B0）→ RAW 编码 + 增量刷新。
 *
 * 触摸手势（v3.0 改为「滑动=移动鼠标、单点=左键」，更接近真实鼠标）：
 *   · 单指滑动          = **移动鼠标指针**（不发按键，指到哪就是哪）
 *   · 单指轻点          = 鼠标左键单击
 *   · 长按 0.5 秒       = 鼠标右键单击
 *   · 长按后继续拖动    = 左键按住拖动（框选 / 拖拽文件）
 *   · 双指一起轻点      = 鼠标右键（更顺手）
 *   · 双指上下滑        = 滚轮上/下（button4/5）
 *   · 双指左右滑        = 滚轮左/右（button6/7）
 *   · 三指一起轻点      = 鼠标中键
 */
public class VncView extends View {

    public interface Listener {
        void onConnected(int width, int height);
        void onStatus(String msg);
        void onError(String msg);
    }

    private static final int MASK_LEFT = 1, MASK_MIDDLE = 2, MASK_RIGHT = 4;
    private static final int MASK_WUP = 8, MASK_WDOWN = 16, MASK_WLEFT = 32, MASK_WRIGHT = 64;
    private static final int LONG_PRESS_MS = 500;
    private static final int TAP_MS = 700;         // 多指「轻点」的时长上限（超过算长按，不触发点击）
    private static final float SLOP = 18f;         // 超过这个位移就算拖动，取消长按
    private static final float WHEEL_STEP = 36f;   // 每滚动这么多像素发一格滚轮

    private final Paint paint = new Paint(Paint.FILTER_BITMAP_FLAG);
    private final Rect dst = new Rect();
    private Bitmap fb;
    private int vw, vh;
    private Socket tcpSock;
    private LocalSocket localSock;
    private DataInputStream in;
    private OutputStream out;
    private final Object wlock = new Object();
    private volatile boolean stop;
    private Thread reader;

    /**
     * 输入事件写队列。
     *
     * ⚠ 教训（真机/模拟器实测）：触摸和按键事件是在**主线程**里产生的，
     * 如果直接在主线程 write() socket，Android 会抛 NetworkOnMainThreadException，
     * 异常被 catch 掉之后事件就**静默丢失**了 —— 表现为「点击偶尔灵、长按右键完全没反应」。
     * 所以输入事件一律入队，由专用写线程串行发出（FIFO 保序）。
     */
    private final LinkedBlockingQueue<byte[]> inQ = new LinkedBlockingQueue<>();
    private volatile boolean writerAlive;
    private Thread writer;

    private Listener listener;
    private String host = "127.0.0.1";
    private int port = 5901;
    /** 非空时走 unix domain socket（参考 tiny_container 的做法：完全绕开 TCP/网络栈）。 */
    private String unixPath = null;
    private float drawScale = 1f;
    private float drawDx, drawDy;
    private int lastX, lastY, mask;

    // ---- 手势状态 ----
    private final Handler gh = new Handler(Looper.getMainLooper());
    private Runnable longPressRunnable;
    private float downX, downY;
    private boolean scrollMode, twoFingerMoved;
    private long twoFingerDownAt;
    private float lastScrollX, lastScrollY;
    private int maxPointers;      // 本次手势最多同时按下几根手指（决定轻点是右键还是中键）

    /**
     * v3.0 手势状态机。
     *
     * 之前是「单指按住 = 左键按下」，于是**每次滑动都会产生一次左键拖动** ——
     * 在桌面里表现为「想移动鼠标，结果一路选中/拖动了图标」。
     * 现在拆成三态：
     *   · 滑动        → 只发 PointerEvent(mask=0)，纯移动指针
     *   · 快速抬手    → 补一次左键「按下+抬起」＝单击
     *   · 长按不动    → 抬手时发右键
     *   · 长按后拖动  → 真正按住左键拖（框选/拖拽）
     */
    private boolean moved;          // 本次单指手势是否超过 SLOP
    private boolean longPressFired; // 长按计时器已触发（还压着不动）
    private boolean leftDragging;   // 长按后拖动 → 左键按住中
    private long downAt;

    public VncView(Context c) { super(c); init(); }

    public VncView(Context c, AttributeSet a) { super(c, a); init(); }

    private void init() {
        paint.setFilterBitmap(true);
        setFocusable(true);
        setFocusableInTouchMode(true);
    }

    public void setServer(String h, int p) { host = h; port = p; unixPath = null; }

    /** 走 unix socket（文件系统命名空间，路径为容器/App 都能看到的真实路径） */
    public void setUnixSocket(String path) { unixPath = path; }

    public void setListener(Listener l) { listener = l; }

    public void connect() {
        // 先彻底停掉旧线程：否则 disconnect() 刚把 stop 置 true，connect() 又立刻置回
        // false，旧 reader 察觉不到自己该退出，两个线程会抢同一个 socket → 帧错乱。
        disconnect();
        stop = false;
        reader = new Thread(this::run, "rfb-client");
        reader.start();
    }

    public void disconnect() {
        stop = true;
        try { closeSocket(); } catch (Exception ignore) { }
        stopWriter();
        Thread t = reader;
        reader = null;
        if (t != null && t != Thread.currentThread()) {
            // socket 已 close，读线程会立刻抛异常退出，join 基本瞬间返回；
            // 上限设小一点，避免万一卡住时阻塞主线程（主线程阻塞 >5s 就是 ANR）
            try { t.join(1500); } catch (InterruptedException ignore) { }
        }
    }

    /**
     * 建立连接。两种方式：
     *   · unix domain socket（推荐，容器里 Xvnc 用 -rfbunixpath 监听）——
     *     完全绕开 TCP/网络栈，不受 localhost 网络策略、端口占用等影响。
     *   · TCP 127.0.0.1:5901（后备）
     */
    private void openSocket() throws IOException {
        if (unixPath != null && !unixPath.isEmpty()) {
            LocalSocket ls = new LocalSocket();
            ls.connect(new LocalSocketAddress(unixPath, LocalSocketAddress.Namespace.FILESYSTEM));
            localSock = ls;
            InputStream is = ls.getInputStream();
            OutputStream os = ls.getOutputStream();
            in = new DataInputStream(new BufferedInputStream(is, 1 << 16));
            out = new BufferedOutputStream(os);
            AppLog.i("vnc", "已连接 unix socket: " + unixPath);
        } else {
            Socket s = new Socket();
            s.connect(new InetSocketAddress(host, port), 8000);
            s.setTcpNoDelay(true);
            tcpSock = s;
            in = new DataInputStream(new BufferedInputStream(s.getInputStream(), 1 << 16));
            out = new BufferedOutputStream(s.getOutputStream());
            AppLog.i("vnc", "已连接 TCP " + host + ":" + port);
        }
    }

    private void closeSocket() {
        try { if (localSock != null) localSock.close(); } catch (Exception ignore) { }
        try { if (tcpSock != null) tcpSock.close(); } catch (Exception ignore) { }
        localSock = null;
        tcpSock = null;
    }

    /** 读超时（两种 socket 都支持）；0 = 不超时 */
    private void setReadTimeout(int ms) {
        try {
            if (localSock != null) localSock.setSoTimeout(ms);
            else if (tcpSock != null) tcpSock.setSoTimeout(ms);
        } catch (Exception ignore) { }
    }

    /** 启动输入事件写线程（握手完成后才能启动，保证握手帧先发） */
    private void startWriter() {
        writerAlive = true;
        writer = new Thread(() -> {
            try {
                while (writerAlive || !inQ.isEmpty()) {
                    byte[] b = inQ.poll(200, TimeUnit.MILLISECONDS);
                    if (b == null) continue;
                    synchronized (wlock) {
                        out.write(b);
                        out.flush();
                    }
                }
            } catch (Exception e) {
                AppLog.e("vnc", "写线程退出: " + e);
            }
        }, "rfb-writer");
        writer.start();
    }

    private void stopWriter() {
        writerAlive = false;
        Thread t = writer;
        writer = null;
        if (t != null && t != Thread.currentThread()) {
            try { t.join(1000); } catch (InterruptedException ignore) { }
        }
    }

    private void run() {
        try {
            openSocket();

            handshake();
            startWriter();          // 握手完成后才开始接收输入事件

            fb = Bitmap.createBitmap(Math.max(1, vw), Math.max(1, vh), Bitmap.Config.ARGB_8888);
            ui(() -> { if (listener != null) listener.onConnected(vw, vh); });

            // ---- SetPixelFormat：小端 32bpp/24depth/true-color R16 G8 B0 ----
            byte[] pf = new byte[20];
            pf[4] = 32; pf[5] = 24; pf[6] = 0; pf[7] = 1;
            pf[8] = 0; pf[9] = (byte) 255; pf[10] = 0; pf[11] = (byte) 255;
            pf[12] = 0; pf[13] = (byte) 255;
            pf[14] = 16; pf[15] = 8; pf[16] = 0;
            write(pf);

            // ---- SetEncodings：RAW ----
            byte[] enc = new byte[8];
            enc[0] = 2; enc[3] = 1;
            enc[7] = 0;
            write(enc);

            request(false);
            postStatus("桌面已连接 " + vw + "×" + vh);

            // ---- 消息循环 ----
            while (!stop) {
                int type = in.readUnsignedByte();
                if (type == 0) {                              // FramebufferUpdate
                    in.readUnsignedByte();
                    int rects = in.readUnsignedShort();
                    for (int r = 0; r < rects && !stop; r++) {
                        int rx = in.readUnsignedShort(), ry = in.readUnsignedShort();
                        int rw = in.readUnsignedShort(), rh = in.readUnsignedShort();
                        int encoding = in.readInt();
                        if (encoding == 0) {                  // RAW
                            int np = rw * rh;
                            byte[] px = readN(np * 4);
                            int[] pixels = new int[np];
                            for (int i = 0; i < np; i++) {
                                pixels[i] = 0xFF000000
                                        | ((px[4 * i + 2] & 0xff) << 16)
                                        | ((px[4 * i + 1] & 0xff) << 8)
                                        | (px[4 * i] & 0xff);
                            }
                            if (fb != null && rx + rw <= fb.getWidth() && ry + rh <= fb.getHeight()) {
                                fb.setPixels(pixels, 0, rw, rx, ry, rw, rh);
                            }
                        } else if (encoding == -223) {        // 伪编码：桌面改尺寸
                            vw = rx; vh = ry;
                            fb = Bitmap.createBitmap(Math.max(1, vw), Math.max(1, vh),
                                    Bitmap.Config.ARGB_8888);
                            request(false);
                            continue;
                        } else {
                            throw new IOException("不支持的编码: " + encoding);
                        }
                    }
                    postInvalidate();
                    request(true);
                } else if (type == 2) {
                    in.read();                                // Bell
                } else if (type == 3) {
                    // ServerCutText: type(1) + padding(3) + length(4) + text
                    readN(3);
                    int l = in.readInt();
                    if (l < 0) throw new IOException("ServerCutText 长度异常");
                    readN(l);
                } else {
                    throw new IOException("未知消息类型 " + type);
                }
            }
        } catch (Exception e) {
            if (!stop) {
                String m = e.getMessage() == null ? e.toString() : e.getMessage();
                ui(() -> { if (listener != null) listener.onError(m); });
            }
        }
    }

    /**
     * 握手。兼容两种服务器行为：
     *   · RFB 3.7/3.8：安全类型列表；选 None(1) 后**大多数**服务器会回 SecurityResult，
     *     但确实有实现在 None 时**不回**。所以这里用短超时探测：收到就校验，
     *     收不到就当没发（否则会一直死等到 socket 超时 → 判失败 → 桌面黑屏）。
     *   · RFB 3.3：服务器直接下发 uint32 安全类型，None 时无 SecurityResult。
     */
    private void handshake() throws Exception {
        byte[] ver = readN(12);
        String vs = new String(ver, "US-ASCII");
        int useMinor = 8;
        try {
            int minor = Integer.parseInt(vs.substring(8, 11));
            useMinor = Math.max(3, Math.min(minor, 8));
        } catch (Exception ignore) { }
        write(String.format("RFB 003.%03d\n", useMinor).getBytes("US-ASCII"));
        AppLog.i("vnc", "服务器版本 " + vs.trim() + " → 协商 3." + useMinor);

        if (useMinor >= 7) {
            int nsec = in.readUnsignedByte();
            if (nsec == 0) {
                int r = in.readInt();
                String reason = "";
                try { reason = new String(readN(r), "UTF-8"); } catch (Exception ignore) { }
                throw new IOException("服务器拒绝连接：" + reason);
            }
            byte[] sec = readN(nsec);
            boolean hasNone = false;
            for (byte b : sec) if ((b & 0xff) == 1) hasNone = true;
            if (!hasNone) throw new IOException("服务器要求认证（本机应为 None）");
            writeByte(1);                                     // 选 None

            // ---- 探测 SecurityResult ----
            byte[] sr = new byte[4];
            int got = 0;
            setReadTimeout(2500);
            try {
                while (got < 4) {
                    int r = in.read(sr, got, 4 - got);
                    if (r < 0) throw new IOException("VNC 连接已关闭");
                    got += r;
                }
            } catch (SocketTimeoutException te) {
                if (got != 0) throw new IOException("SecurityResult 读取不完整");
                AppLog.i("vnc", "服务器未发 SecurityResult（None 免认证），继续");
            } finally {
                setReadTimeout(0);
            }
            if (got == 4) {
                int code = ((sr[0] & 0xff) << 24) | ((sr[1] & 0xff) << 16)
                        | ((sr[2] & 0xff) << 8) | (sr[3] & 0xff);
                if (code != 0) throw new IOException("认证被拒绝（code=" + code + "）");
            }
        } else {
            int secType = in.readInt();
            if (secType != 1) throw new IOException("服务器要求认证（type=" + secType + "）");
        }

        writeByte(1);                                          // ClientInit: shared
        vw = in.readUnsignedShort();
        vh = in.readUnsignedShort();
        readN(16);                                             // ServerInit 像素格式
        int nl = in.readInt();
        readN(nl);                                             // 桌面名
    }

    // ================= 请求/输入 =================
    /**
     * FramebufferUpdateRequest：
     *   byte[0]=3, byte[1]=incremental, byte[2..3]=x, byte[4..5]=y, byte[6..7]=w, byte[8..9]=h
     *
     * ⚠ 这里曾经把 vw/vh 写进了 byte[4..7]（即 y 和 w），height 留 0 →
     *   服务端收到的是「y=1280, w=800, h=0」。TigerVNC/Xvnc 遇到 h==0 会**直接不回复**，
     *   于是画面永远全黑。首帧 x/y 必须为 0，且 w/h 必须是真实屏幕尺寸。
     */
    private void request(boolean incremental) throws IOException {
        byte[] b = new byte[10];
        b[0] = 3;
        b[1] = (byte) (incremental ? 1 : 0);
        b[2] = 0; b[3] = 0;
        b[4] = 0; b[5] = 0;
        b[6] = (byte) ((vw >> 8) & 0xff); b[7] = (byte) (vw & 0xff);
        b[8] = (byte) ((vh >> 8) & 0xff); b[9] = (byte) (vh & 0xff);
        write(b);
    }

    /** 鼠标：mask 位 1=左 2=中 4=右 8=滚上 16=滚下 32=滚左 64=滚右 */
    public void sendPointer(int x, int y, int mask) {
        writeInput(new byte[]{5, (byte) mask,
                (byte) ((x >> 8) & 0xff), (byte) (x & 0xff),
                (byte) ((y >> 8) & 0xff), (byte) (y & 0xff)});
    }

    /** 键盘：keysym（ASCII 直接用字符码；Enter=0xFF0D） */
    public void sendKey(int keysym, boolean down) {
        writeInput(new byte[]{4, (byte) (down ? 1 : 0), 0, 0,
                (byte) ((keysym >> 24) & 0xff), (byte) ((keysym >> 16) & 0xff),
                (byte) ((keysym >> 8) & 0xff), (byte) (keysym & 0xff)});
    }

    /** 输入事件：主线程调用，只入队；实际写 socket 由 rfb-writer 线程做 */
    private void writeInput(byte[] b) {
        if (!writerAlive) return;
        while (inQ.size() > 200) inQ.poll();      // 积压保护：丢最旧的
        inQ.offer(b);
    }

    /** 协议帧（握手 / SetPixelFormat / FramebufferUpdateRequest）：由 reader 线程同步写 */
    private void write(byte[] b) {
        synchronized (wlock) {
            if (out == null) return;
            try {
                out.write(b);
                out.flush();
            } catch (Exception e) {
                AppLog.e("vnc", "写失败: " + e);
            }
        }
    }

    private void writeByte(int v) { write(new byte[]{(byte) v}); }

    private byte[] readN(int n) throws IOException {
        byte[] buf = new byte[n];
        int off = 0;
        while (off < n) {
            int r = in.read(buf, off, n - off);
            if (r < 0) throw new IOException("VNC 连接已关闭");
            off += r;
        }
        return buf;
    }

    private void postStatus(String s) {
        ui(() -> { if (listener != null) listener.onStatus(s); });
    }

    private void ui(Runnable r) { post(r); }

    // ================= 渲染与触摸 =================
    @Override
    protected void onDraw(Canvas canvas) {
        canvas.drawColor(Color.BLACK);
        if (fb == null || vw <= 0 || vh <= 0) return;
        float sw = getWidth() / (float) vw;
        float sh = getHeight() / (float) vh;
        drawScale = Math.min(sw, sh);
        float w = vw * drawScale, h = vh * drawScale;
        drawDx = (getWidth() - w) / 2f;
        drawDy = (getHeight() - h) / 2f;
        dst.set((int) drawDx, (int) drawDy, (int) (drawDx + w), (int) (drawDy + h));
        canvas.drawBitmap(fb, null, dst, paint);
    }

    /** 屏幕坐标 → VNC 坐标 */
    private int[] toVnc(float x, float y) {
        int vx = (int) ((x - drawDx) / drawScale);
        int vy = (int) ((y - drawDy) / drawScale);
        vx = Math.max(0, Math.min(vw - 1, vx));
        vy = Math.max(0, Math.min(vh - 1, vy));
        return new int[]{vx, vy};
    }

    private void cancelLongPressTimer() {
        if (longPressRunnable != null) {
            gh.removeCallbacks(longPressRunnable);
            longPressRunnable = null;
        }
    }

    private void scheduleLongPress() {
        cancelLongPressTimer();
        longPressRunnable = () -> {
            // 长按**不立即**发右键 —— 否则「长按后拖动」会先弹一次右键菜单。
            // 只打标记，等抬手时再决定是右键还是拖动。
            longPressFired = true;
        };
        gh.postDelayed(longPressRunnable, LONG_PRESS_MS);
    }

    private void wheel(int buttonMask) {
        sendPointer(lastX, lastY, buttonMask);
        sendPointer(lastX, lastY, 0);
    }

    /** 单击（按下 + 抬起，同一位置） */
    private void click(int btn) {
        sendPointer(lastX, lastY, btn);
        sendPointer(lastX, lastY, 0);
    }

    @Override
    public boolean onTouchEvent(MotionEvent ev) {
        if (fb == null || vw <= 0) return false;
        switch (ev.getActionMasked()) {
            case MotionEvent.ACTION_DOWN: {
                downX = ev.getX(); downY = ev.getY();
                downAt = System.currentTimeMillis();
                scrollMode = false; twoFingerMoved = false;
                moved = false; longPressFired = false; leftDragging = false;
                maxPointers = 1;
                int[] p = toVnc(downX, downY);
                lastX = p[0]; lastY = p[1];
                mask = 0;
                sendPointer(lastX, lastY, 0);     // 只把指针移过去，不按键
                scheduleLongPress();
                return true;
            }
            case MotionEvent.ACTION_POINTER_DOWN: {
                cancelLongPressTimer();
                longPressFired = false;
                if (leftDragging) {               // 多指介入 → 松开左键，避免"粘住"
                    sendPointer(lastX, lastY, 0);
                    leftDragging = false;
                }
                maxPointers = Math.max(maxPointers, ev.getPointerCount());
                if (ev.getPointerCount() >= 2) {
                    AppLog.i("vnc", "POINTER_DOWN ptrs=" + ev.getPointerCount());
                }
                if (ev.getPointerCount() >= 2 && !scrollMode) {
                    mask = 0;
                    scrollMode = true;
                    twoFingerMoved = false;
                    twoFingerDownAt = System.currentTimeMillis();
                    lastScrollY = (ev.getY(0) + ev.getY(1)) / 2f;
                    lastScrollX = (ev.getX(0) + ev.getX(1)) / 2f;
                }
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                maxPointers = Math.max(maxPointers, ev.getPointerCount());
                if (ev.getPointerCount() >= 2) {
                    if (!scrollMode) {
                        scrollMode = true;
                        twoFingerMoved = false;
                        twoFingerDownAt = System.currentTimeMillis();
                        lastScrollY = (ev.getY(0) + ev.getY(1)) / 2f;
                        lastScrollX = (ev.getX(0) + ev.getX(1)) / 2f;
                        return true;
                    }
                    float cy = (ev.getY(0) + ev.getY(1)) / 2f;
                    float cx = (ev.getX(0) + ev.getX(1)) / 2f;
                    float dy = cy - lastScrollY;
                    float dx = cx - lastScrollX;
                    if (Math.abs(dy) >= Math.abs(dx) && Math.abs(dy) > WHEEL_STEP) {
                        wheel(dy > 0 ? MASK_WUP : MASK_WDOWN);   // 手指下滑 = 内容上滚
                        lastScrollY = cy;
                        twoFingerMoved = true;
                    } else if (Math.abs(dx) > WHEEL_STEP) {
                        wheel(dx > 0 ? MASK_WLEFT : MASK_WRIGHT);
                        lastScrollX = cx;
                        twoFingerMoved = true;
                    }
                    return true;
                }

                // ---- 单指 ----
                if (Math.abs(ev.getX() - downX) > SLOP || Math.abs(ev.getY() - downY) > SLOP) {
                    if (!moved) {
                        AppLog.i("vnc", "单指开始滑动 → 移动鼠标（不发按键）");
                    }
                    moved = true;
                    cancelLongPressTimer();
                }
                int[] p = toVnc(ev.getX(), ev.getY());
                lastX = p[0]; lastY = p[1];

                if (longPressFired && moved && !leftDragging) {
                    // 长按后继续拖 → 左键按住，开始框选/拖拽
                    leftDragging = true;
                    AppLog.i("vnc", "长按后拖动 → 左键拖拽");
                }
                // 拖动中带左键，否则纯移动指针
                sendPointer(lastX, lastY, leftDragging ? MASK_LEFT : 0);
                return true;
            }
            case MotionEvent.ACTION_POINTER_UP: {
                cancelLongPressTimer();
                if (ev.getPointerCount() - 1 <= 1) {
                    long dt = System.currentTimeMillis() - twoFingerDownAt;
                    boolean quick = !twoFingerMoved && dt < TAP_MS;
                    if (maxPointers >= 2) {
                        AppLog.i("vnc", "POINTER_UP ptrs=" + ev.getPointerCount()
                                + " max=" + maxPointers + " moved=" + twoFingerMoved
                                + " dt=" + dt + " quick=" + quick + " → "
                                + (maxPointers >= 3 ? "中键" : "右键"));
                    }
                    if (quick) {
                        // 双指一起轻点 = 右键；三指一起轻点 = 中键
                        click(maxPointers >= 3 ? MASK_MIDDLE : MASK_RIGHT);
                    }
                    scrollMode = false;
                    mask = 0;
                }
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                cancelLongPressTimer();
                boolean cancelled = ev.getActionMasked() == MotionEvent.ACTION_CANCEL;
                if (maxPointers >= 2) {
                    AppLog.i("vnc", "UP/CANCEL action=" + ev.getActionMasked()
                            + " ptrs=" + ev.getPointerCount() + " max=" + maxPointers);
                    scrollMode = false;
                    mask = 0;
                    return true;
                }
                if (leftDragging) {
                    sendPointer(lastX, lastY, 0);        // 松开左键
                    leftDragging = false;
                    AppLog.i("vnc", "单指抬手：结束左键拖拽");
                } else if (!cancelled && !moved) {
                    // 没有位移 → 算「点」：长按过就是右键，否则左键
                    AppLog.i("vnc", "单指抬手：点击 " + (longPressFired ? "右键" : "左键")
                            + "（耗时 " + (System.currentTimeMillis() - downAt) + "ms）");
                    click(longPressFired ? MASK_RIGHT : MASK_LEFT);
                } else {
                    AppLog.i("vnc", "单指抬手：只移动了鼠标（moved=" + moved
                            + " cancelled=" + cancelled + "）");
                }
                mask = 0;
                scrollMode = false;
                longPressFired = false;
                return true;
            }
            default:
                return true;
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        cancelLongPressTimer();
        disconnect();
        super.onDetachedFromWindow();
    }
}
