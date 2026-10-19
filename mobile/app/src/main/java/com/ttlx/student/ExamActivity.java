package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.card.MaterialCardView;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * 答题界面（原生，Material 3）：
 *   · 选择题 —— 原生触摸点选（圆角卡片选项），不走容器
 *   · 操作题 —— 原生显示题干 + 按钮，「在容器中打开」进 proot 桌面用 WPS/浏览器完成，
 *               交卷时手机端自动打包上传
 *   底部操作栏套用【毛玻璃】效果（GlassNavBar）
 */
public class ExamActivity extends AppCompatActivity {

    private NetClient net;
    private JSONArray qs = new JSONArray();
    private long recordId;
    private String title = "";
    private boolean practice = false;
    private int durationSec = 0;

    private int idx = 0;
    private int[] sel;                       // 选择题所选下标
    private String[] opText;                 // 操作题文本（保留）
    private JSONObject[] opGrade;            // 操作题判分缓存
    /** qid → 素材预览图（服务端 getMaterial 随素材下发的 previews，标准答案效果图） */
    private final java.util.Map<String, JSONArray> previews = new java.util.HashMap<>();

    private TextView titleLabel, timeLabel, progressLabel, stemView, opStatus;
    private LinearLayout navBar, optionBox, opBox;
    private HorizontalScrollView navScroll;
    private MaterialButton prevBtn, nextBtn;
    private GlassNavBar glassBar;
    private List<MaterialButton> navBtns = new ArrayList<>();

    private Handler ui = new Handler(Looper.getMainLooper());
    private volatile boolean hbStop = false;
    private volatile boolean submitted = false;
    private int remain;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        net = TTLX.net();
        if (net == null) { finish(); return; }
        title = getIntent().getStringExtra("title");
        recordId = getIntent().getLongExtra("recordId", 0);
        practice = getIntent().getBooleanExtra("practice", false);
        durationSec = getIntent().getIntExtra("duration", 0);
        try {
            qs = new JSONArray(getIntent().getStringExtra("questions"));
        } catch (Exception ignore) { }
        sel = new int[qs.length()];
        opText = new String[qs.length()];
        opGrade = new JSONObject[qs.length()];
        for (int i = 0; i < sel.length; i++) { sel[i] = -1; opText[i] = ""; }

