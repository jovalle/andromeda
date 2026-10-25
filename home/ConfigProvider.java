package us.andromeda.home;

import android.content.ComponentName;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.pm.ActivityInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

public final class ConfigProvider extends ContentProvider {
    static final Uri URI = Uri.parse("content://us.andromeda.home.config");

    @Override public boolean onCreate() { return true; }

    @Override public Bundle call(String method, String arg, Bundle extras) {
        // DUMP also protects provider entry; only the owner-profile ADB shell may configure it.
        if (Binder.getCallingUid() != 2000 && Binder.getCallingUid() != 0) {
            throw new SecurityException("Configuration requires the ADB shell");
        }
        var prefs = getContext().getSharedPreferences("home", 0);
        if ("configure".equals(method)) {
            try {
                if (arg == null || arg.length() > 65536) throw new IllegalArgumentException("Invalid configuration size");
                JSONObject config = new JSONObject(new String(Base64.decode(arg, Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
                JSONArray rows = config.getJSONArray("rows");
                if (rows.length() < 1 || rows.length() > 8) throw new IllegalArgumentException("Expected 1–8 rows");
                for (int i = 0; i < rows.length(); i++) validateRow(rows.getJSONArray(i));
                JSONArray dock = config.getJSONArray("dock");
                if (dock.length() != 4) throw new IllegalArgumentException("Expected four dock apps");
                validateRow(dock);
                JSONArray hidden = config.getJSONArray("hidden");
                if (hidden.length() > 100) throw new IllegalArgumentException("Too many hidden packages");
                for (int i = 0; i < hidden.length(); i++) {
                    if (!hidden.getString(i).matches("[A-Za-z][A-Za-z0-9_.]+")) throw new IllegalArgumentException("Invalid hidden package");
                }
                if (!prefs.edit().putString("configuration", arg).commit()) throw new IllegalStateException("Could not save home configuration");
                getContext().getContentResolver().notifyChange(URI, null);
            } catch (Exception error) {
                throw new IllegalArgumentException("Home configuration rejected: " + error.getMessage(), error);
            }
        } else if (!"get".equals(method)) {
            throw new IllegalArgumentException("Unknown configuration method");
        }
        Bundle result = new Bundle();
        result.putString("configuration", prefs.getString("configuration", ""));
        return result;
    }

    private void validateRow(JSONArray row) throws Exception {
        if (row.length() < 1 || row.length() > 4) throw new IllegalArgumentException("Expected 1–4 apps per row");
        for (int i = 0; i < row.length(); i++) {
            JSONObject app = row.getJSONObject(i);
            String label = app.getString("label");
            ComponentName component = ComponentName.unflattenFromString(app.getString("component"));
            if (label.isBlank() || label.length() > 80 || component == null) throw new IllegalArgumentException("Invalid app");
            ActivityInfo activity = getContext().getPackageManager().getActivityInfo(component, 0);
            if (!activity.exported || !activity.enabled || !activity.applicationInfo.enabled) throw new IllegalArgumentException("App is unavailable: " + label);
        }
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) { return null; }
    @Override public String getType(Uri uri) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
