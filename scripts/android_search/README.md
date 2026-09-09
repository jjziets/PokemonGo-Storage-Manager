# Full search text observer

`pokemgr.adb.search_text.read_search_text(adb)` returns the complete focused
Pokémon GO `EditText` value, including text clipped offscreen. An empty string
means the editor is confirmed empty; `None` means no trustworthy value was
available. The caller owns navigation, exact query comparison, Enter, and the
subsequent storage-screen check.

The helper runs once through `app_process`, with a fresh nonce and explicit
logical display ID. It reads only the focused Pokémon GO application window on
that display and requires one visible, non-password, focused editable node.
It does not inject input, install an APK, enable an accessibility service in
settings, or change the keyboard. Existing accessibility services are retained.
The connection closes after each observation; a seven-second watchdog bounds
the remote process if Android stops answering. The host additionally validates
the frozen device/display before and after the operation and discards evidence
invalidated by input or pause.

The Android16/Fold6 virtual display exposes the entire search query while its
native keyboard dialog is open. After Enter, Pokémon GO exposes only a Unity
SurfaceView: the helper correctly returns `None` there. Query verification must
therefore happen before Enter. It cannot independently certify that the game
has applied the query or completed loading the matching storage contents.

Run `.venv/bin/python scripts/android_search/build.py` to build locally. The
same command runs lazily on the first read if the cached source/build/jar hashes
do not match. It needs the installed JDK17 and Android36 SDK/D8 toolchain;
`--java-home`, `--sdk`, `--platform`, and `--build-tools` override discovery.
The helper jar is pushed to a content-addressed `/data/local/tmp` path and its
SHA256 is checked before use. It is not replaced while another call may use it.

The display-bound UiAutomation constructor/connection use Android framework
reflection because no public SDK constructor exists for a standalone shell
observer. These are verified on Android16, not guaranteed on every Android
release. Missing APIs, timeouts, ambiguous fields, and unsupported layouts
produce unavailable evidence; there is no fallback to a different display,
truncated OCR, or a copy of the text that the caller attempted to send.

Primary API references:

- [UiAutomation windows across all displays](https://developer.android.com/reference/android/app/UiAutomation#getWindowsOnAllDisplays())
- [Accessibility node text](https://developer.android.com/reference/android/view/accessibility/AccessibilityNodeInfo#getText())
- [Android16 UiAutomation implementation](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-16.0.0_r1/core/java/android/app/UiAutomation.java)
