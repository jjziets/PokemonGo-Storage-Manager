import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from pokemgr.adb.controller import ADBError
from pokemgr.adb import search_text


NONCE = "1234567890abcdef1234567890abcdef"
QUERY = "!shiny&!shadow&!dynamax&!gigantamax&!lucky"


def report(text=QUERY):
    return {
        "schema": 1, "nonce": NONCE, "display": 31,
        "package": search_text.PACKAGE, "status": "ok",
        "window_id": 5138, "window_focused": True, "window_active": True,
        "editors": [{"text": text, "package": search_text.PACKAGE,
                     "class": "android.widget.EditText", "focused": True,
                     "editable": True, "visible": True, "password": False,
                     "showing_hint": False, "hint_text": None,
                     "selection_start": 0, "selection_end": 0}],
    }


def encoded(data):
    return (search_text.PREFIX + json.dumps(data) + "\n").encode()


def completed(stdout=b"", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, b"")


class SearchTextParserTests(unittest.TestCase):
    def parse(self, data):
        return search_text._parse_text(encoded(data), nonce=NONCE, display_id=31)

    def test_full_query_preserved_and_empty_distinct_from_unavailable(self):
        for value in (QUERY, "", "a b&!c,d", "éволюция", " " * 3):
            with self.subTest(value=value):
                self.assertEqual(self.parse(report(value)), value)
        self.assertIsNone(self.parse({**report(), "status": "unavailable"}))

    def test_rejects_wrong_operation_or_target(self):
        for key, value in (("nonce", "f" * 32), ("display", 0), ("display", True),
                           ("package", "com.android.inputmethod"), ("schema", True),
                           ("window_id", -1), ("window_id", True),
                           ("window_focused", False), ("window_active", False)):
            with self.subTest(key=key, value=value):
                self.assertIsNone(self.parse({**report(), key: value}))

    def test_rejects_wrong_editor_and_ambiguous_editors(self):
        for key, value in (("package", "another.app"), ("focused", False),
                           ("editable", False), ("visible", False), ("password", True),
                           ("class", "android.view.SurfaceView"), ("showing_hint", None),
                           ("selection_start", -1), ("selection_start", False),
                           ("text", 42), ("text", "x" * 513)):
            with self.subTest(key=key, value=value):
                data = report()
                data["editors"][0][key] = value
                self.assertIsNone(self.parse(data))
        for editors in ([], [report()["editors"][0]] * 2, [None], {}):
            self.assertIsNone(self.parse({**report(), "editors": editors}))

    def test_placeholder_is_confirmed_empty_not_actual_query(self):
        data = report("Search Pokémon")
        data["editors"][0].update(showing_hint=True, hint_text="Search Pokémon")
        self.assertEqual(self.parse(data), "")
        data["editors"][0]["selection_end"] = 1
        self.assertIsNone(self.parse(data))

    def test_null_text_requires_empty_selection_and_no_hint(self):
        data = report(None)
        self.assertEqual(self.parse(data), "")
        data["editors"][0]["selection_start"] = 1
        self.assertIsNone(self.parse(data))
        data["editors"][0].update(selection_start=0, hint_text="Search")
        self.assertIsNone(self.parse(data))

    def test_live_literal_empty_with_unexposed_selection_is_confirmed_empty(self):
        data = report("")
        data["editors"][0].update(hint_text="", selection_start=-1, selection_end=-1)
        self.assertEqual(self.parse(data), "")
        data["editors"][0]["text"] = None
        self.assertIsNone(self.parse(data))
        data["editors"][0].update(text="", showing_hint=True)
        self.assertIsNone(self.parse(data))

    def test_hidden_hint_ambiguity_does_not_authorize_query(self):
        data = report("Search")
        data["editors"][0]["hint_text"] = "Search"
        self.assertIsNone(self.parse(data))

    def test_rejects_missing_duplicate_malformed_and_excess_output(self):
        valid = encoded(report())
        duplicate_key = valid.replace(b'"schema": 1', b'"schema": 1, "schema": 1')
        for raw in (b"", valid + valid, duplicate_key, b"\xff", b"x" * 16385,
                    b"POKEMGR_SEARCH_V1 []\n", b"POKEMGR_SEARCH_V1 {\n"):
            with self.subTest(raw=raw[:40]):
                self.assertIsNone(search_text._parse_text(raw, nonce=NONCE, display_id=31))


