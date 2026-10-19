package com.ttlx.student;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 可随意拖动的浮窗（**App 内部**实现，不需要 SYSTEM_ALERT_WINDOW 悬浮窗权限）。
 *
 * 结构：
 *   ┌─────────────────────────────┐
 *   │ ☰ 标题            ［－］    │ ← 标题栏：按住即可拖动；右侧按钮折叠/展开
 *   ├─────────────────────────────┤
 *   │ 内容区（body）              │
 *   └─────────────────────────────┘
 *
 * 用法：
 *   FloatPanel p = new FloatPanel(ctx, "题目");
 *   p.setBody(myView);
 *   root.addView(p);            // root 必须是 FrameLayout
 *   p.placeTopRight();          // 初始位置
 *   p.restore("qwin");          // 恢复上次位置（可选）
 */
public class FloatPanel extends LinearLayout {

    private final TextView titleView;
    private final TextView foldBtn;
    private final LinearLayout body;
    private final Context ctx;
    private final SharedPreferences sp;

    private boolean folded = false;
    private String posKey = null;

    private float dragStartRawX, dragStartRawY;
    private int dragStartLeft, dragStartTop;
    private boolean dragging;

    public FloatPanel(Context c, String title) {
        super(c);
        this.ctx = c;
        this.sp = c.getSharedPreferences("ttlx_stu", Context.MODE_PRIVATE);
        setOrientation(VERTICAL);

        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xE6141B2A);                 // 深色半透明，不遮死桌面
        bg.setCornerRadius(dp(12));
        bg.setStroke(dp(1), 0x66FFFFFF);
        setBackground(bg);
        setElevation(dp(8));

        // ---- 标题栏（拖动把手）----
        LinearLayout head = new LinearLayout(c);
        head.setOrientation(HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setPadding(dp(10), dp(6), dp(6), dp(6));

        TextView grip = new TextView(c);
        grip.setText("⠿");
        grip.setTextSize(15);
        grip.setTextColor(0xAAFFFFFF);
        grip.setPadding(0, 0, dp(6), 0);
        head.addView(grip);

        titleView = new TextView(c);
        titleView.setText(title);
        titleView.setTextSize(13);
        titleView.setTextColor(0xFFDCE6F7);
        titleView.setSingleLine(true);
        head.addView(titleView, new LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        foldBtn = new TextView(c);
        foldBtn.setText("－");
        foldBtn.setTextSize(15);
        foldBtn.setTextColor(0xFF8FA3C8);
        foldBtn.setGravity(Gravity.CENTER);
        foldBtn.setPadding(dp(10), dp(2), dp(10), dp(2));
        foldBtn.setOnClickListener(v -> setFolded(!folded));
        head.addView(foldBtn);

        addView(head, new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        // ---- 内容区 ----
        body = new LinearLayout(c);
        body.setOrientation(VERTICAL);
        body.setPadding(dp(10), 0, dp(10), dp(8));
        addView(body, new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        head.setOnTouchListener(new OnTouchListener() {
            @Override public boolean onTouch(View v, MotionEvent e) {
                return onHeadTouch(v, e);
            }
        });
        head.setOnClickListener(v -> { /* 拖动把手，不响应点击 */ });
    }

    // ================= 拖动 =================
    private boolean onHeadTouch(View v, MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN: {
                dragging = false;
                dragStartRawX = e.getRawX();
                dragStartRawY = e.getRawY();
                FrameLayout.LayoutParams lp = lp();
                dragStartLeft = lp == null ? 0 : lp.leftMargin;
                dragStartTop = lp == null ? 0 : lp.topMargin;
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                float dx = e.getRawX() - dragStartRawX;
                float dy = e.getRawY() - dragStartRawY;
                if (!dragging && (Math.abs(dx) > dp(6) || Math.abs(dy) > dp(6))) dragging = true;
                if (!dragging) return true;
                FrameLayout.LayoutParams lp = lp();
                if (lp == null) return true;
                View parent = (View) getParent();
                int pw = parent == null ? 0 : parent.getWidth();
                int ph = parent == null ? 0 : parent.getHeight();
                int w = getWidth(), h = getHeight();
                int left = clamp(dragStartLeft + (int) dx, -w / 2, Math.max(0, pw - w / 2));
                int top = clamp(dragStartTop + (int) dy, 0, Math.max(0, ph - h / 3));
                lp.leftMargin = left;
                lp.topMargin = top;
                setLayoutParams(lp);
                return true;
            }
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL: {
                if (dragging) savePos();
                dragging = false;
                return true;
            }
            default:
                return true;
        }
    }

    private static int clamp(int v, int lo, int hi) {
        if (hi < lo) hi = lo;
        return v < lo ? lo : (v > hi ? hi : v);
    }

    private FrameLayout.LayoutParams lp() {
        ViewGroup.LayoutParams p = getLayoutParams();
        return (p instanceof FrameLayout.LayoutParams) ? (FrameLayout.LayoutParams) p : null;
    }

    // ================= 折叠 =================
    public void setFolded(boolean f) {
        folded = f;
        body.setVisibility(f ? GONE : VISIBLE);
        foldBtn.setText(f ? "＋" : "－");
        savePos();
    }

    public boolean isFolded() { return folded; }

    public void setTitle(String t) { titleView.setText(t); }

    /** 内容区（往里 addView） */
    public LinearLayout body() { return body; }

    public void setBody(View v) {
        body.removeAllViews();
        body.addView(v, new LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
    }

    // ================= 位置持久化 =================
    /** 放到左上角下方一点（避开系统状态栏/刘海） */
    public void placeTopLeft(int marginLeftDp, int marginTopDp) {
        FrameLayout.LayoutParams lp = lp();
        if (lp == null) return;
        lp.leftMargin = dp(marginLeftDp);
        lp.topMargin = dp(marginTopDp);
        setLayoutParams(lp);
    }

    public void useKey(String key) { this.posKey = key; }

    /** 恢复上次拖动到的位置与折叠状态（没存过则用 defaultFolded） */
    public void restore(boolean defaultFolded) {
        if (posKey == null) return;
        FrameLayout.LayoutParams lp = lp();
        if (lp == null) return;
        lp.leftMargin = sp.getInt(posKey + "_x", lp.leftMargin);
        lp.topMargin = sp.getInt(posKey + "_y", lp.topMargin);
        setLayoutParams(lp);
        setFolded(sp.getBoolean(posKey + "_fold", defaultFolded));
    }

    public void savePos() {
        if (posKey == null) return;
        FrameLayout.LayoutParams lp = lp();
        if (lp == null) return;
        sp.edit().putInt(posKey + "_x", lp.leftMargin)
                 .putInt(posKey + "_y", lp.topMargin)
                 .putBoolean(posKey + "_fold", folded)
                 .apply();
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                ctx.getResources().getDisplayMetrics());
    }

    /** 小圆角按钮样式（给外部用） */
    public static GradientDrawable chip(int color, int radiusDp, Context c) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP,
                radiusDp, c.getResources().getDisplayMetrics()));
        return d;
    }

    public static int TEXT_LIGHT = Color.parseColor("#DCE6F7");
}