        setContentView(buildUi());
        render();
        startHeartbeat();
        if (durationSec > 0) {
            remain = durationSec;
            tickTimer();
        }
    }

    // ============================== UI ==============================
    private View buildUi() {
        android.widget.FrameLayout root = new android.widget.FrameLayout(this);
        root.setBackgroundColor(getResources().getColor(R.color.md_surface));

        // 主列（顶栏 + 题号条 + 内容）——也是毛玻璃底栏的捕获源
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);

        // 顶栏（Material：表面色 + 标题 + 计时）
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(18), dp(14), dp(14), dp(10));
        titleLabel = new TextView(this);
        titleLabel.setText(title.isEmpty() ? "答题" : title);
        titleLabel.setTextColor(getResources().getColor(R.color.md_on_surface));
        titleLabel.setTextSize(20);
        titleLabel.setTypeface(Typeface.DEFAULT_BOLD);
        titleLabel.setSingleLine(true);
        top.addView(titleLabel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        timeLabel = new TextView(this);
        timeLabel.setTextColor(getResources().getColor(R.color.md_error));
        timeLabel.setTextSize(15);
        timeLabel.setTypeface(Typeface.DEFAULT_BOLD);
        timeLabel.setPadding(dp(10), dp(4), dp(10), dp(4));
        top.addView(timeLabel);
        column.addView(top);

        // 题号导航（横向滚动的小胶囊）
        navScroll = new HorizontalScrollView(this);
        navScroll.setBackgroundColor(Color.TRANSPARENT);
        navBar = new LinearLayout(this);
        navBar.setPadding(dp(12), dp(4), dp(12), dp(6));
        navBar.setGravity(Gravity.CENTER_VERTICAL);
        String lastModule = null;
        for (int i = 0; i < qs.length(); i++) {
            final int k = i;
            JSONObject qq = qs.optJSONObject(i);
            // 按模块分组：题号前插一个模块小标签（与桌面端左侧导航一致的观感）
            String mod = moduleOf(qq);
            if (mod != null && !mod.equals(lastModule)) {
                lastModule = mod;
                TextView tag = new TextView(this);
                tag.setText(mod);
                tag.setTextSize(11);
                tag.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
                tag.setSingleLine(true);
                tag.setPadding(dp(8), 0, dp(6), 0);
                LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT, dp(38));
                if (i > 0) tlp.leftMargin = dp(10);
                tag.setLayoutParams(tlp);
                navBar.addView(tag);
            }
            MaterialButton b = new MaterialButton(this);
            b.setText(String.valueOf(i + 1));
            b.setTextSize(13);
            b.setAllCaps(false);
            b.setCornerRadius(dp(14));
            b.setMinWidth(dp(46));
            b.setMinimumWidth(dp(46));
            b.setPadding(0, 0, 0, 0);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(46), dp(38));
            lp.rightMargin = dp(6);
            b.setLayoutParams(lp);
            b.setOnClickListener(v -> gotoQ(k));
            navBar.addView(b);
            navBtns.add(b);
        }
        navScroll.addView(navBar);
        column.addView(navScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 内容
        ScrollView sv = new ScrollView(this);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(16), dp(6), dp(16), dp(112));   // 底部给毛玻璃留空间
        sv.addView(body);

        progressLabel = new TextView(this);
        progressLabel.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        progressLabel.setTextSize(12);
        body.addView(progressLabel);

        stemView = new TextView(this);
        stemView.setTextSize(16);
        stemView.setTextColor(getResources().getColor(R.color.md_on_surface));
        stemView.setLineSpacing(0, 1.15f);
        LinearLayout.LayoutParams stlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        stlp.topMargin = dp(8);
        body.addView(stemView, stlp);

        optionBox = new LinearLayout(this);
        optionBox.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams olp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        olp.topMargin = dp(12);
        body.addView(optionBox, olp);

        opBox = new LinearLayout(this);
        opBox.setOrientation(LinearLayout.VERTICAL);
        body.addView(opBox, olp);
        column.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        root.addView(column, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // 底部【毛玻璃】操作栏
        glassBar = new GlassNavBar(this);
        LinearLayout bar = new LinearLayout(this);
        bar.setPadding(dp(10), dp(10), dp(10), dp(12));
        bar.setGravity(Gravity.CENTER_VERTICAL);
        prevBtn = barBtn("上一题", false);
        prevBtn.setOnClickListener(v -> gotoQ(idx - 1));
        bar.addView(prevBtn);
        nextBtn = barBtn("下一题", false);
        nextBtn.setOnClickListener(v -> gotoQ(idx + 1));
        bar.addView(nextBtn);
        MaterialButton sub = barBtn("交 卷", true);
        sub.setOnClickListener(v -> confirmSubmit());
        bar.addView(sub);
        glassBar.addView(bar, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        android.widget.FrameLayout.LayoutParams glp = new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        glp.gravity = Gravity.BOTTOM;
        root.addView(glassBar, glp);
        glassBar.attachBackdrop(column);
        return root;
    }

    private MaterialButton barBtn(String text, boolean primary) {
        MaterialButton b = new MaterialButton(this);
        b.setText(text);
        b.setTextSize(15);
        b.setAllCaps(false);
        b.setCornerRadius(dp(24));
        if (primary) {
            b.setBackgroundColor(getResources().getColor(R.color.md_error));
            b.setTextColor(Color.WHITE);
        } else {
            b.setBackgroundColor(getResources().getColor(R.color.md_primary_container));
            b.setTextColor(getResources().getColor(R.color.md_on_primary_container));
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(48), 1f);
        lp.leftMargin = dp(4);
        lp.rightMargin = dp(4);
        b.setLayoutParams(lp);
        return b;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    /** 题号导航的模块名（服务端下发的 module / course，与桌面端分组口径一致） */
    private String moduleOf(JSONObject q) {
        if (q == null) return null;
        String m = q.optString("module", "");
        if (!m.isEmpty()) return m;
        int c = q.optInt("course", 0);
        return c > 0 ? ("模块" + c) : null;
    }

    /**
     * 虚拟程序类型推断（与桌面端 `vapp_of_local` / 服务端 `vapp_of` 同口径）。
     *
     * ⚠ 为什么要兜底：老服务端、错题重做等场景下发的操作题**可能没有 vapp 字段**，
     * 那样桌面里就不会出现「虚拟程序」按钮，学生根本没法作答（桌面端早就踩过这个坑）。
     */
    private String vappOf(JSONObject q) {
        if (q == null) return "";
        String v = q.optString("vapp", "");
        if (!v.isEmpty()) return v;
        int t = q.optInt("type", 0);
        int st = q.optInt("subType", 0);
        // 文件管理题（subType=2 或带 material.zip）绝不启虚拟程序
        if (st == 2 || q.optBoolean("hasZip", false)) return "";
        String d = q.optString("stem", "");
        if (st == 11 || d.contains("邮箱") || d.contains("qq.com")
                || (d.contains("邮件") && d.contains("收件"))) return "email";
        if (t == 11 || t == 12 || t == 31 || t == 32) {
            if (st == 6 || st == 7) return "edge";
            if (st == 8 || st == 9 || st == 10) return "win10";
            if (d.matches("(?s).*(收藏|主页|历史记录|临时文件|Internet\\s*选项|cookie|Cookie).*"))
                return "edge";
            return "win10";
        }
        return "";
    }

    // ============================== 渲染 ==============================
    private JSONObject q() { return qs.optJSONObject(idx); }

    private boolean isOperate(JSONObject q) {
        if (q == null) return false;
        if ("operate".equals(q.optString("refType"))) return true;
        JSONArray o = q.optJSONArray("options");
        return o == null || o.length() == 0;
    }

    private void render() {
        JSONObject q = q();
        if (q == null) return;
        boolean op = isOperate(q);
        progressLabel.setText("第 " + (idx + 1) + " / " + qs.length() + " 题"
                + (op ? "（操作题 · 在容器中完成）" : "（选择题）"));

        if (op) {
            stemView.setText(fmtOpStem(q.optString("stem", "")));
        } else {
            stemView.setText((idx + 1) + ". " + q.optString("stem", ""));
        }

        optionBox.removeAllViews();
        opBox.removeAllViews();
        if (!op) {
            buildOptions(q);
        } else {
            buildOperate(q);
        }

        prevBtn.setEnabled(idx > 0);
        boolean last = idx == qs.length() - 1;
        nextBtn.setVisibility(last ? View.GONE : View.VISIBLE);
        refreshNav();
    }

    private void buildOptions(final JSONObject q) {
        JSONArray opts = q.optJSONArray("options");
        if (opts == null) return;
        for (int i = 0; i < opts.length() && i < 6; i++) {
            JSONObject o = opts.optJSONObject(i);
            String k = o == null ? String.valueOf((char) ('A' + i)) : o.optString("k", String.valueOf((char) ('A' + i)));
            String t = o == null ? "" : o.optString("t", "");
            if (t.isEmpty()) continue;
            final int fi = i;
            boolean picked = sel[idx] == i;

            MaterialCardView card = new MaterialCardView(this);
            card.setRadius(dp(18));
            card.setCardElevation(dp(picked ? 2 : 0));
            card.setCardBackgroundColor(Color.parseColor(picked ? "#DCE9FF" : "#FFFFFF"));
            if (picked) {
                card.setStrokeColor(getResources().getColor(R.color.md_primary));
                card.setStrokeWidth(dp(2));
            } else {
                card.setStrokeColor(Color.parseColor("#E3E6EF"));
                card.setStrokeWidth(dp(1));
            }
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(14), dp(14), dp(14), dp(14));
            card.addView(row);

            TextView mark = new TextView(this);
            mark.setText(k);
            mark.setTextSize(16);
            mark.setTypeface(Typeface.DEFAULT_BOLD);
            mark.setGravity(Gravity.CENTER);
            mark.setBackgroundColor(Color.parseColor(picked ? "#2D6CDF" : "#EDF2FB"));
            mark.setTextColor(Color.parseColor(picked ? "#FFFFFF" : "#5A6070"));
            LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(dp(34), dp(34));
            mark.setLayoutParams(mlp);
            row.addView(mark);

            TextView tv = new TextView(this);
            tv.setText("  " + t);
            tv.setTextSize(15);
            tv.setTextColor(getResources().getColor(R.color.md_on_surface));
            LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            row.addView(tv, tlp);

            LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            clp.bottomMargin = dp(10);
            card.setLayoutParams(clp);
            card.setOnClickListener(v -> {
                sel[idx] = fi;
                render();
            });
            optionBox.addView(card);
        }
    }

    private void buildOperate(final JSONObject q) {
        final String qid = String.valueOf(q.opt("qid"));

        opStatus = new TextView(this);
        opStatus.setTextSize(13);
        opStatus.setTextColor(Color.parseColor("#718096"));
        opBox.addView(opStatus);

        String kind = kindName(q);
        TextView note = new TextView(this);
        note.setText("本题为 " + kind + "。点「下载素材」→「打开桌面」，"
                + "在容器桌面里用对应程序完成并保存，回来点「交卷」自动打包上传。");
        note.setTextSize(13);
        note.setTextColor(Color.parseColor("#718096"));
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        nlp.topMargin = dp(4);
        opBox.addView(note, nlp);

        Row r1 = new Row();
        r1.add(opBtn("下载素材", true, v -> downloadMaterial(q)));
        r1.add(opBtn("打开桌面", true, v -> openDesktop(q)));
        opBox.addView(r1.view);

        // ---- 素材预览图（标准答案效果图）放最底下 ----
        addPreviewBlock(qid);

        refreshOpStatus(q);
    }

    /**
     * 素材预览图：服务端随素材下发的 previews（base64）。
     * 位置按需求放在题目内容**最底下**，横向可滑，点一下可放大看。
     */
    private void addPreviewBlock(String qid) {
        JSONArray pv = previews.get(qid);
        if (pv == null || pv.length() == 0) return;

        TextView cap = new TextView(this);
        cap.setText("参考效果图（" + pv.length() + " 张，左右滑动查看，点图放大）");
        cap.setTextSize(12);
        cap.setTextColor(Color.parseColor("#718096"));
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.topMargin = dp(12);
        opBox.addView(cap, clp);

        LinearLayout strip = new LinearLayout(this);
        strip.setOrientation(LinearLayout.HORIZONTAL);
        strip.setPadding(0, dp(6), 0, dp(6));

        int shown = 0;
        for (int i = 0; i < pv.length(); i++) {
            JSONObject it = pv.optJSONObject(i);
            if (it == null) continue;
            String data = it.optString("data", "");
            if (data.isEmpty()) continue;
            final android.graphics.Bitmap bm;
            try {
                byte[] raw = android.util.Base64.decode(data, android.util.Base64.DEFAULT);
                bm = android.graphics.BitmapFactory.decodeByteArray(raw, 0, raw.length);
            } catch (Throwable t) {
                continue;
            }
            if (bm == null) continue;

            android.widget.ImageView iv = new android.widget.ImageView(this);
            iv.setImageBitmap(bm);
            iv.setAdjustViewBounds(true);
            iv.setScaleType(android.widget.ImageView.ScaleType.FIT_CENTER);
            iv.setBackgroundColor(Color.parseColor("#FFFFFF"));
            iv.setPadding(dp(3), dp(3), dp(3), dp(3));
            LinearLayout.LayoutParams ilp = new LinearLayout.LayoutParams(dp(220), dp(160));
            ilp.rightMargin = dp(8);
            iv.setLayoutParams(ilp);
            iv.setOnClickListener(v -> showPreviewFull(bm, it.optString("name", "")));
            strip.addView(iv);
            shown++;
        }
        if (shown == 0) return;

        HorizontalScrollView hsv = new HorizontalScrollView(this);
        hsv.addView(strip);
        LinearLayout.LayoutParams hlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hlp.topMargin = dp(2);
        opBox.addView(hsv, hlp);
    }

    /** 点预览图 → 放大查看（全屏可缩放） */
    private void showPreviewFull(android.graphics.Bitmap bm, String name) {
        android.widget.ImageView big = new android.widget.ImageView(this);
        big.setImageBitmap(bm);
        big.setAdjustViewBounds(true);
        big.setScaleType(android.widget.ImageView.ScaleType.FIT_CENTER);
        big.setBackgroundColor(Color.parseColor("#FFFFFF"));
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.parseColor("#FFFFFF"));
        sv.addView(big, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        new MaterialAlertDialogBuilder(this)
                .setTitle(name.isEmpty() ? "参考效果图" : name)
                .setView(sv)
                .setPositiveButton("关闭", null)
                .show();
    }

    private class Row {
        LinearLayout view = new LinearLayout(ExamActivity.this);
        Row() { view.setOrientation(LinearLayout.HORIZONTAL); }
        void add(MaterialButton b) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, dp(46), 1f);
            lp.rightMargin = dp(8);
            lp.topMargin = dp(8);
            view.addView(b, lp);
        }
    }

    private MaterialButton opBtn(String text, boolean primary, View.OnClickListener l) {
        MaterialButton b = new MaterialButton(this);
        b.setText(text);
        b.setTextSize(14);
        b.setAllCaps(false);
        b.setCornerRadius(dp(22));
        if (primary) {
            b.setBackgroundColor(getResources().getColor(R.color.md_primary));
            b.setTextColor(Color.WHITE);
        } else {
            b.setBackgroundColor(getResources().getColor(R.color.md_primary_container));
            b.setTextColor(getResources().getColor(R.color.md_on_primary_container));
        }
        b.setOnClickListener(l);
        return b;
    }

    private String kindName(JSONObject q) {
        int t = q.optInt("type", 0);
        if (t == 24) return "MySQL 数据库操作题（手机无 MySQL，建议电脑完成；可查看素材）";
        if (t == 25) return "Python 编程题（在容器里点「下载素材」生成 answer.py 后编辑保存）";
        String vapp = vappOf(q);
        if ("edge".equals(vapp)) return "浏览器操作题（桌面里点「虚拟程序」启动 Edge，或用 Chromium）";
        if ("email".equals(vapp)) return "邮件设置题（桌面里点「虚拟程序」启动邮件程序）";
        if ("win10".equals(vapp)) return "Windows 系统设置题（桌面里点「虚拟程序」启动 Win10 设置）";
        if (!vapp.isEmpty()) return "虚拟程序操作题（桌面里点「虚拟程序」启动）";
        if (q.optInt("subType", 0) == 2) return "文件管理操作题";
        if (q.optInt("subType", 0) == 6) return "网页操作题（容器内浏览器完成）";
        return "Office / 设计软件操作题";
    }

    private void refreshOpStatus(JSONObject q) {
        if (opStatus == null) return;
        String qid = String.valueOf(q.opt("qid"));
        File d = ContainerBridge.questionDir(this, qid);
        int n = 0;
        File[] fs = d.listFiles();
        if (fs != null) {
            for (File f : fs) {
                String name = f.getName();
                if ("material.zip".equals(name) || name.startsWith(".")) continue;
                if (name.toLowerCase().endsWith(".exe")) continue;   // 虚拟程序本体不算作答
                n++;
            }
        }
        opStatus.setText(n > 0 ? "✔ 作答文件夹里已有 " + n + " 个文件（交卷时自动上传）"
                : "作答文件夹目前为空：先「下载素材」，再「打开桌面」作答");
    }

    private String fmtOpStem(String s) {
        if (s == null) return "";
        return s.replace("\r\n", "\n").replace("\r", "\n");
    }

    private void refreshNav() {
        for (int i = 0; i < navBtns.size(); i++) {
            MaterialButton b = navBtns.get(i);
            boolean cur = i == idx;
            boolean done = answered(i);
            if (cur) {
                b.setBackgroundColor(getResources().getColor(R.color.md_primary));
                b.setTextColor(Color.WHITE);
            } else if (done) {
                b.setBackgroundColor(getResources().getColor(R.color.md_success));
                b.setTextColor(Color.WHITE);
            } else {
                b.setBackgroundColor(getResources().getColor(R.color.md_primary_container));
                b.setTextColor(getResources().getColor(R.color.md_on_primary_container));
            }
        }
        if (glassBar != null) glassBar.markDirty();
    }

    private boolean answered(int i) {
        JSONObject q = qs.optJSONObject(i);
        if (q == null) return false;
        if (isOperate(q)) {
            String qid = String.valueOf(q.opt("qid"));
            File d = ContainerBridge.questionDir(this, qid);
            File[] fs = d.listFiles();
            if (fs != null) {
                for (File f : fs) {
                    String n = f.getName();
                    if ("material.zip".equals(n) || n.startsWith(".")) continue;
                    // 虚拟程序 exe 是「打开桌面」时自动放进去的，不算学生作答 —— 否则
                    // 一进桌面题号就变绿（桌面端踩过同样的坑，见交接文档第 14 条）
                    if (n.toLowerCase().endsWith(".exe")) continue;
                    return true;
                }
            }
            return false;
        }
        return sel[i] >= 0;
    }

    private void gotoQ(int k) {
        if (k < 0 || k >= qs.length() || k == idx) return;
        if (idx < opText.length && opBox.getChildCount() > 0) opText[idx] = "";
        idx = k;
        render();
        navScroll.smoothScrollTo(navBtns.get(k).getLeft() - dp(100), 0);
    }

    // ============================== 操作题：素材 / 打开 ==============================
    /** 操作题依赖容器环境：未就绪时引导（配置在后台自动进行，不打断做选择题） */
    private boolean ensureEnv() {
        SetupManager sm = new SetupManager(this);
        if (sm.isReady()) return true;
        SetupRunner.startIfNeeded(this);
        new MaterialAlertDialogBuilder(this)
                .setTitle("操作题环境正在准备")
                .setMessage("首次使用需自动下载并配置（约 10~20 分钟，可后台进行）。\n\n"
                        + "当前进度：" + SetupRunner.status + "\n\n"
                        + "建议先做其它选择题，环境会在后台继续准备。")
                .setPositiveButton("查看进度", (d, w) ->
                        startActivity(new Intent(this, SetupActivity.class)))
                .setNegativeButton("继续答题", null)
                .show();
        return false;
    }

    private void downloadMaterial(final JSONObject q) {
        if (!ensureEnv()) return;
        final String qid = String.valueOf(q.opt("qid"));
        final String t = String.valueOf(q.opt("type"));
        // v3.0：虚拟程序题不再弹窗拦一道，直接下素材；下载完再给一句说明
        doDownload(q, qid, t);
    }

    /** 虚拟程序题的说明文案：手机端现在**能跑**这些程序（容器里用 mono），给可执行的指引 */
    private String vappTip(JSONObject q, String vapp) {
        if ("edge".equals(vapp)) {
            return "本题是浏览器操作题：进桌面后点「虚拟程序」启动 Edge 模拟器即可作答"
                    + "（也可以用容器里的 Chromium，同内核、书签格式一致，判分兼容）。";
        }
        if ("email".equals(vapp)) {
            return "本题是邮件设置题：进桌面后点「虚拟程序」启动邮件程序即可作答，"
                    + "按题目要求设置好、保存后回答题界面点「交卷」。";
        }
        return "本题是 Windows 设置题：进桌面后点「虚拟程序」启动 Win10 设置程序即可作答，"
                + "按题目要求操作完，回答题界面点「交卷」自动上传判分。";
    }

    private void doDownload(final JSONObject q, final String qid, final String t) {
        toast("正在下载素材…");
        new Thread(() -> {
            String err = null;
            try {
                if ("25".equals(t)) {
                    // Python 题：直接把题干写成 answer.py 模板
                    File d = ContainerBridge.questionDir(ExamActivity.this, qid);
                    File py = new File(d, "answer.py");
                    if (!py.exists()) {
                        try (FileOutputStream fo = new FileOutputStream(py)) {
                            fo.write(q.optString("stem", "").getBytes("UTF-8"));
                        }
                    }
                } else if ("24".equals(t)) {
                    // MySQL 题：素材是 datadir 快照（专用接口）
                    JSONObject r = net.getMaterialZip(qid);
                    if (r.optInt("code") != 0) {
                        AppLog.e("mat", "getMaterialZip " + qid + " code=" + r.optInt("code")
                                + " msg=" + r.optString("msg"));
                        throw new Exception(r.optString("msg", "该题没有素材包"));
                    }
                    String b64 = r.optString("zip", "");
                    if (b64.isEmpty()) throw new Exception("素材为空");
                    byte[] zip = android.util.Base64.decode(b64, android.util.Base64.DEFAULT);
                    File d = ContainerBridge.questionDir(ExamActivity.this, qid);
                    File zp = new File(d, "material.zip");
                    try (FileOutputStream fo = new FileOutputStream(zp)) {
                        fo.write(zip);
                    }
                    unzip(zp, d);
                } else {
                    // Office/文件管理/网页题：通用素材接口（getMaterial，文件列表）
                    JSONObject r = net.call("getMaterial", new JSONObject().put("timuID", qid));
                    if (r.optInt("code") != 0) {
                        AppLog.e("mat", "getMaterial " + qid + " code=" + r.optInt("code")
                                + " msg=" + r.optString("msg"));
                        throw new Exception(r.optString("msg", "素材获取失败"));
                    }
                    JSONArray files = r.optJSONArray("files");
                    // 预览图（标准答案效果图）随素材一起下发 —— 收下来放题目最底下显示
                    JSONArray pv = r.optJSONArray("previews");
                    if (pv != null && pv.length() > 0) previews.put(qid, pv);
                    if (files == null || files.length() == 0) {
                        final String vp = vappOf(q);
                        ui.post(() -> {
                            if (vp.isEmpty()) {
                                toast("该题没有服务端素材（可直接在作答文件夹里创建文件）");
                                openDesktop(q);
                            } else {
                                toast("该题没有服务端素材");
                                alert("本题提示", vappTip(q, vp));
                            }
                        });
                        return;
                    }
                    File d = ContainerBridge.questionDir(ExamActivity.this, qid);
                    for (int i = 0; i < files.length(); i++) {
                        JSONObject f = files.optJSONObject(i);
                        String name = f.optString("name", "");
                        String data = f.optString("data", "");
                        if (name.isEmpty() || data.isEmpty()) continue;
                        File out = new File(d, name);
                        File parent = out.getParentFile();
                        if (parent != null) parent.mkdirs();
                        try (FileOutputStream fo = new FileOutputStream(out)) {
                            fo.write(android.util.Base64.decode(data, android.util.Base64.DEFAULT));
                        }
                    }
                }
            } catch (Exception e) {
                err = e.getMessage();
                AppLog.e("mat", "素材下载异常 " + qid + ": " + e);
            }
            final String ferr = err;
            ui.post(() -> {
                if (ferr != null) {
                    alert("下载失败", ferr + "\n\n（已记入日志，可用『导出并分享日志』反馈）");
                } else {
                    toast("素材已就绪");
                    refreshOpStatus(q);
                    refreshNav();
                    render();      // 重新渲染：把刚收到的预览图显示到题目最底下
                }
            });
        }, "ttlx-mat").start();
    }

    private void unzip(File zp, File dir) throws Exception {
        try (ZipInputStream zin = new ZipInputStream(new FileInputStream(zp))) {
            ZipEntry e;
            byte[] buf = new byte[65536];
            while ((e = zin.getNextEntry()) != null) {
                File out = new File(dir, e.getName());
                if (!out.getCanonicalPath().startsWith(dir.getCanonicalPath())) continue;
                if (e.isDirectory()) { out.mkdirs(); continue; }
                File p = out.getParentFile();
                if (p != null) p.mkdirs();
                try (FileOutputStream fo = new FileOutputStream(out)) {
                    int n;
                    while ((n = zin.read(buf)) > 0) fo.write(buf, 0, n);
                }
            }
        }
    }

    /**
     * 「打开桌面」：直接进容器桌面做题（v3.0 起「在容器中打开」按钮的功能就是这个）。
     * 会把当前题目（题干 / qid / 虚拟程序类型）一起带过去 —— 桌面里有**可随意移动的
     * 题目浮窗**显示要求，还有「虚拟程序」按钮可以一键起 Win10/Edge/邮件程序。
     */
    private void openDesktop(final JSONObject q) {
        if (!ensureEnv()) return;
        Intent it = new Intent(this, DesktopActivity.class);
        it.putExtra(DesktopActivity.EXTRA_STEM, fmtOpStem(q.optString("stem", "")));
        it.putExtra(DesktopActivity.EXTRA_QID, String.valueOf(q.opt("qid")));
        it.putExtra(DesktopActivity.EXTRA_VAPP, vappOf(q));
        startActivity(it);
    }

    private boolean isEmptyDir(File d) {
        File[] fs = d.listFiles();
        if (fs == null) return true;
        for (File f : fs) if (!"material.zip".equals(f.getName()) && !f.getName().startsWith(".")) return false;
        return true;
    }

    private File findMainDoc(File d) {
        File[] fs = d.listFiles();
        if (fs == null) return null;
        String[] exts = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".html", ".htm", ".accdb", ".psd"};
        for (String ext : exts) {
            for (File f : fs) {
                if (f.isFile() && f.getName().toLowerCase().endsWith(ext)
                        && !f.getName().startsWith("material")) return f;
            }
        }
        return null;
    }

    // ============================== 交卷 ==============================
    private void confirmSubmit() {
        int un = 0;
        for (int i = 0; i < qs.length(); i++) if (!answered(i)) un++;
        new MaterialAlertDialogBuilder(this)
                .setTitle("确认交卷")
                .setMessage(un > 0 ? ("还有 " + un + " 题没有作答/没有作答文件，确定交卷吗？")
                        : "确定要交卷吗？")
                .setPositiveButton("交卷", (d, w) -> doSubmit())
                .setNegativeButton("再检查一下", null)
                .show();
    }

    private void doSubmit() {
        if (submitted) return;
        submitted = true;
        final androidx.appcompat.app.AlertDialog dlg = waitDialog("正在交卷，请稍候…\n（操作题需要先打包上传，可能需要几十秒）");
        new Thread(() -> {
            String err = null;
            JSONObject merged = null;
            try {
                // 1) 上传操作题
                for (int i = 0; i < qs.length(); i++) {
                    JSONObject q = qs.optJSONObject(i);
                    if (!isOperate(q)) continue;
                    uploadOne(q);
                }
                // 2) 提交
                JSONArray answers = new JSONArray();
                for (int i = 0; i < qs.length(); i++) {
                    JSONObject q = qs.optJSONObject(i);
                    JSONObject a = new JSONObject();
                    a.put("qid", q.opt("qid"));
                    if (isOperate(q)) {
                        a.put("refType", "operate");
                        a.put("answer", opText[i] == null ? "" : opText[i]);
                    } else {
                        a.put("refType", "obj");
                        a.put("answer", sel[i] >= 0 ? String.valueOf((char) ('A' + sel[i])) : "");
                    }
                    answers.put(a);
                }
                JSONObject r = practice ? net.submitPractice(answers) : net.submit(recordId, answers);
                if (r.optInt("code") != 0) throw new Exception(r.optString("msg", "提交失败"));
                merged = mergeResult(r);
            } catch (Exception e) {
                err = e.getMessage();
            }
            final String ferr = err;
            final JSONObject fm = merged;
            ui.post(() -> {
                try { dlg.dismiss(); } catch (Exception ignore) { }
                hbStop = true;
                if (isFinishing() || isDestroyed()) return;   // 页面已关，别再弹窗
                if (ferr != null) {
                    submitted = false;
                    alert("交卷失败", ferr + "\n\n请检查网络后重新交卷。");
                    return;
                }
                if (fm.optBoolean("scoreHidden", false)) {
                    alert("交卷成功", fm.optString("msg", "本次考试成绩暂未公布，请等待教师发布。"));
                } else {
                    String head = "交卷成功   得分 " + fmt(fm.opt("score")) + " / " + fmt(fm.opt("total"));
                    ResultDialog.show(this, fm.optJSONArray("detail"), head);
                }
                finish();
            });
        }, "ttlx-submit").start();
    }

    /** 合并操作题判分明细（与桌面端一致） */
    private JSONObject mergeResult(JSONObject r) throws Exception {
        boolean hidden = r.optBoolean("scoreHidden", false);
        JSONObject out = new JSONObject();
        out.put("scoreHidden", hidden);
        out.put("msg", r.optString("msg", ""));
        if (hidden) return out;
        double score = r.optDouble("score", 0);
        double total = r.optDouble("total", 0);
        JSONArray detail = r.optJSONArray("detail");
        if (detail == null) detail = new JSONArray();
        List<String> have = new ArrayList<>();
        for (int i = 0; i < detail.length(); i++) {
            JSONObject d = detail.optJSONObject(i);
            if ("operate".equals(d.optString("refType"))) have.add(String.valueOf(d.opt("qid")));
        }
        for (int i = 0; i < qs.length(); i++) {
            JSONObject q = qs.optJSONObject(i);
            if (!isOperate(q)) continue;
            String qid = String.valueOf(q.opt("qid"));
            if (have.contains(qid)) continue;
            JSONObject g = opGrade[i];
            JSONObject d = new JSONObject();
            d.put("qid", q.opt("qid"));
            d.put("refType", "operate");
            d.put("stem", q.optString("stem", ""));
            d.put("fullScore", 20.0);
            if (g != null && !g.optBoolean("pending", false)) {
                d.put("score", g.optDouble("score", 0));
                d.put("note", g.optString("note", ""));
                d.put("pending", false);
            } else {
                d.put("score", JSONObject.NULL);
                d.put("note", g == null ? "未检测到作答文件" : g.optString("note", "判分中"));
                d.put("pending", true);
            }
            detail.put(d);
            if (practice) {
                score += g != null && !g.optBoolean("pending", false) ? g.optDouble("score", 0) : 0;
                total += 20.0;
            }
        }
        out.put("score", score);
        out.put("total", total);
        out.put("detail", detail);
        return out;
    }

    private void uploadOne(JSONObject q) {
        int i = -1;
        for (int k = 0; k < qs.length(); k++) {
            if (qs.optJSONObject(k) == q) { i = k; break; }
        }
        String qid = String.valueOf(q.opt("qid"));
        File d = ContainerBridge.questionDir(this, qid);
        if (isEmptyDir(d)) return;
        try {
            JSONObject args = new JSONObject();
            int t = q.optInt("type", 0);
            if (t == 24) {
                args.put("mysqlZip", b64(zipDir(d, new String[]{"material.zip", "_tmp_mysqld.log"})));
            } else if (t == 25) {
                File py = new File(d, "answer.py");
                if (!py.exists()) return;
                String code = readText(py);
                args.put("code", code);
            } else if (!vappOf(q).isEmpty()) {
                File log = findLog(d);
                if (log == null) return;
                args.put("vapp", vappOf(q));
                // 判分文件是二进制，必须发 base64 原始字节（不能当文本读）
                args.put("log", readBytesB64(log));
            } else {
                args.put("paperZip", b64(zipDir(d, new String[]{"material.zip"})));
                if (q.optInt("subType", 0) == 6) {
                    try {
                        JSONArray fav = readBookmarks();
                        if (fav.length() > 0) args.put("favorites", fav);
                    } catch (Exception ignore) { }
                }
            }
            JSONObject r = net.uploadOperate(practice, recordId, qid, args);
            if (i >= 0) opGrade[i] = r;
        } catch (Exception e) {
            try {
                JSONObject g = new JSONObject();
                g.put("pending", true);
                g.put("note", "上传失败：" + e.getMessage());
                if (i >= 0) opGrade[i] = g;
            } catch (Exception ignore) { }
        }
    }

    private String b64(byte[] b) {
        return android.util.Base64.encodeToString(b, android.util.Base64.NO_WRAP);
    }

    private byte[] zipDir(File dir, String[] exclude) throws Exception {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        List<String> ex = new ArrayList<>();
        if (exclude != null) for (String s : exclude) ex.add(s);
        String base = dir.getCanonicalPath();
        try (ZipOutputStream zos = new ZipOutputStream(bos)) {
            addToZip(dir, dir, zos, ex, base);
        }
        return bos.toByteArray();
    }

    private void addToZip(File root, File cur, ZipOutputStream zos, List<String> ex, String base)
            throws Exception {
        File[] fs = cur.listFiles();
        if (fs == null) return;
        for (File f : fs) {
            String name = f.getName();
            if (".keep".equals(name) || ex.contains(name)) continue;
            if (f.isDirectory()) {
                addToZip(root, f, zos, ex, base);
            } else {
                String rel = f.getCanonicalPath().substring(base.length() + 1).replace('\\', '/');
                zos.putNextEntry(new ZipEntry(rel));
                try (FileInputStream in = new FileInputStream(f)) {
                    byte[] buf = new byte[65536];
                    int n;
                    while ((n = in.read(buf)) > 0) zos.write(buf, 0, n);
                }
                zos.closeEntry();
            }
        }
    }

    private String readText(File f) throws Exception {
        try (InputStream in = new FileInputStream(f)) {
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            return new String(bos.toByteArray(), "UTF-8");
        }
    }

    /**
     * 读文件 → **base64（原始字节）**。
     *
     * ⚠ 关键：虚拟程序的判分文件是 **.NET BinaryFormatter 二进制**
     * （桌面端也是 `base64.b64encode(f.read())` 发的）。
     * 原来这里用 readText()（UTF-8 解码）会把非文本字节变成 U+FFFD → 判分文件损坏 →
     * 服务端 base64.b64decode 直接解不出或解出垃圾 → 判分必然失败。
     */
    private String readBytesB64(File f) throws Exception {
        try (InputStream in = new FileInputStream(f)) {
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            return android.util.Base64.encodeToString(bos.toByteArray(),
                    android.util.Base64.NO_WRAP);
        }
    }

    private File findLog(File d) {
        File[] fs = d.listFiles();
        if (fs == null) return null;
        File best = null;
        for (File f : fs) {
            if (!f.isFile() || "material.zip".equals(f.getName())) continue;
            String n = f.getName().toLowerCase();
            if (n.endsWith(".log") || n.endsWith(".txt") || !n.contains(".")) {
                if (best == null || f.lastModified() > best.lastModified()) best = f;
            }
        }
        return best;
    }

    /** 浏览器收藏夹（Chromium 格式，Edge/Chromium 通用）——网页收藏题判分用 */
    private JSONArray readBookmarks() {
        JSONArray out = new JSONArray();
        List<File> candidates = new ArrayList<>();
        File rootfs = ContainerBridge.rootfs(this);
        candidates.add(new File(rootfs, "root/.config/chromium/Default/Bookmarks"));
        candidates.add(new File(rootfs, "root/.config/microsoft-edge/Default/Bookmarks"));
        candidates.add(new File(rootfs, "root/.config/chromium-browser/Default/Bookmarks"));
        for (File bm : candidates) {
            if (!bm.exists()) continue;
            try {
                JSONObject data = new JSONObject(readText(bm));
                JSONObject roots = data.optJSONObject("roots");
                if (roots == null) continue;
                collectFav(roots.optJSONObject("bookmark_bar"), out);
                collectFav(roots.optJSONObject("other"), out);
                collectFav(roots.optJSONObject("synced"), out);
            } catch (Exception ignore) { }
        }
        return out;
    }

    private void collectFav(JSONObject node, JSONArray out) {
        if (node == null) return;
        if ("url".equals(node.optString("type"))) {
            try {
                JSONObject it = new JSONObject();
                it.put("name", node.optString("name", ""));
                it.put("url", node.optString("url", ""));
                out.put(it);
            } catch (Exception ignore) { }
        }
        JSONArray ch = node.optJSONArray("children");
        if (ch != null) for (int i = 0; i < ch.length(); i++) collectFav(ch.optJSONObject(i), out);
    }

    // ============================== 心跳 / 计时 ==============================
    private void startHeartbeat() {
        new Thread(() -> {
            while (!hbStop && !submitted) {
                try {
                    JSONArray qids = new JSONArray();
                    JSONArray ans = new JSONArray();
                    for (int i = 0; i < qs.length(); i++) {
                        JSONObject q = qs.optJSONObject(i);
                        qids.put(q.opt("qid"));
                        JSONObject a = new JSONObject();
                        a.put("qid", q.opt("qid"));
                        if (isOperate(q)) {
                            a.put("refType", "operate");
                            a.put("sel", -1);
                            if (answered(i)) ans.put(a);
                        } else {
                            a.put("refType", "obj");
                            a.put("sel", sel[i]);
                            if (sel[i] >= 0) ans.put(a);
                        }
                    }
                    String cur = qs.length() > 0 ? String.valueOf(qs.optJSONObject(idx).opt("qid")) : "";
                    net.heartbeat(practice ? "practice" : "homework", recordId, cur, qids, ans);
                } catch (Exception ignore) { }
                try { Thread.sleep(5000); } catch (InterruptedException e) { break; }
            }
        }, "ttlx-hb").start();
    }

    private void tickTimer() {
        ui.postDelayed(() -> {
            // 页面已在销毁过程中就不要再弹窗了（否则可能 BadTokenException）
            if (submitted || isFinishing() || isDestroyed()) return;
            remain--;
            if (remain <= 0) {
                timeLabel.setText("00:00");
                alert("时间到", "时间到，自动交卷！");
                doSubmit();
                return;
            }
            int m = remain / 60, s = remain % 60;
            timeLabel.setText(String.format("%02d:%02d", m, s));
            tickTimer();
        }, 1000);
    }

    private String fmt(Object o) {
        if (o == null || o == JSONObject.NULL) return "-";
        if (o instanceof Double) return String.valueOf(Math.round((Double) o * 10) / 10.0);
        return String.valueOf(o);
    }

    // ============================== 工具 ==============================
    private androidx.appcompat.app.AlertDialog waitDialog(String msg) {
        LinearLayout box = new LinearLayout(this);
        box.setPadding(dp(24), dp(20), dp(24), dp(20));
        TextView t = new TextView(this);
        t.setText(msg);
        t.setTextSize(15);
        box.addView(t);
        androidx.appcompat.app.AlertDialog d = new MaterialAlertDialogBuilder(this).setView(box).setCancelable(false).create();
        d.show();
        return d;
    }

    private void alert(String title, String msg) {
        new MaterialAlertDialogBuilder(this).setTitle(title).setMessage(msg)
                .setPositiveButton("好", null).show();
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }

    @Override
    public void onBackPressed() {
        new MaterialAlertDialogBuilder(this)
                .setTitle("退出答题")
                .setMessage("答题还没交卷，退出将不保存选择题作答。确定退出吗？")
                .setPositiveButton("退出", (d, w) -> {
                    hbStop = true;
                    finish();
                })
                .setNegativeButton("继续答题", null)
                .show();
    }

    @Override
    protected void onDestroy() {
        hbStop = true;
        super.onDestroy();
    }
}
