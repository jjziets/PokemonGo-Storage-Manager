"""Build the pinned local scrcpy frame exporter and read-only copying dylib.

Installs pinned build tools in a cache-only venv. Uses existing Homebrew SDL3
and FFmpeg headers/libraries and the matching official 4.1 server. Does not
install a global binary, launch scrcpy, or access a phone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
COMMIT = "2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0"
SERVER_SHA256 = "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
TOOLS = ("meson==1.9.1", "ninja==1.13.0", "pkgconf==3.0.1.post0")
ASSETS = {
    "scrcpy.png": "8e8ca237898faa16014cdd118396af53405b423f3db0508c50cc3edce08eb313",
    "disconnected.png": "e394873cd3e2cc3ab0cca6212b10ed2a8a0fad11a05675c8a9fa6f26f3ae12c0",
}


def run(command, *, cwd=None, env=None, capture=False):
    print("Running:", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True,
                          text=True, capture_output=capture)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "cache/scrcpy-frame-source")
    parser.add_argument("--build", type=Path, default=ROOT / "cache/scrcpy-frame-build")
    parser.add_argument("--tools", type=Path, default=ROOT / "cache/scrcpy-frame-build-tools")
    parser.add_argument("--server", type=Path,
                        default=Path("/opt/homebrew/opt/scrcpy/share/scrcpy/scrcpy-server"))
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 4, 8))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This local exporter build currently targets macOS")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    server = args.server.resolve()
    if not server.is_file() or hashlib.sha256(server.read_bytes()).hexdigest() != SERVER_SHA256:
        parser.error("Expected the official matching scrcpy 4.1 server SHA256")

    source = args.source.resolve()
    build = args.build.resolve()
    tooling = args.tools.resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        run(["git", "clone", "--depth", "1", "--branch", "v4.1",
             "https://github.com/Genymobile/scrcpy.git", source])
    head = run(["git", "rev-parse", "HEAD"], cwd=source, capture=True).stdout.strip()
    if head != COMMIT:
        parser.error(f"Source is {head}; expected pinned {COMMIT}. No checkout was changed.")
    patch = PACKAGE / "scrcpy-v4.1.patch"
    already_patched = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                                      cwd=source, capture_output=True).returncode == 0
    if not already_patched:
        run(["git", "apply", "--check", patch], cwd=source)
        run(["git", "apply", patch], cwd=source)
    for name in ("pokemgr_frame_sink.c", "pokemgr_frame_sink.h"):
        shutil.copy2(PACKAGE / name, source / "app/src" / name)

    python = tooling / "bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(tooling)
    marker = tooling / "pokemgr-pins.json"
    if not marker.exists() or json.loads(marker.read_text()) != list(TOOLS):
        run([python, "-m", "pip", "install", "--disable-pip-version-check", *TOOLS])
        marker.write_text(json.dumps(TOOLS))
    environment = dict(os.environ)
    environment["PATH"] = str(tooling / "bin") + os.pathsep + environment.get("PATH", "")
    environment["PKG_CONFIG_PATH"] = os.pathsep.join([
        "/opt/homebrew/opt/sdl3/lib/pkgconfig", "/opt/homebrew/opt/ffmpeg/lib/pkgconfig",
        "/opt/homebrew/opt/libusb/lib/pkgconfig", "/opt/homebrew/lib/pkgconfig",
    ])
    # pkgconf 3.0.1.post0's console wrapper exits without running its binary
    # when PKG_CONFIG_PATH is set. Resolve the actual pinned native executable.
    pkgconf = run([python, "-c", "import pkgconf; print(pkgconf._get_executable())"],
                  capture=True).stdout.strip()
    if not Path(pkgconf).is_file():
        parser.error("Pinned pkgconf install produced no native executable")
    environment["PKG_CONFIG"] = pkgconf
    meson = tooling / "bin/meson"
    setup = [meson, "setup", build, source, "--buildtype=release",
             f"-Dprebuilt_server={server}", "-Dportable=true", "-Dv4l2=false"]
    if (build / "build.ninja").exists():
        setup.append("--reconfigure")
    run(setup, env=environment)
    run([meson, "compile", "-C", build, "-j", str(args.jobs)], env=environment)
    dylib = build / "libpk_frame_reader.dylib"
    run(["/usr/bin/clang", "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
         "-dynamiclib", "-install_name", "@rpath/libpk_frame_reader.dylib",
         PACKAGE / "frame_reader.c", "-o", dylib])
    # Portable client locates this matching server beside its executable.
    shutil.copy2(server, build / "app/scrcpy-server")
    for name, digest in ASSETS.items():
        asset = source / "app/data" / name
        if hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
            parser.error(f"Pinned official asset changed: {name}")
        shutil.copy2(asset, build / "app" / name)
    manifest = dict(source_commit=COMMIT, server_sha256=SERVER_SHA256,
                    binary=str(build / "app/scrcpy"), reader_library=str(dylib),
                    build_tools=list(TOOLS), assets=ASSETS,
                    inputs={name: hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest()
                            for name in ("pokemgr_frame_sink.c", "pokemgr_frame_sink.h",
                                         "frame_reader.c", "scrcpy-v4.1.patch", "build.py")})
    (build / "pokemgr-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
