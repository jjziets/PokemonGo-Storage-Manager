"""Launch an isolated Pokemon GO stream and a manager bound to that display.

Run with the project's Python after closing the existing manager and stream.
Closing either window ends the paired session. The game returns to the phone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokemgr.adb.controller import ADBController  # noqa: E402
from run import add_scan_start_arguments, validate_scan_start_arguments  # noqa: E402


SESSION_ENVIRONMENT = (
    'POKEMGR_DISPLAY_ID', 'POKEMGR_CAPTURE_DISPLAY_ID', 'POKEMGR_DEVICE_SERIAL',
    'POKEMGR_CAPTURE_FORMAT', 'POKEMGR_FRAME_BUFFER', 'POKEMGR_FRAME_SESSION',
    'POKEMGR_STREAM_PID', 'POKEMGR_FRAME_READER', 'POKEMGR_NATIVE_OCR',
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_stream_tools() -> tuple[Path, Path]:
    """Reuse the matching local build, rebuilding only when its inputs changed."""
    from scripts.scrcpy_frame_sink import build
    directory = ROOT / 'cache/scrcpy-frame-build'
    binary = directory / 'app/scrcpy'
    reader = directory / 'libpk_frame_reader.dylib'
    manifest_path = directory / 'pokemgr-build.json'
    inputs = ('pokemgr_frame_sink.c', 'pokemgr_frame_sink.h', 'frame_reader.c',
              'scrcpy-v4.1.patch', 'build.py')

    def current():
        try:
            manifest = json.loads(manifest_path.read_text())
            return (manifest.get('source_commit') == build.COMMIT
                    and manifest.get('server_sha256') == build.SERVER_SHA256
                    and manifest.get('binary') == str(binary)
                    and manifest.get('reader_library') == str(reader)
                    and binary.is_file() and os.access(binary, os.X_OK) and reader.is_file()
                    and _digest(binary.parent / 'scrcpy-server') == build.SERVER_SHA256
                    and manifest.get('assets') == build.ASSETS
                    and all(_digest(binary.parent / name) == digest
                            for name, digest in build.ASSETS.items())
                    and all(manifest.get('inputs', {}).get(name) == _digest(build.PACKAGE / name)
                            for name in inputs))
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    if not current():
        subprocess.run([sys.executable, str(build.PACKAGE / 'build.py')], cwd=ROOT, check=True)
    if not current():
        raise RuntimeError('The local stream client build is missing or does not match its source')
    return binary, reader


def _prepare_clock_helper() -> Path:
    from scripts.android_clock import build
    jar = ROOT / 'cache/android-clock/pokemgr-clock.jar'

    def current():
        try:
            manifest = json.loads(jar.with_suffix('.build.json').read_text())
            return (manifest.get('class') == 'pokemgr.tools.ClockSample'
                    and manifest.get('sha256') == _digest(jar)
                    and manifest.get('source_sha256') == _digest(build.SOURCE)
                    and manifest.get('build_script_sha256') == _digest(Path(build.__file__)))
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    if not current():
        subprocess.run([sys.executable, str(Path(build.__file__))], cwd=ROOT, check=True)
    if not current():
        raise RuntimeError('The Android clock helper build is missing or stale')
    return jar


def _deploy_clock_helper(adb: ADBController, jar: Path) -> None:
    """Deploy only the source-matched helper, before the paired GUI starts."""
    from pokemgr.adb.clock_sync import DEFAULT_REMOTE_JAR
    remote = shlex.quote(DEFAULT_REMOTE_JAR)
    # Previous runs deliberately leave the helper read-only. This is the one
    # launcher-owned file, with no running paired manager at this point.
    result = adb._run(['shell', f'if [ -e {remote} ]; then chmod 0644 {remote}; fi'])
    if result.returncode != 0:
        raise RuntimeError('Could not prepare the Android clock helper destination')
    result = adb._run(['push', str(jar), DEFAULT_REMOTE_JAR])
    if result.returncode != 0:
        raise RuntimeError('Could not deploy the Android clock helper')
    result = adb._run(['shell', f'chmod 0444 {remote} && sha256sum {remote}'])
    digest = result.stdout.decode(errors='replace').strip().split()
    if result.returncode != 0 or not digest or digest[0] != _digest(jar):
        raise RuntimeError('The deployed Android clock helper did not verify')


def capture_displays(output: str) -> set[str]:
    return set(re.findall(
        r'^Display (\d+) \(Virtual display\):.*displayName="scrcpy"',
        output, re.MULTILINE,
    ))


def game_on_display(output: str, display_id: str) -> bool:
    block = re.search(
        rf'^Display #{re.escape(display_id)} \(.*?(?=^Display #|\Z)',
        output, re.MULTILINE | re.DOTALL,
    )
    return bool(block and re.search(
        r'^\s*topResumedActivity=.*com\.nianticlabs\.pokemongo/',
        block.group(0), re.MULTILINE,
    ))


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='ADB serial; defaults to the sole device')
    parser.add_argument('--capture-backend', choices=('stream', 'jpeg'), default='stream',
                        help='stream uses the paired frame window and native OCR; jpeg selects legacy capture')
    add_scan_start_arguments(parser)
    args = parser.parse_args()
    validate_scan_start_arguments(parser, args)
    scrcpy = shutil.which('scrcpy') if args.capture_backend == 'jpeg' else None
    if args.capture_backend == 'jpeg' and not scrcpy:
        parser.error('scrcpy is not installed or is not on PATH')
    existing_gui = subprocess.run(
        ['pgrep', '-f', r'(^|[[:space:]])('
         + re.escape(str(ROOT / 'run.py'))
         + r'|(\./)?run\.py)[[:space:]]+gui([[:space:]]|$)'],
        capture_output=True, text=True,
    )
    if existing_gui.returncode == 0:
        parser.error('Close the existing Storage Manager window first')
    if existing_gui.returncode != 1:
        parser.error('Could not check for an existing Storage Manager process')

    # This launcher discovers a new display; never inherit an old session ID.
    for key in SESSION_ENVIRONMENT:
        os.environ.pop(key, None)
    reader_library = clock_jar = None
    if args.capture_backend == 'stream':
        binary, reader_library = _prepare_stream_tools()
        scrcpy = str(binary)
        clock_jar = _prepare_clock_helper()
    adb = ADBController()
    if args.serial:
        adb.connect(args.serial)
    else:
        devices = adb.get_devices()
        if len(devices) != 1:
            parser.error('Connect one phone, or select it with --serial')
        adb.connect(devices[0])
    info = adb.get_device_info()
    before = capture_displays(adb.shell('dumpsys SurfaceFlinger --display-id'))
    if before:
        parser.error('Close the existing scrcpy stream for this phone first')

    command = [
        scrcpy, f'--serial={adb.serial}', '--no-audio', '--max-fps=30',
        f'--new-display={info.width}x{info.height}/{info.density}',
        '--no-vd-system-decorations', '--no-vd-destroy-content',
        '--display-ime-policy=local', '--start-app=com.nianticlabs.pokemongo',
        '--turn-screen-off', '--stay-awake', '--keep-active',
        '--window-title=Pokemon GO · App-only stream', '--window-height=900',
    ]
    stream = manager = None
    temporary = None
    stream_environment = dict(os.environ)
    frame_path = frame_session = None
    lines: queue.Queue[str] = queue.Queue()
    try:
        if args.capture_backend == 'stream':
            _deploy_clock_helper(adb, clock_jar)
            temporary = tempfile.TemporaryDirectory(prefix='pokemgr-stream-')
            frame_session = secrets.token_hex(16)
            frame_path = str(Path(temporary.name) / f'{frame_session}.frames')
            stream_environment.update(POKEMGR_FRAME_BUFFER=frame_path,
                                      POKEMGR_FRAME_SESSION=frame_session)
            stream_environment['SCRCPY_SERVER_PATH'] = str(Path(scrcpy).parent / 'scrcpy-server')
            adb_executable = (adb.adb_path if Path(adb.adb_path).is_absolute()
                              else shutil.which(adb.adb_path))
            if not adb_executable:
                raise RuntimeError('Could not locate ADB for the local stream client')
            stream_environment['ADB'] = str(Path(adb_executable).resolve())
        # A virtual display does not wake a sleeping phone. Wake it explicitly
        # before moving the game, then darken only the physical panel below.
        adb.turn_phone_screen_on()
        stream = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=ROOT, env=stream_environment,
        )

        def read_output():
            for line in stream.stdout:
                print(line.rstrip(), flush=True)
                lines.put(line)

        threading.Thread(target=read_output, daemon=True).start()
        deadline = time.monotonic() + 25
        display_id = None
        while time.monotonic() < deadline and stream.poll() is None:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            match = re.search(r'New display: .*\(id=(\d+)\)', line)
            if match:
                display_id = match.group(1)
                break
        if display_id is None:
            raise RuntimeError('scrcpy did not create an isolated display')

        capture_id = None
        while time.monotonic() < deadline and stream.poll() is None:
            new_ids = capture_displays(
                adb.shell('dumpsys SurfaceFlinger --display-id')
            ) - before
            if len(new_ids) > 1:
                raise RuntimeError('More than one new capture display; refusing to guess')
            if len(new_ids) == 1:
                capture_id = new_ids.pop()
                break
            time.sleep(0.2)
        if capture_id is None:
            raise RuntimeError('The isolated display has no capture surface')

        while time.monotonic() < deadline and stream.poll() is None:
            if game_on_display(adb.shell('dumpsys activity activities'), display_id):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Pokemon GO did not become active on its app display')
        # Samsung can wake the physical panel when moving the game, after
        # scrcpy's initial --turn-screen-off. Apply display-only power-off
        # after the app has actually moved; keep-active maintains the game.
        powered_off = adb._run(['shell', 'cmd', 'display', 'power-off', '0'])
        if powered_off.returncode != 0:
            raise RuntimeError('Could not turn off the physical display')

        environment = dict(os.environ)
        environment['POKEMGR_DISPLAY_ID'] = display_id
        environment['POKEMGR_CAPTURE_DISPLAY_ID'] = capture_id
        environment['POKEMGR_DEVICE_SERIAL'] = adb.serial
        environment['POKEMGR_CAPTURE_FORMAT'] = 'jpeg'
        if args.capture_backend == 'stream':
            environment.update(
                POKEMGR_FRAME_BUFFER=frame_path, POKEMGR_FRAME_SESSION=frame_session,
                POKEMGR_STREAM_PID=str(stream.pid), POKEMGR_FRAME_READER=str(reader_library),
                POKEMGR_NATIVE_OCR='1',
            )
        print(f'Starting manager on app display {display_id}', flush=True)
        manager_command = [sys.executable, str(ROOT / 'run.py'), 'gui']
        if args.start_scan:
            manager_command.append('--start-scan')
            if args.skip_first is not None:
                manager_command.extend(['--skip-first', str(args.skip_first)])
            if args.resume_species is not None:
                manager_command.extend(['--resume-species', args.resume_species])
            if args.resume_cp is not None:
                manager_command.extend(['--resume-cp', str(args.resume_cp)])
        manager = subprocess.Popen(
            manager_command,
            env=environment, cwd=ROOT,
        )
        while stream.poll() is None and manager.poll() is None:
            time.sleep(0.25)
        return 0
    finally:
        # Stop the scanner before removing its display. scrcpy restores the
        # prior keep-awake setting and moves the game back to the main display.
        stop(manager)
        stop(stream)
        adb.close_stream_capture()
        if temporary is not None:
            temporary.cleanup()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        print(f'App stream stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
