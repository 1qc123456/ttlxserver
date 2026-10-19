package com.ttlx.student;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;

/**
 * 完整 PC 虚拟键盘（用于容器桌面里给 Linux 应用发按键）。
 *
 * 覆盖：Esc / F1~F12 / 主键区全部按键 / 方向键 / Ins Del Home End PgUp PgDn /
 *       PrtSc ScrLk Pause / Ctrl Win Alt Menu —— 只**不含右侧小键盘**（按需求）。
 * 右上角有一个醒目的「收起键盘」按钮。
 *
 * 交互反馈：每个键都有按下变色（state_pressed）；修饰键点一下会**锁定并高亮成蓝色**
 * （Shift/Ctrl/Alt/Win），再按其它键时自动「按下修饰键 → 按键 → 抬起修饰键」并解锁。
 *
 * 尺寸自适应：横屏手机屏幕很矮，键盘高度按屏幕高度的 55% 反推单行高度
 * （26dp~40dp 之间），避免把桌面全挡住。
 */
public class PcKeyboard extends LinearLayout {

    public interface Sender {
        void key(int keysym, boolean down);
        void onCollapse();
    }

    // ---- X11 keysym ----
    private static final int ESC = 0xFF1B, F1 = 0xFFBE, BKSP = 0xFF08, TAB = 0xFF09;
    private static final int CAPS = 0xFFE5, ENTER = 0xFF0D;
    private static final int SHIFT_L = 0xFFE1, SHIFT_R = 0xFFE2;
    private static final int CTRL_L = 0xFFE3, CTRL_R = 0xFFE4;
    private static final int ALT_L = 0xFFE9, ALT_R = 0xFFEA;
    private static final int SUPER_L = 0xFFEB, SUPER_R = 0xFFEC, MENU = 0xFF67;
    private static final int LEFT = 0xFF51, UP = 0xFF52, RIGHT = 0xFF53, DOWN = 0xFF54;
    private static final int INS = 0xFF63, DEL = 0xFFFF, HOME = 0xFF50, END = 0xFF57;
    private static final int PGUP = 0xFF55, PGDN = 0xFF56;
    private static final int PRINT = 0xFF61, SCROLL = 0xFF14, PAUSE = 0xFF13;
    private static final int SPACE = 0x0020;

    private static final int BG_PANEL = 0xF2141B2A;
    private static final int C_KEY = 0xFF2C3852;        // 普通键
    private static final int C_KEY_DOWN = 0xFF5A7099;   // 普通键按下
    private static final int C_FUNC = 0xFF232C3E;       // 功能键（Esc/F1-F12 等）
    private static final int C_FUNC_DOWN = 0xFF4A5670;
    private static final int C_MOD = 0xFF3A4A6B;        // 修饰键未锁定
    private static final int C_MOD_DOWN = 0xFF6C7EA8;
    private static final int C_MOD_ON = 0xFF4F86F8;     // 修饰键已锁定（高亮）
    private static final int C_MOD_ON_DOWN = 0xFF7AA8FF;
    private static final int C_KEY_TEXT = 0xFFE8EEF8;
    private static final int C_ACCENT = 0xFFFF8A3D;
    private static final int C_ACCENT_DOWN = 0xFFFFB067;

    private final Sender sender;
    private final LinkedHashSet<Integer> latched = new LinkedHashSet<>();
    private final List<KeyView> modViews = new ArrayList<>();
    private int rowH;

    private static class K {
        final String label; final int ks; final float w; final boolean mod; final boolean func;
        K(String l, int k, float w) { this(l, k, w, false, false); }
        K(String l, int k, float w, boolean mod) { this(l, k, w, mod, false); }
        K(String l, int k, float w, boolean mod, boolean func) {
            label = l; ks = k; this.w = w; this.mod = mod; this.func = func;
        }
        static K gap(float w) { return new K("", 0, w); }
    }

    private static class KeyView {
        final TextView v; final int ks; final boolean mod;
        KeyView(TextView v, int ks, boolean mod) { this.v = v; this.ks = ks; this.mod = mod; }
    }

