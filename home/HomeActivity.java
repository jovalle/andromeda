package us.andromeda.home;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.database.ContentObserver;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Base64;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.util.HashMap;
import java.util.HashSet;
import org.json.JSONArray;
import org.json.JSONObject;

public final class HomeActivity extends Activity {
    private String rendered;
    private JSONObject configuration;
    private final ContentObserver observer = new ContentObserver(new Handler(Looper.getMainLooper())) {
        @Override public void onChange(boolean changed) { render(); }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getContentResolver().registerContentObserver(ConfigProvider.URI, false, observer);
        render();
    }
    @Override public void onResume() { super.onResume(); render(); }
    @Override public void onDestroy() { getContentResolver().unregisterContentObserver(observer); super.onDestroy(); }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    private LinearLayout column() {
        LinearLayout result = new LinearLayout(this);
        result.setOrientation(LinearLayout.VERTICAL);
        return result;
    }

    private void render() {
        String data = getSharedPreferences("home", 0).getString("configuration", "");
        if (data.equals(rendered)) return;
        try {
            if (data.isEmpty()) throw new IllegalStateException("Home has not been configured in Andromeda yet.");
            configuration = new JSONObject(new String(Base64.decode(data, Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
            LinearLayout root = column();
            root.setBackgroundColor(0x80000000);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(dp(12) + bars.left, dp(24) + bars.top, dp(12) + bars.right, dp(8) + bars.bottom);
                return insets;
            });
            ScrollView scroll = new ScrollView(this);
            LinearLayout rows = column();
            JSONArray entries = configuration.getJSONArray("rows");
            for (int i = 0; i < entries.length(); i++) rows.addView(row(entries.getJSONArray(i)));
            scroll.addView(rows);
            root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
            Button catalog = new Button(this, null, android.R.attr.borderlessButtonStyle);
            catalog.setText("All apps");
            catalog.setAllCaps(false);
            catalog.setOnClickListener(view -> showCatalog());
            LinearLayout.LayoutParams catalogParams = new LinearLayout.LayoutParams(dp(144), dp(48));
            catalogParams.gravity = Gravity.CENTER_HORIZONTAL;
            catalogParams.bottomMargin = dp(16);
            root.addView(catalog, catalogParams);
            root.addView(row(configuration.getJSONArray("dock")));
            setContentView(root);
            root.requestApplyInsets();
            rendered = data;
        } catch (Exception error) {
            TextView message = new TextView(this);
            message.setText("Home unavailable\n" + error.getMessage());
            message.setPadding(dp(24), dp(48), dp(24), dp(24));
            message.setBackgroundColor(Color.BLACK);
            message.setTextColor(Color.WHITE);
            setContentView(message);
        }
    }

    private LinearLayout row(JSONArray apps) throws Exception {
        LinearLayout result = new LinearLayout(this);
        result.setGravity(Gravity.TOP);
        for (int i = 0; i < 4; i++) {
            View item = i < apps.length() ? icon(apps.getJSONObject(i)) : new View(this);
            result.addView(item, new LinearLayout.LayoutParams(0, -2, 1));
        }
        return result;
    }

    private View icon(JSONObject app) throws Exception {
        String label = app.getString("label");
        ComponentName component = ComponentName.unflattenFromString(app.getString("component"));
        LinearLayout button = column();
        button.setGravity(Gravity.CENTER_HORIZONTAL);
        button.setPadding(dp(4), dp(8), dp(4), dp(16));
        button.setMinimumHeight(dp(112));
        button.setFocusable(true);
        button.setClickable(true);
        button.setContentDescription(label);
        button.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        TypedValue feedback = new TypedValue();
        getTheme().resolveAttribute(android.R.attr.selectableItemBackground, feedback, true);
        button.setBackgroundResource(feedback.resourceId);
        ImageView image = new ImageView(this);
        image.setImageDrawable(getPackageManager().getActivityIcon(component));
        image.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        button.addView(image, new LinearLayout.LayoutParams(dp(52), dp(52)));
        TextView text = new TextView(this);
        text.setText(label);
        text.setTextSize(13);
        text.setTextColor(Color.WHITE);
        text.setShadowLayer(dp(2), 0, dp(1), Color.BLACK);
        text.setGravity(Gravity.CENTER);
        text.setMinLines(2);
        text.setMaxLines(2);
        text.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(-1, -2);
        labelParams.topMargin = dp(8);
        button.addView(text, labelParams);
        button.setOnClickListener(view -> {
            try {
                startActivity(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                    .setComponent(component).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED));
            } catch (Exception error) { Toast.makeText(this, label + " is unavailable", Toast.LENGTH_LONG).show(); }
        });
        button.setOnLongClickListener(view -> {
            startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + component.getPackageName())));
            return true;
        });
        return button;
    }

    private void showCatalog() {
        try {
            HashSet<String> hidden = new HashSet<>();
            JSONArray list = configuration.getJSONArray("hidden");
            for (int i = 0; i < list.length(); i++) hidden.add(list.getString(i));
            HashMap<String, JSONObject> preferred = new HashMap<>();
            JSONArray rows = configuration.getJSONArray("rows");
            for (int i = 0; i <= rows.length(); i++) {
                JSONArray row = i < rows.length() ? rows.getJSONArray(i) : configuration.getJSONArray("dock");
                for (int j = 0; j < row.length(); j++) {
                    JSONObject app = row.getJSONObject(j);
                    preferred.put(ComponentName.unflattenFromString(app.getString("component")).getPackageName(), app);
                }
            }
            var resolved = getPackageManager().queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0);
            resolved.sort(new ResolveInfo.DisplayNameComparator(getPackageManager()));
            JSONArray apps = new JSONArray();
            HashSet<String> seen = new HashSet<>();
            for (ResolveInfo info : resolved) {
                String pkg = info.activityInfo.packageName;
                if (hidden.contains(pkg) || !seen.add(pkg)) continue;
                JSONObject app = preferred.get(pkg);
                if (app == null) app = new JSONObject().put("label", info.loadLabel(getPackageManager()).toString())
                    .put("component", new ComponentName(pkg, info.activityInfo.name).flattenToString());
                apps.put(app);
            }
            LinearLayout contents = column();
            for (int start = 0; start < apps.length(); start += 4) {
                JSONArray row = new JSONArray();
                for (int i = start; i < Math.min(start + 4, apps.length()); i++) row.put(apps.getJSONObject(i));
                contents.addView(row(row));
            }
            ScrollView scroll = new ScrollView(this);
            scroll.addView(contents);
            new AlertDialog.Builder(this).setTitle("All apps").setView(scroll)
                .setNeutralButton("Settings", (dialog, which) -> startActivity(new Intent(Settings.ACTION_SETTINGS)))
                .setPositiveButton("Close", null).show();
        } catch (Exception error) { Toast.makeText(this, "Could not load apps", Toast.LENGTH_LONG).show(); }
    }
}
