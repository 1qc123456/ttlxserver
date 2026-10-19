package com.ttlx.student;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 容器前台服务：常驻运行 proot（Xvnc + noVNC + XFCE + 学生端）。
 * - 切到后台 / 锁屏时容器不中断（带部分唤醒锁）
 * - 输出写入 filesDir/container.log，供 APK 内「日志」按钮查看
 */
public class ContainerService extends Service {

    public static final String ACTION_RESTART = "com.ttlx.student.RESTART";
    private static volatile Process sProc = null;
    private static volatile boolean sRunning = false;
    private PowerManager.WakeLock wakeLock;

    public static boolean isRunning() {
        return sRunning && sProc != null && sProc.isAlive();
    }

    /** 结束容器：proot 带 --kill-on-exit，杀主进程即可带走整棵进程树。
     *
     *  ⚠ 坑（已在模拟器上实测复现 ANR）：这里**绝不能在主线程 waitFor()**。
     *  proot 卡死（例如在不支持 ptrace 容器转译的环境里）时它收不到 SIGTERM，
     *  waitFor() 会永久阻塞；而 onStartCommand / onDestroy 都跑在主线程上，
     *  结果就是「TTLX学生端没有响应」。改成：先 destroy()，后台线程限时等待，
     *  超时直接 destroyForcibly()。 */
    public static void stop(Context ctx) {
        sRunning = false;
        final Process p = sProc;
        sProc = null;
        if (p == null) return;
        try { p.destroy(); } catch (Exception ignore) { }
        new Thread(() -> {
            try {
                if (!p.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) {
                    p.destroyForcibly();
                }
            } catch (Exception ignore) {
                try { p.destroyForcibly(); } catch (Exception ignore2) { }
            }
        }, "ttlx-container-stop").start();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        startForegroundCompat();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startForegroundCompat();
        if (intent != null && ACTION_RESTART.equals(intent.getAction())) {
            stop(this);
        }
        if (!isRunning()) {
            new Thread(this::runContainer, "ttlx-container").start();
        }
        return START_STICKY;
    }

    private void runContainer() {
        SetupManager sm = new SetupManager(this);
        if (!sm.isReady()) {
            appendLog("容器尚未完成初始化，先运行 SetupManager");
            return;
        }
        try {
            // 每次启动都刷新容器脚本：否则 APK 升级后容器里跑的还是旧 ttlx_start.sh
            try {
                sm.refreshContainerScripts();
                appendLog("容器脚本已刷新");
            } catch (Exception e) {
                appendLog("!! 刷新容器脚本失败：" + e);
            }
            ProcessBuilder pb = sm.buildProot("/root/ttlx-pkg/ttlx_start.sh");
            appendLog("=== 启动容器 ===");
            sProc = pb.start();
            sRunning = true;
            acquireWakeLock();
            try (BufferedReader r = new BufferedReader(
                    new InputStreamReader(sProc.getInputStream(), "UTF-8"))) {
                String line;
                while ((line = r.readLine()) != null) {
                    appendLog(line);
                }
            }
            sProc.waitFor();
            appendLog("=== 容器已退出（code=" + sProc.exitValue() + "）===");
        } catch (Exception e) {
            appendLog("容器启动失败：" + e);
        } finally {
            sRunning = false;
            releaseWakeLock();
        }
    }

    // ---- 日志 ----
    public File logFile() {
        return new File(getFilesDir(), "container.log");
    }

    private void appendLog(String line) {
        try (FileOutputStream fos = new FileOutputStream(logFile(), true);
             PrintWriter w = new PrintWriter(fos)) {
            String ts = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
            w.println("[" + ts + "] " + line);
        } catch (Exception ignore) { }
    }

    // ---- 通知 ----
    private void startForegroundCompat() {
        String chId = "ttlx-container";
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm.getNotificationChannel(chId) == null) {
                nm.createNotificationChannel(new NotificationChannel(
                        chId, "学生端容器", NotificationManager.IMPORTANCE_LOW));
            }
        }
        Intent it = new Intent(this, MainActivity.class);
        it.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, it,
                Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);

        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            b = new Notification.Builder(this, chId);
        } else {
            b = new Notification.Builder(this);
        }
        b.setSmallIcon(android.R.drawable.ic_menu_compass)
                .setContentTitle("学生端运行中")
                .setContentText("容器与桌面保持运行，点击返回")
                .setOngoing(true)
                .setContentIntent(pi);
        startForeground(1001, b.build());
    }

    // ---- 唤醒锁（锁屏后容器仍可运行） ----
    private void acquireWakeLock() {
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "ttlx:container");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception ignore) { }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        } catch (Exception ignore) { }
        wakeLock = null;
    }

    @Override
    public void onDestroy() {
        stop(this);
        releaseWakeLock();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
