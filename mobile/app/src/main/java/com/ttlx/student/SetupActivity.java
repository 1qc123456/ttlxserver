package com.ttlx.student;

import androidx.appcompat.app.AppCompatActivity;
import com.google.android.material.button.MaterialButton;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * 环境进度页（观察者）：
 * 配置在 SetupRunner 后台线程里跑（登录后自动开始），本页只负责显示进度与日志，
 * 随时可以返回继续做选择题，环境会继续在后台准备。
 */
public class SetupActivity extends AppCompatActivity {

    private TextView status, logView;
    private ProgressBar bar;
    private MaterialButton backBtn;
    private ScrollView logScroll;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private boolean polling = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(buildUi());
        SetupRunner.startIfNeeded(this);
        startPoll();
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#1B2233"));
        root.setPadding(dp(20), dp(26), dp(20), dp(18));
        root.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView logo = new TextView(this);
        logo.setText("正在准备操作题环境");
        logo.setTextColor(Color.WHITE);
        logo.setTextSize(22);
        logo.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(logo);

        TextView sub = new TextView(this);
        sub.setText("自动下载并配置（约 10~20 分钟）· 可返回先做选择题，后台会继续准备");
        sub.setTextColor(Color.parseColor("#9FB3D9"));
        sub.setTextSize(12);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subLp.topMargin = dp(8);
        root.addView(sub, subLp);

        status = new TextView(this);
        status.setText("准备中…");
        status.setTextColor(Color.parseColor("#7FB2FF"));
        status.setTextSize(14);
        LinearLayout.LayoutParams stLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        stLp.topMargin = dp(18);
        root.addView(status, stLp);

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        LinearLayout.LayoutParams pbLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(10));
        pbLp.topMargin = dp(10);
        root.addView(bar, pbLp);

        logScroll = new ScrollView(this);
        logView = new TextView(this);
        logView.setTextColor(Color.parseColor("#B8C7E0"));
        logView.setTextSize(10);
        logScroll.addView(logView);
        LinearLayout.LayoutParams scLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        scLp.topMargin = dp(14);
        root.addView(logScroll, scLp);

        LinearLayout btns = new LinearLayout(this);
        btns.setOrientation(LinearLayout.HORIZONTAL);
        backBtn = new MaterialButton(this);
        backBtn.setCornerRadius(dp(24));
        backBtn.setText("返回（后台继续）");
        backBtn.setTextColor(Color.WHITE);
        backBtn.setBackgroundColor(Color.parseColor("#4F86F8"));
        backBtn.setAllCaps(false);
        backBtn.setOnClickListener(v -> {
            polling = false;
            finish();
        });
        btns.addView(backBtn, new LinearLayout.LayoutParams(0, dp(50), 2f));
        MaterialButton retry2 = new MaterialButton(this);
        retry2.setCornerRadius(dp(24));
        retry2.setText("重 试");
        retry2.setAllCaps(false);
        retry2.setBackgroundColor(Color.parseColor("#8A5A00"));
        retry2.setTextColor(Color.WHITE);
        retry2.setOnClickListener(v -> {
            // 强制重跑：清掉就绪标记再配置（组件缺失时标记是脏的，必须重来一遍）
            SetupRunner.restart(this);
        });
        LinearLayout.LayoutParams r2lp = new LinearLayout.LayoutParams(0, dp(50), 1f);
        r2lp.leftMargin = dp(10);
        btns.addView(retry2, r2lp);
        MaterialButton shareBtn = new MaterialButton(this);
        shareBtn.setCornerRadius(dp(24));
        shareBtn.setText("分享日志");
        shareBtn.setAllCaps(false);
        shareBtn.setBackgroundColor(Color.parseColor("#3A4A6B"));
        shareBtn.setTextColor(Color.WHITE);
        shareBtn.setOnClickListener(v -> LogShare.share(this));
        LinearLayout.LayoutParams sLp2 = new LinearLayout.LayoutParams(0, dp(50), 1f);
        sLp2.leftMargin = dp(10);
        btns.addView(shareBtn, sLp2);
        LinearLayout.LayoutParams rLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rLp.topMargin = dp(10);
        root.addView(btns, rLp);
        return root;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void startPoll() {
        polling = true;
        ui.postDelayed(new Runnable() {
            @Override public void run() {
                if (!polling) return;
                status.setText(SetupRunner.status);
                int p = SetupRunner.progress;
                if (p < 0) {
                    bar.setIndeterminate(true);
                } else {
                    bar.setIndeterminate(false);
                    bar.setProgress(p);
                }
                logView.setText(SetupRunner.logTail(30000));
                logScroll.fullScroll(View.FOCUS_DOWN);
                if (SetupRunner.done) {
                    backBtn.setText("完成，返回");
                    ui.postDelayed(() -> {
                        if (polling) { polling = false; finish(); }
                    }, 1200);
                    return;
                }
                ui.postDelayed(this, 700);
            }
        }, 300);
    }

    @Override
    protected void onDestroy() {
        polling = false;
        super.onDestroy();
    }
}
