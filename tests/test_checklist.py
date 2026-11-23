"""Checklist regressions; every process, GPU preflight and input is synthetic."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchagent.common import ROOT, read, write
from benchagent.checks import SCHEMA
from benchagent.parser import parse_artifacts
from benchagent.presentation import check_table
from benchagent.reporter import report_run, render_run_markdown
from benchagent.runner import execute
from benchagent.validator import validate_run
from support import make_fixture, FakeTracker, execution

PLAN = {"action": "run", "scene": "default", "frames": 20}

def checks(result):
    return {item["id"]: item for item in result["checks"]}

class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.folder = Path(self.tmp.name)
        self.raw = self.folder / "raw"
        make_fixture(self.raw)
        self.data = parse_artifacts(self.raw)
        self.execution = execution()

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self):
        return validate_run(PLAN, self.execution, self.data)

    def test_complete_stable_ids_and_evidence(self):
        result = self.validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["checks_schema"], SCHEMA)
        self.assertEqual([c["id"] for c in result["checks"]], [f"V{i:02d}" for i in range(1, 13)])
        self.assertTrue(all(c["status"] == "PASS" for c in result["checks"]))
        for item in result["checks"]:
            self.assertTrue(item["evidence"])
            self.assertIsNotNone(item["actual"])
        self.assertEqual(checks(result)["V03"]["actual"]["actual_frames"], 20)
        self.assertEqual(checks(result)["V10"]["actual"]["residual_threshold"], .03)

    def test_marker_frame_and_identity_fail_at_corresponding_checks(self):
        for patch_values, code in [
            ({"stdout": ""}, "V02"),
            ({"fresh_output": False}, "V05"),
            ({"identity_unchanged": False}, "V05"),
            ({"returncode": 2}, "V01"),
        ]:
            with self.subTest(code=code, patch_values=patch_values):
                result = validate_run(PLAN, {**self.execution, **patch_values}, self.data)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(checks(result)[code]["status"], "FAIL")
        self.data["rows"].pop()
        result = self.validate()
        self.assertEqual(checks(result)["V03"]["status"], "FAIL")
        self.assertEqual(checks(result)["V03"]["actual"]["actual_frames"], 19)

    def test_partial_coverage_is_noncritical_warning(self):
        self.data["traces"][0]["iterations"] = [3]
        result = self.validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(checks(result)["V07"]["status"], "PASS")
        item = checks(result)["V08"]
        self.assertEqual(item["status"], "WARNING")
        self.assertFalse(item["required"])
        self.assertEqual(item["actual"]["covered_solves"], 39)
        self.assertEqual(item["actual"]["coverage"], 39 / 40)
        row = next(r for r in check_table(result["checks"]) if r["编号"] == "V08")
        self.assertEqual(row["结论"], "告警")
        self.assertIn("97.50%", row["实际值"])
        self.assertIn("第 1 帧", row["说明"])

    def test_duplicate_pcg_is_required_failure(self):
        self.data["traces"].append(self.data["traces"][0])
        result = self.validate()
        item = checks(result)["V07"]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(item["status"], "FAIL")
        self.assertFalse(item["actual"]["unique_valid_frames"])

    def test_limits_and_graph_have_distinct_check_ids(self):
        self.data["rows"][0]["diagnostic_budget_hits"] = 1
        result = self.validate()
        self.assertEqual(checks(result)["V11"]["status"], "FAIL")
        self.assertEqual(checks(result)["V12"]["status"], "PASS")
        self.assertEqual(checks(result)["V11"]["actual"]["violations"][0]["frame"], 1)
        self.data["rows"][0]["diagnostic_budget_hits"] = 0
        self.data["rows"][0]["fallbacks"] = 1
        result = self.validate()
        self.assertEqual(checks(result)["V12"]["status"], "FAIL")

    def test_configuration_observation_includes_wrong_value(self):
        self.data["metadata"]["residual_threshold"] = .001
        result = self.validate()
        item = checks(result)["V10"]
        self.assertEqual(item["status"], "FAIL")
        self.assertEqual(item["expected"]["residual_threshold"], .03)
        self.assertEqual(item["actual"]["residual_threshold"], .001)

    def test_parse_error_skips_unavailable_statistics(self):
        result = validate_run(PLAN, self.execution, parse_error="ValueError: invalid JSON")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(checks(result)["V06"]["status"], "FAIL")
        self.assertEqual(checks(result)["V03"]["status"], "SKIPPED")
        self.assertIsNone(checks(result)["V03"]["actual"])
        self.assertEqual(checks(result)["V01"]["status"], "PASS")

    def test_timeout_cannot_pass_dependent_checks(self):
        result = validate_run(PLAN, {**self.execution, "returncode": -9, "timeout": True, "stdout": ""})
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(checks(result)["V01"]["status"], "FAIL")
        for code in ("V03", "V04", "V06", "V07", "V08", "V09", "V10", "V11", "V12"):
            self.assertEqual(checks(result)[code]["status"], "SKIPPED")

    def test_report_contains_check_table_and_raw_schema(self):
        result = self.validate()
        report_run(self.folder, PLAN, self.execution, result, self.data["metrics"])
        md = (self.folder / "report.md").read_text(encoding="utf-8")
        raw = read(self.folder / "report.json")
        self.assertIn("## 逐项校验", md)
        self.assertIn("| 编号 | 检查项 | 结论 | 必需 | 期望值 | 实际值 | 证据路径 | 说明 |", md)
        self.assertIn("raw/frames.csv", md)
        self.assertEqual(raw["checks_schema"], SCHEMA)
        self.assertEqual(len(raw["checks"]), 12)
        self.assertEqual(raw["status"], "PASS")

    def test_nonfinite_observed_config_is_failed_and_can_be_archived(self):
        self.data["metadata"]["residual_weight"] = float("inf")
        result = self.validate()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(checks(result)["V10"]["actual"]["residual_weight"], "非有限数值：正无穷")
        report_run(self.folder, PLAN, self.execution, result, self.data["metrics"])
        self.assertEqual(read(self.folder / "report.json")["status"], "FAIL")

    def test_historical_report_does_not_invent_passed_checks(self):
        md = render_run_markdown({"status": "PASS", "run_id": "old", "errors": [], "warnings": []})
        self.assertIn("未记录逐项校验清单", md)
        self.assertNotIn("| V01 |", md)

class SyntheticProcess:
    corrupt = False
    def __init__(self, command, **kwargs):
        self.pid = 999999
        raw = Path(command[command.index("--benchmark-output-dir") + 1])
        make_fixture(raw)
        if self.corrupt:
            (raw / "status.json").write_text("{broken", encoding="utf-8")
        kwargs["stdout"].write(b"TEST_COMPLETE frames=20\n")
    def wait(self, timeout=None):
        return 0

class RunnerChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        (self.root / "configs").mkdir()
        write(self.root / "configs/c0.json", read(ROOT / "configs/c0.json"))
    def tearDown(self):
        self.tmp.cleanup()
    def run_mocked(self, ready=True, process=SyntheticProcess):
        with patch("benchagent.runner.ROOT", self.root),patch("benchagent.runner.ProcessTracker",FakeTracker), \
             patch("benchagent.runner.inspect", return_value={"ready": ready, "gpu": "synthetic", "errors": [] if ready else ["missing build"]}), \
             patch("benchagent.runner.identity", return_value={"synthetic": True}), \
             patch("benchagent.runner.subprocess.Popen", process):
            return execute(PLAN)

    def test_success_is_archived_with_preflight_and_all_checks(self):
        result = self.run_mocked()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["checks"]), 13)
        self.assertEqual(checks(result)["P00"]["status"], "PASS")
        raw = read(self.root / "runs" / result["run_id"] / "report.json")
        self.assertEqual(len(raw["checks"]), 13)

    def test_preflight_skip_preserves_failed_preflight_and_skipped_execution(self):
        result = self.run_mocked(ready=False)
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(checks(result)["P00"]["status"], "FAIL")
        self.assertTrue(all(c["status"] == "SKIPPED" for c in result["checks"] if c["id"].startswith("V")))

    def test_corrupt_json_produces_structured_failure_not_empty_checklist(self):
        class CorruptProcess(SyntheticProcess):
            corrupt = True
        result = self.run_mocked(process=CorruptProcess)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(checks(result)["V06"]["status"], "FAIL")
        self.assertEqual(checks(result)["V04"]["status"], "SKIPPED")
        self.assertEqual(checks(result)["V02"]["status"], "PASS")

    def test_launch_error_is_explicit_and_no_successful_process_invented(self):
        def launch_error(*args, **kwargs):
            raise OSError("synthetic launch failure")
        result = self.run_mocked(process=launch_error)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(checks(result)["E00"]["status"], "FAIL")
        self.assertEqual(checks(result)["V01"]["status"], "SKIPPED")
        meta = read(self.root / "runs" / result["run_id"] / "run_meta.json")
        self.assertIn("synthetic launch failure", meta["execution_error"])

from support import WorkbenchHarness

class ChecklistUITests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench()
    def tearDown(self):self.teardown_workbench()
    def test_chinese_check_table_is_displayed_without_execution(self):
        from benchagent.checks import skipped_checks
        from benchagent.common import write
        write(self.ui_root/"runs/synthetic-checklist-ui-only/report.json",{
            "status":"SKIPPED","plan":PLAN,"errors":[],"warnings":[],
            "checks":skipped_checks("合成界面测试，未启动仿真")})
        app=self.app();app.sidebar.radio(key="navigation").set_value("测试报告").run()
        self.assertEqual(len(app.exception),0)
        table=next(d.value for d in app.dataframe if "检查项" in d.value.columns)
        self.assertEqual(list(table.columns),["编号","检查项","结论","必需","期望值","实际值","证据路径","说明"])
        self.assertEqual(table["结论"].tolist(),["已跳过"]*12)
        self.no_process_mock.assert_not_called()
