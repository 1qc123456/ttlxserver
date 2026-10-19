package com.ttlx.student;

import android.content.Context;
import android.content.res.AssetManager;

import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.tukaani.xz.XZInputStream;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * TTLX 学生端（APK 版）· 环境管理器
 *
 * 首启全自动（无需任何手动操作）：
 *   ① 解出 proot 与依赖库（随 APK 打包）
 *   ② 从国内镜像站下载 Debian arm64 根文件系统（约 95MB，多镜像自动切换）
 *   ③ Java 侧解压 tar.xz 到私有目录
 *   ④ 在容器内执行 container_setup.sh：换镜像源 → 装桌面/VNC/Python/浏览器/WPS
 *   ⑤ 写完成标记
 * 之后每次启动：只跑 ttlx_start.sh（Xvnc + noVNC + XFCE + 学生端）
 */
public class SetupManager {

    public interface CB {
        void log(String msg);
        void progress(int pct);
    }

    private final Context ctx;
    private final File files;

    public SetupManager(Context c) {
        this.ctx = c.getApplicationContext();
        this.files = ctx.getFilesDir();
    }

    public File binDir()      { return new File(files, "bin"); }
    public File tmpDir()      { return new File(files, "tmp"); }
    public File rootfsDir()   { return new File(files, "rootfs"); }
    public File pkgDir()      { return new File(files, "pkg"); }
    public File readyFlag()   { return new File(files, ".ttlx_ready"); }

    public boolean isRootfsReady() {
        return new File(rootfsDir(), "bin/bash").exists();
    }

    /**
     * 环境是否真的可用。
     *
     * ⚠ 血的教训：原来只看 `.ttlx_ready` 标记文件，而容器里的 container_setup.sh
     * **无条件**写这个标记 —— 于是 apt 全部失败、Xvnc/websockify/startxfce4 一个都没装上时，
     * App 依然显示「环境已就绪」，桌面却永远出不来（真机日志：
     * `Xvnc: command not found` / `websockify: command not found` / `startxfce4: command not found`）。
     * 现在必须**真的在 rootfs 里查到桌面二进制**才算就绪。
     */
    public boolean isReady() {
        if (!readyFlag().exists() || !isRootfsReady()) return false;
        if (desktopPresent()) return true;
        // 标记在、但组件不在 → 标记是脏的，清掉，让上层重新走一遍初始化
        clearReady();
        return false;
    }

    /** rootfs 里是否真的装上了「X server + XFCE」 */
    public boolean desktopPresent() {
        File r = rootfsDir();
        boolean x = false, xfce = false;
        for (String p : new String[]{"usr/bin/Xvnc", "usr/bin/Xtigervnc", "usr/bin/Xvfb"}) {
            if (new File(r, p).exists()) { x = true; break; }
        }
        for (String p : new String[]{"usr/bin/startxfce4", "usr/bin/xfce4-session"}) {
            if (new File(r, p).exists()) { xfce = true; break; }
        }
        return x && xfce;
    }

    /** 就绪判定的文字说明（诊断页用） */
    public String readyDetail() {
        File r = rootfsDir();
        StringBuilder sb = new StringBuilder();
        sb.append("readyFlag=").append(readyFlag().exists())
          .append(" rootfs=").append(isRootfsReady())
          .append(" desktop=").append(desktopPresent());
        for (String p : new String[]{"usr/bin/Xvnc", "usr/bin/Xvfb", "usr/bin/startxfce4",
                "usr/bin/websockify"}) {
            sb.append('\n').append("    ").append(p).append(" : ")
              .append(new File(r, p).exists() ? "有" : "无");
        }
        String miss = missingComponents();
        sb.append('\n').append("    可选组件缺失 : ")
          .append(miss.isEmpty() ? "无 ✔" : miss);
        return sb.toString();
    }

