package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.method.ScrollingMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * 容器桌面：**内置原生 VNC 客户端**（VncView，直连 Xvnc:5901）为主显示，
 * noVNC 网页模式（WebView）作为后备。
 *
 * 顶栏两行（横屏/竖屏都不会被裁切）：
 *   第一行：标题 + 「返 回」
 *   第二行：重 启 / 键 盘 / 诊 断 / 分享日志（等宽，永远放得下）
 */
public class DesktopActivity extends AppCompatActivity {

    private static final String VNC_URL =
            "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=2000";
    /** Termux:X11 的显示号（X server 跑在 App 进程内） */
    private static final String X11_DISPLAY = ":0";

    /** 从答题界面带过来的当前题目（浮窗显示用） */
    public static final String EXTRA_STEM = "stem";
    public static final String EXTRA_QID = "qid";
    public static final String EXTRA_VAPP = "vapp";

    private FrameLayout root;
    private VncView vnc;
    private WebView web;
    private TextView status;
    private MaterialButton fbBtn;
    private PcKeyboard keyboard;
    private FloatPanel toolbar;      // 可拖动/可折叠的工具栏（不再压住桌面）
    private FloatPanel qwin;         // 可随意移动的题目浮窗
    private String stem = "";
    private String qid = "";
    private String vapp = "";
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean usingWeb = false;
    private int vncRetries = 0;
    private boolean autoOpened = false;   // 本次进入是否已自动打开过考生文件夹/题目

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        stem = getIntent().getStringExtra(EXTRA_STEM);
        if (stem == null) stem = "";
        qid = getIntent().getStringExtra(EXTRA_QID);
        if (qid == null) qid = "";
        vapp = getIntent().getStringExtra(EXTRA_VAPP);
        if (vapp == null) vapp = "";
        setContentView(buildUi());

