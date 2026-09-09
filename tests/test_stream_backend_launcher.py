"""Paired stream launcher setup and cleanup with every process/device mocked."""

import io
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import stream_pokemon


class StreamBackendLauncherTests(unittest.TestCase):
    def launch(self, *, backend=None, failure=False):
        stale = {key: "stale" for key in stream_pokemon.SESSION_ENVIRONMENT}
        self.enterContext(patch.dict(os.environ, stale))
        adb = Mock(serial="phone", adb_path="/mock/adb")
        adb.get_device_info.return_value = SimpleNamespace(width=96, height=128, density=420)
        adb.shell.side_effect = [
            "", 'Display 115 (Virtual display): displayName="scrcpy"',
            'Display #15 (activities from top to bottom):\n'
            '  topResumedActivity=ActivityRecord{x com.nianticlabs.pokemongo/.Game}',
        ]
        adb._run.return_value.returncode = 0
        stream = Mock(stdout=io.StringIO("New display: 96x128 (id=15)\n"), pid=12345)
        stream.poll.return_value = None
        manager = Mock()
        manager.poll.return_value = 0
        events, launches = [], []

        def controller():
            self.assertTrue(all(key not in os.environ for key in stream_pokemon.SESSION_ENVIRONMENT))
            events.append("discover")
            return adb

        def spawn(command, **kwargs):
            launches.append((command, kwargs))
            if len(launches) == 1:
                events.append("stream")
                if backend != "jpeg":
                    environment = kwargs["env"]
                    buffer = Path(environment["POKEMGR_FRAME_BUFFER"])
                    self.assertFalse(buffer.exists())
                    self.assertEqual(stat.S_IMODE(buffer.parent.stat().st_mode), 0o700)
                    self.assertRegex(environment["POKEMGR_FRAME_SESSION"], r"^[0-9a-f]{32}$")
                    for key in ("POKEMGR_STREAM_PID", "POKEMGR_FRAME_READER", "POKEMGR_NATIVE_OCR",
                                "POKEMGR_DISPLAY_ID", "POKEMGR_CAPTURE_DISPLAY_ID"):
                        self.assertNotIn(key, environment)
                return stream
            events.append("manager")
            if failure:
                raise RuntimeError("manager launch failed")
            return manager

        args = ["stream_pokemon.py", "--serial", "phone"]
        if backend:
            args.extend(("--capture-backend", backend))
        with patch.object(stream_pokemon.sys, "argv", args), \
             patch.object(stream_pokemon.shutil, "which", return_value="/mock/system-scrcpy"), \
             patch.object(stream_pokemon, "_prepare_stream_tools", return_value=(
                 Path("/mock/local/app/scrcpy"), Path("/mock/local/libpk_frame_reader.dylib"),
             )) as build, \
             patch.object(stream_pokemon, "_prepare_clock_helper", return_value=Path("/mock/clock.jar")) as clock, \
             patch.object(stream_pokemon, "_deploy_clock_helper", side_effect=lambda *_: events.append("clock deploy")) as deploy, \
             patch.object(stream_pokemon.subprocess, "run", return_value=SimpleNamespace(returncode=1)), \
             patch.object(stream_pokemon, "ADBController", side_effect=controller), \
             patch.object(stream_pokemon.subprocess, "Popen", side_effect=spawn), \
             patch.object(stream_pokemon.threading, "Thread", side_effect=lambda target, **kwargs: SimpleNamespace(start=target)), \
             patch.object(stream_pokemon.time, "monotonic", return_value=0), \
             patch.object(stream_pokemon, "stop", side_effect=lambda process: events.append(
                 "stop manager" if process is manager else "stop stream" if process is stream else "stop none")):
            if failure:
                with self.assertRaisesRegex(RuntimeError, "manager launch failed"):
                    stream_pokemon.main()
            else:
                self.assertEqual(stream_pokemon.main(), 0)
        if backend == "jpeg":
            build.assert_not_called()
            clock.assert_not_called()
            deploy.assert_not_called()
        else:
            build.assert_called_once()
            clock.assert_called_once()
            deploy.assert_called_once_with(adb, Path("/mock/clock.jar"))
        adb.close_stream_capture.assert_called_once()
        return launches, events

    def test_default_backend_binds_private_buffer_and_writer_to_gui(self):
        launches, events = self.launch()
        stream_command, stream_args = launches[0]
        _, gui_args = launches[1]
        self.assertEqual(stream_command[0], "/mock/local/app/scrcpy")
        writer, reader = stream_args["env"], gui_args["env"]
        self.assertEqual(writer["ADB"], "/mock/adb")
        self.assertEqual(reader["POKEMGR_FRAME_BUFFER"], writer["POKEMGR_FRAME_BUFFER"])
        self.assertEqual(reader["POKEMGR_FRAME_SESSION"], writer["POKEMGR_FRAME_SESSION"])
        self.assertEqual(reader["POKEMGR_STREAM_PID"], "12345")
        self.assertEqual(reader["POKEMGR_FRAME_READER"], "/mock/local/libpk_frame_reader.dylib")
        self.assertEqual(reader["POKEMGR_NATIVE_OCR"], "1")
        self.assertEqual(reader["POKEMGR_CAPTURE_FORMAT"], "jpeg")
        self.assertEqual((reader["POKEMGR_DISPLAY_ID"], reader["POKEMGR_CAPTURE_DISPLAY_ID"]), ("15", "115"))
        self.assertFalse(Path(reader["POKEMGR_FRAME_BUFFER"]).parent.exists())
        self.assertEqual(events, ["discover", "clock deploy", "stream", "manager", "stop manager", "stop stream"])

    def test_explicit_jpeg_backend_keeps_legacy_capture_without_buffer_or_native_ocr(self):
        launches, _ = self.launch(backend="jpeg")
        self.assertEqual(launches[0][0][0], "/mock/system-scrcpy")
        for _, arguments in launches:
            for key in ("POKEMGR_FRAME_BUFFER", "POKEMGR_FRAME_SESSION", "POKEMGR_STREAM_PID",
                        "POKEMGR_FRAME_READER", "POKEMGR_NATIVE_OCR"):
                self.assertNotIn(key, arguments["env"])
        self.assertEqual(launches[1][1]["env"]["POKEMGR_CAPTURE_FORMAT"], "jpeg")

    def test_failed_gui_launch_cleans_private_directory_after_stopping_stream(self):
        launches, events = self.launch(failure=True)
        path = Path(launches[0][1]["env"]["POKEMGR_FRAME_BUFFER"])
        self.assertFalse(path.parent.exists())
        self.assertEqual(events[-2:], ["stop none", "stop stream"])

    def test_each_launch_uses_a_new_session_and_directory(self):
        first, _ = self.launch()
        second, _ = self.launch()
        self.assertNotEqual(first[0][1]["env"]["POKEMGR_FRAME_SESSION"],
                            second[0][1]["env"]["POKEMGR_FRAME_SESSION"])
        self.assertNotEqual(first[0][1]["env"]["POKEMGR_FRAME_BUFFER"],
                            second[0][1]["env"]["POKEMGR_FRAME_BUFFER"])

    def test_clock_deployment_verifies_device_bytes_and_restores_read_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "clock.jar"
            jar.write_bytes(b"test clock helper")
            expected = hashlib.sha256(jar.read_bytes()).hexdigest()
            adb = Mock()
            adb._run.side_effect = [
                SimpleNamespace(returncode=0), SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=0, stdout=f"{expected}  /data/local/tmp/pokemgr-clock.jar\n".encode()),
            ]
            stream_pokemon._deploy_clock_helper(adb, jar)
            command = adb._run.call_args_list[-1].args[0]
            self.assertIn("chmod 0444", command[1])
            self.assertIn("sha256sum", command[1])

    def test_clock_deployment_does_not_accept_a_different_device_jar(self):
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "clock.jar"
            jar.write_bytes(b"test clock helper")
            adb = Mock()
            adb._run.side_effect = [
                SimpleNamespace(returncode=0), SimpleNamespace(returncode=0),
                SimpleNamespace(returncode=0, stdout=b"wrong digest\n"),
            ]
            with self.assertRaisesRegex(RuntimeError, "did not verify"):
                stream_pokemon._deploy_clock_helper(adb, jar)


if __name__ == "__main__":
    unittest.main()
