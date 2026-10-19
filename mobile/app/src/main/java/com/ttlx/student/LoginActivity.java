package com.ttlx.student;

import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.card.MaterialCardView;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import com.google.android.material.textfield.TextInputEditText;
import com.google.android.material.textfield.TextInputLayout;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/** 登录页（Material 3 风格） */
public class LoginActivity extends AppCompatActivity {

    private TextInputEditText hostEdit, userEdit, pwdEdit;
    private MaterialButton loginBtn;
    private TextView tip;
    private volatile boolean busy = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
        String last = TTLX.lastHost(this);
        hostEdit.setText(last.isEmpty() ? "127.0.0.1" : last);
        if (getSupportActionBar() != null) getSupportActionBar().hide();
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(getResources().getColor(R.color.md_surface));
        root.setGravity(Gravity.CENTER_HORIZONTAL);

        android.widget.ScrollView sv = new android.widget.ScrollView(this);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setGravity(Gravity.CENTER_HORIZONTAL);
        col.setPadding(dp(24), dp(48), dp(24), dp(32));
        sv.addView(col);
        root.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        ImageView logo = new ImageView(this);
        logo.setImageResource(R.drawable.stu);
        logo.setClipToOutline(true);
        LinearLayout.LayoutParams lgLp = new LinearLayout.LayoutParams(dp(96), dp(96));
        col.addView(logo, lgLp);

        TextView title = new TextView(this);
        title.setText("学生端");
        title.setTextSize(28);
        title.setTextColor(getResources().getColor(R.color.md_on_surface));
        title.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        LinearLayout.LayoutParams tLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tLp.topMargin = dp(16);
        col.addView(title, tLp);

        TextView sub = new TextView(this);
        sub.setText("考试系统 · 手机版");
        sub.setTextSize(14);
        sub.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        LinearLayout.LayoutParams sLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sLp.topMargin = dp(6);
        sLp.bottomMargin = dp(24);
        col.addView(sub, sLp);

