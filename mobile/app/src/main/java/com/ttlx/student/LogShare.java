package com.ttlx.student;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 「导出并分享日志」：把诊断信息（设备信息 + 容器日志 + 环境配置日志 + 目录清单）
 * 汇总成一个 txt 写到 cache 目录，然后调用系统分享面板（微信/QQ/邮箱/文件管理器…）。
 */
public class LogShare {

    /** 生成日志文件（返回文件；失败返回 null） */
    public static File buildLogFile(Context ctx) {
        try {
            StringBuilder sb = new StringBuilder();
            String ts = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(new Date());
            sb.append("================ TTLX 学生端 诊断日志 ================\n");
            sb.append("导出时间 : ").append(ts).append("\n");
            sb.append("机型     : ").append(Build.MANUFACTURER).append(" ").append(Build.MODEL).append("\n");
            sb.append("系统     : Android ").append(Build.VERSION.RELEASE)
              .append(" (API ").append(Build.VERSION.SDK_INT).append(")\n");
            sb.append("CPU 架构 : ").append(Build.SUPPORTED_ABIS.length > 0 ? Build.SUPPORTED_ABIS[0] : "?").append("\n");
            try {
                android.content.pm.PackageInfo pi = ctx.getPackageManager()
                        .getPackageInfo(ctx.getPackageName(), 0);
                sb.append("应用版本 : ").append(pi.versionName)
                  .append(" (versionCode ").append(pi.versionCode).append(")\n");
            } catch (Exception e) {
                sb.append("应用版本 : ?\n");
            }
            sb.append("服务器   : ").append(TTLX.lastHost(ctx)).append("\n");
            SetupManager sm = new SetupManager(ctx);
            sb.append("环境状态 : ready=").append(sm.isReady())
              .append("  rootfs=").append(sm.isRootfsReady())
              .append("  当前阶段=").append(SetupRunner.status).append("\n");
            sb.append("容器运行 : ").append(ContainerService.isRunning()).append("\n");

            sb.append("\n================ 容器日志（container.log） ================\n");
            sb.append(readTextTail(new File(ctx.getFilesDir(), "container.log"), 40000));
            sb.append("\n================ 环境配置日志（setup.log，含 apt 输出） ================\n");
            sb.append(readTextTail(new File(ctx.getFilesDir(), "setup.log"), 60000));
            sb.append("\n================ 环境配置日志（本次运行） ================\n");
            sb.append(SetupRunner.logTail(40000));
            sb.append("\n================ App 运行日志（网络错误等） ================\n");
            sb.append(AppLog.tail(40000));
            sb.append("\n================ 容器内部日志 ================\n");
            File rootfs = ContainerBridge.rootfs(ctx);
            sb.append("--- /var/log/ttlx-setup.log（容器内初始化全过程） ---\n");
            sb.append(readTextTail(new File(rootfs, "var/log/ttlx-setup.log"), 60000));
            sb.append("\n");
            sb.append("--- /var/log/ttlx-apt.log（apt 原始输出，诊断安装失败用） ---\n");
            sb.append(readTextTail(new File(rootfs, "var/log/ttlx-apt.log"), 60000));
            sb.append("\n");
            sb.append("--- /var/log/ttlx-wps.log（WPS 安装全过程） ---\n");
            sb.append(readTextTail(new File(rootfs, "var/log/ttlx-wps.log"), 30000));
            sb.append("\n");
            sb.append("--- /var/log/ttlx-vapp.log（虚拟程序启动日志） ---\n");
            sb.append(readTextTail(new File(rootfs, "var/log/ttlx-vapp.log"), 30000));
            sb.append("\n");
            sb.append("--- /var/log/ttlx-vapp-install.log（mono 依赖安装） ---\n");
            sb.append(readTextTail(new File(rootfs, "var/log/ttlx-vapp-install.log"), 30000));
            sb.append("\n");
            for (String lg : new String[]{
                    "var/log/ttlx-desktop-diagnosis.log", "var/log/xvnc.log",
                    "var/log/websockify.log", "var/log/xfce.log"}) {
                sb.append("--- /").append(lg).append(" ---\n");
                sb.append(readTextTail(new File(rootfs, lg), 20000));
                sb.append("\n");
            }

            sb.append("\n================ 目录清单 ================\n");
            sb.append("files/ : ").append(listNames(ctx.getFilesDir())).append("\n");
            sb.append("rootfs exists : ").append(rootfs.exists()).append("\n");
            File wd = new File(rootfs, "root/studytemp/考生文件夹");
            if (wd.exists()) sb.append("考生文件夹 : ").append(listNames(wd)).append("\n");
            File pkg = new File(rootfs, "root/ttlx-pkg");
            if (pkg.exists()) sb.append("容器引导包 : ").append(listNames(pkg)).append("\n");

            File out = new File(ctx.getCacheDir(),
                    "TTLX日志_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".txt");
            try (FileOutputStream fo = new FileOutputStream(out)) {
                fo.write(sb.toString().getBytes("UTF-8"));
            }
            return out;
        } catch (Exception e) {
            return null;
        }
    }

    /** 导出并调起系统分享 */
    public static void share(Activity act) {
        File f = buildLogFile(act);
        if (f == null) {
            Toast.makeText(act, "日志导出失败", Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            Uri uri = FileProvider.getUriForFile(act,
                    act.getPackageName() + ".fileprovider", f);
            Intent i = new Intent(Intent.ACTION_SEND);
            i.setType("text/plain");
            i.putExtra(Intent.EXTRA_STREAM, uri);
            i.putExtra(Intent.EXTRA_SUBJECT, "TTLX 学生端日志");
            i.putExtra(Intent.EXTRA_TEXT, "TTLX 学生端诊断日志（见附件）："
                    + new SimpleDateFormat("MM-dd HH:mm", Locale.US).format(new Date()));
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            act.startActivity(Intent.createChooser(i, "导出并分享日志"));
        } catch (Exception e) {
            Toast.makeText(act, "分享失败：" + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private static String listNames(File dir) {
        File[] fs = dir.listFiles();
        if (fs == null) return "(空/不可读)";
        StringBuilder sb = new StringBuilder();
        for (File x : fs) {
            if (sb.length() > 800) { sb.append(" …"); break; }
            sb.append(x.getName());
            if (x.isDirectory()) sb.append("/");
            sb.append("  ");
        }
        return sb.toString();
    }

    private static String readTextTail(File f, int maxChars) {
        if (f == null || !f.exists()) return "(暂无)";
        try (BufferedReader r = new BufferedReader(new FileReader(f))) {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
                if (sb.length() > maxChars) sb.delete(0, maxChars / 3);
            }
            return sb.toString();
        } catch (Exception e) {
            return "(读取失败: " + e.getMessage() + ")";
        }
    }
}
