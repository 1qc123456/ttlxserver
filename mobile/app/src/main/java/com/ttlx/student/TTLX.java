package com.ttlx.student;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;

import java.util.ArrayList;
import java.util.List;

/** 全局单例：网络客户端 + 本地配置（记住的服务器地址 / 节点列表） */
public class TTLX {

    private static NetClient net;

    public static synchronized NetClient net() { return net; }

    public static synchronized void setNet(NetClient n) { net = n; }

    public static synchronized void logout() {
        if (net != null) net.close();
        net = null;
    }

    private static SharedPreferences sp(Context c) {
        return c.getApplicationContext().getSharedPreferences("ttlx_stu", Context.MODE_PRIVATE);
    }

    public static String lastHost(Context c) {
        return sp(c).getString("last_host", "");
    }

    public static void setLastHost(Context c, String h) {
        sp(c).edit().putString("last_host", h).apply();
    }

    public static List<String> nodes(Context c) {
        List<String> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(sp(c).getString("nodes", "[]"));
            for (int i = 0; i < a.length(); i++) {
                String s = a.optString(i, "").trim();
                if (!s.isEmpty() && !out.contains(s)) out.add(s);
            }
        } catch (Exception ignore) { }
        return out;
    }

    public static void setNodes(Context c, JSONArray arr) {
        try {
            List<String> out = new ArrayList<>();
            for (int i = 0; i < arr.length(); i++) {
                String s = arr.optString(i, "").trim();
                if (!s.isEmpty() && !out.contains(s)) out.add(s);
            }
            sp(c).edit().putString("nodes", new JSONArray(out).toString()).apply();
        } catch (Exception ignore) { }
    }
}
