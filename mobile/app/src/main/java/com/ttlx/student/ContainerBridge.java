package com.ttlx.student;

import android.content.Context;
import android.content.Intent;
import android.os.Build;

import java.io.File;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * 容器桥：手机侧（Java）与 proot 容器（Debian）之间的对接。
 *
 * 关键映射：容器路径 /root/studytemp/… ↔ 手机真实路径 filesDir/rootfs/root/studytemp/…
 * —— 手机端下载的素材直接写进 rootfs 里的考生文件夹，容器里的 WPS 就能打开；
 *    容器里保存的作答文件，手机端交卷时直接打包上传。
 */
public class ContainerBridge {

    public static File rootfs(Context c) {
        return new File(c.getFilesDir(), "rootfs");
    }

    /** 容器内绝对路径 → 手机上的实际文件路径 */
    public static File hostPath(Context c, String containerPath) {
        String rel = containerPath.startsWith("/") ? containerPath.substring(1) : containerPath;
        return new File(rootfs(c), rel);
    }

    /** 考生文件夹（容器内 /root/studytemp/考生文件夹/<qid>）在手机上的真实路径 */
    public static File questionDir(Context c, String qid) {
        File d = hostPath(c, "/root/studytemp/考生文件夹/" + qid);
        d.mkdirs();
        return d;
    }

    public static String questionDirInContainer(String qid) {
        return "/root/studytemp/考生文件夹/" + qid;
    }

