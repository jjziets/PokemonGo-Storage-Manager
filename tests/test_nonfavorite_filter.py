"""Only the bounded keeper workflow may open a mutable favorite query."""

import unittest
from unittest.mock import Mock

from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.execution.executor import Executor


class NonfavoriteFilterTests(unittest.TestCase):
    def setUp(self):
        profile = CalibrationProfile("test", "serial", "968x2376", 420,
                                     ScreenRegions.default_for_resolution(968, 2376, density=420))
        self.executor = Executor(Mock(), profile, Mock())
        self.nav = self.executor.nav
        for name in ("navigate_to_storage", "enter_search", "tap_first_pokemon", "open_first_appraisal"):
            setattr(self.nav, name, Mock(return_value=True))
        self.nav.read_filtered_count_verified = Mock(return_value=2)

    def test_generic_actions_cannot_use_mutable_membership(self):
        for query in ("favorite", "!favorite", "cp20&!favorite", "shiny,favorite"):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, "favorite state"):
                self.executor._open_pass(query, verify_count=True)
        self.nav.navigate_to_storage.assert_not_called()

    def test_opt_in_requires_exact_negative_conjunction_and_verified_count(self):
        for query, verified in (("cp20", True), ("favorite", True), ("!favorite", False),
                                ("!favorite&favorite", True), ("!favorite&!favorite", True),
                                ("!favorite,cp20", True), ("!favorite|cp20", True)):
            with self.subTest(query=query, verified=verified), self.assertRaises(ValueError):
                self.executor._open_pass(query, verify_count=verified, nonfavorite=True)
        self.nav.navigate_to_storage.assert_not_called()

    def test_refresh_count_mismatch_holds_before_tile_input(self):
        with self.assertRaisesRegex(RuntimeError, "expected 1, saw 2"):
            self.executor._open_pass("cp20&!favorite", verify_count=True,
                                     nonfavorite=True, expected_count=1)
        self.nav.enter_search.assert_called_once_with("cp20&!favorite", verify=True)
        self.nav.tap_first_pokemon.assert_not_called()

    def test_final_verification_checks_count_without_opening_another_pokemon(self):
        self.assertEqual(2, self.executor._open_pass("cp20&!favorite", verify_count=True,
                         nonfavorite=True, expected_count=2, open_appraisal=False))
        self.nav.read_filtered_count_verified.assert_called_once()
        self.nav.tap_first_pokemon.assert_not_called()

    def test_expected_zero_is_verified_and_never_opens_a_tile(self):
        self.nav.read_filtered_count_verified.return_value = 0
        self.assertEqual(0, self.executor._open_pass("cp20&!favorite", verify_count=True,
                         nonfavorite=True, expected_count=0))
        self.nav.tap_first_pokemon.assert_not_called()
        self.nav.read_filtered_count_verified.return_value = None
        with self.assertRaisesRegex(RuntimeError, "not independently confirmed"):
            self.executor._open_pass("cp20&!favorite", verify_count=True,
                                      nonfavorite=True, expected_count=0)


if __name__ == "__main__":
    unittest.main()