        // 输入卡片
        MaterialCardView card = new MaterialCardView(this);
        card.setRadius(dp(24));
        card.setCardElevation(dp(1));
        card.setCardBackgroundColor(Color.WHITE);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(16), dp(18), dp(16), dp(18));
        card.addView(form);
        LinearLayout.LayoutParams cLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        col.addView(card, cLp);

        hostEdit = field(form, "服务器地址", "可带端口，默认 5943", false);
        userEdit = field(form, "学号 / 账号", "请输入账号", false);
        pwdEdit = field(form, "密码", "请输入密码", true);

        tip = new TextView(this);
        tip.setTextSize(12);
        tip.setTextColor(getResources().getColor(R.color.md_primary));
        tip.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tpLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        tpLp.topMargin = dp(8);
        form.addView(tip, tpLp);

        loginBtn = new MaterialButton(this);
        loginBtn.setText("登 录");
        loginBtn.setTextSize(17);
        loginBtn.setCornerRadius(dp(26));
        loginBtn.setAllCaps(false);
        loginBtn.setBackgroundColor(getResources().getColor(R.color.md_primary));
        loginBtn.setTextColor(Color.WHITE);
        loginBtn.setOnClickListener(v -> doLogin());
        LinearLayout.LayoutParams bLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(54));
        bLp.topMargin = dp(14);
        form.addView(loginBtn, bLp);

        TextView note = new TextView(this);
        note.setText("首次连接远程服务器约需 15~40 秒（内网穿透首帧较慢），请耐心等待");
        note.setTextSize(11);
        note.setTextColor(getResources().getColor(R.color.md_on_surface_variant));
        note.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams nLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        nLp.topMargin = dp(20);
        col.addView(note, nLp);

        return root;
    }

    private TextInputEditText field(LinearLayout parent, String label, String hint, boolean pwd) {
        TextInputLayout wrap = new TextInputLayout(this);
        wrap.setHint(label);
        // ⚠ 坑：TextInputLayout 的浮动标签（label）在空值时是「占位符」形态，
        //   如果再给内部 EditText 设 hint，两段文字会叠在同一位置（界面上出现重影）。
        //   正确做法：占位提示交给 TextInputLayout.setPlaceholderText()。
        wrap.setPlaceholderText(hint);
        wrap.setBoxCornerRadii(dp(16), dp(16), dp(16), dp(16));
        wrap.setBoxBackgroundMode(TextInputLayout.BOX_BACKGROUND_OUTLINE);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(10);
        parent.addView(wrap, lp);
        TextInputEditText e = new TextInputEditText(this);
        e.setSingleLine(true);
        e.setTextSize(16);
        if (pwd) e.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        wrap.addView(e);
        return e;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    // ---------------- 登录逻辑（与之前一致） ----------------
    private void doLogin() {
        if (busy) return;
        final String host = hostEdit.getText() == null ? "" : hostEdit.getText().toString().trim();
        final String user = userEdit.getText() == null ? "" : userEdit.getText().toString().trim();
        final String pwd = pwdEdit.getText() == null ? "" : pwdEdit.getText().toString();
        if (host.isEmpty() || user.isEmpty()) {
            Toast.makeText(this, "请填写服务器地址和账号", Toast.LENGTH_SHORT).show();
            return;
        }
        busy = true;
        loginBtn.setEnabled(false);
        loginBtn.setText("连接中…");
        tip.setText("正在连接 " + host + " …");
        TTLX.setLastHost(this, host);

        new Thread(() -> {
            NetClient net = new NetClient(host);
            String err = null;
            JSONObject r = null;
            try {
                net.connect();
                runOnUiThread(() -> tip.setText("已连接，正在登录…"));
                r = net.login(user, pwd);
            } catch (Exception e) {
                err = e.getMessage();
            }
            final String ferr = err;
            final JSONObject fr = r;
            final NetClient fnet = net;
            runOnUiThread(() -> {
                busy = false;
                loginBtn.setEnabled(true);
                loginBtn.setText("登 录");
                tip.setText("");
                if (ferr != null) {
                    showConnectError(host, ferr);
                    return;
                }
                if (fr == null || fr.optInt("code") != 0) {
                    new MaterialAlertDialogBuilder(this)
                            .setTitle("登录失败")
                            .setMessage(fr == null ? "未知错误" : fr.optString("msg", "未知错误"))
                            .setPositiveButton("好", null).show();
                    return;
                }
                if (!"student".equals(fr.optString("role"))) {
                    new MaterialAlertDialogBuilder(this)
                            .setTitle("提示").setMessage("该账号不是学生账号")
                            .setPositiveButton("好", null).show();
                    return;
                }
                TTLX.setNet(fnet);
                fetchNodes(fnet);
                startActivity(new Intent(this, MainActivity.class));
                finish();
            });
        }, "ttlx-login").start();
    }

    private void fetchNodes(NetClient net) {
        new Thread(() -> {
            try {
                JSONObject r = net.getNodes();
                JSONArray nodes = r.optJSONArray("nodes");
                if (nodes != null && nodes.length() > 0) TTLX.setNodes(this, nodes);
            } catch (Exception ignore) { }
        }, "ttlx-nodes").start();
    }

    private void showConnectError(String host, String err) {
        NetClient tmp = new NetClient(host);
        String msg = tmp.friendlyError(new Exception(err));
        List<String> nodes = TTLX.nodes(this);
        MaterialAlertDialogBuilder b = new MaterialAlertDialogBuilder(this)
                .setTitle("连接失败")
                .setMessage(msg)
                .setPositiveButton("重试", (d, w) -> doLogin())
                .setNegativeButton("关闭", null);
        if (!nodes.isEmpty()) {
            b.setNeutralButton("更换节点", (d, w) -> showNodePicker(nodes));
        }
        b.show();
    }

    private void showNodePicker(List<String> nodes) {
        String[] arr = nodes.toArray(new String[0]);
        new MaterialAlertDialogBuilder(this)
                .setTitle("选择节点")
                .setItems(arr, (d, which) -> {
                    hostEdit.setText(arr[which]);
                    doLogin();
                })
                .setNegativeButton("取消", null)
                .show();
    }
}
