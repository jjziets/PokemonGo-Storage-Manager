# TRACEWEAVER: file-role=native-build-migration-verification; verifies=VER-STREAM-ACTIVITY-001; req=REQ-STREAM-001; trace=TRACE-STREAM-001
"""Managed patch migration fixtures use only disposable local repositories."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.scrcpy_frame_sink import build


class SourceMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="pk-source-migration-"))
        self.root = Path(directory)
        self.source, self.output, self.package = [self.root / name for name in ("source", "build", "package")]
        for directory in (self.source / "app/src", self.output, self.package):
            directory.mkdir(parents=True)
        self.file = self.source / "app/meson.build"
        self.file.write_text("base\n")
        (self.source / "keep.txt").write_text("unrelated\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Native fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--no-gpg-sign", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD").decode().strip()
        self.enterContext(patch.object(build, "COMMIT", self.head))
        self.file.write_text("base\nold exporter patch\n")
        self.old_patch = build._tracked_patch(self.source)
        self.file.write_text("base\nnew exporter patch\n")
        self.new_patch = build._tracked_patch(self.source)
        (self.package / "scrcpy-v4.1.patch").write_bytes(self.new_patch)
        self.file.write_text("base\nold exporter patch\n")
        self.old_copies = {}
        for name in build.SINK_SOURCES:
            (self.package / name).write_text("current " + name)
            if name in ("pokemgr_frame_sink.c", "pokemgr_frame_sink.h"):
                content = ("previous " + name).encode()
                self.old_copies[name] = content
                (self.source / "app/src" / name).write_bytes(content)
        self.manifest = dict(source_commit=self.head, inputs={
            "scrcpy-v4.1.patch": hashlib.sha256(self.old_patch).hexdigest(),
            **{name: hashlib.sha256(content).hexdigest() for name, content in self.old_copies.items()},
        })
        self.write_manifest()

    def git(self, *arguments):
        return subprocess.run(["git", *arguments], cwd=self.source, capture_output=True, check=True).stdout

    def write_manifest(self):
        (self.output / "pokemgr-build.json").write_text(json.dumps(self.manifest))

    def prepare(self):
        build.prepare_source(self.source, self.output, self.package)

    def test_proven_upgrade_keeps_original_patch_and_new_files(self):
        self.prepare()

        self.assertEqual(self.new_patch, build._tracked_patch(self.source))
        backups = list(self.output.glob("pokemgr-managed-patch-*.patch"))
        self.assertEqual(1, len(backups))
        self.assertEqual(self.old_patch, backups[0].read_bytes())
        self.assertEqual(0o600, backups[0].stat().st_mode & 0o777)
        for name in build.SINK_SOURCES:
            self.assertEqual((self.package / name).read_bytes(),
                             (self.source / "app/src" / name).read_bytes())

    def test_retry_after_source_upgrade_with_old_build_manifest_is_safe(self):
        self.prepare()
        self.prepare()
        self.assertEqual(self.new_patch, build._tracked_patch(self.source))

    def test_clean_source_after_interrupted_reverse_can_finish(self):
        self.file.write_text("base\n")
        self.prepare()
        self.assertEqual(self.new_patch, build._tracked_patch(self.source))

    def test_fresh_clean_source_without_manifest_can_install(self):
        self.file.write_text("base\n")
        (self.output / "pokemgr-build.json").unlink()
        for name in self.old_copies:
            (self.source / "app/src" / name).unlink()
        self.prepare()
        self.assertEqual(self.new_patch, build._tracked_patch(self.source))

    def test_extra_tracked_edit_is_preserved(self):
        (self.source / "keep.txt").write_text("user edit\n")
        before = build._tracked_patch(self.source)
        with self.assertRaisesRegex(RuntimeError, "tracked cache changes"):
            self.prepare()
        self.assertEqual(before, build._tracked_patch(self.source))

    def test_staged_edit_is_preserved(self):
        (self.source / "keep.txt").write_text("staged edit\n")
        self.git("add", "keep.txt")
        before = self.git("diff", "--cached")
        with self.assertRaisesRegex(RuntimeError, "staged changes"):
            self.prepare()
        self.assertEqual(before, self.git("diff", "--cached"))
        self.assertEqual("base\nold exporter patch\n", self.file.read_text())

    def test_modified_copied_source_and_unknown_new_destination_are_preserved(self):
        for name in ("pokemgr_frame_sink.c", "pokemgr_activity.m"):
            with self.subTest(name=name):
                destination = self.source / "app/src" / name
                original = destination.read_bytes() if destination.exists() else None
                destination.write_text("user content")
                with self.assertRaisesRegex(RuntimeError, "modified cached source"):
                    self.prepare()
                self.assertEqual(b"user content", destination.read_bytes())
                self.assertEqual(self.old_patch, build._tracked_patch(self.source))
                if original is None:
                    destination.unlink()
                else:
                    destination.write_bytes(original)

    def test_unproven_manifest_patch_hash_prevents_reverse(self):
        self.manifest["inputs"]["scrcpy-v4.1.patch"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "not proven"):
            self.prepare()
        self.assertEqual(self.old_patch, build._tracked_patch(self.source))

    def test_invalid_new_patch_fails_preflight_before_reverse(self):
        (self.package / "scrcpy-v4.1.patch").write_text("invalid patch")
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertEqual(self.old_patch, build._tracked_patch(self.source))

    def test_new_apply_failure_restores_only_saved_old_patch(self):
        original_git = build._git
        new_path = self.package / "scrcpy-v4.1.patch"

        def fail_new_apply(source, *arguments):
            if arguments == ("apply", new_path):
                raise subprocess.CalledProcessError(1, ["git", "apply"])
            return original_git(source, *arguments)

        with patch.object(build, "_git", side_effect=fail_new_apply):
            with self.assertRaisesRegex(RuntimeError, "previous managed patch was restored"):
                self.prepare()
        self.assertEqual(self.old_patch, build._tracked_patch(self.source))
        for name, content in self.old_copies.items():
            self.assertEqual(content, (self.source / "app/src" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
