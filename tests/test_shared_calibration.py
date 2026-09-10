"""Compatible virtual canvases share coordinates, never device verification."""

# TRACEWEAVER: file-role=shared-calibration-tests; req=REQ-SCAN-002,REQ-STREAM-001; trace=TRACE-SCAN-002,TRACE-STREAM-001; verifies=VER-SCAN-001

import copy
import json
from dataclasses import fields, replace
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pokemgr.adb.device import DeviceInfo
from pokemgr.calibration.evidence import confirm_calibration_evidence, write_calibration_evidence
from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import BBox, ScreenRegions


class SharedCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.info = DeviceInfo("NewTablet", "new-device", 968, 2376, 420)

    def donor(self, *, model="Phone", serial="donor", target=None, x=123,
              duration=777, verified=False):
        info = target or replace(self.info, model=model, serial=serial)
        profile = CalibrationProfile.create_default(info)
        profile.regions.menu_button = (x, 2200)
        profile.regions.swipe_duration_ms = duration
        profile.calibrated_at = "2025-01-01T00:00:00+00:00"
        profile.metadata["operator_note"] = "belongs to donor"
        if verified:
            profile.mark_verified("donor-only-manifest.json", [{"species": "A"}, {"species": "B"}])
        return profile

    def save(self, profile, *, legacy=False):
        path = profile.save(self.root)
        if legacy:
            legacy_path = self.root / f"{profile.legacy_fingerprint}.json"
            path.rename(legacy_path)
            return legacy_path
        return path

    def test_same_device_profiles_are_density_qualified_and_remain_separate(self):
        first = self.donor(target=self.info, x=100, duration=321)
        other_info = replace(self.info, density=480)
        second = self.donor(target=other_info, x=200, duration=654)
        paths = self.save(first), self.save(second)
        self.assertNotEqual(*paths)
        self.assertTrue(paths[0].name.endswith("_968x2376_dpi420.json"))
        self.assertTrue(paths[1].name.endswith("_968x2376_dpi480.json"))
        for info, x, duration in ((self.info, 100, 321), (other_info, 200, 654)):
            found = CalibrationProfile.find_for_device(info, self.root)
            self.assertEqual((x, 2200), found.regions.menu_button)
            self.assertEqual(duration, found.regions.swipe_duration_ms)

    def test_exact_device_profile_wins_over_a_verified_donor(self):
        own = self.donor(target=self.info, x=50, duration=456)
        self.save(own)
        self.save(self.donor(x=200, verified=True))
        found = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(own.regions.to_dict(), found.regions.to_dict())
        self.assertEqual(own.metadata, found.metadata)

    def test_exact_legacy_profile_preserves_overrides_and_verification_read_only(self):
        own = self.donor(target=self.info, x=50, duration=456, verified=True)
        path = self.save(own, legacy=True)
        original = path.read_bytes()
        self.save(self.donor(x=200, verified=True))
        found = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(own.regions.to_dict(), found.regions.to_dict())
        self.assertEqual(own.metadata, found.metadata)
        self.assertEqual(own.validation_results, found.validation_results)
        self.assertEqual(original, path.read_bytes())
        self.assertFalse(CalibrationProfile.path_for_device(self.info, self.root).exists())

    def test_density_qualified_device_override_supersedes_its_legacy_file(self):
        legacy = self.donor(target=self.info, x=50, duration=456)
        path = self.save(legacy, legacy=True)
        original = path.read_bytes()
        new = self.donor(target=self.info, x=70, duration=321)
        self.save(new)
        found = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(new.regions.to_dict(), found.regions.to_dict())
        self.assertEqual(original, path.read_bytes())

    def test_legacy_fallback_requires_all_device_geometry_fields_to_match(self):
        profile = self.donor(target=self.info)
        path = self.save(profile, legacy=True)
        original = json.loads(path.read_text())
        changes = (
            {"device_model": "Wrong"}, {"serial": "Wrong"}, {"resolution": "1440x2304"},
            {"density": 480}, {"density": 420.0}, {"layout": "tablet"},
            {"regions": original["regions"] | {"screen_width": 1440}},
            {"regions": original["regions"] | {"screen_height": 2304}},
        )
        for change in changes:
            with self.subTest(change=change):
                payload = copy.deepcopy(original) | change
                path.write_text(json.dumps(payload))
                unchanged = path.read_bytes()
                self.assertIsNone(CalibrationProfile.find_for_device(self.info, self.root))
                self.assertEqual(unchanged, path.read_bytes())

    def test_mismatching_qualified_override_holds_without_borrowing_or_overwriting(self):
        path = self.save(self.donor(target=self.info))
        payload = json.loads(path.read_text())
        payload["density"] = 480
        path.write_text(json.dumps(payload))
        original = path.read_bytes()
        self.save(self.donor(verified=True))
        with self.assertRaisesRegex(ValueError, "does not match device geometry"):
            CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(original, path.read_bytes())

    def test_new_device_borrows_coordinates_without_timing_verification_or_writes(self):
        donor = self.donor(verified=True)
        path = self.save(donor)
        before = {file.name: file.read_bytes() for file in self.root.iterdir()}
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        expected = donor.regions.to_dict()
        expected["swipe_duration_ms"] = CalibrationProfile.create_default(self.info).regions.swipe_duration_ms
        self.assertEqual(expected, shared.regions.to_dict())
        self.assertEqual((self.info.model, self.info.serial, self.info.resolution, self.info.density, "phone"),
                         (shared.device_model, shared.serial, shared.resolution, shared.density, shared.layout))
        self.assertEqual([], shared.validation_results)
        self.assertEqual("unverified", shared.metadata["verification_status"])
        self.assertEqual("shared_profile", shared.metadata["coordinate_source"])
        self.assertEqual(donor.fingerprint, shared.metadata["shared_from_fingerprint"])
        self.assertEqual(path.name, shared.metadata["shared_from_profile"])
        for field in ("verified_at", "verification_manifest", "operator_note"):
            self.assertNotIn(field, shared.metadata)
        self.assertNotEqual(donor.calibrated_at, shared.calibrated_at)
        self.assertEqual(before, {file.name: file.read_bytes() for file in self.root.iterdir()})
        shared.regions.cp_region.x += 1
        self.assertEqual(before[path.name], path.read_bytes())
        self.assertNotEqual(donor.regions.cp_region.x, shared.regions.cp_region.x)

    def test_explicit_save_of_shared_profile_preserves_future_device_overrides(self):
        self.save(self.donor(verified=True))
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        shared.regions.swipe_duration_ms = 333
        shared.regions.menu_button = (150, 2200)
        path = shared.save(self.root)
        found = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(path, found.path(self.root))
        self.assertEqual(shared.regions.to_dict(), found.regions.to_dict())
        self.assertEqual("unverified", found.metadata["verification_status"])

    def test_verified_donor_tier_wins_over_conflicting_unverified_coordinates(self):
        preferred = self.donor(serial="verified", x=80, verified=True)
        self.save(preferred)
        self.save(self.donor(serial="unverified", x=200))
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(preferred.regions.menu_button, shared.regions.menu_button)

    def test_conflicting_donors_in_preferred_tier_do_not_infer_coordinates(self):
        for verified in (False, True):
            with self.subTest(verified=verified), tempfile.TemporaryDirectory() as directory:
                self.donor(serial="a", x=80, verified=verified).save(directory)
                self.donor(serial="b", x=200, verified=verified).save(directory)
                if verified:
                    self.donor(serial="weaker", x=80).save(directory)
                self.assertIsNone(CalibrationProfile.find_for_device(self.info, directory))
                default = CalibrationProfile.create_default(self.info)
                self.assertEqual("unverified", default.metadata["verification_status"])
                self.assertEqual("template", default.metadata["coordinate_source"])

    def test_agreeing_donors_ignore_timing_and_choose_provenance_deterministically(self):
        first = self.donor(serial="a", duration=100)
        second = self.donor(serial="z", duration=999)
        self.save(second)
        self.save(first)
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(first.fingerprint, shared.metadata["shared_from_fingerprint"])
        self.assertEqual(CalibrationProfile.create_default(self.info).regions.swipe_duration_ms,
                         shared.regions.swipe_duration_ms)

    def test_qualified_donor_supersedes_its_conflicting_legacy_coordinates(self):
        self.save(self.donor(x=200, verified=True), legacy=True)
        self.save(self.donor(x=80, verified=True))
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual((80, 2200), shared.regions.menu_button)

    def test_other_resolution_density_or_layout_cannot_be_shared(self):
        for change in ({"resolution": "1440x2304"}, {"density": 480}, {"layout": "tablet"}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                path = self.donor().save(directory)
                payload = json.loads(path.read_text()) | change
                path.write_text(json.dumps(payload))
                self.assertIsNone(CalibrationProfile.find_for_device(self.info, directory))

    def test_unreadable_future_and_misnamed_files_are_not_shared(self):
        (self.root / "broken.json").write_text("{")
        future = self.save(self.donor(serial="future"))
        payload = json.loads(future.read_text()) | {"schema_version": 999}
        future.write_text(json.dumps(payload))
        misnamed = self.save(self.donor(serial="other"))
        misnamed.rename(self.root / "unrelated.json")
        self.assertIsNone(CalibrationProfile.find_for_device(self.info, self.root))
        self.save(self.donor(serial="valid"))
        self.assertIsNotNone(CalibrationProfile.find_for_device(self.info, self.root))

    def test_shared_rectangles_require_integer_positive_dimensions_inside_canvas(self):
        path = self.save(self.donor(verified=True))
        original = json.loads(path.read_text())
        rectangle_fields = [item.name for item in fields(ScreenRegions) if item.type is BBox]
        invalid_parts = (
            {"x": -1}, {"y": -1}, {"w": 0}, {"h": 0}, {"w": -1}, {"h": -1},
            {"x": 2000}, {"y": 2376}, {"w": 969}, {"h": 2377},
            {"x": 1.0}, {"y": True}, {"w": "20"}, {"h": False},
        )
        for name in rectangle_fields:
            for change in invalid_parts:
                with self.subTest(field=name, change=change):
                    payload = copy.deepcopy(original)
                    payload["regions"][name].update(change)
                    path.write_text(json.dumps(payload))
                    before = path.read_bytes()
                    self.assertIsNone(CalibrationProfile.find_for_device(self.info, self.root))
                    self.assertEqual(before, path.read_bytes())

    def test_shared_points_require_exactly_two_integer_pixels_inside_canvas(self):
        path = self.save(self.donor(verified=True))
        original = json.loads(path.read_text())
        point_fields = [item.name for item in fields(ScreenRegions)
                        if item.type is not BBox and item.name not in (
                            "screen_width", "screen_height", "swipe_duration_ms")]
        invalid_points = (
            [-1, 2200], [0, -1], [968, 0], [0, 2376],
            [0.0, 0], [0, True], ["0", 0], [], [0], [0, 0, 0], "", False,
        )
        for name in point_fields:
            for point in invalid_points:
                with self.subTest(field=name, point=point):
                    payload = copy.deepcopy(original)
                    payload["regions"][name] = point
                    path.write_text(json.dumps(payload))
                    before = path.read_bytes()
                    self.assertIsNone(CalibrationProfile.find_for_device(self.info, self.root))
                    self.assertEqual(before, path.read_bytes())

    def test_only_optional_shared_navigation_targets_can_be_absent(self):
        path = self.save(self.donor())
        original = json.loads(path.read_text())
        for definition in fields(ScreenRegions):
            if definition.name in ("screen_width", "screen_height", "swipe_duration_ms"):
                continue
            with self.subTest(field=definition.name):
                payload = copy.deepcopy(original)
                payload["regions"][definition.name] = None
                path.write_text(json.dumps(payload))
                shared = CalibrationProfile.find_for_device(self.info, self.root)
                if definition.default is None:
                    self.assertIsNotNone(shared)
                    self.assertEqual("unverified", shared.metadata["verification_status"])
                else:
                    self.assertIsNone(shared)

    def test_valid_boundary_coordinates_and_ignored_donor_timing_can_be_shared(self):
        profile = self.donor()
        profile.regions.favorite_star_region = BBox(0, 0, 968, 2376)
        profile.regions.menu_button = (0, 0)
        profile.regions.swipe_end = (967, 2375)
        profile.regions.swipe_duration_ms = "invalid donor timing is not copied"
        self.save(profile)
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(profile.regions.favorite_star_region, shared.regions.favorite_star_region)
        self.assertEqual((0, 0), shared.regions.menu_button)
        self.assertEqual((967, 2375), shared.regions.swipe_end)
        self.assertEqual(CalibrationProfile.create_default(self.info).regions.swipe_duration_ms,
                         shared.regions.swipe_duration_ms)

    def test_invalid_verified_donor_is_rejected_before_preference_selection(self):
        invalid = self.donor(serial="verified", verified=True)
        invalid.regions.favorite_star_region.x = 2000
        self.save(invalid)
        valid = self.donor(serial="unverified", x=100)
        self.save(valid)
        shared = CalibrationProfile.find_for_device(self.info, self.root)
        self.assertEqual(valid.fingerprint, shared.metadata["shared_from_fingerprint"])
        self.assertEqual("unverified", shared.metadata["verification_status"])

    def test_donor_sanity_checks_do_not_change_own_profile_loading(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                own = self.donor(target=self.info, x=-1)
                own.regions.favorite_star_region.x = 2000
                path = own.save(directory)
                if legacy:
                    path.rename(Path(directory) / f"{own.legacy_fingerprint}.json")
                found = CalibrationProfile.find_for_device(self.info, directory)
                self.assertEqual(own.regions.to_dict(), found.regions.to_dict())

    def test_backups_preserve_legacy_bytes_and_do_not_create_a_new_profile(self):
        path = self.save(self.donor(target=self.info), legacy=True)
        original = path.read_bytes()
        first = CalibrationProfile.backup_for_device(self.info, self.root, timestamp="fixed")
        second = CalibrationProfile.backup_for_device(self.info, self.root, timestamp="fixed")
        self.assertNotEqual(first, second)
        self.assertEqual(original, first.read_bytes())
        self.assertEqual(original, second.read_bytes())
        self.assertEqual(original, path.read_bytes())
        self.assertFalse(CalibrationProfile.path_for_device(self.info, self.root).exists())


class CalibrationEvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.info = DeviceInfo("Phone", "one", 96, 237, 420)
        self.profile = CalibrationProfile.create_default(self.info)
        result = write_calibration_evidence(
            self.profile, Image.new("RGB", (96, 237), "white"), output_root=self.root,
        )
        self.manifest = result["manifest_path"]
        self.original = json.loads(self.manifest.read_text())
        self.truth = [{"species": "Zubat", "cp": 10}, {"species": "Zorua", "cp": 882}]

    def test_matching_legacy_evidence_remains_usable_with_exact_device_geometry(self):
        payload = copy.deepcopy(self.original)
        payload["device"]["fingerprint"] = self.profile.legacy_fingerprint
        self.manifest.write_text(json.dumps(payload))
        confirm_calibration_evidence(self.manifest, self.profile, self.truth)
        self.assertEqual("verified", self.profile.metadata["verification_status"])

    def test_old_fingerprint_cannot_authorize_other_density_or_missing_metadata(self):
        for change in ({"density": 480}, {"density": 420.0}, {"density": None},
                       {"layout": "tablet"}, {"serial": "two"}, {"model": "Tablet"},
                       {"resolution": "237x96"}):
            with self.subTest(change=change):
                payload = copy.deepcopy(self.original)
                payload["device"].update(change)
                payload["device"]["fingerprint"] = self.profile.legacy_fingerprint
                self.manifest.write_text(json.dumps(payload))
                original = self.manifest.read_bytes()
                with self.assertRaisesRegex(ValueError, "device geometry"):
                    confirm_calibration_evidence(self.manifest, self.profile, self.truth)
                self.assertEqual(original, self.manifest.read_bytes())
                self.assertEqual("unverified", self.profile.metadata["verification_status"])

    def test_shared_coordinates_cannot_borrow_donor_verification_evidence(self):
        profiles = self.root / "profiles"
        self.profile.save(profiles)
        shared = CalibrationProfile.find_for_device(replace(self.info, model="Tablet", serial="two"), profiles)
        with self.assertRaisesRegex(ValueError, "Evidence is for"):
            confirm_calibration_evidence(self.manifest, shared, self.truth)
        self.assertEqual("unverified", shared.metadata["verification_status"])
        self.assertEqual([], shared.validation_results)


if __name__ == "__main__":
    unittest.main()
