"""Caught Nidoran text uses a strict same-frame sex icon, never a nickname."""
# TRACEWEAVER: file-role=nidoran-acquisition-tests; req=REQ-IDENTITY-001; trace=TRACE-IDENTITY-001; verifies=VER-SCAN-001
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from PIL import Image
from pokemgr.calibration.profile import CalibrationProfile
from pokemgr.calibration.regions import ScreenRegions
from pokemgr.indexer.snapshot import AppraisalSnapshot
from pokemgr.indexer.state_machine import IndexingStateMachine


class NidoranAcquisitionTests(unittest.TestCase):
    def setUp(self):
        profile=CalibrationProfile(device_model='test',serial='test',resolution='968x2376',
            density=420,regions=ScreenRegions.default_for_resolution(968,2376,density=420))
        self.sm=IndexingStateMachine(Mock(),profile,Mock())
        self.addCleanup(self.sm._close_reader)
        self.enterContext(patch('pokemgr.reader.ocr.is_in_gym',return_value=False))
        self.sm.reader.native_fields=Mock(side_effect=lambda image: SimpleNamespace(
            caught_species=image.info['caught'],in_gym=False))
        self.sm.reader.specimen_gender=Mock(side_effect=lambda image:image.info['sex'])
        self.sm.reader.read_hp=Mock(return_value=94)
        self.sm.reader.read_detail_screen=Mock(side_effect=lambda image,**_kw:{
            'species':'Nidoran Female','display_name':'My renamed Pokemon',
            'cp':-1,'confidence':.95,'gender':'female',
        })
        self.sm.reader.read_appraisal_screen=Mock(return_value={
            'atk':12,'def_':15,'sta':15,'confidence':.95,
        })
        self.sm.reader.read_cp=Mock(return_value=(-1,0))

    def read(self,caught,sex):
        image=Image.new('RGB',(968,2376),'white')
        image.info.update(caught=caught,sex=sex)
        detail,appraisal=self.sm._read_appraisal_snapshot(image)
        return image,AppraisalSnapshot.from_reads(detail,appraisal)

    def test_two_noisy_caught_reads_confirm_same_exact_male_without_cp_or_model_input(self):
        keys=[]
        for caught in ("Nidorano'",'Nidorano'):
            image,snapshot=self.read(caught,'male')
            self.assertTrue(snapshot.read_complete)
            self.assertEqual('Nidoran Male',snapshot.caught_species)
            self.assertEqual('male',snapshot.gender)
            self.assertEqual('My renamed Pokemon',snapshot.display_name)
            decision=self.sm._validate_appraisal_snapshot(snapshot,image)
            self.assertTrue(decision.accepted)
            self.assertEqual(('Nidoran Male',575,94,12,15,15),decision.snapshot.identity_key)
            keys.append(self.sm._cp_recovery_identity(snapshot))
            self.sm.reader.specimen_gender.assert_called_with(image)
        self.assertEqual(keys[0],keys[1])
        self.sm.reader.read_cp.assert_not_called()
        self.sm.adb.tap.assert_not_called()
        self.sm.adb.swipe.assert_not_called()

    def test_fresh_opposite_icon_does_not_reuse_previous_male_resolution(self):
        first_image,first=self.read('Nidorano','male')
        second_image,second=self.read('Nidorano','female')
        self.assertEqual('Nidoran Male',first.caught_species)
        self.assertEqual('Nidoran Female',second.caught_species)
        self.assertNotEqual(self.sm._cp_recovery_identity(first),self.sm._cp_recovery_identity(second))
        self.assertEqual([first_image,second_image],
                         [call.args[0] for call in self.sm.reader.specimen_gender.call_args_list])

    def test_apostrophe_only_symbol_read_keeps_exact_live_case_identity(self):
        self.sm.reader.read_hp.return_value=73
        self.sm.reader.read_appraisal_screen.return_value={
            'atk':13,'def_':12,'sta':12,'confidence':.95,
        }
        for caught in ('Nidoran♂',"Nidoran'",'Nidoran’'):
            with self.subTest(caught=caught):
                image,snapshot=self.read(caught,'male')
                self.assertTrue(snapshot.read_complete)
                decision=self.sm._validate_appraisal_snapshot(snapshot,image)
                self.assertTrue(decision.accepted)
                self.assertEqual(('Nidoran Male',353,73,13,12,12),
                                 decision.snapshot.identity_key)
        self.sm.reader.read_cp.assert_not_called()
        self.sm.adb.tap.assert_not_called()
        self.sm.adb.swipe.assert_not_called()

    def test_missing_or_contradictory_sex_evidence_cannot_authorize_cp_or_recovery(self):
        for caught,sex in (("Nidorano'",''),("Nidoran'",''),('Nidoran 4',''),('Nidoran’','none'),('Nidoran♀','male'),('Nidoran♂','female'),('Nidoranxyz','male')):
            with self.subTest(caught=caught,sex=sex):
                image,snapshot=self.read(caught,sex)
                self.assertFalse(snapshot.read_complete)
                self.assertFalse(self.sm._validate_appraisal_snapshot(snapshot,image).accepted)
                self.assertIsNone(self.sm._cp_recovery_identity(snapshot))
        self.sm.reader.read_cp.assert_not_called()
        self.sm.adb.tap.assert_not_called()

    def test_digit_symbol_read_keeps_exact_female_live_case_identity(self):
        self.sm.reader.read_hp.return_value=83
        self.sm.reader.read_appraisal_screen.return_value={
            'atk':15,'def_':14,'sta':15,'confidence':.95,
        }
        for caught in ('Nidoran♀','Nidoran 4','Nidoran4’'):
            with self.subTest(caught=caught):
                image,snapshot=self.read(caught,'female')
                self.assertTrue(snapshot.read_complete)
                decision=self.sm._validate_appraisal_snapshot(snapshot,image)
                self.assertTrue(decision.accepted)
                self.assertEqual(('Nidoran Female',348,83,15,14,15),
                                 decision.snapshot.identity_key)
        self.sm.reader.read_cp.assert_not_called()
        self.sm.adb.tap.assert_not_called()
        self.sm.adb.swipe.assert_not_called()

    def test_unrelated_caught_species_never_invokes_nidoran_sex_repair(self):
        _image,snapshot=self.read('Nidorino','male')
        self.assertEqual('Nidorino',snapshot.caught_species)
        self.assertTrue(snapshot.read_complete)
        self.sm.reader.specimen_gender.assert_not_called()


if __name__=='__main__': unittest.main()
