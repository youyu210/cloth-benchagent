"""Display tests use fabricated evidence only; no simulator or network calls."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchagent.common import ROOT, read
from benchagent.presentation import diagnostic_text, metric_table, summary_table
from benchagent.reporter import report_run, report_suite

class ChineseReportTests(unittest.TestCase):
    def test_units_percentages_and_missing_values(self):
        rows = {r["指标"]: r for r in metric_table(
            {"solver_time_total_s": .2, "solver_time_mean_ms_per_frame": 10,
             "pcg_iterations_coverage": .5}, {"frames": 20})}
        self.assertEqual(rows["物理步总耗时"], {"指标": "物理步总耗时", "数值": "0.200000", "单位": "秒"})
        self.assertEqual(rows["平均每帧物理步耗时"]["单位"], "毫秒/帧")
        self.assertEqual(rows["PCG 求解明细覆盖率"]["数值"], "50.00%")
        self.assertEqual(rows["PCG 迭代次数（已记录明细）"]["数值"], "未采集")
        self.assertNotIn("solver_time_total_s", str(list(rows)))

    def test_summary_uses_aggregate_pcg_and_chinese_status(self):
        reports = [{
            "task_id": "smoke-20", "status": "PASS", "plan": {"frames": 20},
            "pcg_iterations_total_from_csv": 140,
            "pcg_iterations_total_from_stats": 70, "pcg_iterations_coverage": .5,
        }, {"task_id": "standard-50", "status": "FAIL", "plan": {"frames": 50}}]
        rows = summary_table(reports)
        self.assertEqual(rows[0]["任务"], "短程检查（20 帧）")
        self.assertEqual(rows[0]["状态"], "通过")
        self.assertEqual(rows[0]["PCG 迭代总次数（逐帧聚合）"], "140")
        self.assertEqual(rows[0]["PCG 明细覆盖率"], "50.00%")
        self.assertEqual(rows[1]["状态"], "失败")
        self.assertEqual(rows[1]["物理步总耗时（秒）"], "未采集")
        self.assertEqual(rows[1]["异常数"], "未采集")
        self.assertEqual(rows[1]["告警数"], "未采集")

    def test_single_markdown_is_chinese_json_schema_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            folder = Path(tmp)
            report_run(folder,
                {"action": "run", "scene": "default", "frames": 20},
                {"wall_seconds": .7, "implementation_commit": "synthetic"},
                {"status": "PASS", "errors": [], "warnings": ["PCG detail missing in frame 2"]},
                {"actual_frames": 20, "solver_time_total_s": .2})
            md = (folder / "report.md").read_text(encoding="utf-8")
            raw = read(folder / "report.json")
            self.assertIn("执行状态：通过", md)
            self.assertIn("| 物理步总耗时 | 0.200000 | 秒 |", md)
            self.assertIn("第 2 帧缺少 PCG 求解明细", md)
            self.assertIn("进程墙钟耗时 | 0.700000 | 秒", md)
            self.assertNotIn("| solver_time_total_s", md)
            self.assertNotIn("None", md)
            self.assertEqual(raw["status"], "PASS")
            self.assertEqual(raw["solver_time_total_s"], .2)
            self.assertNotIn("物理步总耗时", raw)

    def test_failed_suite_retains_chinese_missing_values(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as tmp:
            reports = [{"task_id": task, "status": "SKIPPED", "plan": {"frames": n}}
                       for task, n in (("smoke-20", 20), ("standard-50", 50), ("extended-100", 100))]
            summary = report_suite(Path(tmp), reports)
            md = (Path(tmp) / "benchmark_summary.md").read_text(encoding="utf-8")
            self.assertEqual(summary["status"], "SKIPPED")
            self.assertIn("执行状态：已跳过", md)
            self.assertIn("扩展检查（100 帧）", md)
            self.assertIn("PCG 明细覆盖率", md)
            self.assertNotIn("None", md)

    def test_missing_coverage_is_not_zero(self):
        rows = metric_table({})
        coverage = next(r for r in rows if r["指标"] == "PCG 求解明细覆盖率")
        self.assertEqual(coverage["数值"], "未采集")

    def test_chinese_diagnostics(self):
        self.assertEqual(diagnostic_text("Frame 7: fallbacks"), "第 7 帧：Graph 执行发生回退")
        self.assertEqual(diagnostic_text("Partial PCG detail in frame 3"), "第 3 帧的 PCG 求解明细仅部分覆盖")
        self.assertIn("超时", diagnostic_text("RuntimeError: TIMEOUT: process tree terminated"))

from support import WorkbenchHarness
from benchagent.common import write

class ChineseUITests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench()
    def tearDown(self):self.teardown_workbench()
    def test_single_result_has_chinese_metric_table_without_simulation(self):
        write(self.ui_root/"runs/synthetic-display-only/report.json",{
            "status":"SKIPPED","plan":{"frames":20},"errors":[],"warnings":[]})
        app=self.app();app.sidebar.radio(key="navigation").set_value("测试报告").run()
        self.assertEqual(len(app.exception),0)
        table=next(d.value for d in app.dataframe if "指标" in d.value.columns)
        self.assertEqual(list(table.columns),["指标","数值","单位","来源"])
        self.assertIn("物理步总耗时",table["指标"].tolist())
        self.assertIn("未采集",table["数值"].tolist())
        self.no_process_mock.assert_not_called()

    def test_suite_result_has_chinese_columns(self):
        write(self.ui_root/"runs/synthetic-suite/smoke-20/report.json",{
            "status":"SKIPPED","plan":{"frames":20},"errors":[],"warnings":[]})
        write(self.ui_root/"runs/synthetic-suite/benchmark_summary.json",{
            "status":"SKIPPED","tasks":[{"task_id":"smoke-20","status":"SKIPPED"}]})
        app=self.app();app.sidebar.radio(key="navigation").set_value("测试报告").run()
        self.assertEqual(len(app.exception),0)
        table=next(d.value for d in app.dataframe if "任务" in d.value.columns)
        self.assertIn("物理步总耗时（秒）",table.columns)
        self.assertEqual(table["状态"][0],"已跳过")
        self.assertEqual(table["任务"][0],"短程检查（20 帧）")
        self.no_process_mock.assert_not_called()
