package com.ttlx.student;

import android.app.Activity;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import android.graphics.Color;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** 成绩 / 答题明细弹窗（原生） */
public class ResultDialog {

    public static void show(Activity act, JSONArray detail, String head) {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(act, 18), dp(act, 14), dp(act, 18), dp(act, 8));

        double sc = 0, tot = 0;
        if (detail != null) {
            for (int i = 0; i < detail.length(); i++) {
                JSONObject d = detail.optJSONObject(i);
                sc += d.optDouble("score", 0);
                tot += d.optDouble("fullScore", 0);
            }
        }
        String title = head != null ? head : ("总分：" + round1(sc) + " / " + round1(tot));
        TextView hl = new TextView(act);
        hl.setText(title);
        hl.setTextSize(18);
        hl.setTypeface(Typeface.DEFAULT_BOLD);
        hl.setTextColor(Color.parseColor("#2D5FE8"));
        box.addView(hl);

        ScrollView sv = new ScrollView(act);
        LinearLayout list = new LinearLayout(act);
        list.setOrientation(LinearLayout.VERTICAL);
        if (detail == null || detail.length() == 0) {
            TextView t = new TextView(act);
            t.setText("暂无明细");
            t.setTextSize(14);
            t.setTextColor(Color.parseColor("#718096"));
            list.addView(t);
        } else {
            for (int i = 0; i < detail.length(); i++) {
                JSONObject d = detail.optJSONObject(i);
                boolean op = "operate".equals(d.optString("refType"));
                String stem = d.optString("stem", "").replace("\n", " ");
                if (stem.length() > 60) stem = stem.substring(0, 60) + "…";
                String line;
                if (op) {
                    Object sc2 = d.opt("score");
                    boolean pend = d.optBoolean("pending", false);
                    line = (i + 1) + ".【操作】" + stem + "  "
                            + (pend ? "判分中" : (sc2 == null || sc2 == JSONObject.NULL ? "-" : sc2 + "分"))
                            + (d.optString("note", "").isEmpty() ? "" : "\n      " + d.optString("note"));
                } else {
                    boolean ok = d.optBoolean("correct", false);
                    line = (i + 1) + "." + (ok ? "√" : "×") + " " + stem
                            + "\n      我:" + d.optString("your", "") + "  对:" + d.optString("right", "")
                            + "  " + d.opt("score") + "分";
                }
                TextView t = new TextView(act);
                t.setText(line);
                t.setTextSize(13);
                t.setTextColor(Color.parseColor("#2D3748"));
                t.setPadding(0, dp(act, 7), 0, dp(act, 7));
                list.addView(t);
                ViewGroup.LayoutParams lp = t.getLayoutParams();
                if (lp instanceof LinearLayout.LayoutParams) {
                    ((LinearLayout.LayoutParams) lp).width = ViewGroup.LayoutParams.MATCH_PARENT;
                }
            }
        }
        sv.addView(list);
        // 横屏手机上屏幕很矮：列表高度不能写死 380dp，否则弹窗会被裁掉
        android.util.DisplayMetrics dm = act.getResources().getDisplayMetrics();
        int maxH = (int) (Math.min(dm.widthPixels, dm.heightPixels) * 0.55f);
        box.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Math.min(dp(act, 380), maxH)));

        new MaterialAlertDialogBuilder(act)
                .setView(box)
                .setPositiveButton("关闭", null)
                .show();
    }

    private static double round1(double v) {
        return Math.round(v * 10) / 10.0;
    }

    private static int dp(Activity a, int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                a.getResources().getDisplayMetrics());
    }
}
