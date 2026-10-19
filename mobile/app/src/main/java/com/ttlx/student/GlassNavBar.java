package com.ttlx.student;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewTreeObserver;
import android.widget.FrameLayout;

/**
 * 毛玻璃底栏（透明玻璃效果）
 *
 * 实现思路与 GitHub 上开源方案一致（Dimezis/BlurView、RealtimeBlurView 等）：
 *   ① 把「栏后面的内容」绘制到一张降采样小位图上（1/6 尺寸，开销极小）
 *   ② 对小位图做两轮盒式模糊（分离式行列模糊，近似高斯）
 *   ③ 绘制时把小图放大铺满 + 半透明白色渐变叠加 + 顶部发丝线 → 磨砂玻璃观感
 * 优点：不依赖 RenderScript / RenderEffect（新旧机型一致），不引入外部依赖。
 *
 * 用法：
 *   GlassNavBar glass = new GlassNavBar(ctx);
 *   glass.addView(你的底部导航内容);
 *   glass.attachBackdrop(被覆盖的内容视图);     // 通常是上方的主内容容器
 *   glass.markDirty();                          // 列表刷新后手动标记
 */
public class GlassNavBar extends FrameLayout {

    private View backdrop;
    private Bitmap small;
    private int downscale = 6;          // 降采样倍率（越大越省、越糊）
    private int blurRadius = 5;         // 小图上的模糊半径
    private long lastCapture = 0;
    private boolean dirty = true;
    private boolean polling = false;

    private final Paint bmpPaint = new Paint(Paint.FILTER_BITMAP_FLAG);
    private final Paint overlayPaint = new Paint();
    private final Paint borderPaint = new Paint();
    private final Rect srcRect = new Rect();

    public GlassNavBar(Context c) {
        super(c);
        setWillNotDraw(false);
        borderPaint.setColor(0x14000000);                      // 顶部发丝线（8% 黑）
        overlayPaint.setShader(new LinearGradient(0, 0, 0, dp(80),
                new int[]{0x9EFFFFFF, 0xEAFFFFFF, 0xF7FFFFFF},
                new float[]{0f, 0.45f, 1f}, Shader.TileMode.CLAMP));
        setElevation(dp(10));
        setClipToPadding(false);
    }

    private float dp(float v) {
        return v * getResources().getDisplayMetrics().density;
    }

    /** 兜底轮询：内容变化（列表刷新等）也能跟上。⚠ 必须在 detach 时停掉，
     *  否则 Runnable 会一直自我重投递 → View 泄漏 + 后台耗电。 */
    private final Runnable pollTick = new Runnable() {
        @Override public void run() {
            if (backdrop != null && isShown()) markDirty();
            if (isAttachedToWindow()) postDelayed(this, 900);
        }
    };