    // ---------------- 容器进程控制 ----------------
    public static void startContainer(Context c) {
        Intent svc = new Intent(c, ContainerService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(svc);
        else c.startService(svc);
    }

    public static void restartContainer(Context c) {
        Intent svc = new Intent(c, ContainerService.class);
        svc.setAction(ContainerService.ACTION_RESTART);
        c.startService(svc);
    }

    /** 等桌面端口就绪（6080=noVNC） */
    public static boolean waitDesktop(int seconds) {
        for (int i = 0; i < seconds; i++) {
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress("127.0.0.1", 6080), 1200);
                return true;
            } catch (Exception ignore) { }
            try { Thread.sleep(1000); } catch (InterruptedException ignore) { }
        }
        return false;
    }

    // ---------------- 在容器里打开文件 / 文件夹 ----------------
    /** 异步：确保容器在跑 → 在容器桌面里用默认程序打开（Office 走 WPS、网页走浏览器） */
    public static void openInContainer(final Context c, final String containerPath,
                                       final Callback cb) {
        new Thread(() -> {
            try {
                boolean up = waitDesktop(60);
                if (!up) {
                    startContainer(c);
                    up = waitDesktop(60);
                }
                if (!up) { post(cb, "桌面容器启动超时，请稍后再试（或点顶栏「桌面」查看）"); return; }
                SetupManager sm = new SetupManager(c);
                String cmd = "DISPLAY=:1 bash /root/ttlx-pkg/open_in_container.sh '"
                        + containerPath.replace("'", "'\\''") + "'";
                ProcessBuilder pb = sm.buildProotShell(cmd);
                Process p = pb.start();
                // 限时等待：proot 卡死时不能无限等，否则界面一直停在「正在容器中打开…」
                if (!p.waitFor(60, java.util.concurrent.TimeUnit.SECONDS)) {
                    p.destroyForcibly();
                    post(cb, "在容器中打开超时（60 秒）。可点顶栏「桌面」手动操作。");
                    return;
                }
                post(cb, null);
            } catch (Exception e) {
                post(cb, "打开失败：" + e.getMessage());
            }
        }, "ttlx-open").start();
    }

    // ---------------- 进桌面后自动摆出本题 ----------------
    /**
     * 打开某题的「考生文件夹」+ 题目主文档（真机反馈：操作题进桌面后什么都没打开）。
     *
     * 用 **detached proot**（不带 `--kill-on-exit`）：thunar / WPS 是长期运行的 GUI，
     * 带 `--kill-on-exit` 的话 proot 一退出就把它们杀掉，窗口一闪而过。
     * 而且**不等它返回**（GUI 会一直跑着），只把输出丢掉。
     */
    public static void autoOpenQuestion(final Context c, final String qid) {
        if (qid == null || qid.isEmpty()) return;
        new Thread(() -> {
            try {
                if (!waitDesktop(60)) {
                    startContainer(c);
                    if (!waitDesktop(60)) return;
                }
                SetupManager sm = new SetupManager(c);
                String cmd = "bash /root/ttlx-pkg/open_question.sh '"
                        + qid.replace("'", "'\\''") + "'";
                ProcessBuilder pb = sm.buildProotShellDetached(cmd);
                pb.redirectErrorStream(true);
                // Android 的 ProcessBuilder.Redirect 没有 DISCARD，直接追加到日志文件
                // （顺便留个排查痕迹：为什么没自动打开）
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(
                        new File(c.getFilesDir(), "open.log")));
                pb.start();      // 故意不等：GUI 会一直跑
                AppLog.i("open", "已请求自动打开本题 " + qid + " 的文件夹/文档");
            } catch (Exception e) {
                AppLog.e("open", "自动打开失败: " + e);
            }
        }, "ttlx-autoopen").start();
    }

    private static void post(final Callback cb, final String err) {
        if (cb == null) return;
        new android.os.Handler(android.os.Looper.getMainLooper()).post(() -> cb.done(err));
    }

    // ---------------- 启动虚拟程序（Win10 设置 / Edge / 邮件） ----------------
    /**
     * 在容器桌面里用 mono 启动本题的虚拟程序。
     *
     * 为什么能跑：这三个 exe 是 .NET Framework WinForms 程序，Debian arm64 有 mono
     * 可以运行（窗口渲染在容器桌面 DISPLAY=:1 上）。程序会把操作记录（判分文件）
     * 写在**考生文件夹/<qid>/** 里，交卷时 App 直接读它上传判分。
     *
     * @param qid   题号
     * @param vapp  win10 / edge / email
     */
    public static void launchVapp(final Context c, final String qid, final String vapp,
                                  final Callback cb) {
        new Thread(() -> {
            try {
                boolean up = waitDesktop(60);
                if (!up) {
                    startContainer(c);
                    up = waitDesktop(60);
                }
                if (!up) { post(cb, "桌面容器还没起来，请先打开桌面再试。"); return; }
                SetupManager sm = new SetupManager(c);
                // 首次可能需要现装 mono（约 1~3 分钟），所以给足超时
                String cmd = "bash /root/ttlx-pkg/vapp_run.sh '"
                        + qid.replace("'", "'\\''") + "' '"
                        + vapp.replace("'", "'\\''") + "'";
                // ⚠ 必须用 detached 版本：带 --kill-on-exit 时 proot 一退出就会
                // 把 mono 一起杀掉，虚拟程序窗口会一闪而过。
                ProcessBuilder pb = sm.buildProotShellDetached(cmd);
                pb.redirectErrorStream(true);
                Process p = pb.start();
                StringBuilder out = new StringBuilder();
                try (java.io.BufferedReader r = new java.io.BufferedReader(
                        new java.io.InputStreamReader(p.getInputStream(), "UTF-8"))) {
                    String line;
                    while ((line = r.readLine()) != null) out.append(line).append('\n');
                }
                if (!p.waitFor(420, java.util.concurrent.TimeUnit.SECONDS)) {
                    p.destroyForcibly();
                    post(cb, "启动虚拟程序超时（420 秒）。\n\n" + tail(out.toString()));
                    return;
                }
                int code = p.exitValue();
                if (code == 0) {
                    AppLog.i("vapp", "虚拟程序已启动：" + vapp + " qid=" + qid);
                    post(cb, null);
                } else {
                    AppLog.e("vapp", "虚拟程序启动失败 code=" + code + "\n" + out);
                    post(cb, "启动失败（退出码 " + code + "）。\n\n" + tail(out.toString()));
                }
            } catch (Exception e) {
                post(cb, "启动失败：" + e.getMessage());
            }
        }, "ttlx-vapp").start();
    }

    private static String tail(String s) {
        if (s == null) return "";
        s = s.trim();
        return s.length() > 1200 ? s.substring(s.length() - 1200) : s;
    }

    public interface Callback { void done(String err); }
}
