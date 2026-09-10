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


# TRACEWEAVER: file-role=native-stream-build; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001
PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]
COMMIT = "2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0"
SERVER_SHA256 = "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae"
TOOLS = ("meson==1.9.1", "ninja==1.13.0", "pkgconf==3.0.1.post0")
ASSETS = {
    "scrcpy.png": "8e8ca237898faa16014cdd118396af53405b423f3db0508c50cc3edce08eb313",
    "disconnected.png": "e394873cd3e2cc3ab0cca6212b10ed2a8a0fad11a05675c8a9fa6f26f3ae12c0",
}
# TRACEWEAVER: req=REQ-STREAM-001; trace=TRACE-STREAM-001
# Shared with the launcher so adding native code invalidates old cached builds.
INPUTS = ("pokemgr_frame_sink.c", "pokemgr_frame_sink.h", "pokemgr_activity.m",
          "pokemgr_activity.h", "frame_reader.c", "scrcpy-v4.1.patch", "build.py")
SINK_SOURCES = ("pokemgr_frame_sink.c", "pokemgr_frame_sink.h",
                "pokemgr_activity.m", "pokemgr_activity.h")


# TRACEWEAVER: entrypoint=run; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001
def run(command, *, cwd=None, env=None, capture=False):
    print("Running:", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True,
                          text=True, capture_output=capture)


def _git(source, *args):
    return subprocess.run(["git", *map(str, args)], cwd=source, check=True,
                          capture_output=True).stdout


def _tracked_patch(source):
    return _git(source, "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                "--abbrev=7", "--src-prefix=a/", "--dst-prefix=b/", "--unified=3",
                "--no-renames", "--diff-algorithm=myers", "HEAD", "--")


# TRACEWEAVER: entrypoint=prepare_source; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001
def prepare_source(source, build, package=PACKAGE):
    """Upgrade only an exactly proven managed patch; preserve unrelated edits."""
    if _git(source, "rev-parse", "HEAD").decode().strip() != COMMIT:
        raise RuntimeError("Cached source is not the pinned scrcpy commit")
    manifest_path = build / "pokemgr-build.json"
    try:
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        previous_inputs = manifest.get("inputs", {})
        if not isinstance(previous_inputs, dict):
            raise ValueError("Invalid native build input manifest")
    except (OSError, ValueError, AttributeError) as exc:
        raise RuntimeError("Could not verify the previous native build manifest") from exc

    # These files are untracked in the upstream cache. Never overwrite a user's
    # edits or an unknown file just because it has one of our destination names.
    for name in SINK_SOURCES:
        destination = source / "app/src" / name
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise RuntimeError(f"Managed source destination is not a regular file: {name}")
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            allowed = {hashlib.sha256((package / name).read_bytes()).hexdigest()}
            if manifest.get("source_commit") == COMMIT:
                allowed.add(previous_inputs.get(name))
            if digest not in allowed:
                raise RuntimeError(f"Preserving modified cached source: {name}")

    if _git(source, "diff", "--cached", "--name-only"):
        raise RuntimeError("Preserving staged changes in the cached scrcpy source")
    patch = package / "scrcpy-v4.1.patch"
    new_patch = patch.read_bytes()
    current = _tracked_patch(source)
    if current == new_patch:
        pass  # Already exactly this managed patch, including all tracked files.
    elif not current:
        _git(source, "apply", "--check", patch)
        _git(source, "apply", patch)
    else:
        old_digest = hashlib.sha256(current).hexdigest()
        if (manifest.get("source_commit") != COMMIT
                or old_digest != previous_inputs.get("scrcpy-v4.1.patch")):
            raise RuntimeError("Preserving tracked cache changes: old managed patch is not proven")
        build.mkdir(parents=True, exist_ok=True)
        backup = build / f"pokemgr-managed-patch-{old_digest}.patch"
        if backup.exists() and backup.read_bytes() != current:
            raise RuntimeError("Previous managed patch backup changed")
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(current)
            backup.chmod(0o600)
        # The clean index is pinned HEAD. --cached --check tests the new patch
        # against that base without touching the index or current working tree.
        _git(source, "apply", "--reverse", "--check", backup)
        _git(source, "apply", "--cached", "--check", patch)
        if _git(source, "diff", "--cached", "--name-only") or _tracked_patch(source) != current:
            raise RuntimeError("Cached source changed during managed patch preflight")
        _git(source, "apply", "--reverse", backup)
        try:
            if _tracked_patch(source):
                raise RuntimeError("Unexpected tracked changes after reversing managed patch")
            _git(source, "apply", "--check", patch)
            _git(source, "apply", patch)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            try:
                _git(source, "apply", "--check", backup)
                _git(source, "apply", backup)
            except subprocess.CalledProcessError as rollback_error:
                raise RuntimeError(f"Patch upgrade and restoration failed; original patch kept at {backup}") from rollback_error
            raise RuntimeError("Patch upgrade failed; the previous managed patch was restored") from exc
    if _tracked_patch(source) != new_patch:
        raise RuntimeError("Cached source is not exactly the new managed patch")
    for name in SINK_SOURCES:
        shutil.copy2(package / name, source / "app/src" / name)


# TRACEWEAVER: entrypoint=main; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001
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
    prepare_source(source, build)

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
                            for name in INPUTS})
    (build / "pokemgr-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
