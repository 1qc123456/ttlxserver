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
import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.card.MaterialCardView;

import org.json.JSONArray;
import org.json.JSONObject;

/** 错题本（长列表）：点一条 → 查看解析（题干 + 选项 + 我的答案 + 正确答案） */
public class WrongBookActivity extends AppCompatActivity {

    private LinearLayout listBox;
    private TextView hintView;

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
        com.google.android.material.button.MaterialButton back = topBtn();
        back.setOnClickListener(v -> finish());
        top.addView(back);
        TextView t = new TextView(this);
        t.setText("  错题本");
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

    private com.google.android.material.button.MaterialButton topBtn() {
        com.google.android.material.button.MaterialButton b =
                new com.google.android.material.button.MaterialButton(this);
        b.setText("←");
        b.setTextSize(16);
        b.setAllCaps(false);
        b.setBackgroundColor(Color.TRANSPARENT);
        b.setTextColor(getResources().getColor(R.color.md_primary));
        return b;
    }

    private void load() {
        NetClient net = TTLX.net();
        if (net == null) { finish(); return; }
        new Thread(() -> {
            String err = null;
            JSONArray arr = null;
            try {
                JSONObject r = net.wrongList();
                if (r.optInt("code") != 0) err = r.optString("msg", "加载失败");
                else arr = r.optJSONArray("wrong");
            } catch (Exception e) {
                err = e.getMessage();
                AppLog.e("wrong", "错题本加载失败: " + e);
            }
            final String ferr = err;
            final JSONArray fa = arr;
            runOnUiThread(() -> {
                if (ferr != null) {
                    hintView.setText("加载失败：" + ferr);
                    return;
                }
                int n = fa == null ? 0 : fa.length();
                if (n == 0) {
                    hintView.setText("暂无错题，继续保持！");
                    return;
                }
                hintView.setText("共 " + n + " 道错题，点击查看解析");
                for (int i = 0; i < n; i++) {
                    JSONObject w = fa.optJSONObject(i);
                    listBox.addView(itemCard(w, i + 1));
                }
            });
        }, "ttlx-wrong").start();
    }

    private View itemCard(JSONObject w, int no) {
        boolean op = "operate".equals(w.optString("refType"));
        String stem = w.optString("stem", "").replace("\n", " ");
        if (stem.length() > 90) stem = stem.substring(0, 90) + "…";

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

        TextView tag = new TextView(this);
        tag.setText(op ? "【操作题】" : ("【模块" + w.optInt("course", 0) + "】"));
        tag.setTextSize(11);
        tag.setTextColor(getResources().getColor(R.color.md_primary));
        box.addView(tag);

        TextView t = new TextView(this);
        t.setText((no) + ". " + stem);
        t.setTextSize(14);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tlp.topMargin = dp(3);
        box.addView(t, tlp);

        TextView ans = new TextView(this);
        if (op) {
            ans.setText("我的答案：(见上传文件)   正确答案：自动判分");
        } else {
            ans.setText("我的答案：" + w.optString("my_answer", "(未答)")
                    + "    正确答案：" + w.optString("right_answer", ""));
        }
        ans.setTextSize(12);
        ans.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        LinearLayout.LayoutParams alp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        alp.topMargin = dp(5);
        box.addView(ans, alp);

        card.setOnClickListener(v -> QuestionDetailDialog.show(this, w));
        return card;
    }
}
