import copy
import json
import tempfile
import unittest
from pathlib import Path
from benchagent.common import ROOT
from benchagent.parser import parse_artifacts, number
from benchagent.validator import validate_run
from support import make_fixture, execution

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.raw=Path(self.tmp.name)/"raw"
        make_fixture(self.raw)
        self.data=parse_artifacts(self.raw)
        self.plan={"action":"run","scene":"default","frames":20}
        self.exe=execution()
    def tearDown(self): self.tmp.cleanup()
    def validate(self): return validate_run(self.plan,self.exe,self.data)
    def test_valid_and_units(self):
        self.assertEqual(self.validate()["status"],"PASS")
        m=self.data["metrics"]
        self.assertEqual(m["solver_time_total_s"],.2)
        self.assertEqual(m["solver_time_mean_ms_per_frame"],10)
        self.assertEqual(m["newton_iterations_total"],40)
        self.assertEqual(m["pcg_iterations_total_from_stats"],140)
        self.assertEqual(m["pcg_iterations_coverage"],1)
    def test_execution_failures(self):
        for patch in [dict(returncode=1),dict(timeout=True),dict(identity_unchanged=False),
                      dict(fresh_output=False),dict(stdout=""),dict(stdout="TEST_COMPLETE frames=19\n"),
                      dict(stdout="TEST_COMPLETE frames=20\nTEST_COMPLETE frames=20\n")]:
            with self.subTest(patch=patch):
                self.assertEqual(validate_run(self.plan,{**self.exe,**patch},self.data)["status"],"FAIL")
    def test_wrong_frames_and_status(self):
        for key,value in [("completed",False),("finite_state",False),("frames_completed",19)]:
            old=self.data["status"][key];self.data["status"][key]=value
            self.assertEqual(self.validate()["status"],"FAIL")
            self.data["status"][key]=old
        self.data["rows"].pop()
        self.assertEqual(self.validate()["status"],"FAIL")
    def test_sequence(self):
        self.data["rows"][1]["frame"]=1
        self.assertEqual(self.validate()["status"],"FAIL")
    def test_c0_identity(self):
        for key,value in [("termination","toi"),("residual_threshold",.001),
                          ("converter_indexed_reduce",False),("method","base"),
                          ("profile_stages",True),("cloth_scene","bunny"),
                          ("initial_physical_frame",5),("residual_weight",1)]:
            with self.subTest(key=key):
                old=self.data["metadata"][key];self.data["metadata"][key]=value
                self.assertEqual(self.validate()["status"],"FAIL")
                self.data["metadata"][key]=old
    def test_safety_counters(self):
        for key in ("newton_cap_hits","diagnostic_budget_hits","fallbacks","nonfinite_vertices"):
            self.data["rows"][0][key]=1
            self.assertEqual(self.validate()["status"],"FAIL")
            self.data["rows"][0][key]=0
    def test_missing_pcg_is_warning_not_zero(self):
        self.data["traces"].pop()
        result=self.validate()
        self.assertEqual(result["status"],"PASS")
        self.assertTrue(result["warnings"])
        self.assertEqual(self.data["metrics"]["pcg_iterations_coverage"],.95)
    def test_partial_pcg(self):
        self.data["traces"][0]["iterations"]=[3]
        self.assertEqual(self.validate()["status"],"PASS")
        self.assertEqual(self.data["metrics"]["pcg_iterations_coverage"],39/40)
    def test_pcg_mismatch(self):
        self.data["traces"][0]["iterations"]=[3,5]
        self.assertEqual(self.validate()["status"],"FAIL")
    def test_duplicate_pcg(self):
        self.data["traces"].append(self.data["traces"][0])
        self.assertEqual(self.validate()["status"],"FAIL")
    def test_no_graph(self):
        for row in self.data["rows"]: row["direction_launches"]=0
        self.assertEqual(self.validate()["status"],"FAIL")
    def test_invalid_numeric_metrics(self):
        for value in ("nan","inf","-1",True,"1.5"):
            with self.subTest(value=value),self.assertRaises(ValueError):
                number(value,integer=True)
    def test_zero_frames(self):
        for p in self.raw.iterdir(): p.unlink()
        self.raw.rmdir();make_fixture(self.raw,0)
        with self.assertRaises(ValueError): parse_artifacts(self.raw)
    def test_corrupt_json(self):
        (self.raw/"status.json").write_text("{broken")
        with self.assertRaises(ValueError): parse_artifacts(self.raw)
    def test_nonfinite_json(self):
        (self.raw/"collision_summary.json").write_text('{"collision_counter_total": NaN,"collision_pair_max":0}')
        with self.assertRaises(ValueError): parse_artifacts(self.raw)
    def test_missing_file(self):
        (self.raw/"frames.csv").unlink()
        with self.assertRaises(FileNotFoundError): parse_artifacts(self.raw)
    def test_original_exit_needs_no_budget(self):
        self.data["rows"][0].update(exit_reason="original_direction",budget_active=0,budget_valid=0)
        self.assertEqual(self.validate()["status"],"PASS")