class SearchTextObservationTests(unittest.TestCase):
    def setUp(self):
        self.adb = Mock(serial="serial", display_id=31, _stream_generation=5)
        self.adb._run.return_value = completed(encoded(report()))
        self.prepare = patch.object(search_text, "_prepare_helper", return_value="/data/local/tmp/helper.jar")
        self.prepare.start()
        self.addCleanup(self.prepare.stop)
        nonce = patch.object(search_text.uuid, "uuid4", return_value=Mock(hex=NONCE))
        nonce.start()
        self.addCleanup(nonce.stop)

    def test_reads_exact_text_with_bounded_command_and_validation(self):
        self.assertEqual(search_text.read_search_text(self.adb), QUERY)
        self.assertEqual(self.adb.validate_display_target.call_count, 2)
        command = self.adb._run.call_args.args[0]
        self.assertEqual(command[0], "shell")
        self.assertIn(f"SearchText 31 {NONCE}", command[1])
        self.assertEqual(self.adb._run.call_args.kwargs["timeout"], 8)

    def test_generation_change_during_final_validation_discards_text(self):
        calls = [0]
        def validate():
            calls[0] += 1
            if calls[0] == 2:
                self.adb._stream_generation += 1
        self.adb.validate_display_target.side_effect = validate
        self.assertIsNone(search_text.read_search_text(self.adb))

    def test_generation_change_during_remote_read_discards_text(self):
        def run(*args, **kwargs):
            self.adb._stream_generation += 1
            return completed(encoded(report()))
        self.adb._run.side_effect = run
        self.assertIsNone(search_text.read_search_text(self.adb))

    def test_final_target_failure_propagates_even_after_good_read(self):
        self.adb.validate_display_target.side_effect = [None, ADBError("display replaced")]
        with self.assertRaisesRegex(ADBError, "display replaced"):
            search_text.read_search_text(self.adb)

    def test_physical_target_identity_is_also_frozen(self):
        self.adb.display_id = None
        data = report()
        data["display"] = 0
        def run(*args, **kwargs):
            self.adb.serial = "another-serial"
            return completed(encoded(data))
        self.adb._run.side_effect = run
        with self.assertRaisesRegex(ADBError, "device/display changed"):
            search_text.read_search_text(self.adb)

    def test_unbound_transport_is_not_used(self):
        self.adb.serial = None
        self.assertIsNone(search_text.read_search_text(self.adb))
        self.adb._run.assert_not_called()

    def test_timeout_is_unavailable_and_still_validates_target(self):
        self.adb._run.side_effect = ADBError("timeout")
        self.assertIsNone(search_text.read_search_text(self.adb))
        self.assertEqual(self.adb.validate_display_target.call_count, 2)


class SearchTextDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.jar = Path("/local/search.jar")
        self.digest = "a" * 64
        self.remote = f"/data/local/tmp/pokemgr-search-{self.digest}.jar"
        self.adb = Mock(serial="serial", display_id=31)
        del self.adb._search_helper_identity
        local = patch.object(search_text, "_local_jar", return_value=(self.jar, self.digest))
        local.start()
        self.addCleanup(local.stop)

    def test_deploys_only_verified_content_and_reuses_controller_binding(self):
        self.adb._run.side_effect = [completed(), completed(), completed(f"{self.digest}  {self.remote}\n".encode())]
        self.assertEqual(search_text._prepare_helper(self.adb), self.remote)
        self.assertEqual(search_text._prepare_helper(self.adb), self.remote)
        self.assertEqual(self.adb._run.call_count, 3)
        self.assertEqual(self.adb._run.call_args_list[1].args[0], ["push", str(self.jar), self.remote])

    def test_reuses_matching_remote_hash_without_writing(self):
        self.adb._run.return_value = completed(f"{self.digest}  {self.remote}\n".encode())
        self.assertEqual(search_text._prepare_helper(self.adb), self.remote)
        self.assertEqual(self.adb._run.call_count, 1)

    def test_mismatched_existing_helper_is_not_overwritten(self):
        self.adb._run.return_value = completed(f"{'b' * 64}  {self.remote}\n".encode())
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            search_text._prepare_helper(self.adb)
        self.assertEqual(self.adb._run.call_count, 1)

    def test_corrupted_push_is_not_cached(self):
        self.adb._run.side_effect = [completed(), completed(), completed(b"wrong")]
        with self.assertRaisesRegex(RuntimeError, "deployment could not"):
            search_text._prepare_helper(self.adb)
        self.assertFalse(hasattr(self.adb, "_search_helper_identity"))


if __name__ == "__main__":
    unittest.main()
