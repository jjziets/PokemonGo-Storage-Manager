package pokemgr.tools;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.os.HandlerThread;
import android.os.Looper;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.lang.reflect.Constructor;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** One read-only, display-bound observation; never injects input or changes settings. */
public final class SearchText {
    private static final String PACKAGE = "com.nianticlabs.pokemongo";
    private static int visited;

    private static void collect(AccessibilityNodeInfo node, JSONArray editors, int depth)
            throws Exception {
        if (node == null) return;
        if (depth > 24 || ++visited > 256) throw new IllegalStateException("node limit");
        if (!PACKAGE.contentEquals(node.getPackageName() == null ? "" : node.getPackageName())) {
            throw new IllegalStateException("node package changed");
        }
        if (node.isFocused() && node.isEditable()) {
            if (node.isPassword() || !node.isVisibleToUser() || !node.refresh()) {
                throw new IllegalStateException("editor unavailable");
            }
            CharSequence text = node.getText();
            if (text != null && text.length() > 512) throw new IllegalStateException("text limit");
            JSONObject editor = new JSONObject();
            editor.put("text", text == null ? JSONObject.NULL : text.toString());
            editor.put("showing_hint", node.isShowingHintText());
            editor.put("hint_text", node.getHintText() == null
                    ? JSONObject.NULL : node.getHintText().toString());
            editor.put("selection_start", node.getTextSelectionStart());
            editor.put("selection_end", node.getTextSelectionEnd());
            editor.put("package", String.valueOf(node.getPackageName()));
            editor.put("class", String.valueOf(node.getClassName()));
            editor.put("focused", node.isFocused());
            editor.put("editable", node.isEditable());
            editor.put("visible", node.isVisibleToUser());
            editor.put("password", node.isPassword());
            editors.put(editor);
        }
        for (int index = 0; index < node.getChildCount(); ++index) {
            AccessibilityNodeInfo child = node.getChild(index);
            try {
                if (child == null) throw new IllegalStateException("incomplete hierarchy");
                collect(child, editors, depth + 1);
            } finally {
                if (child != null) child.recycle();
            }
        }
    }

    public static void main(String[] args) {
        // A host ADB timeout alone cannot guarantee termination of a remote process.
        Thread watchdog = new Thread(new Runnable() {
            public void run() {
                try { Thread.sleep(7000); } catch (InterruptedException ignored) { return; }
                System.exit(3);
            }
        }, "pokemgr-search-deadline");
        watchdog.setDaemon(true);
        watchdog.start();
        HandlerThread callbacks = new HandlerThread("pokemgr-search-observer");
        callbacks.start();
        UiAutomation ui = null;
        JSONObject result = new JSONObject();
        try {
            if (args.length != 2 || !args[1].matches("[0-9a-f]{32}")) {
                throw new IllegalArgumentException("arguments");
            }
            int display = Integer.parseInt(args[0]);
            if (display < 0) throw new IllegalArgumentException("display");
            result.put("schema", 1);
            result.put("nonce", args[1]);
            result.put("display", display);
            result.put("package", PACKAGE);
            result.put("status", "unavailable");
            Class<?> connectionType = Class.forName("android.app.IUiAutomationConnection");
            Object connection = Class.forName("android.app.UiAutomationConnection")
                    .getConstructor().newInstance();
            // Android 16's hidden constructor binds the observer to the actual display.
            // Reflection failure is unavailable, never a fallback to the default display.
            Constructor<UiAutomation> constructor = UiAutomation.class.getDeclaredConstructor(
                    int.class, Looper.class, connectionType);
            constructor.setAccessible(true);
            ui = constructor.newInstance(display, callbacks.getLooper(), connection);
            UiAutomation.class.getMethod("connectWithTimeout", int.class, long.class).invoke(
                    ui, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES, 3000L);
            AccessibilityServiceInfo service = ui.getServiceInfo();
            service.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                    | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(service);
            Thread.sleep(150);
            ui.clearCache();
            List<AccessibilityWindowInfo> windows = ui.getWindowsOnAllDisplays().get(display);
            AccessibilityWindowInfo focused = null;
            if (windows != null) {
                for (AccessibilityWindowInfo window : windows) {
                    if (window.getDisplayId() != display) {
                        throw new IllegalStateException("display changed");
                    }
                    if (window.isFocused()) {
                        if (focused != null) throw new IllegalStateException("multiple windows");
                        focused = window;
                    }
                }
            }
            if (focused == null || !focused.isActive()
                    || focused.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) {
                throw new IllegalStateException("no focused app window");
            }
            AccessibilityNodeInfo root = focused.getRoot();
            JSONArray editors = new JSONArray();
            try {
                if (root == null || !root.refresh()) throw new IllegalStateException("no root");
                collect(root, editors, 0);
            } finally {
                if (root != null) root.recycle();
            }
            // A disappearing/replaced dialog must not lend its old editor text to a new UI.
            ui.clearCache();
            List<AccessibilityWindowInfo> current = ui.getWindowsOnAllDisplays().get(display);
            int stillFocused = 0;
            if (current != null) for (AccessibilityWindowInfo window : current) {
                if (window.isFocused()) {
                    if (window.getId() != focused.getId() || !window.isActive()
                            || window.getDisplayId() != display) {
                        throw new IllegalStateException("focused window changed");
                    }
                    ++stillFocused;
                }
            }
            if (stillFocused != 1 || editors.length() != 1) {
                throw new IllegalStateException("no unique editor");
            }
            result.put("window_id", focused.getId());
            result.put("window_focused", true);
            result.put("window_active", true);
            result.put("editors", editors);
            result.put("status", "ok");
        } catch (Throwable error) {
            try { result.put("status", "unavailable"); } catch (Exception ignored) { }
        } finally {
            if (ui != null) try {
                UiAutomation.class.getMethod("disconnect").invoke(ui);
            } catch (Throwable ignored) { }
            callbacks.quitSafely();
        }
        System.out.println("POKEMGR_SEARCH_V1 " + result.toString());
        System.exit(0);
    }
}
