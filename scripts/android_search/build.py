#!/usr/bin/env python3
"""Build the search-text observer offline with installed javac and Android D8.

No Gradle, network downloads, APK installation, or device access is performed.
The jar has fixed ZIP metadata and is reproducible with the same toolchain.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


SOURCE = Path(__file__).with_name("SearchText.java")
ROOT = SOURCE.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=Path(
        os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        or Path.home() / "Library/Android/sdk"))
    parser.add_argument("--build-tools", default="36.0.0")
    parser.add_argument("--platform", default="android-36")
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "cache/android-search/pokemgr-search.jar")
    args = parser.parse_args()

    java_home = args.java_home
    if java_home is None:
        installed_jdk = Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")
        if os.environ.get("JAVA_HOME"):
            java_home = Path(os.environ["JAVA_HOME"])
        elif installed_jdk.is_dir():
            java_home = installed_jdk
        else:
            javac_on_path = shutil.which("javac")
            if javac_on_path is None:
                parser.error("Install a JDK or pass --java-home")
            java_home = Path(javac_on_path).resolve().parents[1]
    javac, java = java_home / "bin/javac", java_home / "bin/java"
    d8 = args.sdk / "build-tools" / args.build_tools / "lib/d8.jar"
    android_jar = args.sdk / "platforms" / args.platform / "android.jar"
    for path in (javac, java, d8, android_jar):
        if not path.is_file():
            parser.error(f"Required installed tool is missing: {path}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pokemgr-search-") as temporary:
        work = Path(temporary)
        classes, dex = work / "classes", work / "dex"
        classes.mkdir()
        dex.mkdir()
        subprocess.run([
            str(javac), "--release", "8", "-g:none", "-encoding", "UTF-8",
            "-cp", str(android_jar), "-d", str(classes), str(SOURCE),
        ], check=True)
        subprocess.run([
            str(java), "-cp", str(d8), "com.android.tools.r8.D8", "--release",
            "--min-api", "30", "--lib", str(android_jar), "--output", str(dex),
            *[str(path) for path in sorted(classes.rglob("*.class"))],
        ], check=True)
        entry = zipfile.ZipInfo("classes.dex", date_time=(1980, 1, 1, 0, 0, 0))
        entry.create_system = 3
        entry.external_attr = 0o100644 << 16
        with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_STORED) as jar:
            jar.writestr(entry, (dex / "classes.dex").read_bytes())

    version = subprocess.run([str(javac), "-version"], check=True,
                             capture_output=True, text=True)
    manifest = {
        "schema": 1,
        "class": "pokemgr.tools.SearchText",
        "jar": str(args.output.resolve()),
        "sha256": digest(args.output),
        "source_sha256": digest(SOURCE),
        "build_script_sha256": digest(Path(__file__)),
        "javac": (version.stdout + version.stderr).strip(),
        "build_tools": args.build_tools,
        "d8_sha256": digest(d8),
        "android_platform": args.platform,
        "android_jar_sha256": digest(android_jar),
    }
    args.output.with_suffix(".build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