    /**
     * 桌面可用、但**可选组件**缺失的清单（Chromium / WPS）。
     *
     * ⚠ 背景：初始化中途被打断（重启 App / 系统杀进程）会让 apt 全部失败，
     * 于是 X server + XFCE 装上了（桌面能用），但 Chromium / WPS 没装上。
     * 这类"半成品"不该让整个环境判为不可用，但也不能放着不管 ——
     * 上层（SetupRunner）会据此**自动补装一次**。
     */
    public String missingComponents() {
        File r = rootfsDir();
        StringBuilder sb = new StringBuilder();
        boolean chromium = new File(r, "usr/bin/chromium").exists()
                || new File(r, "usr/bin/chromium-browser").exists()
                || new File(r, "usr/lib/chromium/chromium").exists();
        if (!chromium) sb.append("Chromium ");
        boolean wps = new File(r, "usr/bin/wps").exists()
                || new File(r, "usr/bin/wps-office").exists()
                || new File(r, "opt/kingsoft/wps-office/office6/wps").exists()
                // WPS 装不上时 LibreOffice 是回退，也算"办公套件就位"
                || new File(r, "usr/bin/soffice").exists()
                || new File(r, "usr/bin/libreoffice").exists();
        if (!wps) sb.append("办公套件 ");
        // 虚拟程序（Win10 设置 / Edge / 邮件）依赖 mono
        boolean mono = new File(r, "usr/bin/mono").exists()
                || new File(r, "usr/bin/mono-sgen").exists();
        if (!mono) sb.append("mono(虚拟程序) ");
        return sb.toString().trim();
    }

    // ================= ① 解出二进制与引导包 =================
    private static final String[] BIN_ASSETS = {
            "proot", "proot-legacy", "libtalloc.so.2", "libandroid-shmem.so",
    };

    /**
     * 选定的 proot 变体（自检后决定）。
     * 参考 tiny_container：它同时带 proot-latest 和 proot-classic 两个变体，
     * 因为 proot 与设备内核的兼容性因机而异 —— 一个跑不起来时换另一个往往就好了。
     * 这里 `proot` = tiny_container 打过补丁的 proot-latest，`proot-legacy` = 原来的。
     */
    private static volatile String prootVariant = null;

    /** 当前应使用的 proot 可执行文件路径 */
    public File prootBin() {
        String v = prootVariant;
        if (v == null) v = "proot";
        File f = new File(binDir(), v);
        return f.isFile() ? f : new File(binDir(), "proot");
    }

    /**
     * proot 自检：真的 exec 一次 `proot --version`。
     * 很多「容器起不来」其实是在这一步就挂了（缺库 / 内核不支持 ptrace / 被 SELinux 拦），
     * 提前测出来能立刻定位，也能自动切到可用的变体。
     */
    public void pickProot(CB cb) {
        for (String v : new String[]{"proot", "proot-legacy"}) {
            File bin = new File(binDir(), v);
            if (!bin.isFile() || bin.length() == 0) continue;
            bin.setExecutable(true, false);
            String out = runCapture(new String[]{bin.getAbsolutePath(), "--version"});
            if (out != null && !out.trim().isEmpty()) {
                prootVariant = v;
                cb.log("proot 自检通过：" + v + " → " + out.trim().replace('\n', ' '));
                return;
            }
            cb.log("!! proot 自检失败：" + v + "（" + (out == null ? "无法执行" : out.trim()) + "）");
        }
        prootVariant = null;
        cb.log("!! 两个 proot 变体都无法运行 —— 本机很可能不支持 proot 容器");
    }

