"""An empty partition is proven by repeated explicit storage-count evidence."""

# TRACEWEAVER: file-role=verified-storage-count-tests; req=REQ-SCAN-003; trace=TRACE-SCAN-006; verifies=VER-SCAN-001
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.adb.controller import ADBError
from pokemgr.adb.navigator import GameNavigator
from tests.test_stable_scan_loop import profile


def capture(sequence):
    image = Image.new("RGB", (968, 2376), "white")
    image.info.update(pokemgr_capture_started_at=float(sequence),
                      pokemgr_capture_finished_at=sequence + .1)
    return image


# TRACEWEAVER: verifies=VER-SCAN-001; req=REQ-SCAN-003; trace=TRACE-SCAN-006
class VerifiedFilteredCountTests(unittest.TestCase):
    def setUp(self):
        self.adb = Mock()
        self.cancelled = False
        self.nav = GameNavigator(self.adb, profile().regions, cancelled=lambda: self.cancelled)
        self.nav.detect_screen = Mock(return_value="storage")
        self.ocr = self.enterContext(patch("pytesseract.image_to_string"))
        self.sleep = self.enterContext(patch("pokemgr.adb.navigator.time.sleep"))

    def observations(self, texts, frames=None):
        self.ocr.reset_mock()
        observations = texts + [texts[-1]] * (4 - len(texts))
        self.ocr.side_effect = [
            text for observation in observations
            for text in (observation if isinstance(observation, tuple) else (observation,))
        ]
        self.adb.screencap.reset_mock()
        self.adb.screencap.side_effect = frames or [capture(i) for i in range(1, 5)]

    def read(self):
        result = self.nav.read_filtered_count_verified()
        self.adb.tap.assert_not_called()
        self.adb.swipe.assert_not_called()
        self.adb.key_event.assert_not_called()
        self.adb.input_text.assert_not_called()
        self.adb.shell.assert_not_called()
        return result

    def test_positive_counts_and_explicit_zero_headers_require_two_captures(self):
        cases = (("Q(2622)", 2622), ("(12)", 12), ("2947/3250", 2947),
                 ("123", 123), ("Q(0)", 0), ("(0)", 0), ("Q ( 0 )", 0), ("0/3500", 0))
        for text, expected in cases:
            with self.subTest(text=text):
                self.observations([text, text])
                self.assertEqual(expected, self.read())
                self.assertEqual(2, self.adb.screencap.call_count)
                self.assertEqual(2, self.ocr.call_count)

    def test_bare_zero_clipped_conflicting_or_impossible_headers_are_unknown(self):
        for text in ("0", "Q0", "Q(0", "0)", "Q(0) 12", "10001", "Q(10001)",
                     "3501/3500", "0/0", "", "word 0", "-1", "1.0", "(2)(2)"):
            with self.subTest(text=text):
                self.observations([(text, text)] * 4)
                self.assertIsNone(self.read())
                self.assertEqual(4, self.adb.screencap.call_count)

    def test_one_explicit_zero_is_not_enough_and_noise_breaks_consecutive_agreement(self):
        self.observations([("", ""), ("", ""), ("", ""), "Q(0)"])
        self.assertIsNone(self.read())
        self.observations(["Q(0)", ("", ""), "Q(0)", "Q(0)"])
        self.assertEqual(0, self.read())
        self.assertEqual(4, self.adb.screencap.call_count)

    def test_disagreeing_valid_counts_hold_instead_of_selecting_one(self):
        for texts in (["Q(0)", "Q(2)"], ["Q(2)", "Q(0)"], ["Q(12)", "Q(13)"],
                      ["Q(0)", ("", ""), "Q(2)", "Q(2)"]):
            with self.subTest(texts=texts):
                self.observations(texts)
                self.assertIsNone(self.read())

    def test_every_fresh_capture_revalidates_storage_before_ocr(self):
        for screens, expected_ocr in ((["game_map"], 0), (["storage", "detail"], 1),
                                      (["storage", "game_map"], 1)):
            with self.subTest(screens=screens):
                self.observations(["Q(0)", "Q(0)"])
                self.nav.detect_screen.reset_mock()
                self.nav.detect_screen.side_effect = screens
                self.assertIsNone(self.read())
                self.assertEqual(expected_ocr, self.ocr.call_count)
                self.assertEqual(len(screens), self.nav.detect_screen.call_count)

    def test_retry_after_unreadable_text_cannot_ocr_a_lost_storage_screen(self):
        self.observations([("", ""), "Q(0)", "Q(0)"])
        self.nav.detect_screen.side_effect = ["storage", "game_map", "storage"]
        self.assertIsNone(self.read())
        self.assertEqual(2, self.ocr.call_count)
        self.assertEqual(2, self.adb.screencap.call_count)

    def test_lost_capacity_slash_uses_normal_polarity_on_two_independent_frames(self):
        self.observations([("32463250", "3246/3250")] * 2)
        self.assertEqual(3246, self.read())
        self.assertEqual(2, self.adb.screencap.call_count)
        self.assertEqual(2, self.nav.detect_screen.call_count)
        self.assertEqual(4, self.ocr.call_count)
        for primary, fallback in zip(self.ocr.call_args_list[::2], self.ocr.call_args_list[1::2]):
            self.assertEqual(primary.kwargs, fallback.kwargs)
            self.assertTrue((fallback.args[0] == 255 - primary.args[0]).all())

    def test_fallback_never_invents_slash_or_accepts_invalid_capacity(self):
        for fallback in ("32463250", "3246 3250", "3246/", "3251/3250", "0/0", "0",
                         "3246", "3250"):
            with self.subTest(fallback=fallback):
                self.observations([("32463250", fallback)] * 4)
                self.assertIsNone(self.read())
                self.assertEqual(4, self.adb.screencap.call_count)
                self.assertEqual(8, self.ocr.call_count)

    def test_fallback_accepts_only_complete_parenthesized_or_capacity_headers(self):
        for fallback, expected in (("Q(12)", 12), ("(0)", 0), ("0/3250", 0)):
            with self.subTest(fallback=fallback):
                self.observations([("", fallback)] * 2)
                self.assertEqual(expected, self.read())
                self.assertEqual(2, self.adb.screencap.call_count)

    def test_fallback_preserves_lighter_strokes_lost_by_primary_threshold(self):
        frames = [capture(1), capture(2)]
        for image in frames:
            image.paste((150, 150, 150), (400, 155, 410, 185))
        self.observations([("32463250", "3246/3250")] * 2, frames)
        self.assertEqual(3246, self.read())
        primary, fallback = (call.args[0] for call in self.ocr.call_args_list[:2])
        self.assertEqual(0, primary[100, 525])
        self.assertEqual(0, primary[0, 0])
        self.assertEqual(0, fallback[100, 525])
        self.assertEqual(255, fallback[0, 0])

    def test_fallback_cannot_confirm_from_one_frame_or_copied_evidence(self):
        self.observations([("", "")] * 3 + [("32463250", "3246/3250")])
        self.assertIsNone(self.read())
        first = capture(1)
        self.observations([("32463250", "3246/3250")] * 2, [first, first.copy()])
        self.assertIsNone(self.read())

    def test_fallback_counts_must_agree_with_other_frame_primary_or_fallback(self):
        for texts in (["3245/3250", ("32463250", "3246/3250")],
                      [("32463250", "3246/3250"), ("32473250", "3247/3250")]):
            with self.subTest(texts=texts):
                self.observations(texts)
                self.assertIsNone(self.read())
                self.assertEqual(2, self.adb.screencap.call_count)

    def test_cancellation_before_or_during_fallback_prevents_verified_count(self):
        for cancelled_call in (1, 2, 4):
            with self.subTest(cancelled_call=cancelled_call):
                self.cancelled = False
                self.observations([("32463250", "3246/3250")] * 2)
                calls = 0

                def recognize(*_args, **_kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == cancelled_call:
                        self.cancelled = True
                    return "32463250" if calls % 2 else "3246/3250"

                self.ocr.side_effect = recognize
                self.assertIsNone(self.read())
                self.assertEqual(cancelled_call, self.ocr.call_count)

    def test_fallback_ocr_failure_is_unknown(self):
        self.observations([("32463250", "3246/3250")] * 2)
        self.ocr.side_effect = ["32463250", OSError("fallback OCR unavailable")]
        self.assertIsNone(self.read())
        self.assertEqual(1, self.adb.screencap.call_count)
        self.assertEqual(2, self.ocr.call_count)

    def test_cancellation_at_each_external_read_boundary_returns_unknown(self):
        for stage in ("before", "capture", "screen", "ocr", "wait"):
            with self.subTest(stage=stage):
                self.cancelled = False
                self.sleep.side_effect = None
                self.nav.detect_screen.side_effect = None
                self.observations(["Q(0)", "Q(0)"])

                def cancel_and_return(value):
                    self.cancelled = True
                    return value

                if stage == "before":
                    self.cancelled = True
                elif stage == "capture":
                    self.adb.screencap.side_effect = lambda: cancel_and_return(capture(1))
                elif stage == "screen":
                    self.nav.detect_screen.side_effect = lambda _image: cancel_and_return("storage")
                elif stage == "ocr":
                    self.ocr.side_effect = lambda *_args, **_kwargs: cancel_and_return("Q(0)")
                else:
                    self.sleep.side_effect = lambda _seconds: cancel_and_return(None)
                self.assertIsNone(self.read())
                self.assertLessEqual(self.adb.screencap.call_count, 1)

    def test_cancel_during_second_ocr_cannot_return_verified_zero(self):
        self.observations(["Q(0)", "Q(0)"])
        calls = 0

        def recognize(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.cancelled = True
            return "Q(0)"

        self.ocr.side_effect = recognize
        self.assertIsNone(self.read())
        self.assertEqual(2, self.ocr.call_count)

    def test_failed_transport_or_ocr_is_unknown_not_empty(self):
        self.observations(["Q(0)", "Q(0)"])
        self.adb.screencap.side_effect = ADBError("display unavailable")
        self.assertIsNone(self.read())
        self.ocr.assert_not_called()
        self.observations(["Q(0)", "Q(0)"])
        self.ocr.side_effect = OSError("OCR unavailable")
        self.assertIsNone(self.read())

    def test_stale_copied_or_unordered_capture_evidence_cannot_confirm_zero(self):
        for fault in ("same_object", "copy", "missing", "backwards", "overlap", "nan", "infinite", "boolean"):
            with self.subTest(fault=fault):
                first, second = capture(1), capture(2)
                if fault == "same_object":
                    second = first
                elif fault == "copy":
                    second = first.copy()
                elif fault == "missing":
                    second.info.pop("pokemgr_capture_started_at")
                elif fault == "backwards":
                    second.info["pokemgr_capture_finished_at"] = 1.0
                elif fault == "overlap":
                    second.info["pokemgr_capture_started_at"] = 1.1
                elif fault == "nan":
                    second.info["pokemgr_capture_started_at"] = float("nan")
                elif fault == "infinite":
                    second.info["pokemgr_capture_finished_at"] = float("inf")
                else:
                    second.info["pokemgr_capture_started_at"] = True
                self.observations(["Q(0)", "Q(0)"], [first, second])
                self.assertIsNone(self.read())

    def test_stream_confirmation_requires_consistent_independent_source_metadata(self):
        for fault in (None, "duplicate_pts", "sequence", "session", "clock", "missing"):
            with self.subTest(fault=fault):
                first, second = capture(1), capture(2)
                for index, image in enumerate((first, second), 1):
                    image.info.update(pokemgr_stream_session="session", pokemgr_stream_sequence=index,
                                      pokemgr_stream_pts_us=index * 1000, pokemgr_source_clock_generation=1)
                if fault == "duplicate_pts":
                    second.info["pokemgr_stream_pts_us"] = 1000
                elif fault == "sequence":
                    second.info["pokemgr_stream_sequence"] = 1
                elif fault == "session":
                    second.info["pokemgr_stream_session"] = "other"
                elif fault == "clock":
                    second.info["pokemgr_source_clock_generation"] = 2
                elif fault == "missing":
                    second.info.pop("pokemgr_stream_pts_us")
                self.observations(["Q(0)", "Q(0)"], [first, second])
                self.assertEqual(0 if fault is None else None, self.read())

    def test_compatible_clock_refresh_can_confirm_but_invalidation_cannot(self):
        for fault in (None, "changed_token", "missing_token", "invalid_token", "backward_clock"):
            with self.subTest(fault=fault):
                first, second = capture(1), capture(2)
                for index, image in enumerate((first, second), 1):
                    image.info.update(pokemgr_stream_session="session", pokemgr_stream_sequence=index,
                                      pokemgr_stream_pts_us=index * 1000, pokemgr_source_clock_generation=index + 1,
                                      pokemgr_source_clock_continuity="a" * 32)
                if fault == "changed_token":
                    second.info["pokemgr_source_clock_continuity"] = "b" * 32
                elif fault == "missing_token":
                    second.info.pop("pokemgr_source_clock_continuity")
                elif fault == "invalid_token":
                    second.info["pokemgr_source_clock_continuity"] = "invalid"
                elif fault == "backward_clock":
                    second.info["pokemgr_source_clock_generation"] = 1
                self.observations(["Q(0)", "Q(0)"], [first, second])
                self.assertEqual(0 if fault is None else None, self.read())

    def test_full_count_glyph_height_is_preserved_for_each_ocr_read(self):
        self.observations(["Q(2622)", "Q(2622)"])
        self.assertEqual(2622, self.read())
        for call in self.ocr.call_args_list:
            self.assertEqual((250, 1850), call.args[0].shape)


if __name__ == "__main__":
    unittest.main()