        SetupManager sm = new SetupManager(this);
        if (!sm.isReady()) {
            // 不要只弹个 Toast 就退出——把当前进度摊开，并给一条明确的路
            new MaterialAlertDialogBuilder(this)
                    .setTitle("桌面环境还没准备好")
                    .setCancelable(false)
                    .setMessage("当前进度：" + SetupRunner.status
                            + "\n\n首次使用需要先在后台下载并配置操作题环境（约 10~20 分钟）。")
                    .setPositiveButton("查看进度", (d, w) -> {
                        startActivity(new Intent(this, SetupActivity.class));
                        finish();
                    })
                    .setNegativeButton("返回", (d, w) -> finish())
                    .show();
            return;
        }
        // ⚠ Termux:X11 在真机上连续 5 次都没能起来（`X0` socket 始终不出现，
        //   服务跑在独立进程 `:xserver` 里，失败原因拿不到 logcat）。
        //   而 **Xvnc + 内置 VNC 客户端一路是通的**（unix socket 直连，4.2MB 画面已收到）。
        //   所以这里**不再等待 X11**，直接用 VNC —— 桌面出得更快，也不再弹
        //   "App 侧 X server 未就绪，改用 VNC 模式" 这种让人困惑的提示。
        //   （startXServer()/openX11() 代码保留，将来要再试随时可以接回来。）
        ContainerBridge.startContainer(this);
        status.setText("正在启动桌面（首次约 30~60 秒）…");
        // 第一次进桌面：自动弹一次手势教程（后台照常启动，不耽误）
        GestureHelp.showOnce(this);
        startVncMode();
    }

    // ==================== 主路：Termux:X11 ====================

    /**
     * X socket 目录 = **App 私有 tmp**（`<filesDir>/tmp`）。
     *
     * ⚠ 这里踩过一个坑：原来传 `TMPDIR=<filesDir>/rootfs/tmp` 并等同一个路径，
     * 结果真机上 `X0` **从来没出现过**。对照参考项目 tiny_container 才发现：
     * 它的 `TMPDIR` 就是 `<filesDir>/tmp`（不是 rootfs 里的），并且把这个目录
     * **bind 成容器里的 `/tmp`** —— 两边路径天然重合，不管 Termux:X11 认不认 TMPDIR 都对。
     * 现在 SetupManager 的 proot 参数里加了 `-b <filesDir>/tmp:/tmp`，与此处保持一致。
     */
    private File x11TmpDir() {
        File t = new File(getFilesDir(), "tmp");
        t.mkdirs();
        return t;
    }

    /**
     * 找 socket：**新位置（<filesDir>/tmp）优先，老位置（rootfs/tmp）兜底**。
     *
     * 为什么要兜底：容器**没重启**时仍在用旧的 proot 参数（`/tmp` = rootfs/tmp），
     * 此时 socket 落在老位置。不兜底的话 VNC 会退到 TCP（还能用，但白绕一圈）、
     * X11 则永远找不到。两边都看，升级期间也能正常工作。
     */
    private File findSock(String name) {
        File a = new File(new File(x11TmpDir(), name).getAbsolutePath());
        if (a.exists()) return a;
        File b = new File(ContainerBridge.rootfs(this), "tmp/" + name);
        return b.exists() ? b : a;
    }

    private String x11SockPath() {
        return findSock(".X11-unix/X0").getAbsolutePath();
    }

    /** 在 App 进程内启动 Termux:X11 的 X server（DISPLAY=:0） */
    private void startXServer() {
        try {
            File rootfs = ContainerBridge.rootfs(this);
            File tmp = x11TmpDir();
            File xkb = new File(rootfs, "usr/share/X11/xkb");
            new File(tmp, ".X11-unix").mkdirs();
            Intent it = new Intent(this, com.termux.x11.CmdEntryPointService.class);
            it.setAction(com.termux.x11.CmdEntryPointService.ACTION_START);
            it.putExtra(com.termux.x11.CmdEntryPointService.EXTRA_ARGS,
                    new String[]{"", X11_DISPLAY});
            // TERMUX_X11_OVERRIDE_PACKAGE：告诉 Termux:X11「你被嵌在 com.ttlx.student 里」，
            // 否则它会按 com.termux.x11 的路径找文件/配置而失败（参考 tiny_container 的做法）。
            // TERMUX_X11_DEBUG=1：让它把启动日志写出来，排查 X0 起不来时很有用。
            it.putExtra(com.termux.x11.CmdEntryPointService.EXTRA_ENV_KEYS,
                    new String[]{"TMPDIR", "XKB_CONFIG_ROOT",
                            "TERMUX_X11_OVERRIDE_PACKAGE", "TERMUX_X11_DEBUG"});
            it.putExtra(com.termux.x11.CmdEntryPointService.EXTRA_ENV_VALUES,
                    new String[]{tmp.getAbsolutePath(), xkb.getAbsolutePath(),
                            getPackageName(), "1"});
            startService(it);
            AppLog.i("x11", "已请求启动 X server: DISPLAY=" + X11_DISPLAY
                    + " TMPDIR=" + tmp + " XKB=" + xkb);
        } catch (Throwable t) {
            AppLog.e("x11", "启动 X server 失败: " + t);
        }
    }

    /** 打开 Termux:X11 自带的桌面界面（它负责画面渲染、触摸转鼠标、键盘） */
    private void openX11() {
        try {
            Intent it = new Intent(this, com.termux.x11.MainActivity.class);
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            startActivity(it);
        } catch (Throwable t) {
            AppLog.e("x11", "打开 X11 界面失败: " + t);
            showFallback("打开 X11 桌面界面失败：" + t, true);
        }
    }

    // ==================== 备路：内置 VNC 客户端 ====================

    private void startVncMode() {
        new Thread(() -> {
            final String sock = vncUnixPath();
            final int mode = waitVnc(60);
            ui.post(() -> {
                if (mode == 0) {
                    showFallback("桌面启动失败：App 侧 X server 没起来，容器里的 Xvnc 也没就绪。", true);
                    return;
                }
                status.setVisibility(View.GONE);
                fbBtn.setVisibility(View.GONE);
                vnc.setVisibility(View.VISIBLE);
                web.setVisibility(View.GONE);
                if (mode == 1) {
                    vnc.setUnixSocket(sock);
                } else {
                    vnc.setServer("127.0.0.1", 5901);
                }
                vnc.setListener(new VncView.Listener() {
                    @Override public void onConnected(int w, int h) {
                        vncRetries = 0;
                        autoOpenedOnce();
                    }
                    @Override public void onStatus(String s) {
                        ui.post(() -> status.setText(s));
                    }
                    @Override public void onError(String msg) {
                        ui.post(() -> {
                            // 自动重连：Xvnc 端口已开但会话还没就绪时会先 EOF，
                            // 手点「重启」太折腾；这里先自己退避重试几次。
                            if (vncRetries < 3) {
                                vncRetries++;
                                status.setVisibility(View.VISIBLE);
                                status.setText("VNC 连接中断，正在重连（" + vncRetries + "/3）…");
                                ui.postDelayed(() -> {
                                    vnc.disconnect();
                                    vnc.connect();
                                }, 3000);
                                return;
                            }
                            showFallback("VNC 连接失败：" + msg, true);
                        });
                    }
                });
                vnc.connect();
            });
        }, "ttlx-vnc-mode").start();
    }

    /** 容器里 Xvnc 的 unix socket 在 App 侧的真实路径（新老两个位置都找，见 findSock） */
    private String vncUnixPath() {
        return findSock(".ttlx.vnc").getAbsolutePath();
    }

    /**
     * 等待 VNC 就绪。返回 0=超时，1=unix socket 可用，2=TCP 5901 可用。
     * 先等 unix socket（参考 tiny_container：绕开网络栈，最省事）。
     */
    private int waitVnc(int seconds) {
        File sock = new File(vncUnixPath());
        for (int i = 0; i < seconds; i++) {
            if (sock.exists()) return 1;
            if (portOk(5901)) return 2;
            try { Thread.sleep(1000); } catch (InterruptedException ignore) { }
        }
        return 0;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        // ---- 原生 VNC 视图 ----
        vnc = new VncView(this);
        root.addView(vnc, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // ---- 网页后备视图（默认隐藏）----
        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setUseWideViewPort(true);
        ws.setLoadWithOverviewMode(true);
        ws.setCacheMode(WebSettings.LOAD_NO_CACHE);
        if (android.os.Build.VERSION.SDK_INT >= 21)
            ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        web.setWebViewClient(new WebViewClient());
        web.setBackgroundColor(Color.BLACK);
        web.setVisibility(View.GONE);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // ---- 状态提示（居中）+ 后备按钮 ----
        LinearLayout centerBox = new LinearLayout(this);
        centerBox.setOrientation(LinearLayout.VERTICAL);
        centerBox.setGravity(Gravity.CENTER_HORIZONTAL);

        status = new TextView(this);
        status.setText("正在启动桌面容器…");
        status.setTextColor(Color.parseColor("#8FA3C8"));
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER);
        status.setPadding(dp(20), dp(20), dp(20), dp(20));
        centerBox.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        fbBtn = new MaterialButton(this);
        fbBtn.setText("改用网页模式");
        fbBtn.setTextColor(Color.WHITE);
        fbBtn.setBackgroundColor(Color.parseColor("#33405C"));
        fbBtn.setAllCaps(false);
        fbBtn.setVisibility(View.GONE);
        fbBtn.setOnClickListener(v -> showWeb());
        centerBox.addView(fbBtn, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(44)));

        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.gravity = Gravity.CENTER;
        root.addView(centerBox, clp);

        // ---- 浮动工具栏（可拖动 + 可折叠；默认折叠成一个小条，**不挡桌面**）----
        toolbar = new FloatPanel(this, "桌面菜单");
        toolbar.useKey("dlg_toolbar");
        toolbar.setBody(buildToolbarBody());
        // 固定宽度：body 里是 MATCH_PARENT 的等宽行，若父容器用 WRAP_CONTENT 会塌成 0 宽
        FrameLayout.LayoutParams tlp = new FrameLayout.LayoutParams(
                dp(280), ViewGroup.LayoutParams.WRAP_CONTENT);
        tlp.gravity = Gravity.TOP | Gravity.START;
        root.addView(toolbar, tlp);
        toolbar.placeTopLeft(8, 8);
        toolbar.restore(true);        // 默认收起，桌面完整可见（点「＋」展开）

        // ---- 题目浮窗（可随意移动；不需要悬浮窗权限）----
        if (!stem.isEmpty()) {
            qwin = new FloatPanel(this, "本题要求");
            qwin.useKey("dlg_qwin");
            qwin.setBody(buildStemView(stem));
            FrameLayout.LayoutParams qlp = new FrameLayout.LayoutParams(
                    dp(300), ViewGroup.LayoutParams.WRAP_CONTENT);
            qlp.gravity = Gravity.TOP | Gravity.START;
            root.addView(qwin, qlp);
            qwin.placeTopLeft(8, 64);
            qwin.restore(false);      // 题目浮窗默认展开（要能看见要求）
        }

        // ---- PC 虚拟键盘（默认隐藏）----
        keyboard = new PcKeyboard(this, new PcKeyboard.Sender() {
            @Override public void key(int keysym, boolean down) {
                if (vnc != null) vnc.sendKey(keysym, down);
            }
            @Override public void onCollapse() {
                keyboard.setVisibility(View.GONE);
            }
        });
        keyboard.setVisibility(View.GONE);
        FrameLayout.LayoutParams klp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        klp.gravity = Gravity.BOTTOM;
        root.addView(keyboard, klp);

        return root;
    }

    /** 工具栏内容（放进 FloatPanel 的 body）：两行按钮，等宽，不会被裁 */
    private View buildToolbarBody() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.VERTICAL);
        bar.setPadding(0, 0, 0, 0);

        java.util.List<String> names = new java.util.ArrayList<>();
        java.util.List<View.OnClickListener> acts = new java.util.ArrayList<>();
        names.add("返 回");      acts.add(v -> finish());
        names.add("重 启");      acts.add(v -> restart());
        names.add("键 盘");      acts.add(v -> toggleKeyboard());
        names.add("手 势");      acts.add(v -> GestureHelp.show(this));
        names.add("诊 断");      acts.add(v -> showDiagnosis());
        names.add("修 复");      acts.add(v -> repairEnv());
        names.add("分享日志");   acts.add(v -> LogShare.share(this));
        if (!vapp.isEmpty()) {
            names.add("虚拟程序"); acts.add(v -> launchVapp());
        }

        LinearLayout row = null;
        for (int i = 0; i < names.size(); i++) {
            if (i % 3 == 0) {
                row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                if (i > 0) rlp.topMargin = dp(4);
                bar.addView(row, rlp);
            }
            MaterialButton b = btn(names.get(i), acts.get(i));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(30), 1f);
            lp.leftMargin = dp(3);
            lp.rightMargin = dp(3);
            row.addView(b, lp);
        }
        return bar;
    }

    /** 题目浮窗的内容：可滚动的题干 */
    private View buildStemView(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(12);
        tv.setTextColor(Color.parseColor("#E6EDF8"));
        tv.setLineSpacing(dp(2), 1f);
        tv.setTextIsSelectable(true);
        ScrollView sv = new ScrollView(this);
        sv.addView(tv, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        android.util.DisplayMetrics dm = getResources().getDisplayMetrics();
        int h = (int) (Math.min(dm.widthPixels, dm.heightPixels) * 0.42f);
        sv.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, h));
        return sv;
    }

    /** 在容器里启动本题的虚拟程序（Win10 / Edge / 邮件），用 mono 跑 .NET exe */
    private void launchVapp() {
        if (vapp.isEmpty()) return;
        Toast.makeText(this, "正在启动虚拟程序…（首次要先装 mono，可能要几分钟）",
                Toast.LENGTH_SHORT).show();
        ContainerBridge.launchVapp(this, qid, vapp, msg -> {
            if (msg == null) return;
            ui.post(() -> {
                // ⚠ 这个回调最长可能在 7 分钟后才回来（首次装 mono）。用户很可能早就
                //   退出桌面了 —— 直接 show() 会抛 BadTokenException 把 App 崩掉。
                if (!alive()) return;
                new MaterialAlertDialogBuilder(this)
                        .setTitle("虚拟程序启动失败")
                        .setMessage(msg + "\n\n可点「诊 断」看 /var/log/ttlx-vapp.log 与 "
                                + "ttlx-vapp-install.log（mono 是否装上）。")
                        .setPositiveButton("知道了", null)
                        .show();
            });
        });
    }

    /** Activity 还活着吗（弹窗前必须判，否则 BadTokenException 直接崩） */
    private boolean alive() {
        return !isFinishing() && !isDestroyed();
    }

    /** 桌面第一次连上后，自动把「考生文件夹」和题目主文档打开（只做一次） */
    private void autoOpenedOnce() {
        if (autoOpened || qid.isEmpty()) return;
        autoOpened = true;
        ContainerBridge.autoOpenQuestion(this, qid);
    }

    /**
     * 「修 复」：重新跑一遍容器初始化（container_setup.sh）。
     *
     * 用途：容器是旧版本装的（例如升级前装的环境），缺 mono / 缺办公套件时，
     * `isReady()` 认为"环境可用"就不会再跑初始化 —— 于是新加的组件永远装不上。
     * 这里给用户一个明确的入口，会补齐缺失组件（不会重下 rootfs）。
     */
    private void repairEnv() {
        new MaterialAlertDialogBuilder(this)
                .setTitle("修复容器环境")
                .setMessage("将重新检查并补装容器内组件（办公套件 / Chromium / 虚拟程序运行时 mono 等）。\n\n"
                        + "当前缺失：" + new SetupManager(this).missingComponents() + "\n\n"
                        + "已有内容不会重新下载，约 1~5 分钟，可在进度页看日志。")
                .setPositiveButton("开始修复", (d, w) -> {
                    SetupRunner.restart(this);
                    startActivity(new Intent(this, SetupActivity.class));
                    finish();
                })
                .setNegativeButton("取消", null)
                .show();
    }

    /** 出错/超时时：提示写到居中状态栏，并让后备按钮出现；withHint=true 时附上诊断入口 */
    private void showFallback(String msg, boolean withHint) {
        status.setVisibility(View.VISIBLE);
        status.setText(msg + (withHint
                ? "\n\n左上角「桌面菜单」里点开「诊 断」可看原因，或点「重 启」再试。" : ""));
        fbBtn.setVisibility(View.VISIBLE);
    }

    private MaterialButton btn(String text, View.OnClickListener l) {
        MaterialButton b = new MaterialButton(this);
        b.setText(text);
        b.setTextSize(12);
        b.setAllCaps(false);
        b.setTextColor(Color.WHITE);
        b.setBackgroundColor(Color.parseColor("#33405C"));
        b.setPadding(dp(8), 0, dp(8), 0);
        b.setCornerRadius(dp(14));
        b.setInsetTop(0);
        b.setInsetBottom(0);
        b.setMinWidth(0);
        b.setOnClickListener(l);
        return b;
    }

    private void showWeb() {
        usingWeb = true;
        vnc.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        status.setVisibility(View.GONE);
        fbBtn.setVisibility(View.GONE);
        ContainerBridge.startContainer(this);
        new Thread(() -> {
            waitPort(6080, 60);
            ui.post(() -> web.loadUrl(VNC_URL));
        }, "ttlx-web").start();
    }

    // ---- 键盘 ----
    private void toggleKeyboard() {
        keyboard.setVisibility(
                keyboard.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
    }

    private boolean waitPort(int port, int seconds) {
        for (int i = 0; i < seconds; i++) {
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress("127.0.0.1", port), 1200);
                return true;
            } catch (Exception ignore) { }
            try { Thread.sleep(1000); } catch (InterruptedException ignore) { }
        }
        return false;
    }

    private void restart() {
        new MaterialAlertDialogBuilder(this)
                .setTitle("重启容器")
                .setMessage("将重启桌面容器（约 30 秒）。")
                .setPositiveButton("重启", (d, w) -> {
                    ContainerBridge.restartContainer(this);
                    usingWeb = false;
                    vncRetries = 0;
                    web.setVisibility(View.GONE);
                    vnc.setVisibility(View.VISIBLE);
                    fbBtn.setVisibility(View.GONE);
                    status.setVisibility(View.VISIBLE);
                    status.setText("正在重启…");
                    // ⚠ waitVnc 会阻塞最长 45 秒，绝不能放在主线程（否则必 ANR）
                    new Thread(() -> {
                        try { Thread.sleep(2000); } catch (InterruptedException ignore) { }
                        final int mode = waitVnc(45);
                        ui.post(() -> {
                            if (mode != 0) {
                                status.setVisibility(View.GONE);
                                fbBtn.setVisibility(View.GONE);
                                if (mode == 1) vnc.setUnixSocket(vncUnixPath());
                                else vnc.setServer("127.0.0.1", 5901);
                                vnc.disconnect();
                                vnc.connect();
                            } else {
                                showFallback("重启超时（unix socket 与 5901 端口都没就绪）。", true);
                            }
                        });
                    }, "ttlx-vnc-restart").start();
                })
                .setNegativeButton("取消", null)
                .show();
    }

    // ==================== 诊断 ====================

    /** 读文件尾部（容器日志就在本应用私有目录里，可直接读） */
    private String tail(File f, int maxChars) {
        if (f == null || !f.isFile()) return "（不存在：" + (f == null ? "?" : f.getName()) + "）";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new FileReader(f))) {
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
                if (sb.length() > maxChars * 2) sb.delete(0, maxChars);
            }
        } catch (Exception e) {
            return "（读取失败：" + e + "）";
        }
        int n = sb.length();
        return n > maxChars ? "…\n" + sb.substring(n - maxChars) : sb.toString();
    }

    /** 现场体检 + 容器日志，直接在手机上把原因摊开。
     *  ⚠ 端口探测是网络操作，必须放到后台线程做（主线程 connect 会抛
     *    NetworkOnMainThreadException，结果永远显示"不可连"，反而误导排查）。 */
    private void showDiagnosis() {
        new Thread(() -> {
            final boolean ok5901 = portOk(5901);
            final boolean ok6080 = portOk(6080);
            final boolean sockExists = new File(vncUnixPath()).exists();
            final boolean x0Exists = new File(x11SockPath()).exists();
            ui.post(() -> {
                if (!alive()) return;      // 弹窗前判活，避免 BadTokenException
                showDiagnosisDialog(ok5901, ok6080, sockExists, x0Exists);
            });
        }, "ttlx-diag").start();
    }

    private void showDiagnosisDialog(boolean ok5901, boolean ok6080,
                                     boolean sockExists, boolean x0Exists) {
        StringBuilder sb = new StringBuilder();
        SetupManager sm = new SetupManager(this);
        sb.append("== 环境 ==\n");
        sb.append("rootfs 就绪 : ").append(sm.isRootfsReady() ? "是" : "否").append('\n');
        sb.append("环境就绪   : ").append(sm.isReady() ? "是 ✔" : "否 ✗（桌面组件缺失）").append('\n');
        sb.append("就绪明细   : ").append(sm.readyDetail()).append('\n');
        sb.append("容器服务   : ").append(ContainerService.isRunning() ? "运行中" : "未运行").append('\n');
        sb.append("显示模式   : ").append(usingWeb ? "网页(noVNC)" : "Termux:X11 / VNC").append('\n');
        sb.append("重连次数   : ").append(vncRetries).append('\n');
        sb.append("tmp 目录   : ").append(x11TmpDir()).append('\n');
        sb.append("X11 :0 sock: ").append(x0Exists ? "有 ✔（主路可用）" : "无").append('\n');
        sb.append("unix sock  : ").append(sockExists ? "有 ✔" : "无").append('\n');
        sb.append("5901 可连  : ").append(ok5901 ? "是" : "否").append('\n');
        sb.append("6080 可连  : ").append(ok6080 ? "是" : "否").append('\n');
        sb.append("屏幕方向   : ").append(getResources().getConfiguration().orientation
                == android.content.res.Configuration.ORIENTATION_LANDSCAPE ? "横屏" : "竖屏").append('\n');
        sb.append("可选组件缺 : ").append(sm.missingComponents().isEmpty()
                ? "无 ✔" : sm.missingComponents()).append('\n');

        File rootfs = ContainerBridge.rootfs(this);
        sb.append("\n== 容器内初始化 /var/log/ttlx-setup.log（含 apt 输出）==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-setup.log"), 3500));
        sb.append("\n== apt 原始输出 /var/log/ttlx-apt.log（安装失败看这里）==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-apt.log"), 2500));
        sb.append("\n== WPS 安装 /var/log/ttlx-wps.log（WPS 为什么没装上）==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-wps.log"), 2500));
        sb.append("\n== 虚拟程序 /var/log/ttlx-vapp.log（exe 为什么跑不起来）==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-vapp.log"), 2500));
        sb.append("\n== 虚拟程序依赖 /var/log/ttlx-vapp-install.log（mono 装没装上）==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-vapp-install.log"), 2000));
        sb.append("\n== 桌面诊断 /var/log/ttlx-desktop-diagnosis.log ==\n");
        sb.append(tail(new File(rootfs, "var/log/ttlx-desktop-diagnosis.log"), 3000));
        sb.append("\n== Xvnc /var/log/xvnc.log ==\n");
        sb.append(tail(new File(rootfs, "var/log/xvnc.log"), 3000));
        sb.append("\n== 兜底 Xvfb /var/log/xvfb.log ==\n");
        sb.append(tail(new File(rootfs, "var/log/xvfb.log"), 1200));
        sb.append("\n== 兜底 x11vnc /var/log/x11vnc.log ==\n");
        sb.append(tail(new File(rootfs, "var/log/x11vnc.log"), 1200));
        sb.append("\n== XFCE /var/log/xfce.log ==\n");
        sb.append(tail(new File(rootfs, "var/log/xfce.log"), 1200));
        sb.append("\n== 容器启动 container.log ==\n");
        sb.append(tail(new File(getFilesDir(), "container.log"), 2500));
        sb.append("\n== App 侧日志 ==\n");
        sb.append(AppLog.tail(2500));

        TextView tv = new TextView(this);
        tv.setText(sb.toString());
        tv.setTextSize(10);
        tv.setTextColor(Color.parseColor("#E8EEF8"));
        tv.setPadding(dp(12), dp(12), dp(12), dp(12));
        tv.setMovementMethod(new ScrollingMovementMethod());
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.parseColor("#141B2A"));
        sv.addView(tv);
        // 横屏手机很矮：给诊断内容一个不超过屏幕 62% 的固定高度，保证弹窗不被裁
        android.util.DisplayMetrics dm = getResources().getDisplayMetrics();
        int maxH = (int) (Math.min(dm.widthPixels, dm.heightPixels) * 0.62f);
        sv.setLayoutParams(new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, maxH));

        new MaterialAlertDialogBuilder(this)
                .setTitle("桌面诊断")
                .setView(sv)
                .setPositiveButton("关闭", null)
                .setNeutralButton("分享日志", (d, w) -> LogShare.share(this))
                .show();
    }

    private boolean portOk(int port) {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", port), 800);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public void onBackPressed() {
        finish();      // 返回原生答题界面，容器由前台服务托管
    }
}
