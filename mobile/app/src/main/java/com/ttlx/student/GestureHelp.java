package com.ttlx.student;

import android.app.Activity;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.google.android.material.dialog.MaterialAlertDialogBuilder;

/**
 * 桌面操作指南（手势教程）。
 *
 * 第一次进入容器桌面时自动弹出一次（用 SharedPreferences 记标记）；
 * 之后随时可以点桌面顶栏的「手 势」再看。
 */
public class GestureHelp {

    private static final String PREFS = "ttlx_stu";
    // v3.0：手势改了（滑动=移动鼠标、单点=左键），换 key 让老用户也能重新看到一次
    private static final String KEY = "gesture_help_shown_v3";

    public static boolean hasShown(Context c) {
        return c.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .getBoolean(KEY, false);
    }

    public static void markShown(Context c) {
        c.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putBoolean(KEY, true).apply();
    }

    /** 首次进入桌面时调用：没看过就弹一次并记标记 */
    public static void showOnce(Activity act) {
        if (hasShown(act)) return;
        markShown(act);
        show(act, true);
    }

    public static void show(Activity act) {
        show(act, false);
    }

    private static void show(Activity act, boolean firstTime) {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(act, 18), dp(act, 12), dp(act, 18), dp(act, 6));

        TextView intro = new TextView(act);
        intro.setText("容器桌面就是一台 Linux 电脑，用手指当鼠标用：");
        intro.setTextSize(13);
        intro.setTextColor(Color.parseColor("#5F5E5A"));
        intro.setLineSpacing(0, 1.2f);
        box.addView(intro);

        box.addView(section(act, "鼠标操作（v3.0：滑动=移动鼠标，单点=左键）"));
        box.addView(row(act, "单指滑动", "**移动鼠标指针**（指到哪就是哪，不会误选中东西）"));
        box.addView(row(act, "单指轻点", "鼠标左键单击：点按钮、点图标、打开文件"));
        box.addView(row(act, "长按约半秒", "鼠标右键单击：弹出右键菜单"));
        box.addView(row(act, "长按后拖动", "按住左键拖动：框选内容、拖拽文件/窗口"));
        box.addView(row(act, "双指一起轻点", "鼠标右键（和长按一样，更顺手）"));
        box.addView(row(act, "双指上下滑", "滚轮：滚动文档 / 网页"));
        box.addView(row(act, "双指左右滑", "横向滚动"));
        box.addView(row(act, "三指一起轻点", "鼠标中键（浏览器里新标签页打开链接）"));

        box.addView(section(act, "桌面上的浮动窗口"));
        box.addView(row(act, "题目浮窗", "显示本题要求；按住它的标题栏可以**拖到任意位置**，点右侧「－」收起"));
        box.addView(row(act, "桌面菜单", "左上角那个小条。点右侧「＋」展开按钮；同样可以拖动"));

        box.addView(section(act, "桌面菜单里的按钮"));
        box.addView(row(act, "虚拟程序", "Win10 设置 / Edge / 邮件 题的作答程序，一键启动（只有这几类题才出现）"));
        box.addView(row(act, "键 盘", "完整 PC 键盘：Esc / F1~F12 / 方向键等；Shift、Ctrl 点一下会锁定变蓝"));
        box.addView(row(act, "诊 断", "桌面出问题时点它，直接看端口状态与 Xvnc 日志"));
        box.addView(row(act, "重 启", "桌面卡住 / 黑屏时重启容器（约 30 秒）"));
        box.addView(row(act, "分享日志", "把诊断日志通过微信 / QQ 发给老师"));

        TextView foot = new TextView(act);
        foot.setText(firstTime
                ? "\n这条提示只在第一次打开桌面时自动出现，之后可点桌面菜单里的「手 势」随时再看。"
                : "\n提示：键盘里点 Shift / Ctrl / Alt 会锁定并高亮，再按其它键即等效于电脑上按住不放。");
        foot.setTextSize(12);
        foot.setTextColor(Color.parseColor("#8A6D3B"));
        foot.setLineSpacing(0, 1.2f);
        box.addView(foot);

        ScrollView sv = new ScrollView(act);
        sv.addView(box);
        android.util.DisplayMetrics dm = act.getResources().getDisplayMetrics();
        int maxH = (int) (Math.min(dm.widthPixels, dm.heightPixels) * 0.66f);
        sv.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, maxH));

        new MaterialAlertDialogBuilder(act)
                .setTitle(firstTime ? "桌面操作指南（第一次使用）" : "桌面操作指南")
                .setView(sv)
                .setPositiveButton("开始使用", null)
                .show();
    }

    private static TextView section(Activity act, String s) {
        TextView t = new TextView(act);
        t.setText(s);
        t.setTextSize(13);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(act.getResources().getColor(R.color.md_primary));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(act, 30));
        lp.topMargin = dp(act, 10);
        t.setLayoutParams(lp);
        return t;
    }

    /** 一行：[手势标签] + 说明 */
    private static LinearLayout row(Activity act, String key, String desc) {
        LinearLayout r = new LinearLayout(act);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rlp.topMargin = dp(act, 6);
        r.setLayoutParams(rlp);

        TextView k = new TextView(act);
        k.setText(key);
        k.setTextSize(13);
        k.setTypeface(Typeface.DEFAULT_BOLD);
        k.setTextColor(Color.parseColor("#185FA5"));
        k.setGravity(Gravity.CENTER);
        k.setBackground(chip(act));
        k.setPadding(dp(act, 10), dp(act, 6), dp(act, 10), dp(act, 6));
        r.addView(k, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView d = new TextView(act);
        d.setText(desc);
        d.setTextSize(13);
        d.setTextColor(Color.parseColor("#2C2C2A"));
        d.setLineSpacing(0, 1.15f);
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        dlp.leftMargin = dp(act, 10);
        r.addView(d, dlp);
        return r;
    }

    private static GradientDrawable chip(Activity act) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(Color.parseColor("#E6F1FB"));
        g.setCornerRadius(dp(act, 8));
        g.setStroke(dp(act, 1), Color.parseColor("#85B7EB"));
        return g;
    }

    private static int dp(Activity a, int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                a.getResources().getDisplayMetrics());
    }
}