    public PcKeyboard(Context c, Sender s) {
        super(c);
        this.sender = s;
        setOrientation(VERTICAL);
        setBackgroundColor(BG_PANEL);
        setPadding(dp(6), dp(4), dp(6), dp(6));
        // 横屏手机很矮：键盘总高控制在屏幕的 ~55%
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int screenH = Math.min(dm.widthPixels, dm.heightPixels);
        int budget = (int) (screenH * 0.55f) - dp(34) - dp(12);
        rowH = Math.max(dp(26), Math.min(dp(42), budget / 6));
        build();
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private GradientDrawable round(int color) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(5));
        return g;
    }

    private Drawable bg(int normal, int pressed, int selected) {
        StateListDrawable d = new StateListDrawable();
        d.addState(new int[]{android.R.attr.state_pressed}, round(pressed));
        d.addState(new int[]{android.R.attr.state_selected}, round(selected));
        d.addState(new int[]{}, round(normal));
        return d;
    }

    // ==================== 布局 ====================
    private void build() {
        addView(buildHeader(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(32)));

        LinearLayout body = new LinearLayout(getContext());
        body.setOrientation(HORIZONTAL);

        LinearLayout main = new LinearLayout(getContext());
        main.setOrientation(VERTICAL);
        LinearLayout nav = new LinearLayout(getContext());
        nav.setOrientation(VERTICAL);

        addRow(main, row0());
        addRow(main, row1());
        addRow(main, row2());
        addRow(main, row3());
        addRow(main, row4());
        addRow(main, row5());

        addRow(nav, new K[]{
                new K("Ins", INS, 1), new K("Home", HOME, 1), new K("PgUp", PGUP, 1)});
        addRow(nav, new K[]{
                new K("Del", DEL, 1), new K("End", END, 1), new K("PgDn", PGDN, 1)});
        addRow(nav, new K[]{
                new K("PrtSc", PRINT, 1), new K("ScrLk", SCROLL, 1), new K("Pause", PAUSE, 1)});
        addRow(nav, new K[]{K.gap(1), new K("↑", UP, 1), K.gap(1)});
        addRow(nav, new K[]{new K("←", LEFT, 1), new K("↓", DOWN, 1), new K("→", RIGHT, 1)});
        addRow(nav, new K[]{K.gap(1), K.gap(1), K.gap(1)});

        body.addView(main, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 15f));
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 3.2f);
        nlp.leftMargin = dp(4);
        body.addView(nav, nlp);
        addView(body, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
    }

    /** 顶栏：左标题 + 右上角醒目的「收起键盘」按钮 */
    private View buildHeader() {
        LinearLayout h = new LinearLayout(getContext());
        h.setOrientation(HORIZONTAL);
        h.setGravity(Gravity.CENTER_VERTICAL);

        TextView t = new TextView(getContext());
        t.setText("滑动=移动鼠标 · 单点=左键 · 长按=右键 · 长按拖动=拖拽 · 双指滑=滚动");
        t.setTextColor(0xFF8FA3C8);
        t.setTextSize(10);
        h.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView collapse = new TextView(getContext());
        collapse.setText("⌄  收起键盘");
        collapse.setTextColor(Color.WHITE);
        collapse.setTextSize(12);
        collapse.setTypeface(Typeface.DEFAULT_BOLD);
        collapse.setGravity(Gravity.CENTER);
        collapse.setBackground(bg(C_ACCENT, C_ACCENT_DOWN, C_ACCENT));
        collapse.setClickable(true);
        collapse.setPadding(dp(14), 0, dp(14), 0);
        collapse.setOnClickListener(v -> {
            if (sender != null) sender.onCollapse();
        });
        h.addView(collapse, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(28)));
        return h;
    }

    private void addRow(LinearLayout parent, K[] keys) {
        LinearLayout row = new LinearLayout(getContext());
        row.setOrientation(HORIZONTAL);
        for (K k : keys) {
            LinearLayout.LayoutParams lp =
                    new LinearLayout.LayoutParams(0, rowH, k.w);
            lp.leftMargin = dp(2);
            lp.rightMargin = dp(2);
            if (k.label.isEmpty()) {
                row.addView(new View(getContext()), lp);
            } else {
                row.addView(makeKey(k), lp);
            }
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(3);
        parent.addView(row, lp);
    }

    private View makeKey(final K k) {
        TextView tv = new TextView(getContext());
        tv.setText(k.label);
        tv.setTextColor(C_KEY_TEXT);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_PX, rowH * 0.34f);
        tv.setGravity(Gravity.CENTER);
        tv.setSingleLine(true);
        tv.setPadding(dp(1), 0, dp(1), 0);
        if (k.mod) {
            tv.setBackground(bg(C_MOD, C_MOD_DOWN, C_MOD_ON));
        } else if (k.func) {
            tv.setBackground(bg(C_FUNC, C_FUNC_DOWN, C_FUNC));
        } else {
            tv.setBackground(bg(C_KEY, C_KEY_DOWN, C_KEY));
        }
        tv.setClickable(true);
        tv.setFocusable(false);
        tv.setOnClickListener(v -> {
            if (k.mod) { toggleMod(k.ks, tv); return; }
            press(k.ks);
        });
        if (k.mod) modViews.add(new KeyView(tv, k.ks, true));
        return tv;
    }

    // ==================== 按键逻辑 ====================
    private void toggleMod(int ks, TextView v) {
        boolean on = !latched.contains(ks);
        if (on) latched.add(ks); else latched.remove(ks);
        v.setSelected(on);          // 由 StateListDrawable 换成高亮色
    }

    private void press(int ks) {
        if (sender == null) return;
        if (latched.isEmpty()) {
            sender.key(ks, true);
            sender.key(ks, false);
            return;
        }
        // 锁定中的修饰键：先按下 → 敲键 → 再抬起，然后解锁
        for (int m : latched) sender.key(m, true);
        sender.key(ks, true);
        sender.key(ks, false);
        List<Integer> rev = new ArrayList<>(latched);
        for (int i = rev.size() - 1; i >= 0; i--) sender.key(rev.get(i), false);
        clearLatch();
    }

    private void clearLatch() {
        latched.clear();
        for (KeyView kv : modViews) kv.v.setSelected(false);
    }

    // ==================== 各行定义 ====================
    private K[] row0() {
        List<K> l = new ArrayList<>();
        l.add(new K("Esc", ESC, 1.6f, false, true));
        for (int i = 0; i < 12; i++) l.add(new K("F" + (i + 1), F1 + i, 1f, false, true));
        l.add(K.gap(1.4f));
        return l.toArray(new K[0]);
    }

    private K[] row1() {
        List<K> l = new ArrayList<>();
        l.add(new K("`", 0x0060, 1f, false, true));
        for (int i = 1; i <= 9; i++) l.add(new K(String.valueOf(i), 0x30 + i, 1f));
        l.add(new K("0", 0x0030, 1f));
        l.add(new K("-", 0x002D, 1f));
        l.add(new K("=", 0x003D, 1f));
        l.add(new K("⌫", BKSP, 2f, false, true));
        return l.toArray(new K[0]);
    }

    private K[] row2() {
        List<K> l = new ArrayList<>();
        l.add(new K("Tab", TAB, 1.5f, false, true));
        String[] q = {"q", "w", "e", "r", "t", "y", "u", "i", "o", "p"};
        for (String s : q) l.add(new K(s.toUpperCase(), s.charAt(0), 1f));
        l.add(new K("[", 0x005B, 1f));
        l.add(new K("]", 0x005D, 1f));
        l.add(new K("\\", 0x005C, 1.5f));
        return l.toArray(new K[0]);
    }

    private K[] row3() {
        List<K> l = new ArrayList<>();
        l.add(new K("Caps", CAPS, 1.8f, false, true));
        String[] a = {"a", "s", "d", "f", "g", "h", "j", "k", "l"};
        for (String s : a) l.add(new K(s.toUpperCase(), s.charAt(0), 1f));
        l.add(new K(";", 0x003B, 1f));
        l.add(new K("'", 0x0027, 1f));
        l.add(new K("Enter", ENTER, 2.2f, false, true));
        return l.toArray(new K[0]);
    }

    private K[] row4() {
        List<K> l = new ArrayList<>();
        l.add(new K("Shift", SHIFT_L, 2.2f, true, true));
        String[] z = {"z", "x", "c", "v", "b", "n", "m"};
        for (String s : z) l.add(new K(s.toUpperCase(), s.charAt(0), 1f));
        l.add(new K(",", 0x002C, 1f));
        l.add(new K(".", 0x002E, 1f));
        l.add(new K("/", 0x002F, 1f));
        l.add(new K("Shift", SHIFT_R, 2.8f, true, true));
        return l.toArray(new K[0]);
    }

    private K[] row5() {
        return new K[]{
                new K("Ctrl", CTRL_L, 1.4f, true, true),
                new K("Win", SUPER_L, 1.2f, true, true),
                new K("Alt", ALT_L, 1.2f, true, true),
                new K("Space", SPACE, 6.4f),
                new K("Alt", ALT_R, 1.2f, true, true),
                new K("Win", SUPER_R, 1.2f, true, true),
                new K("Menu", MENU, 1.2f, false, true),
                new K("Ctrl", CTRL_R, 1.4f, true, true),
        };
    }
}
