"""Exact native CP evidence precedes slower OCR on an ambiguous appraisal."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from pokemgr.indexer.snapshot import AppraisalSnapshot
from pokemgr.indexer.state_machine import IndexingStateMachine


class NativeCPPriorityTests(unittest.TestCase):
    def setUp(self):
        self.scanner = object.__new__(IndexingStateMachine)
        self.scanner.use_calculated_cp = True
        self.scanner._abort = False
        self.scanner.reader = SimpleNamespace(
            _native_enabled=True, native_cp=Mock(return_value=(4287, .9)),
            read_cp=Mock(return_value=(4287, .9)),
        )
        self.snapshot = AppraisalSnapshot.from_reads(
            dict(species="Dragonite", display_name="Dragonite",
                 caught_species="Dragonite", cp=-1, hp=188),
            dict(atk=15, def_=15, sta=15),
        )
        self.frame = Image.new("RGB", (4, 4))
        species = {"dragonite": dict(name="Dragonite", base_atk=263,
                                     base_def=198, base_sta=209)}
        self.catalog = patch("pokemgr.pvp.resolver._default_species_map",
                             return_value=species)
        self.catalog.start()
        self.addCleanup(self.catalog.stop)

    def read(self):
        return self.scanner._validate_appraisal_snapshot(self.snapshot, self.frame)

    def test_exact_native_candidate_avoids_legacy_ocr(self):
        result = self.read()
        self.assertTrue(result.accepted)
        self.assertEqual((result.snapshot.cp, result.level), (4287, 50.0))
        candidates = self.scanner.reader.native_cp.call_args.kwargs["expected_cps"]
        self.assertIn(4287, candidates)
        self.assertGreater(len(candidates), 1)
        self.scanner.reader.read_cp.assert_not_called()

    def test_missing_native_value_preserves_legacy_recovery(self):
        self.scanner.reader.native_cp.return_value = (-1, 0.)
        self.assertTrue(self.read().accepted)
        self.scanner.reader.read_cp.assert_called_once()

    def test_conflict_cannot_be_overridden_by_legacy_ocr(self):
        self.scanner.reader.native_cp.return_value = (-1, -1.)
        self.assertFalse(self.read().accepted)
        self.scanner.reader.read_cp.assert_not_called()

    def test_abort_during_native_read_discards_result(self):
        def abort(*args, **kwargs):
            self.scanner._abort = True
            return 4287, .9
        self.scanner.reader.native_cp.side_effect = abort
        self.assertFalse(self.read().accepted)
        self.scanner.reader.read_cp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
