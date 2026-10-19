package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import android.view.View;
import android.widget.Toast;
import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.card.MaterialCardView;

import org.json.JSONArray;
import org.json.JSONObject;

/** 历史成绩（长列表）：点一条 → 答题详情（题干/选项/我的答案/正确答案/得分） */
public class HistoryActivity extends AppCompatActivity {

    private LinearLayout listBox;
    private TextView hintView;
    private JSONArray rows = new JSONArray();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        setContentView(buildUi());
        load();
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(getResources().getColor(R.color.md_surface));

        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(16), dp(14), dp(10), dp(10));
        com.google.android.material.button.MaterialButton back =
                new com.google.android.material.button.MaterialButton(this);
        back.setText("←");
        back.setTextSize(16);
        back.setAllCaps(false);
        back.setBackgroundColor(Color.TRANSPARENT);
        back.setTextColor(getResources().getColor(R.color.md_primary));
        back.setOnClickListener(v -> finish());
        top.addView(back);
        TextView t = new TextView(this);
        t.setText("  历史成绩");
        t.setTextSize(20);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        top.addView(t);
        root.addView(top);

        hintView = new TextView(this);
        hintView.setText("正在加载…");
        hintView.setTextSize(13);
        hintView.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        hintView.setPadding(dp(20), dp(4), dp(16), dp(4));
        root.addView(hintView);

        ScrollView sv = new ScrollView(this);
        listBox = new LinearLayout(this);
        listBox.setOrientation(LinearLayout.VERTICAL);
        listBox.setPadding(dp(14), dp(4), dp(14), dp(24));
        sv.addView(listBox);
        root.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    private void load() {
        NetClient net = TTLX.net();
        if (net == null) { finish(); return; }
        new Thread(() -> {
            String err = null;
            JSONArray arr = null;
            try {
                JSONObject r = net.myHistory();
                if (r.optInt("code") != 0) err = r.optString("msg", "加载失败");
                else arr = r.optJSONArray("history");
            } catch (Exception e) {
                err = e.getMessage();
                AppLog.e("hist", "历史成绩加载失败: " + e);
            }
            final String ferr = err;
            final JSONArray fa = arr;
            runOnUiThread(() -> render(ferr, fa));
        }, "ttlx-hist").start();
    }

    private void render(String err, JSONArray arr) {
        if (err != null) {
            hintView.setText("加载失败：" + err);
            return;
        }
        rows = arr == null ? new JSONArray() : arr;
        if (rows.length() == 0) {
            hintView.setText("暂无历史记录");
            return;
        }
        hintView.setText("共 " + rows.length() + " 条记录，点击查看答题详情");
        for (int i = 0; i < rows.length(); i++) {
            JSONObject h = rows.optJSONObject(i);
            listBox.addView(recordCard(h));
        }
    }

    private View recordCard(JSONObject h) {
        MaterialCardView card = new MaterialCardView(this);
        card.setRadius(dp(18));
        card.setCardElevation(dp(1));
        card.setCardBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.bottomMargin = dp(10);
        card.setLayoutParams(clp);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(13), dp(16), dp(13));
        card.addView(box);

        TextView title = new TextView(this);
        title.setText(h.optString("title", ""));
        title.setTextSize(15);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(getResources().getColor(R.color.md_on_surface));
        box.addView(title);

        String kind = "exam".equals(h.optString("kind")) ? "正式考试" : "平时作业";
        Object score = h.opt("score");
        TextView meta = new TextView(this);
        meta.setText(kind + "   得分：" + (score == null ? "-" : score.toString())
                + "   " + h.optString("at", ""));
        meta.setTextSize(12);
        meta.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        LinearLayout.LayoutParams mlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        mlp.topMargin = dp(5);
        box.addView(meta, mlp);

        TextView go = new TextView(this);
        go.setText("查看答题详情 ›");
        go.setTextSize(12);
        go.setTextColor(getResources().getColor(R.color.md_primary));
        go.setGravity(Gravity.RIGHT);
        box.addView(go);

        card.setOnClickListener(v -> {
            long rid = h.optLong("id", 0);
            if (rid <= 0) return;
            waitDlg("正在加载答题详情…");
            new Thread(() -> {
                String err = null;
                JSONArray detail = null;
                try {
                    JSONObject r = TTLX.net().recordDetail(rid);
                    if (r.optInt("code") != 0) err = r.optString("msg", "加载失败");
                    else detail = r.optJSONArray("detail");
                } catch (Exception e) {
                    err = e.getMessage();
                    AppLog.e("hist", "详情加载失败: " + e);
                }
                final String ferr = err;
                final JSONArray fd = detail;
                runOnUiThread(() -> {
                    dismissWait();
                    if (ferr != null) {
                        Toast.makeText(this, ferr, Toast.LENGTH_SHORT).show();
                        return;
                    }
                    ResultDialog.show(this, fd == null ? new JSONArray() : fd, null);
                });
            }, "ttlx-hd").start();
        });
        return card;
    }

    private androidx.appcompat.app.AlertDialog waitDlg;

    private void waitDlg(String msg) {
        LinearLayout box = new LinearLayout(this);
        box.setPadding(dp(24), dp(20), dp(24), dp(20));
        TextView t = new TextView(this);
        t.setText(msg);
        t.setTextSize(15);
        box.addView(t);
        waitDlg = new com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
                .setView(box).setCancelable(false).create();
        waitDlg.show();
    }

    private void dismissWait() {
        if (waitDlg != null) {
            try { waitDlg.dismiss(); } catch (Exception ignore) { }
            waitDlg = null;
        }
    }
}
