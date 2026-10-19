package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.NumberPicker;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.card.MaterialCardView;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import com.google.android.material.bottomnavigation.BottomNavigationView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 主界面（Material 3，借鉴 SukiSU Ultra 风格）：
 *   大标题 + 圆角卡片列表 + 底部【毛玻璃】导航栏（GlassNavBar）
 */
public class MainActivity extends AppCompatActivity {

    private NetClient net;
    private FrameLayout content;
    private GlassNavBar glass;
    private BottomNavigationView navView;
    private int cur = 0;
    private TextView envBar;
    private final android.os.Handler uiH = new android.os.Handler(android.os.Looper.getMainLooper());
    private boolean envPoll = false;

    private static final String[] MODULES = {
            "1 信息与数据", "2 进制编码", "3 操作系统", "4 信息安全",
            "5 计算机硬件", "6 软件程序", "7 多媒体", "8 计算机网络"};

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        net = TTLX.net();
        if (net == null) {
            startActivity(new Intent(this, LoginActivity.class));
            finish();
            return;
        }
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        setContentView(buildUi());
        showTab(0);
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(getResources().getColor(R.color.md_surface));

        // 主列（顶部栏 + 进度条 + 内容）：也是毛玻璃的"背景捕获源"
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);

        // ---- 顶部大标题栏 ----
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(20), dp(16), dp(12), dp(6));
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(this);
        t.setText("学生端");
        t.setTextSize(26);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        titles.addView(t);
        TextView h = new TextView(this);
        h.setText(net.addr());
        h.setTextSize(12);
        h.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        titles.addView(h);
        top.addView(titles, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        ImageView deskIcon = new ImageView(this);
        deskIcon.setImageResource(R.drawable.ic_desktop);
        deskIcon.setImageTintList(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_primary)));
        deskIcon.setPadding(dp(12), dp(12), dp(12), dp(12));
        deskIcon.setOnClickListener(v -> openDesktop());
        top.addView(deskIcon, new LinearLayout.LayoutParams(dp(48), dp(48)));
        column.addView(top);

        // ---- 环境进度条（自动配置提示） ----
        envBar = new TextView(this);
        envBar.setTextSize(12);
        envBar.setTextColor(getResources().getColor(R.color.md_warn));
        envBar.setBackgroundColor(getResources().getColor(R.color.md_warn_container));
        envBar.setPadding(dp(16), dp(8), dp(16), dp(8));
        envBar.setVisibility(View.GONE);
        envBar.setOnClickListener(v -> startActivity(new Intent(this, SetupActivity.class)));
        column.addView(envBar);

        // ---- 内容区 ----
        content = new FrameLayout(this);
        column.addView(content, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(column, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        // ---- 底部【毛玻璃】导航栏 ----
        glass = new GlassNavBar(this);
        BottomNavigationView nav = new BottomNavigationView(this);
        navView = nav;
        nav.inflateMenu(R.menu.nav_main);
        nav.setBackground(null);
        nav.setItemIconTintList(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_primary)));
        nav.setItemTextColor(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_on_surface)));
        nav.setOnItemSelectedListener(item -> {
            if (item.getItemId() == R.id.nav_homework) showTab(0);
            else if (item.getItemId() == R.id.nav_practice) showTab(1);
            else showTab(2);
            return true;
        });
        glass.addView(nav, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        FrameLayout.LayoutParams glp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        glp.gravity = Gravity.BOTTOM;
        root.addView(glass, glp);

        glass.attachBackdrop(column);
        return root;
    }

    // ======================= 页签 =======================
    private void showTab(int i) {
        cur = i;
        content.removeAllViews();
        if (i == 0) content.addView(buildHomeworkPage());
        else if (i == 1) content.addView(buildPracticePage());
        else content.addView(buildMePage());
        if (glass != null) glass.markDirty();
    }

    // ======================= 作业页 =======================
    private View buildHomeworkPage() {
        ScrollView sv = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(6), dp(16), dp(104));   // 底部留出毛玻璃空间
        sv.addView(box);
        loadHomeworks(box);
        return sv;
    }

    private void loadHomeworks(LinearLayout box) {
        box.removeAllViews();
        box.addView(hint("正在加载作业列表…"));
        new Thread(() -> {
            String err = null;
            JSONArray hws = null;
            try {
                JSONObject r = net.stuHomeworks();
                if (r.optInt("code") != 0) err = r.optString("msg", "加载失败");
                else hws = r.optJSONArray("homeworks");
            } catch (Exception e) {
                err = e.getMessage();
                AppLog.e("hw", "作业列表加载失败: " + e);
            }
            final String ferr = err;
            final JSONArray fhws = hws;
            runOnUiThread(() -> {
                box.removeAllViews();
                if (ferr != null) {
                    box.addView(hint("加载失败：" + ferr));
                    return;
                }
                if (fhws == null || fhws.length() == 0) {
                    box.addView(hint("暂无作业，请等待老师发布"));
                    return;
                }
                for (int i = 0; i < fhws.length(); i++) {
                    box.addView(homeworkCard(fhws.optJSONObject(i)));
                }
                if (glass != null) glass.markDirty();
            });
        }, "ttlx-hw").start();
    }

    private View homeworkCard(JSONObject hw) {
        MaterialCardView card = new MaterialCardView(this);
        card.setRadius(dp(20));
        card.setCardElevation(dp(1));
        card.setCardBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.bottomMargin = dp(12);
        card.setLayoutParams(clp);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(18), dp(16), dp(18), dp(14));
        card.addView(box);

        TextView title = new TextView(this);
        title.setText(hw.optString("title", ""));
        title.setTextSize(16);
        title.setTextColor(getResources().getColor(R.color.md_on_surface));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);

        int wstate = hw.optInt("windowState", 0);
        boolean hasScore = !hw.isNull("myScore");
        Object score = hw.opt("myScore");
        String st;
        int colorRes;
        if (wstate == 3) { st = hasScore ? "已交卷  得分 " + score : "已交卷"; colorRes = R.color.md_primary; }
        else if (hasScore) { st = "已得分 " + score; colorRes = R.color.md_primary; }
        else { st = "未作答"; colorRes = R.color.md_success; }
        TextView sl = new TextView(this);
        sl.setText(st);
        sl.setTextSize(13);
        sl.setTextColor(getResources().getColor(colorRes));
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        slp.topMargin = dp(6);
        box.addView(sl, slp);

        String wmsg = hw.optString("windowMsg", "");
        if (!wmsg.isEmpty()) {
            TextView ml = new TextView(this);
            ml.setText(wmsg);
            ml.setTextSize(12);
            ml.setTextColor(getResources().getColor(R.color.md_warn));
            LinearLayout.LayoutParams mlp2 = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            mlp2.topMargin = dp(4);
            box.addView(ml, mlp2);
        }

        long hid = hw.optLong("id", 0);
        MaterialButton btn = new MaterialButton(this);
        btn.setAllCaps(false);
        btn.setTextSize(14);
        btn.setCornerRadius(dp(22));
        if (wstate == 1) {
            btn.setText("未开始");
            btn.setEnabled(false);
        } else if (wstate == 3 || (hasScore && wstate != 1 && wstate != 2)) {
            btn.setText("查看成绩");
            btn.setBackgroundColor(getResources().getColor(R.color.md_primary_container));
            btn.setTextColor(getResources().getColor(R.color.md_on_primary_container));
            btn.setOnClickListener(v -> showHomeworkResult(hid));
        } else {
            btn.setText("开始答题");
            btn.setBackgroundColor(getResources().getColor(R.color.md_primary));
            btn.setTextColor(Color.WHITE);
            btn.setOnClickListener(v -> startHomework(hid));
        }
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(46));
        blp.topMargin = dp(12);
        box.addView(btn, blp);
        return card;
    }

    private void startHomework(long hwId) {
        waitDialog("正在加载试卷题目…");
        new Thread(() -> {
            String err = null;
            JSONObject r = null;
            try {
                r = net.startHomework(hwId);
                if (r.optInt("code") != 0) err = r.optString("msg", "未知错误");
            } catch (Exception e) { err = e.getMessage(); }
            final String ferr = err;
            final JSONObject fr = r;
            runOnUiThread(() -> {
                dismissWait();
                if (ferr != null) { alert("提示", ferr); return; }
                openExam(fr.optLong("recordId", 0), fr.optString("title", ""),
                        fr.optJSONArray("questions"), false, fr.optInt("durationSec", 0));
            });
        }, "ttlx-start").start();
    }

    private void showHomeworkResult(long hwId) {
        waitDialog("正在加载成绩…");
        new Thread(() -> {
            String err = null;
            JSONObject r = null;
            try { r = net.myResult(hwId); } catch (Exception e) { err = e.getMessage(); }
            final String ferr = err;
            final JSONObject fr = r;
            runOnUiThread(() -> {
                dismissWait();
                if (ferr != null) { alert("提示", ferr); return; }
                if (fr.optBoolean("scoreHidden", false)) {
                    alert("提示", fr.optString("msg", "成绩待教师公布"));
                    return;
                }
                JSONArray detail = fr.optJSONArray("detail");
                if (detail == null) detail = new JSONArray();
                ResultDialog.show(MainActivity.this, detail, null);
            });
        }, "ttlx-res").start();
    }

    private void openExam(long recordId, String title, JSONArray questions, boolean practice, int dur) {
        if (questions == null || questions.length() == 0) {
            alert("提示", "没有题目");
            return;
        }
        Intent it = new Intent(this, ExamActivity.class);
        it.putExtra("recordId", recordId);
        it.putExtra("title", title);
        it.putExtra("questions", questions.toString());
        it.putExtra("practice", practice);
        it.putExtra("duration", dur);
        startActivity(it);
    }

    // ======================= 练习页 =======================
    private View buildPracticePage() {
        ScrollView sv = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(6), dp(16), dp(104));
        sv.addView(box);

        box.addView(hint("自主练习不占考试名额、不受时间限制。\n操作题会自动切到容器桌面里完成。"));

        box.addView(practiceCard(R.drawable.ic_practice, "套卷训练",
                "50 道选择题 + 常见操作题（win10/邮件/Word/PPT/Excel/Python）",
                "开始套卷训练", true, v -> startSet()));

        box.addView(practiceCard(R.drawable.ic_homework, "按模块练习",
                "自选模块与题量（勾选 + 每行填题量）",
                "按模块练习", false, v -> startActivity(
                        new Intent(this, ModulePracticeActivity.class))));
        return sv;
    }

    private View practiceCard(int icon, String title, String desc, String btnText,
                              boolean primary, View.OnClickListener l) {
        MaterialCardView card = new MaterialCardView(this);
        card.setRadius(dp(20));
        card.setCardElevation(dp(1));
        card.setCardBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.bottomMargin = dp(12);
        card.setLayoutParams(clp);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(18), dp(16), dp(18), dp(14));
        card.addView(box);

        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        ImageView ic = new ImageView(this);
        ic.setImageResource(icon);
        ic.setImageTintList(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_primary)));
        head.addView(ic, new LinearLayout.LayoutParams(dp(24), dp(24)));
        TextView t = new TextView(this);
        t.setText("  " + title);
        t.setTextSize(16);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        head.addView(t);
        box.addView(head);

        TextView d = new TextView(this);
        d.setText(desc);
        d.setTextSize(13);
        d.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        dlp.topMargin = dp(6);
        box.addView(d, dlp);

        MaterialButton b = new MaterialButton(this);
        b.setText(btnText);
        b.setAllCaps(false);
        b.setCornerRadius(dp(22));
        b.setTextSize(14);
        if (primary) {
            b.setBackgroundColor(getResources().getColor(R.color.md_primary));
            b.setTextColor(Color.WHITE);
        } else {
            b.setBackgroundColor(getResources().getColor(R.color.md_primary_container));
            b.setTextColor(getResources().getColor(R.color.md_on_primary_container));
        }
        b.setOnClickListener(l);
        LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(46));
        blp.topMargin = dp(12);
        box.addView(b, blp);
        return card;
    }

    private void startSet() {
        JSONArray parts = new JSONArray();
        try {
            int[] cnts = new int[8];
            for (int i = 0; i < 8; i++) cnts[i] = 1;
            java.util.Random rnd = new java.util.Random();
            for (int i = 0; i < 42; i++) cnts[rnd.nextInt(8)]++;
            for (int i = 0; i < 8; i++) {
                JSONObject p = new JSONObject();
                p.put("type", "obj"); p.put("course", i + 1); p.put("count", cnts[i]);
                parts.put(p);
            }
            String[][] ops = {{"win10", "5"}, {"win10net", "4"}, {"email", "1"},
                    {"word", "1"}, {"ppt", "1"}, {"excel", "1"}, {"python", "2"}};
            for (String[] o : ops) {
                JSONObject p = new JSONObject();
                p.put("type", "operate"); p.put("opType", o[0]);
                p.put("count", Integer.parseInt(o[1])); p.put("score", 20.0);
                parts.put(p);
            }
        } catch (Exception ignore) { }
        launchPractice(parts, "套卷训练");
    }
    private void launchPractice(JSONArray parts, String title) {
        waitDialog("正在抽题，请稍等…");
        new Thread(() -> {
            String err = null;
            JSONObject r = null;
            try {
                r = net.startPractice(parts);
                if (r.optInt("code") != 0) err = r.optString("msg", "抽题失败");
            } catch (Exception e) { err = e.getMessage(); }
            final String ferr = err;
            final JSONObject fr = r;
            runOnUiThread(() -> {
                dismissWait();
                if (ferr != null) { alert("提示", ferr); return; }
                openExam(0, title, fr.optJSONArray("questions"), true, 0);
            });
        }, "ttlx-practice").start();
    }

    // ======================= 我的页 =======================
    private View buildMePage() {
        ScrollView sv = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(6), dp(16), dp(104));
        sv.addView(box);

        // 账号卡
        MaterialCardView userCard = new MaterialCardView(this);
        userCard.setRadius(dp(20));
        userCard.setCardElevation(dp(0));
        userCard.setCardBackgroundColor(getResources().getColor(R.color.md_primary_container));
        LinearLayout.LayoutParams ucLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        ucLp.bottomMargin = dp(12);
        userCard.setLayoutParams(ucLp);
        LinearLayout ub = new LinearLayout(this);
        ub.setGravity(Gravity.CENTER_VERTICAL);
        ub.setPadding(dp(18), dp(18), dp(18), dp(18));
        userCard.addView(ub);
        ImageView avatar = new ImageView(this);
        avatar.setImageResource(R.drawable.ic_me);
        avatar.setImageTintList(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_on_primary_container)));
        ub.addView(avatar, new LinearLayout.LayoutParams(dp(28), dp(28)));
        String name = "";
        if (net.userInfo != null) {
            name = net.userInfo.optString("displayName", net.userInfo.optString("username", ""));
        }
        TextView who = new TextView(this);
        who.setText("  " + (name.isEmpty() ? "已登录" : name));
        who.setTextSize(17);
        who.setTypeface(Typeface.DEFAULT_BOLD);
        who.setTextColor(getResources().getColor(R.color.md_on_primary_container));
        ub.addView(who);
        box.addView(userCard);

        // 功能列表
        MaterialCardView list = new MaterialCardView(this);
        list.setRadius(dp(20));
        list.setCardElevation(dp(1));
        list.setCardBackgroundColor(Color.WHITE);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        list.addView(col);
        box.addView(list);

        col.addView(meRow(R.drawable.ic_homework, "错题本", "查看做错的题目与解析", v -> startActivity(new Intent(this, WrongBookActivity.class))));
        col.addView(divider());
        col.addView(meRow(R.drawable.ic_practice, "历史成绩", "历次作业/考试成绩与答题详情", v -> startActivity(new Intent(this, HistoryActivity.class))));
        col.addView(divider());
        col.addView(meRow(R.drawable.ic_rocket, "准备桌面环境", "操作题环境（首次需下载，自动进行）", v -> {
            SetupManager sm = new SetupManager(this);
            if (sm.isReady()) {
                Toast.makeText(this, "桌面环境已就绪", Toast.LENGTH_SHORT).show();
                openDesktop();
            } else {
                startActivity(new Intent(this, SetupActivity.class));
            }
        }));
        col.addView(divider());
        col.addView(meRow(R.drawable.ic_share, "导出并分享日志", "导出诊断日志并调起系统分享", v -> LogShare.share(this)));

        MaterialButton out = new MaterialButton(this);
        out.setText("退出登录");
        out.setAllCaps(false);
        out.setCornerRadius(dp(24));
        out.setTextSize(15);
        out.setBackgroundColor(getResources().getColor(R.color.md_error_container));
        out.setTextColor(getResources().getColor(R.color.md_error));
        out.setOnClickListener(v -> {
            TTLX.logout();
            startActivity(new Intent(this, LoginActivity.class));
            finish();
        });
        LinearLayout.LayoutParams olp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        olp.topMargin = dp(16);
        box.addView(out, olp);
        return sv;
    }

    private View meRow(int icon, String title, String desc, View.OnClickListener l) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(18), dp(14), dp(18), dp(14));
        row.setClickable(true);
        row.setOnClickListener(l);
        ImageView ic = new ImageView(this);
        ic.setImageResource(icon);
        ic.setImageTintList(android.content.res.ColorStateList.valueOf(
                getResources().getColor(R.color.md_primary)));
        row.addView(ic, new LinearLayout.LayoutParams(dp(24), dp(24)));
        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(this);
        t.setText("  " + title);
        t.setTextSize(15);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        t.setTypeface(Typeface.DEFAULT_BOLD);
        text.addView(t);
        TextView d = new TextView(this);
        d.setText("  " + desc);
        d.setTextSize(12);
        d.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        text.addView(d);
        row.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView arrow = new TextView(this);
        arrow.setText("›");
        arrow.setTextSize(20);
        arrow.setTextColor(getResources().getColor(R.color.md_outline));
        row.addView(arrow);
        return row;
    }

    private View divider() {
        View v = new View(this);
        v.setBackgroundColor(getResources().getColor(R.color.md_surface_variant));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Math.max(1, dp(1)));
        lp.leftMargin = dp(18);
        lp.rightMargin = dp(18);
        v.setLayoutParams(lp);
        return v;
    }    private void showRecordDetail(long recId) {
        waitDialog("正在加载答题详情…");
        new Thread(() -> {
            String err = null;
            JSONArray arr = null;
            try {
                JSONObject r = net.recordDetail(recId);
                arr = r.optJSONArray("detail");
            } catch (Exception e) { err = e.getMessage(); }
            final String ferr = err;
            final JSONArray fa = arr;
            runOnUiThread(() -> {
                dismissWait();
                if (ferr != null) { alert("提示", ferr); return; }
                ResultDialog.show(this, fa == null ? new JSONArray() : fa, null);
            });
        }, "ttlx-detail").start();
    }

    // ======================= 通用 =======================
    private void openDesktop() {
        SetupManager sm = new SetupManager(this);
        if (!sm.isReady()) {
            startActivity(new Intent(this, SetupActivity.class));
        } else {
            startActivity(new Intent(this, DesktopActivity.class));
        }
    }

    private TextView hint(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(14);
        t.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(10), dp(30), dp(10), dp(30));
        return t;
    }

    private androidx.appcompat.app.AlertDialog waitDlg;

    private void waitDialog(String msg) {
        dismissWait();
        LinearLayout box = new LinearLayout(this);
        box.setPadding(dp(24), dp(20), dp(24), dp(20));
        TextView t = new TextView(this);
        t.setText(msg);
        t.setTextSize(15);
        box.addView(t);
        waitDlg = new MaterialAlertDialogBuilder(this).setView(box).setCancelable(false).create();
        waitDlg.show();
    }

    private void dismissWait() {
        if (waitDlg != null) {
            try { waitDlg.dismiss(); } catch (Exception ignore) { }
            waitDlg = null;
        }
    }

    private void alert(String title, String msg) {
        new MaterialAlertDialogBuilder(this).setTitle(title).setMessage(msg)
                .setPositiveButton("好", null).show();
    }

    private void listDialog(String title, List<String> lines) {
        new MaterialAlertDialogBuilder(this)
                .setTitle(title)
                .setItems(lines.toArray(new String[0]), null)
                .setNegativeButton("关闭", null)
                .show();
    }

    @Override
    protected void onResume() {
        super.onResume();
        SetupRunner.startIfNeeded(this);
        envPoll = true;
        pollEnv();
        if (cur == 0) {
            try { if (navView != null) navView.setSelectedItemId(R.id.nav_homework); } catch (Exception ignore) { }
            showTab(0);
        }
    }

    @Override
    protected void onPause() {
        envPoll = false;
        super.onPause();
    }

    private void pollEnv() {
        uiH.postDelayed(new Runnable() {
            @Override public void run() {
                if (!envPoll || envBar == null) return;
                if (SetupRunner.done) { envBar.setVisibility(View.GONE); return; }
                envBar.setVisibility(View.VISIBLE);
                int p = SetupRunner.progress;
                envBar.setText("操作题环境准备中" + (p >= 0 ? "（" + p + "%）" : "")
                        + "：" + SetupRunner.status + "   · 点击查看进度");
                uiH.postDelayed(this, 1500);
            }
        }, 600);
    }
}
