package us.andromeda.home.probe;

import android.app.Activity;
import android.app.Instrumentation;
import android.net.Uri;
import android.os.Bundle;

public final class Probe extends Instrumentation {
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            getTargetContext().getContentResolver().call(Uri.parse("content://us.andromeda.home.config"),
                "configure", "e30=", null);
            result.putString("outcome", "FAIL: unprivileged app reached the configuration provider");
            finish(Activity.RESULT_CANCELED, result);
        } catch (SecurityException expected) {
            result.putString("outcome", "PASS: unprivileged app cannot configure Home");
            finish(Activity.RESULT_OK, result);
        }
    }
}