    /** 指定被覆盖的内容视图（模糊的就是它） */
    public void attachBackdrop(final View content) {
        backdrop = content;
        dirty = true;
        if (content != null) {
            // 滚动时实时刷新
            content.getViewTreeObserver().addOnScrollChangedListener(
                    () -> markDirty());
        }
        if (!polling) {
            polling = true;
            postDelayed(pollTick, 900);
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        removeCallbacks(pollTick);
        polling = false;
        super.onDetachedFromWindow();
    }

    public void markDirty() {
        dirty = true;
        invalidate();
    }

    // ================= 绘制 =================
    @Override
    protected void dispatchDraw(Canvas canvas) {
        int w = getWidth(), h = getHeight();
        if (w <= 0 || h <= 0) {
            super.dispatchDraw(canvas);
            return;
        }
        long now = SystemClock.uptimeMillis();
        if (dirty && backdrop != null && (now - lastCapture >= 90)) {
            capture();
            lastCapture = now;
            dirty = false;
        }
        if (small != null && !small.isRecycled()) {
            srcRect.set(0, 0, small.getWidth(), small.getHeight());
            canvas.drawBitmap(small, srcRect, new Rect(0, 0, w, h), bmpPaint);
        } else {
            canvas.drawColor(0xEAF7F8FC);                 // 兜底：近白半透明
        }
        canvas.drawRect(0, 0, w, h, overlayPaint);        // 白色渐变叠加 → 玻璃
        canvas.drawRect(0, 0, w, Math.max(1f, dp(0.7f)), borderPaint);  // 顶部发丝线
        super.dispatchDraw(canvas);
    }

    /** 抓取「栏后内容」→ 降采样 → 盒式模糊 */
    private void capture() {
        try {
            int w = getWidth(), h = getHeight();
            int sw = Math.max(8, w / downscale);
            int sh = Math.max(6, h / downscale);
            if (small == null || small.getWidth() != sw || small.getHeight() != sh) {
                if (small != null) small.recycle();
                small = Bitmap.createBitmap(sw, sh, Bitmap.Config.ARGB_8888);
            }
            int[] barLoc = new int[2]; getLocationInWindow(barLoc);
            int[] srcLoc = new int[2]; backdrop.getLocationInWindow(srcLoc);

            Canvas c = new Canvas(small);
            c.drawColor(0xFFF7F8FC);                                  // 底色，避免透明边
            c.scale(1f / downscale, 1f / downscale);
            c.translate(srcLoc[0] - barLoc[0], srcLoc[1] - barLoc[1]);
            backdrop.draw(c);

            boxBlur(small, blurRadius);
            boxBlur(small, Math.max(1, blurRadius - 2));              // 两轮更细腻
        } catch (Throwable ignore) {
            // 捕获失败时保持上一帧/兜底色，不影响功能
        }
    }

    // ================= 盒式模糊（分离式） =================
    private static void boxBlur(Bitmap bmp, int r) {
        int w = bmp.getWidth(), h = bmp.getHeight();
        if (w < 3 || h < 3 || r <= 0) return;
        int[] pix = new int[w * h];
        bmp.getPixels(pix, 0, w, 0, 0, w, h);
        int[] tmp = new int[w * h];
        for (int y = 0; y < h; y++) blurRow(pix, tmp, y * w, w, r);
        for (int x = 0; x < w; x++) blurCol(tmp, pix, x, w, h, r);
        bmp.setPixels(pix, 0, w, 0, 0, w, h);
    }

    private static void blurRow(int[] in, int[] out, int off, int len, int r) {
        int div = 2 * r + 1;
        int a = 0, rr = 0, gg = 0, bb = 0;
        for (int i = -r; i <= r; i++) {
            int c = in[off + clamp(i, 0, len - 1)];
            a += (c >>> 24) & 0xff; rr += (c >>> 16) & 0xff;
            gg += (c >>> 8) & 0xff; bb += c & 0xff;
        }
        for (int i = 0; i < len; i++) {
            out[off + i] = ((a / div) << 24) | ((rr / div) << 16) | ((gg / div) << 8) | (bb / div);
            int cOut = in[off + clamp(i - r, 0, len - 1)];
            int cIn = in[off + clamp(i + r + 1, 0, len - 1)];
            a += ((cIn >>> 24) & 0xff) - ((cOut >>> 24) & 0xff);
            rr += ((cIn >>> 16) & 0xff) - ((cOut >>> 16) & 0xff);
            gg += ((cIn >>> 8) & 0xff) - ((cOut >>> 8) & 0xff);
            bb += (cIn & 0xff) - (cOut & 0xff);
        }
    }

    private static void blurCol(int[] in, int[] out, int x, int w, int h, int r) {
        int div = 2 * r + 1;
        int a = 0, rr = 0, gg = 0, bb = 0;
        for (int i = -r; i <= r; i++) {
            int c = in[clamp(i, 0, h - 1) * w + x];
            a += (c >>> 24) & 0xff; rr += (c >>> 16) & 0xff;
            gg += (c >>> 8) & 0xff; bb += c & 0xff;
        }
        for (int i = 0; i < h; i++) {
            out[i * w + x] = ((a / div) << 24) | ((rr / div) << 16) | ((gg / div) << 8) | (bb / div);
            int cOut = in[clamp(i - r, 0, h - 1) * w + x];
            int cIn = in[clamp(i + r + 1, 0, h - 1) * w + x];
            a += ((cIn >>> 24) & 0xff) - ((cOut >>> 24) & 0xff);
            rr += ((cIn >>> 16) & 0xff) - ((cOut >>> 16) & 0xff);
            gg += ((cIn >>> 8) & 0xff) - ((cOut >>> 8) & 0xff);
            bb += (cIn & 0xff) - (cOut & 0xff);
        }
    }

    private static int clamp(int v, int lo, int hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }
}
