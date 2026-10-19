package com.ttlx.student;

import android.content.Context;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 环境自动配置执行器（登录后后台自动开始，无需人工操作）。
 *
 * 状态供界面轮询显示：running / progress(-1=未知) / status 文案 / 日志尾部。
 * 与 SetupActivity 共用同一套逻辑，避免两处实现漂移。
 */
public class SetupRunner {

    public static volatile boolean running = false;
    public static volatile boolean done = false;
    public static volatile int progress = -1;          // 0-100，-1 表示不确定
    public static volatile String status = "未开始";
    private static final StringBuilder LOG = new StringBuilder();
    private static final Object LOCK = new Object();

    /** 配置日志落盘位置：只有落盘了，「分享日志」才带得走（原来只在内存里，出事完全看不到） */
    private static File logFile;

    public static String logTail(int maxChars) {
        synchronized (LOCK) {
            int n = LOG.length();
            return LOG.substring(Math.max(0, n - maxChars), n);
        }
    }

    private static void log(String msg) {
        String line = "[" + new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date())
                + "] " + msg;
        synchronized (LOCK) {
            LOG.append(line).append('\n');
            if (LOG.length() > 200000) LOG.delete(0, 50000);
        }
        // 追加到文件（容量上限 1MB，超了就截掉前半）
        File f = logFile;
        if (f == null) return;
        try {
            if (f.length() > 1024 * 1024) {
                byte[] all = java.nio.file.Files.readAllBytes(f.toPath());
                byte[] half = new byte[all.length / 2];
                System.arraycopy(all, all.length - half.length, half, 0, half.length);
                java.nio.file.Files.write(f.toPath(), half);
            }
            java.nio.file.Files.write(f.toPath(), (line + "\n").getBytes("UTF-8"),
                    java.nio.file.StandardOpenOption.CREATE,
                    java.nio.file.StandardOpenOption.APPEND);
        } catch (Exception ignore) { }
    }

    /** 已就绪 / 正在跑 → 什么都不做；否则启动后台配置线程 */
    public static void startIfNeeded(final Context ctx) {
        if (running) return;
        final Context app = ctx.getApplicationContext();
        SetupManager sm = new SetupManager(app);
        if (sm.isReady()) {
            // 环境可用，但可选组件（Chromium / WPS）缺失 —— 典型场景是上次初始化
            // 中途被打断，apt 全废，只有 X server + XFCE 装上了。
            // 这里自动补装一次（12 小时内不重复试，避免每次开 App 都白跑一轮）。
            String miss = sm.missingComponents();
            if (!miss.isEmpty() && repairDue(app)) {
                log("环境可用，但检测到组件缺失（" + miss + "）→ 自动补装一次…");
                markRepairTried(app);
                start(app);
                return;
            }
            done = true;
            status = "环境已就绪";
            progress = 100;
            return;
        }
        start(app);
    }

    // ---- 「组件缺失自动补装」节流（30 分钟最多一次） ----
    // 原来定 12 小时太长：真机上 WPS 装失败后，用户升级到修好的版本，
    // 自动补装却被节流挡住，只能自己发现要去点「修 复」。
    private static final String PREF = "ttlx_stu";
    private static final String KEY_REPAIR_AT = "auto_repair_at";
    private static final long REPAIR_INTERVAL_MS = 30L * 60 * 1000;

    private static boolean repairDue(Context ctx) {
        try {
            long last = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                    .getLong(KEY_REPAIR_AT, 0L);
            return System.currentTimeMillis() - last > REPAIR_INTERVAL_MS;
        } catch (Exception e) {
            return true;
        }
    }

    private static void markRepairTried(Context ctx) {
        try {
            ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit()
                    .putLong(KEY_REPAIR_AT, System.currentTimeMillis()).apply();
        } catch (Exception ignore) { }
    }

    /** 强制重新配置（忽略已有标记）—— 供「重新初始化容器」按钮使用 */
    public static void restart(final Context ctx) {
        if (running) return;
        new SetupManager(ctx.getApplicationContext()).clearReady();
        start(ctx);
    }

    private static void start(final Context ctx) {
        final Context app = ctx.getApplicationContext();
        logFile = new File(app.getFilesDir(), "setup.log");
        running = true;
        done = false;
        progress = 0;
        status = "准备中…";
        new Thread(() -> runSetup(app), "ttlx-setup-auto").start();
    }

    private static void runSetup(Context ctx) {
        SetupManager sm = new SetupManager(ctx);
        SetupManager.CB cb = new SetupManager.CB() {
            @Override public void log(String msg) {
                SetupRunner.log(msg);
                status = msg;
            }
            @Override public void progress(int pct) {
                progress = pct;
                status = "正在下载根文件系统 " + pct + "%";
            }
        };
        try {
            log("== 自动配置开始 ==");
            status = "解压运行组件…";
            sm.extractBinaries(cb);
            sm.extractBootstrap(cb);

            if (!sm.isRootfsReady()) {
                status = "探测下载镜像…";
                File tar = new File(sm.tmpDir(), "rootfs.tar.xz");
                if (tar.exists() && tar.length() > 80L * 1024 * 1024) {
                    log("复用已下载的 rootfs.tar.xz（" + (tar.length() / 1048576) + " MB）");
                } else {
                    java.util.List<String> urls = sm.findRootfsUrls(cb);
                    if (urls.isEmpty()) throw new Exception("所有镜像均不可用（请检查网络）");
                    sm.downloadRootfsWithFailover(urls, tar, cb);
                }
                status = "解压根文件系统…";
                progress = -1;
                sm.extractRootfs(tar, cb);
                if (!sm.checkRootfsHealthy()) {
                    log("!! 自检失败：/lib 或动态链接器缺失（符号链接未创建成功）");
                    throw new Exception("根文件系统解压不完整（符号链接失败），请把日志发给老师");
                }
                log("自检通过：/lib、动态链接器、/bin/bash 均可见 ✔");
                tar.delete();
            } else {
                log("根文件系统已存在，跳过下载");
            }

            status = "容器内自动配置（桌面/字体/浏览器/WPS…）";
            progress = -1;
            File inPkg = new File(sm.rootfsDir(), "root/ttlx-pkg");
            SetupManager.deleteRecursive(inPkg);
            copyDir(sm.pkgDir(), inPkg);
            chmodSh(inPkg);

            int code = sm.runScript("/root/ttlx-pkg/container_setup.sh", cb);
            log("配置脚本退出码：" + code);
            if (code == SetupManager.EXIT_STALLED) {
                throw new Exception("容器初始化卡死（长时间无输出）。常见原因："
                        + "① 本机是 x86 模拟器 / ARM 转译环境，proot 容器跑不起来（请换真机）；"
                        + "② 镜像站在安装中途不可达。可点『重试』。");
            }
            if (code != 0) throw new Exception("容器配置失败（退出码 " + code + "）");
            sm.markReady();
            // ★ 必须**重启**容器，不能只 startContainer：
            //   proot 的绑定参数（-b /tmp 等）与容器脚本都是**启动时**固定的。
            //   只 start 的话，容器已经在跑就直接返回，升级后新加的绑定/新脚本
            //   永远不生效 —— 真机日志里"还在等 40 秒"就是这么来的。
            ContainerBridge.restartContainer(ctx);
            status = "环境已就绪 ✔（桌面容器已按新配置重启）";
            progress = 100;
            done = true;
        } catch (Exception e) {
            log("!! 配置失败：" + e);
            status = "配置失败：" + e.getMessage();
        } finally {
            running = false;
        }
    }

    private static void copyDir(File src, File dst) {
        if (!src.exists()) return;
        if (src.isDirectory()) {
            dst.mkdirs();
            File[] cs = src.listFiles();
            if (cs != null) for (File c : cs) copyDir(c, new File(dst, c.getName()));
        } else {
            try {
                dst.getParentFile().mkdirs();
                java.io.FileInputStream in = new java.io.FileInputStream(src);
                java.io.FileOutputStream out = new java.io.FileOutputStream(dst);
                byte[] buf = new byte[65536];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                in.close();
                out.close();
                if (src.getName().endsWith(".sh")) dst.setExecutable(true, false);
            } catch (Exception ignore) { }
        }
    }

    private static void chmodSh(File dir) {
        File[] cs = dir.listFiles();
        if (cs == null) return;
        for (File c : cs) {
            if (c.isDirectory()) chmodSh(c);
            else if (c.getName().endsWith(".sh")) c.setExecutable(true, false);
        }
    }
}