    /** 跑一条命令并抓输出；失败返回 null */
    private String runCapture(String[] argv) {
        Process p = null;
        try {
            ProcessBuilder pb = new ProcessBuilder(argv);
            pb.directory(files);
            pb.redirectErrorStream(true);
            pb.environment().put("LD_LIBRARY_PATH", binDir().getAbsolutePath());
            pb.environment().put("PROOT_TMP_DIR", tmpDir().getAbsolutePath());
            pb.environment().put("PROOT_NO_SECCOMP", "1");
            pb.environment().put("PROOT_LOADER",
                    new File(files, "libexec/proot/loader").getAbsolutePath());
            p = pb.start();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(
                    new InputStreamReader(p.getInputStream(), "UTF-8"))) {
                String line;
                long deadline = System.currentTimeMillis() + 8000;
                while ((line = r.readLine()) != null) {
                    sb.append(line).append('\n');
                    if (System.currentTimeMillis() > deadline) break;
                }
            }
            if (!p.waitFor(8, java.util.concurrent.TimeUnit.SECONDS)) {
                p.destroyForcibly();
                return null;
            }
            return p.exitValue() == 0 ? sb.toString() : null;
        } catch (Throwable t) {
            if (p != null) { try { p.destroyForcibly(); } catch (Throwable ignore) { } }
            return null;
        }
    }

    public void extractBinaries(CB cb) throws IOException {
        binDir().mkdirs();
        tmpDir().mkdirs();
        for (String a : BIN_ASSETS) {
            File dst = new File(binDir(), a);
            if (!dst.exists() || dst.length() == 0) {
                copyAsset(a, dst);
            }
            dst.setExecutable(true, false);
            dst.setReadable(true, false);
        }
        // proot 的 loader（动态链接程序必需！缺失会导致容器内 execve 全部 ENOENT）
        // —— 绝不静默跳过，缺了必须大声报出来。
        File ldir = new File(files, "libexec/proot");
        ldir.mkdirs();
        for (String a : new String[]{"loader", "loader32"}) {
            File dst = new File(ldir, a);
            if (!dst.exists() || dst.length() == 0) {
                copyAsset(a, dst);          // 缺失会抛 IOException → 上层如实报错
            }
            dst.setExecutable(true, false);
            dst.setReadable(true, false);
        }
        File loader = new File(ldir, "loader");
        if (!loader.isFile() || loader.length() == 0) {
            throw new IOException("proot loader 缺失（APK 打包异常，请反馈）");
        }
        cb.log("proot 运行环境就绪（含 loader " + loader.length() + " 字节）");
        pickProot(cb);      // 自检 + 自动选变体
    }

    public void extractBootstrap(CB cb) throws IOException {
        File pkg = pkgDir();
        deleteRecursive(pkg);
        pkg.mkdirs();
        copyTree("bootstrap", pkg);
        cb.log("引导脚本就绪");
    }

    private void copyAsset(String name, File dst) throws IOException {
        AssetManager am = ctx.getAssets();
        try (InputStream in = am.open(name);
             OutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    private void copyTree(String assetDir, File dstDir) throws IOException {
        AssetManager am = ctx.getAssets();
        String[] list = am.list(assetDir);
        if (list == null) return;
        dstDir.mkdirs();
        for (String name : list) {
            String child = assetDir + "/" + name;
            String[] sub = am.list(child);
            File d = new File(dstDir, name);
            if (sub != null && sub.length > 0) {
                copyTree(child, d);
            } else {
                try {
                    copyAsset(child, d);
                    if (name.endsWith(".sh")) d.setExecutable(true, false);
                } catch (IOException e) {
                    d.mkdirs();   // 空目录
                }
            }
        }
    }

    // ================= ② rootfs 下载（多镜像自动切换） =================
    private static final String[] ROOTFS_MIRRORS = {
            "https://mirrors.tuna.tsinghua.edu.cn",
            "https://mirrors.ustc.edu.cn",
            "https://mirrors.bfsu.edu.cn",
            "https://mirror.nju.edu.cn",
    };
    private static final String LXC_PATH =
            "/lxc-images/images/debian/bookworm/arm64/default/";

    /** 解析各镜像的最新日期，返回全部 rootfs.tar.xz 候选 URL（按探测顺序） */
    public java.util.List<String> findRootfsUrls(CB cb) {
        java.util.List<String> urls = new java.util.ArrayList<>();
        for (String m : ROOTFS_MIRRORS) {
            try {
                cb.log("探测镜像：" + m);
                String listing = httpGet(m + LXC_PATH, 15000);
                Matcher mt = Pattern.compile("(\\d{8}_\\d{2}:\\d{2})").matcher(listing);
                String best = null;
                while (mt.find()) {
                    if (best == null || mt.group(1).compareTo(best) > 0) best = mt.group(1);
                }
                if (best != null) {
                    urls.add(m + LXC_PATH + best + "/rootfs.tar.xz");
                }
            } catch (Exception e) {
                cb.log("  不可用：" + e.getMessage());
            }
        }
        return urls;
    }

    /** 多镜像故障转移下载：任一镜像失败自动换下一个 */
    public void downloadRootfsWithFailover(java.util.List<String> urls, File out, CB cb)
            throws IOException {
        Exception last = null;
        for (String u : urls) {
            try {
                downloadRootfs(u, out, cb);
                return;
            } catch (Exception e) {
                last = e;
                cb.log("  下载失败（" + e.getMessage() + "），换下一个镜像…");
            }
        }
        throw new IOException("所有镜像下载失败：" + (last == null ? "无可用镜像" : last.getMessage()));
    }

    private String httpGet(String url, int timeoutMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(timeoutMs);
        c.setReadTimeout(timeoutMs);
        c.setRequestProperty("User-Agent", "ttlx-student");
        try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"))) {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) sb.append(line).append('\n');
            return sb.toString();
        } finally {
            c.disconnect();
        }
    }

    public void downloadRootfs(String url, File out, CB cb) throws IOException {
        cb.log("开始下载根文件系统（约 95MB）：" + url);
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(20000);
        c.setReadTimeout(120000);
        c.setRequestProperty("User-Agent", "ttlx-student");
        long total = c.getContentLengthLong();
        try (InputStream in = new BufferedInputStream(c.getInputStream());
             OutputStream os = new FileOutputStream(out)) {
            byte[] buf = new byte[1 << 16];
            long got = 0;
            int n, last = -1;
            while ((n = in.read(buf)) > 0) {
                os.write(buf, 0, n);
                got += n;
                if (total > 0) {
                    int pct = (int) (got * 100 / total);
                    if (pct != last) { last = pct; cb.progress(pct); }
                }
            }
        } finally {
            c.disconnect();
        }
        cb.log("下载完成：" + out.length() / 1048576 + " MB");
    }

    // ================= ③ 解压 rootfs（tar.xz → 私有目录） =================
    /**
     * 解压 rootfs。
     * ⚠ 坑（已踩）：Android 的 java.nio.file.Files.createSymbolicLink 会抛
     * UnsupportedOperationException —— Debian 根文件系统里有 ~1500 个符号链接
     * （/lib→usr/lib、/bin→usr/bin 等），缺了它们 proot 会报
     * `execve("/usr/bin/env"): No such file or directory`。
     * 必须用 Android 专属的 android.system.Os.symlink()。
     */
    public void extractRootfs(File src, CB cb) throws Exception {
        File dst = rootfsDir();
        deleteRecursive(dst);
        dst.mkdirs();
        String dstCanon = dst.getCanonicalPath();
        cb.log("正在解压根文件系统（首次较慢，请稍候）…");
        int count = 0, dirN = 0, symOk = 0, symFail = 0, hardCopied = 0;
        try (InputStream fin = new BufferedInputStream(new FileInputStream(src));
             XZInputStream xz = new XZInputStream(fin);
             TarArchiveInputStream tin = new TarArchiveInputStream(xz)) {
            TarArchiveEntry e;
            byte[] buf = new byte[65536];
            while ((e = tin.getNextEntry()) != null) {
                String name = e.getName();
                File out = new File(dst, name);
                if (!out.getCanonicalPath().startsWith(dstCanon)) continue;   // 防目录穿越
                if (e.isDirectory()) { out.mkdirs(); dirN++; continue; }
                if (e.isSymbolicLink()) {
                    File p = out.getParentFile();
                    if (p != null) p.mkdirs();
                    if (createSymlink(e.getLinkName(), out, cb, symFail)) symOk++;
                    else symFail++;
                    continue;
                }
                File parent = out.getParentFile();
                if (parent != null) parent.mkdirs();
                if (e.isLink()) {   // 硬链接：能建就建，不能就复制
                    try {
                        android.system.Os.link(new File(dst, e.getLinkName()).getAbsolutePath(),
                                out.getAbsolutePath());
                        continue;
                    } catch (Throwable t1) {
                        try {
                            Files.createLink(out.toPath(), new File(dst, e.getLinkName()).toPath());
                            continue;
                        } catch (Throwable ignore) { }
                        hardCopied++;
                    }
                }
                try (OutputStream os = new FileOutputStream(out)) {
                    int n;
                    while ((n = tin.read(buf)) > 0) os.write(buf, 0, n);
                }
                int mode = e.getMode();
                out.setExecutable((mode & 0100) != 0, false);
                out.setReadable(true, false);
                if (++count % 1000 == 0) cb.log("  已解压 " + count + " 个文件…");
            }
        }
        cb.log(String.format("解压完成：文件 %d，目录 %d，符号链接 %d 成功 / %d 失败%s",
                count, dirN, symOk, symFail,
                hardCopied > 0 ? ("，硬链接改拷贝 " + hardCopied) : ""));
        // 目录权限补一刀（proot 内 root 访问）
        new File(dst, "tmp").mkdirs();
        new File(dst, "root").mkdirs();
    }

    /** 创建符号链接：优先 Android Os.symlink，退回 java.nio（桌面调试用） */
    private boolean createSymlink(String target, File link, CB cb, int failedSoFar) {
        try {
            android.system.Os.symlink(target, link.getAbsolutePath());
            return true;
        } catch (Throwable t1) {
            try {
                Files.createSymbolicLink(link.toPath(), Paths.get(target));
                return true;
            } catch (Throwable t2) {
                if (failedSoFar < 5) {
                    cb.log("  !! 符号链接失败: " + link.getName() + " -> " + target
                            + " (" + t2.getClass().getSimpleName() + ")");
                }
                return false;
            }
        }
    }

    /** rootfs 健康自检：关键解释器与路径必须可见（符号链接齐了才会通过） */
    public boolean checkRootfsHealthy() {
        File dst = rootfsDir();
        boolean libLink = new File(dst, "lib").exists();                       // /lib 是符号链接
        boolean ld = new File(dst, "lib/ld-linux-aarch64.so.1").exists();      // 动态链接器
        boolean env = new File(dst, "usr/bin/env").exists();
        boolean bash = new File(dst, "bin/bash").exists();                     // /bin 是符号链接
        return libLink && ld && env && bash;
    }

    // ================= ④ 运行容器内脚本（proot） =================
    /** 构建 proot 命令；keepAlive=true 用于常驻的启动脚本 */
    public ProcessBuilder buildProot(String scriptPath) {
        List<String> cmd = prootPrefix();
        cmd.add("/bin/bash");
        cmd.add(scriptPath);
        return finishProot(cmd);
    }

    /** 在容器里执行一条 shell 命令（bash -c），用于「容器内打开文件」等一次性操作 */
    public ProcessBuilder buildProotShell(String shellCommand) {
        List<String> cmd = prootPrefix();
        cmd.add("/bin/bash");
        cmd.add("-c");
        cmd.add(shellCommand);
        return finishProot(cmd);
    }

    /**
     * 同 {@link #buildProotShell}，但**不带 `--kill-on-exit`**。
     *
     * 用途：启动**长期运行**的图形程序（虚拟程序 Win10/Edge/邮件 用 mono 跑）。
     * 带 `--kill-on-exit` 时，proot 一退出就会带走整棵进程树 ——
     * 于是「启动脚本返回 → mono 立刻被杀 → 窗口一闪而过」。
     * 去掉它，proot 退出后 mono 会被 reparent 继续活着。
     */
    public ProcessBuilder buildProotShellDetached(String shellCommand) {
        List<String> cmd = new ArrayList<>();
        cmd.add(prootBin().getAbsolutePath());
        cmd.add("--link2symlink");
        // 注意：这里**故意不加** --kill-on-exit
        cmd.add("-0");
        cmd.add("-r"); cmd.add(rootfsDir().getAbsolutePath());
        cmd.add("-b"); cmd.add("/dev");
        cmd.add("-b"); cmd.add("/proc");
        cmd.add("-b"); cmd.add("/sys");
        cmd.add("-b"); cmd.add("/dev/urandom:/dev/random");
        cmd.add("-b"); cmd.add(tmpDir().getAbsolutePath() + ":/tmp");
        File shm = new File(tmpDir(), "shm");
        shm.mkdirs();
        cmd.add("-b"); cmd.add(shm.getAbsolutePath() + ":/dev/shm");
        cmd.add("-w"); cmd.add("/root");
        cmd.add("/usr/bin/env");
        cmd.add("-i");
        cmd.add("HOME=/root");
        cmd.add("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
        cmd.add("TERM=xterm-256color");
        cmd.add("LANG=C.UTF-8");
        cmd.add("DISPLAY=:1");
        cmd.add("/bin/bash");
        cmd.add("-c");
        cmd.add(shellCommand);
        return finishProot(cmd);
    }

    private List<String> prootPrefix() {
        List<String> cmd = new ArrayList<>();
        cmd.add(prootBin().getAbsolutePath());
        cmd.add("--link2symlink");
        cmd.add("--kill-on-exit");
        cmd.add("-0");
        cmd.add("-r"); cmd.add(rootfsDir().getAbsolutePath());
        cmd.add("-b"); cmd.add("/dev");
        cmd.add("-b"); cmd.add("/proc");
        cmd.add("-b"); cmd.add("/sys");
        cmd.add("-b"); cmd.add("/dev/urandom:/dev/random");
        // ★ 把 App 私有 tmp 绑成容器 /tmp —— Termux:X11 的 X socket 与 Xvnc 的 VNC socket
        //   都放这里，两边看到的是**同一个文件**。参考 tiny_container 就是这么做的
        //   （它的 TMPDIR = <filesDir>/tmp，并且把该目录 bind 到 /tmp）。
        cmd.add("-b"); cmd.add(tmpDir().getAbsolutePath() + ":/tmp");
        // /dev/shm：Android 上不一定存在，主动绑一个（Xvnc / Chromium / WPS 需要）
        File shm = new File(tmpDir(), "shm");
        shm.mkdirs();
        try { android.system.Os.chmod(shm.getAbsolutePath(), 01777); } catch (Throwable ignore) { }
        shm.setReadable(true, false);
        shm.setWritable(true, false);
        shm.setExecutable(true, false);
        cmd.add("-b"); cmd.add(shm.getAbsolutePath() + ":/dev/shm");
        cmd.add("-w"); cmd.add("/root");
        cmd.add("/usr/bin/env");
        cmd.add("-i");
        cmd.add("HOME=/root");
        cmd.add("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
        cmd.add("TERM=xterm-256color");
        cmd.add("LANG=C.UTF-8");
        cmd.add("DISPLAY=:1");
        return cmd;
    }

    private ProcessBuilder finishProot(List<String> cmd) {
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.directory(files);
        pb.redirectErrorStream(true);
        pb.environment().put("LD_LIBRARY_PATH", binDir().getAbsolutePath());
        pb.environment().put("PROOT_TMP_DIR", tmpDir().getAbsolutePath());
        pb.environment().put("PROOT_NO_SECCOMP", "1");
        pb.environment().put("PROOT_LOADER", new File(files, "libexec/proot/loader").getAbsolutePath());
        return pb;
    }

    /** 同步执行脚本并把输出逐行回调（用于配置阶段）。
     *
     *  ⚠ 看门狗（重要）：proot 在部分环境（x86 模拟器 / ARM 转译层，如某些模拟器）会
     *  卡在 ptrace 上——进程活着、CPU 不涨、永远没有任何输出、也不报错。
     *  没有看门狗时界面会永远停在「容器内自动配置…」，用户以为在下载，实际是死等。
     *  这里加两道闸：① 长时间无输出（停滞）② 总时长上限。 */
    public static final int EXIT_STALLED = -9;
    // 10 分钟太紧了：WPS 的 deb 有 351MB，解包 + 跑 postinst + 装字体在手机上
    // 可能十几分钟没有任何 stdout 输出（脚本的详细输出都写进了它自己的日志文件），
    // 会被误判成"卡死"而杀掉 —— 真机日志里就是这么中断的。放宽到 25 分钟。
    private static final long STALL_MS = 25 * 60 * 1000L;    // 25 分钟无任何输出 → 判死
    private static final long TOTAL_MS = 90 * 60 * 1000L;    // 总上限 90 分钟

    public int runScript(String scriptPath, CB cb) throws Exception {
        ProcessBuilder pb = buildProot(scriptPath);
        Process p = pb.start();
        final long start = System.currentTimeMillis();
        final long[] lastOut = {start};
        final boolean[] stalled = {false};

        Thread watchdog = new Thread(() -> {
            while (p.isAlive()) {
                try { Thread.sleep(5000); } catch (InterruptedException e) { return; }
                long now = System.currentTimeMillis();
                if (now - lastOut[0] > STALL_MS) {
                    stalled[0] = true;
                    cb.log("!! 容器内脚本已 " + (STALL_MS / 60000) + " 分钟无任何输出，判定卡死，已中止");
                    p.destroyForcibly();
                    return;
                }
                if (now - start > TOTAL_MS) {
                    stalled[0] = true;
                    cb.log("!! 容器内脚本超过 " + (TOTAL_MS / 60000) + " 分钟仍未结束，已中止");
                    p.destroyForcibly();
                    return;
                }
            }
        }, "ttlx-setup-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();

        try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), "UTF-8"))) {
            String line;
            while ((line = r.readLine()) != null) {
                lastOut[0] = System.currentTimeMillis();
                cb.log(line);
            }
        }
        int code = p.waitFor();
        return stalled[0] ? EXIT_STALLED : code;
    }

    public void markReady() throws IOException {
        try (FileOutputStream f = new FileOutputStream(readyFlag())) {
            f.write("ok".getBytes());
        }
    }

    /**
     * 每次启动容器前，把 App 里的容器脚本刷进 rootfs。
     *
     * ⚠ 血的教训：`/root/ttlx-pkg/*.sh` 原来**只在首次 setup 时写入**，
     * 于是 APK 升级后容器里跑的仍是旧脚本 —— 真机日志里容器输出的一直是老文案，
     * 我们改了好几版的 ttlx_start.sh 根本没到设备上。
     */
    public void refreshContainerScripts() throws IOException {
        File inPkg = new File(rootfsDir(), "root/ttlx-pkg");
        deleteRecursive(inPkg);
        copyDirFile(pkgDir(), inPkg);
        chmodShRecursive(inPkg);
    }

    private static void copyDirFile(File src, File dst) throws IOException {
        if (src == null || !src.exists()) return;
        if (src.isDirectory()) {
            dst.mkdirs();
            File[] cs = src.listFiles();
            if (cs != null) for (File c : cs) copyDirFile(c, new File(dst, c.getName()));
            return;
        }
        dst.getParentFile().mkdirs();
        try (FileInputStream in = new FileInputStream(src);
             FileOutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }

    private static void chmodShRecursive(File dir) {
        File[] cs = dir.listFiles();
        if (cs == null) return;
        for (File c : cs) {
            if (c.isDirectory()) chmodShRecursive(c);
            else if (c.getName().endsWith(".sh")) c.setExecutable(true, false);
        }
    }

    public void clearReady() {
        readyFlag().delete();
    }

    // ================= 工具 =================
    public static void deleteRecursive(File f) {
        if (f == null || !f.exists()) return;
        if (f.isDirectory()) {
            File[] cs = f.listFiles();
            if (cs != null) for (File c : cs) deleteRecursive(c);
        }
        f.delete();
    }
}
