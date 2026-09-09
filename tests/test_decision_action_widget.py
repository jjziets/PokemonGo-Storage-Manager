"""Decisions action controls preserve accurate outcomes after worker cleanup."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pokemgr.gui.widgets.decision_review import DecisionReview


def at(seconds):
    return patch("pokemgr.gui.widgets.decision_review.time.monotonic", return_value=float(seconds))


class DecisionActionWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = DecisionReview()
        self.addCleanup(self.panel.deleteLater)
        with at(10):
            self.panel.set_favoriting(True)

    def action_buttons(self):
        return (self.panel.approve_btn, self.panel.favorite_real_btn,
                self.panel.run_engine_btn, self.panel.unfavorite_btn)

    def test_highest_cp_is_a_default_on_rule_alongside_iv_and_pvp_picks(self):
        rules = list(self.panel.rule_checks)
        self.assertEqual(rules[rules.index("KEEP_PERFECT_IV") + 1], "BEST_CP")
        highest_cp = self.panel.rule_checks["BEST_CP"]
        self.assertEqual(highest_cp.text(), "Highest CP")
        self.assertTrue(highest_cp.isChecked())
        self.assertIn("highest current CP per species/form", highest_cp.toolTip())
        self.assertIn("best IV and PvP picks", highest_cp.toolTip())
        enabled = self.panel.get_enabled_rules()
        self.assertTrue({"BEST_CP", "BEST_OVERALL", "BEST_PVP_GL", "BEST_PVP_UL"}.issubset(enabled))
        passes = self.panel.get_selected_fav_passes()
        highest_cp.setChecked(False)
        self.assertEqual(self.panel.get_enabled_rules(), [rule for rule in enabled if rule != "BEST_CP"])
        self.assertEqual(self.panel.get_selected_fav_passes(), passes)

    def test_all_perfect_ivs_is_default_on_and_can_be_disabled_independently(self):
        rules = list(self.panel.rule_checks)
        self.assertEqual(rules[rules.index("BEST_OVERALL") + 1], "KEEP_PERFECT_IV")
        perfect = self.panel.rule_checks["KEEP_PERFECT_IV"]
        self.assertEqual(perfect.text(), "All 100% IVs")
        self.assertTrue(perfect.isChecked())
        self.assertIn("every exact 15/15/15", perfect.toolTip())
        self.assertIn("including duplicates", perfect.toolTip())
        self.assertIn("Uncheck to disable", perfect.toolTip())
        enabled = self.panel.get_enabled_rules()
        self.assertIn("KEEP_PERFECT_IV", enabled)
        perfect.setChecked(False)
        self.assertEqual(self.panel.get_enabled_rules(),
                         [rule for rule in enabled if rule != "KEEP_PERFECT_IV"])
        self.assertTrue(self.panel.rule_checks["BEST_CP"].isChecked())
        self.assertTrue(self.panel.rule_checks["BEST_OVERALL"].isChecked())
        perfect.setChecked(True)
        self.assertEqual(self.panel.get_enabled_rules(), enabled)

    def test_pause_stop_waits_for_terminal_result_and_next_action_resets_controls(self):
        self.assertTrue(all(not button.isEnabled() for button in self.action_buttons()))
        with at(20):
            self.panel.set_fav_paused(True)
        self.assertEqual(self.panel.fav_pause_btn.text(), "Resume")
        self.assertEqual(self.panel.fav_status_label.text(), "Paused")
        self.panel.set_fav_stopping()
        self.assertEqual(self.panel.fav_status_label.text(), "Stopping...")
        self.assertFalse(self.panel.fav_pause_btn.isEnabled())
        self.assertFalse(self.panel.fav_stop_btn.isEnabled())
        self.panel.set_fav_paused(False)
        self.assertEqual(self.panel.fav_status_label.text(), "Stopping...")
        self.assertTrue(all(not button.isEnabled() for button in self.action_buttons()))
        self.panel.on_fav_finished({"checked": 4, "favorited": 2, "aborted": True})
        self.assertTrue(self.panel.fav_status_label.text().startswith("Stopped"))
        self.assertTrue(all(button.isEnabled() for button in self.action_buttons()))
        self.assertFalse(self.panel.fav_log.isHidden())
        self.assertTrue(self.panel.fav_pause_btn.isHidden())
        self.panel.set_favoriting(True, action="Unfavoriting")
        self.assertEqual(self.panel.fav_pause_btn.text(), "Pause")
        self.assertTrue(self.panel.fav_pause_btn.isEnabled())
        self.assertTrue(self.panel.fav_stop_btn.isEnabled())
        self.assertEqual(self.panel.fav_status_label.text(), "Unfavoriting (LIVE)...")
        self.assertEqual(self.panel.fav_progress_bar.format(), "Starting...")
        self.assertEqual(self.panel.fav_log.toPlainText(), "")

    def test_dry_run_counts_describe_intent_for_both_action_types(self):
        for action, key, expected in (("Favoriting", "favorited", "would favorite: 3"),
                                      ("Unfavoriting", "unfavorited", "would unfavorite: 3")):
            with self.subTest(action=action):
                self.panel.set_favoriting(True, dry_run=True, action=action)
                # The initiating dry-run mode survives even if a result omits the flag.
                self.panel.on_fav_finished({key: 3, "checked": 5, "aborted": False})
                status = self.panel.fav_status_label.text()
                self.assertTrue(status.startswith("Dry run complete"))
                self.assertIn(expected, status)
                self.assertIn("checked: 5", status)
                self.assertNotIn("aborted:", status)
                self.assertIn("#8cf", self.panel.fav_status_label.styleSheet())

    def test_explicit_would_count_and_result_dry_flag_are_supported(self):
        self.panel.on_fav_finished({"would_favorite": 3, "checked": 5, "dry_run": True})
        self.assertIn("Dry run complete", self.panel.fav_status_label.text())
        self.assertIn("would favorite: 3", self.panel.fav_status_label.text())
        self.assertNotIn("dry run:", self.panel.fav_status_label.text())

    def test_live_unfavorite_reports_its_actual_count(self):
        self.panel.set_favoriting(True, action="Unfavoriting")
        self.panel.on_fav_finished({"unfavorited": 7, "checked": 10, "skipped": 3})
        self.assertEqual(self.panel.fav_status_label.text(),
                         "Done — unfavorited: 7, checked: 10, skipped: 3")
        self.assertIn("#8f8", self.panel.fav_status_label.styleSheet())

    def test_error_precedes_abort_and_preserves_partial_counts_and_escaped_evidence(self):
        self.panel.on_fav_progress(3, 10, "Dragonite <nickname> & IVs")
        self.panel.on_fav_error("Device <lost>")
        self.assertTrue(all(not button.isEnabled() for button in self.action_buttons()))
        self.panel.on_fav_finished({"favorited": 2, "checked": 3, "error": "Device <lost>",
                                    "aborted": True, "note": "Review <these> & retry"})
        status = self.panel.fav_status_label.text()
        self.assertTrue(status.startswith("Failed — Device <lost>"))
        self.assertIn("favorited: 2", status)
        self.assertIn("Review <these> & retry", status)
        self.assertIn("#f88", self.panel.fav_status_label.styleSheet())
        self.assertEqual(self.panel.fav_status_label.textFormat(), Qt.PlainText)
        log = self.panel.fav_log.toPlainText()
        self.assertIn("Dragonite <nickname> & IVs", log)
        self.assertIn("ERROR: Device <lost>", log)
        self.assertIn("Review <these> & retry", log)
        self.assertFalse(self.panel.fav_log.isHidden())
        self.assertNotEqual(self.panel.fav_progress_bar.maximum(), 0)

    def test_unresolved_outcomes_are_amber_and_preserve_dry_run_wording(self):
        for key in ("unmatched", "ambiguous", "unresolved"):
            for dry in (False, True):
                with self.subTest(key=key, dry=dry):
                    self.panel.set_favoriting(True, dry_run=dry)
                    self.panel.on_fav_finished({"favorited": 2, key: 1, "checked": 3})
                    status = self.panel.fav_status_label.text()
                    self.assertIn("needs review", status)
                    self.assertIn(f"{key}: 1", status)
                    self.assertIn("#fc8", self.panel.fav_status_label.styleSheet())
                    if dry:
                        self.assertTrue(status.startswith("Dry run complete"))
                        self.assertIn("would favorite: 2", status)

    def test_dry_stop_and_dry_failure_do_not_claim_a_completed_live_action(self):
        for result, title in (({"aborted": True}, "Dry run stopped"),
                              ({"error": "Read held", "aborted": True}, "Dry run failed")):
            self.panel.set_favoriting(True, dry_run=True)
            self.panel.on_fav_finished({"favorited": 2, **result})
            self.assertTrue(self.panel.fav_status_label.text().startswith(title))
            self.assertIn("would favorite: 2", self.panel.fav_status_label.text())

    def test_prior_error_is_not_replaced_by_green_completion(self):
        self.panel.on_fav_error("Read failed")
        self.panel.on_fav_finished({"checked": 2})
        self.assertTrue(self.panel.fav_status_label.text().startswith("Completed with errors"))
        self.assertIn("#fc8", self.panel.fav_status_label.styleSheet())
        self.assertIn("Read failed", self.panel.fav_log.toPlainText())

    def test_empty_error_message_still_reports_failure(self):
        self.panel.on_fav_finished({"checked": 0, "error": ""})
        self.assertTrue(self.panel.fav_status_label.text().startswith("Failed"))
        self.assertIn("#f88", self.panel.fav_status_label.styleSheet())
        self.panel.set_favoriting(True)
        self.panel.on_fav_error("")
        self.panel.on_fav_finished({"checked": 0})
        self.assertTrue(self.panel.fav_status_label.text().startswith("Completed with errors"))

    def test_plain_ampersands_and_quotes_remain_literal_in_the_log(self):
        message = 'Search: !shiny&!shadow — "keeper"'
        self.panel.on_fav_progress(1, 10, message)
        self.panel.on_fav_finished({"note": "Read HP & IVs"})
        log = self.panel.fav_log.toPlainText()
        self.assertIn(message, log)
        self.assertIn("Read HP & IVs", log)
        self.assertNotIn("&amp;", log)
        self.assertNotIn("&quot;", log)

    def test_rate_excludes_pause_and_repeated_pause_calls_do_not_double_count(self):
        with at(20):
            self.panel.on_fav_progress(10, 100, "First pass")
        self.assertIn("60/min", self.panel.fav_progress_bar.format())
        with at(30):
            self.panel.set_fav_paused(True)
        with at(40):
            self.panel.set_fav_paused(True)
        with at(50):
            self.panel.set_fav_paused(False)
        with at(55):
            self.panel.set_fav_paused(False)
        with at(60):
            self.panel.on_fav_progress(20, 100, "Continued")
        self.assertIn("40/min", self.panel.fav_progress_bar.format())
        with at(70):
            self.panel.on_fav_progress(0, 0, "Pass: Shiny")
        self.assertEqual(self.panel.fav_progress_bar.maximum(), 0)
        with at(80):
            self.panel.on_fav_progress(2, 20, "Shiny progress")
        self.assertIn("12/min", self.panel.fav_progress_bar.format())

    def test_finished_log_rejects_late_progress_and_rule_pass_choices_are_unchanged(self):
        rules, passes = self.panel.get_enabled_rules(), self.panel.get_selected_fav_passes()
        self.panel.on_fav_finished({"checked": 2, "note": "Filter empty; no Pokemon opened"})
        log = self.panel.fav_log.toPlainText()
        status = self.panel.fav_status_label.text()
        self.panel.on_fav_progress(99, 100, "Late old callback")
        self.panel.on_fav_error("Late old error")
        self.assertEqual(self.panel.fav_log.toPlainText(), log)
        self.assertEqual(self.panel.fav_status_label.text(), status)
        self.assertEqual(self.panel.get_enabled_rules(), rules)
        self.assertEqual(self.panel.get_selected_fav_passes(), passes)
        self.assertIn("stored scan stats", self.panel.run_engine_btn.toolTip())
        self.assertIn("Nicknames do not determine matches", self.panel.favorite_real_btn.toolTip())


if __name__ == "__main__":
    unittest.main()
