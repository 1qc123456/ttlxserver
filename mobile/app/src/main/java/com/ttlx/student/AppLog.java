package com.ttlx.student;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 应用级日志（环形缓冲，仅内存）：记录网络调用失败、素材下载失败等运行时错误，
 * 供「导出并分享日志」带走——容器日志之外，App 侧的错误也能被看到。
 */
public class AppLog {

    private static final StringBuilder BUF = new StringBuilder();
    private static final Object LOCK = new Object();
    private static final int MAX = 120000;
    private static final SimpleDateFormat TS =
            new SimpleDateFormat("MM-dd HH:mm:ss", Locale.US);

    public static void i(String tag, String msg) {
        append("I", tag, msg);
    }

    public static void e(String tag, String msg) {
        append("E", tag, msg);
    }

    private static void append(String level, String tag, String msg) {
        synchronized (LOCK) {
            BUF.append(TS.format(new Date()))
               .append(" [").append(level).append("][").append(tag).append("] ")
               .append(msg).append('\n');
            if (BUF.length() > MAX) BUF.delete(0, MAX / 3);
        }
    }

    public static String tail(int maxChars) {
        synchronized (LOCK) {
            int n = BUF.length();
            return BUF.substring(Math.max(0, n - maxChars), n);
        }
    }
}
