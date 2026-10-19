package com.ttlx.student;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.google.android.material.dialog.MaterialAlertDialogBuilder;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 题目解析弹窗：题干 + 选项（正确答案绿框 / 我的错选红框）+ 我的答案 + 正确答案 + 解析。
 * 数据来源：错题本条目 / 历史成绩的 recordDetail 明细项。
 */
public class QuestionDetailDialog {

    public static void show(Activity act, JSONObject q) {
        boolean op = "operate".equals(q.optString("refType"))
                || q.optJSONArray("options") == null
                || q.optJSONArray("options").length() == 0;

        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(act, 20), dp(act, 16), dp(act, 20), dp(act, 6));

        TextView stem = new TextView(act);
        stem.setText(q.optString("stem", "(无题干)"));
        stem.setTextSize(15);
        stem.setTextColor(act.getResources().getColor(R.color.md_on_surface));
        stem.setLineSpacing(0, 1.15f);
        box.addView(stem);

        if (op) {
            TextView t = new TextView(act);
            t.setText("\n（操作题：作答文件已上传，按判分点给分）");
            t.setTextSize(13);
            t.setTextColor(act.getResources().getColor(R.color.md_on_surface_variant));
            box.addView(t);
        } else {
            JSONArray opts = q.optJSONArray("options");
            String my = q.optString("my_answer", q.optString("your", ""));
            String right = q.optString("right_answer", "");
            if (opts != null) {
                box.addView(sectionLabel(act, "选项"));
                for (int i = 0; i < opts.length(); i++) {
                    JSONObject o = opts.optJSONObject(i);
                    String k = o.optString("k", String.valueOf((char) ('A' + i)));
                    String t = o.optString("t", "");
                    if (t.isEmpty()) continue;
                    boolean isRight = k.equalsIgnoreCase(right.trim());
                    boolean isMy = k.equalsIgnoreCase(my.trim());
                    int bg = Color.WHITE, fg = R.color.md_on_surface, border = R.color.md_outline;
                    String suffix = "";
                    if (isRight) { bg = 0xFFDDF0DE; fg = R.color.md_success; border = R.color.md_success; suffix = "  ✔ 正确答案"; }
                    else if (isMy && !isRight) { bg = 0xFFFFE1E0; fg = R.color.md_error; border = R.color.md_error; suffix = "  ✘ 我的错选"; }
                    else if (isMy) { suffix = "  ✓ 我的答案"; }

                    LinearLayout row = new LinearLayout(act);
                    row.setGravity(Gravity.CENTER_VERTICAL);
                    row.setPadding(dp(act, 10), dp(act, 8), dp(act, 10), dp(act, 8));
                    LinearLayout.LayoutParams rlp = new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                    rlp.topMargin = dp(act, 6);
                    row.setLayoutParams(rlp);
                    row.setBackgroundColor(bg);

                    TextView kk = new TextView(act);
                    kk.setText(k + ".");
                    kk.setTextSize(14);
                    kk.setTypeface(Typeface.DEFAULT_BOLD);
                    kk.setTextColor(act.getResources().getColor(fg));
                    row.addView(kk);

                    TextView tt = new TextView(act);
                    tt.setText(" " + t + suffix);
                    tt.setTextSize(14);
                    tt.setTextColor(act.getResources().getColor(fg));
                    row.addView(tt);
                    box.addView(row);
                }
            }

            box.addView(sectionLabel(act, "答案"));
            box.addView(kv(act, "我的答案", my.isEmpty() ? "(未答)" : my,
                    my.equalsIgnoreCase(right.trim()) ? R.color.md_success : R.color.md_error));
            box.addView(kv(act, "正确答案", right, R.color.md_success));
        }

        // 解析（服务端 resolutionTips，若有）
        String tip = q.optString("tip", "");
        if (!tip.isEmpty()) {
            box.addView(sectionLabel(act, "解析"));
            TextView tp = new TextView(act);
            tp.setText(tip);
            tp.setTextSize(13);
            tp.setTextColor(act.getResources().getColor(R.color.md_on_surface_variant));
            tp.setLineSpacing(0, 1.1f);
            box.addView(tp);
        } else {
            TextView tp = new TextView(act);
            tp.setText("\n解析：暂无");
            tp.setTextSize(13);
            tp.setTextColor(act.getResources().getColor(R.color.md_on_surface_variant));
            box.addView(tp);
        }

        ScrollView sv = new ScrollView(act);
        sv.addView(box, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        new MaterialAlertDialogBuilder(act)
                .setTitle("解析")
                .setView(sv)
                .setPositiveButton("关闭", null)
                .show();
    }

    private static TextView sectionLabel(Activity act, String s) {
        TextView t = new TextView(act);
        t.setText(s);
        t.setTextSize(13);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(act.getResources().getColor(R.color.md_primary));
        t.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(act, 30));
        lp.topMargin = dp(act, 10);
        t.setLayoutParams(lp);
        return t;
    }

    private static TextView kv(Activity act, String k, String v, int colorRes) {
        TextView t = new TextView(act);
        t.setText(k + "：" + v);
        t.setTextSize(14);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(act.getResources().getColor(colorRes));
        t.setPadding(0, dp(act, 3), 0, 0);
        return t;
    }

    private static int dp(Activity a, int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                a.getResources().getDisplayMetrics());
    }
}
