import unittest

from pokemgr.calibration.regions import ScreenRegions, is_tablet_layout


class ScreenRegionsTests(unittest.TestCase):
    def test_tablet_uses_tablet_navigation_and_appraisal_anchors(self):
        regions = ScreenRegions.default_for_resolution(1440, 2304)

        self.assertEqual((240, 760), regions.storage_first_item)
        self.assertEqual((720, 405), regions.storage_search_bar)
        self.assertEqual((720, 2100), regions.map_pokeball)
        self.assertEqual((320, 1775), regions.map_pokemon_button)
        self.assertEqual((1270, 2100), regions.menu_button)
        self.assertEqual((980, 1650), regions.appraise_menu_item)
        self.assertEqual(1635, regions.atk_bar_region.y)
        self.assertEqual(1758, regions.def_bar_region.y)
        self.assertEqual(1882, regions.sta_bar_region.y)

    def test_fold6_keeps_original_reference_anchors(self):
        regions = ScreenRegions.default_for_resolution(968, 2376)

        self.assertEqual((160, 550), regions.storage_first_item)
        self.assertEqual((484, 300), regions.storage_search_bar)
        self.assertEqual((484, 2280), regions.map_pokeball)
        self.assertEqual((910, 2260), regions.menu_button)
        self.assertEqual((580, 1870), regions.appraise_menu_item)
        self.assertEqual((920, 1750), regions.close_appraisal_target)
        self.assertEqual(300, regions.swipe_duration_ms)

    def test_high_density_16_by_9_phone_is_not_a_tablet(self):
        self.assertFalse(is_tablet_layout(1080, 1920, density=420))
        regions = ScreenRegions.default_for_resolution(1080, 1920, density=420)

        self.assertEqual((178, 444), regions.storage_first_item)
        self.assertNotEqual((180, 633), regions.storage_first_item)

    def test_tablet_density_uses_wide_layout(self):
        self.assertTrue(is_tablet_layout(1440, 2304, density=280))

    def test_optional_navigation_targets_round_trip(self):
        regions = ScreenRegions.default_for_resolution(1440, 2304)
        restored = ScreenRegions.from_dict(regions.to_dict())

        self.assertEqual(regions.storage_first_item, restored.storage_first_item)
        self.assertEqual(regions.storage_search_clear, restored.storage_search_clear)


if __name__ == "__main__":
    unittest.main()
