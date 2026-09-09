"""Observe the entire focused Pokémon GO search editor, independently of OCR.

An empty string proves an empty editor. ``None`` means no trustworthy read was
available. The short-lived shell helper never types, installs an APK, or changes
accessibility/keyboard settings. Callers still own committing and verifying the
storage screen; editor text alone is not proof that a search was applied.
"""

import hashlib
import json
import logging
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import uuid

from .controller import ADBError


log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/android_search/SearchText.java"
BUILD_SCRIPT = SOURCE.with_name("build.py")
JAR = ROOT / "cache/android-search/pokemgr-search.jar"
PACKAGE = "com.nianticlabs.pokemongo"
PREFIX = "POKEMGR_SEARCH_V1 "
_prepare_lock = threading.Lock()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local_jar():
    def valid():
        try:
            manifest = json.loads(JAR.with_suffix(".build.json").read_text())
            return (manifest["source_sha256"] == _digest(SOURCE)
                    and manifest["build_script_sha256"] == _digest(BUILD_SCRIPT)
                    and manifest["sha256"] == _digest(JAR))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    if not valid():
        subprocess.run([sys.executable, str(BUILD_SCRIPT)], check=True,
                       capture_output=True, timeout=60)
    if not valid():
        raise RuntimeError("Search observer build could not be verified")
    return JAR, _digest(JAR)


def _prepare_helper(adb):
    with _prepare_lock:
        jar, digest = _local_jar()
        remote = f"/data/local/tmp/pokemgr-search-{digest}.jar"
        identity = (adb.serial, adb.display_id, digest)
        if getattr(adb, "_search_helper_identity", None) == identity:
            return remote
        # Content-addressed deployment avoids replacing a helper in active use.
        check = adb._run(["shell", f"if [ -e {remote} ]; then sha256sum {remote}; fi"], timeout=8)
        if check.returncode != 0:
            raise RuntimeError("Could not check search observer")
        existing = check.stdout.decode("ascii", errors="replace").split()
        if existing and existing[0] != digest:
            raise RuntimeError("Search observer digest mismatch")
        if not existing:
            pushed = adb._run(["push", str(jar), remote], timeout=8)
            if pushed.returncode != 0:
                raise RuntimeError("Could not deploy search observer")
            check = adb._run(["shell", f"chmod 0444 {remote} && sha256sum {remote}"], timeout=8)
            fields = check.stdout.decode("ascii", errors="replace").split()
            if check.returncode != 0 or not fields or fields[0] != digest:
                raise RuntimeError("Search observer deployment could not be verified")
        adb._search_helper_identity = identity
        return remote


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate observer field")
        obj[key] = value
    return obj


def _parse_text(raw, *, nonce, display_id):
    if len(raw) > 16384:
        return None
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
        reports = [line[len(PREFIX):] for line in lines if line.startswith(PREFIX)]
        if len(reports) != 1:
            return None
        data = json.loads(reports[0], object_pairs_hook=_unique_object)
        if (data.get("schema") != 1 or type(data.get("schema")) is not int
                or data.get("nonce") != nonce or data.get("display") != display_id
                or type(data.get("display")) is not int or data.get("package") != PACKAGE
                or data.get("status") != "ok" or data.get("window_focused") is not True
                or data.get("window_active") is not True
                or type(data.get("window_id")) is not int or data["window_id"] < 0):
            return None
        editors = data.get("editors")
        if not isinstance(editors, list) or len(editors) != 1:
            return None
        editor = editors[0]
        if (editor.get("package") != PACKAGE or editor.get("class") != "android.widget.EditText"
                or editor.get("focused") is not True
                or editor.get("editable") is not True or editor.get("visible") is not True
                or editor.get("password") is not False):
            return None
        value = editor.get("text")
        showing_hint = editor.get("showing_hint")
        start, end = editor.get("selection_start"), editor.get("selection_end")
        hint = editor.get("hint_text")
        # The live Unity editor exposes a literal empty value with selection
        # offsets -1/-1. No selection inference is needed for this exact value.
        if value == "" and showing_hint is False and hint in (None, ""):
            return ""
        if (type(showing_hint) is not bool or type(start) is not int or type(end) is not int
                or start < 0 or end < 0 or not (hint is None or isinstance(hint, str))):
            return None
        # Android TextView exposes the placeholder through getText() when empty.
        # Its hint-state flag plus the standard EditText's empty selection proves
        # emptiness; the placeholder itself must never become a query value.
        if showing_hint:
            return "" if start == end == 0 and isinstance(hint, str) and hint and value == hint else None
        if value is None:
            return "" if start == end == 0 and hint in (None, "") else None
        if hint and value == hint:
            return None  # Hidden-hint/real-text ambiguity is not exact evidence.
        return value if isinstance(value, str) and len(value) <= 512 else None
    except (UnicodeError, ValueError, TypeError, AttributeError):
        return None


def read_search_text(adb) -> str | None:
    """Read one exact focused editor on the frozen device/display, or return None.

    Target validation failures propagate: a vanished/replaced display is not an
    ordinary unavailable editor. Input/pause invalidation discards the result.
    """
    adb.validate_display_target()
    serial = adb.serial
    logical_display = adb.display_id
    if not serial:
        return None  # Never deploy/query through an unbound ADB transport.
    generation = getattr(adb, "_stream_generation", None)
    display_id = logical_display if logical_display is not None else 0
    nonce = uuid.uuid4().hex
    text = None
    try:
        remote = _prepare_helper(adb)
        command = (f"CLASSPATH={shlex.quote(remote)} app_process / "
                   f"pokemgr.tools.SearchText {display_id} {nonce}")
        result = adb._run(["shell", command], timeout=8)
        if result.returncode == 0:
            text = _parse_text(result.stdout, nonce=nonce, display_id=display_id)
    except (ADBError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        log.warning("Full search text unavailable (%s)", type(error).__name__)
    finally:
        adb.validate_display_target()
    if serial != adb.serial or logical_display != adb.display_id:
        raise ADBError("Search observer device/display changed")
    if generation != getattr(adb, "_stream_generation", None):
        return None
    return text
