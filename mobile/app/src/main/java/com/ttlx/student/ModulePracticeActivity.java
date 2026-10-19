package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import android.view.View;
import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 按模块练习（独立界面）：
 * 每行 = [勾选框] [模块名] [题量输入框]，勾选哪几个模块、各抽几题自己定。
 */
public class ModulePracticeActivity extends AppCompatActivity {

    private static final String[] MODULES = {
            "1 信息与数据", "2 进制编码", "3 操作系统", "4 信息安全",
            "5 计算机硬件", "6 软件程序", "7 多媒体", "8 计算机网络"};

    private final CheckBox[] boxes = new CheckBox[MODULES.length];
    private final EditText[] counts = new EditText[MODULES.length];

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
        setContentView(buildUi());
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(getResources().getColor(R.color.md_surface));

        // 顶栏
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(14), dp(12), dp(10), dp(10));
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
        t.setText("  按模块练习");
        t.setTextSize(20);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(getResources().getColor(R.color.md_on_surface));
        top.addView(t);
        root.addView(top);

        TextView tip = new TextView(this);
        tip.setText("勾选模块，并在最右侧填入该模块要抽的题量");
        tip.setTextSize(12);
        tip.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        tip.setPadding(dp(20), dp(2), dp(16), dp(6));
        root.addView(tip);

        // 模块行
        ScrollView sv = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(14), dp(4), dp(14), dp(8));
        sv.addView(box);
        for (int i = 0; i < MODULES.length; i++) {
            final int idx = i;
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(8), dp(6), dp(8), dp(6));

            CheckBox cb = new CheckBox(this);
            cb.setChecked(i == 0);
            cb.setOnCheckedChangeListener((b, c) -> {
                counts[idx].setEnabled(c);
                if (c) counts[idx].setText(String.valueOf(10));
            });
            row.addView(cb, new LinearLayout.LayoutParams(dp(40), dp(40)));

            TextView name = new TextView(this);
            name.setText(MODULES[i]);
            name.setTextSize(15);
            name.setTextColor(getResources().getColor(R.color.md_on_surface));
            LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            nlp.leftMargin = dp(4);
            row.addView(name, nlp);

            EditText et = new EditText(this);
            et.setInputType(InputType.TYPE_CLASS_NUMBER);
            et.setText("10");
            et.setTextSize(14);
            et.setGravity(Gravity.CENTER);
            et.setSingleLine(true);
            et.setBackground(new android.graphics.drawable.GradientDrawable() {{
                setCornerRadius(dp(10));
                setColor(Color.WHITE);
                setStroke(dp(1), getResources().getColor(R.color.md_outline));
            }});
            LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(dp(72), dp(42));
            elp.leftMargin = dp(6);
            et.setLayoutParams(elp);
            row.addView(et);
            counts[i] = et;
            box.addView(row);
        }
        root.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 底部开始按钮
        MaterialButton start = new MaterialButton(this);
        start.setText("开始抽题");
        start.setTextSize(17);
        start.setAllCaps(false);
        start.setCornerRadius(dp(26));
        start.setTextColor(Color.WHITE);
        start.setBackgroundColor(getResources().getColor(R.color.md_primary));
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56));
        slp.setMargins(dp(16), dp(10), dp(16), dp(20));
        start.setLayoutParams(slp);
        start.setOnClickListener(v -> doStart());
        root.addView(start);
        return root;
    }

    private void doStart() {
        NetClient net = TTLX.net();
        if (net == null) { finish(); return; }
        JSONArray parts = new JSONArray();
        int total = 0;
        try {
            for (int i = 0; i < boxes.length; i++) {
                if (!boxes[i].isChecked()) continue;
                int cnt = 10;
                try {
                    cnt = Integer.parseInt(counts[i].getText().toString().trim());
                } catch (Exception ignore) { }
                if (cnt <= 0) cnt = 10;
                if (cnt > 60) cnt = 60;
                JSONObject p = new JSONObject();
                p.put("type", "obj");
                p.put("course", i + 1);
                p.put("count", cnt);
                parts.put(p);
                total += cnt;
            }
        } catch (Exception ignore) { }
        if (parts.length() == 0) {
            Toast.makeText(this, "请至少勾选一个模块", Toast.LENGTH_SHORT).show();
            return;
        }
        final JSONArray fparts = parts;
        waitDlg("正在抽题（共 " + total + " 题），请稍等…");
        new Thread(() -> {
            String err = null;
            JSONObject r = null;
            try {
                r = net.startPractice(fparts);
                if (r.optInt("code") != 0) err = r.optString("msg", "抽题失败");
            } catch (Exception e) {
                err = e.getMessage();
                AppLog.e("practice", "模块抽题失败: " + e);
            }
            final String ferr = err;
            final JSONObject fr = r;
            runOnUiThread(() -> {
                dismissWait();
                if (ferr != null) {
                    new com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
                            .setTitle("抽题失败").setMessage(ferr)
                            .setPositiveButton("好", null).show();
                    return;
                }
                JSONArray qs = fr.optJSONArray("questions");
                if (qs == null || qs.length() == 0) {
                    Toast.makeText(this, "没有抽到可练习的题目", Toast.LENGTH_SHORT).show();
                    return;
                }
                Intent it = new Intent(this, ExamActivity.class);
                it.putExtra("recordId", 0L);
                it.putExtra("title", "模块练习");
                it.putExtra("questions", qs.toString());
                it.putExtra("practice", true);
                it.putExtra("duration", 0);
                startActivity(it);
                finish();
            });
        }, "ttlx-mp").start();
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
