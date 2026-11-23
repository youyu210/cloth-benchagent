"""BG-01 tests: isolated fixture folders, no simulation or network."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from benchagent.common import ROOT,sha,write
from benchagent.archive_repository import ArchiveRepository
from benchagent.view_models import build_report_view,metric_rows,keyframe_rows,render_archive_markdown
from support import make_fixture

def report():
    return {"status":"PASS","actual_frames":20,"solver_time_total_s":.2,
            "frame_solver_times_ms":[10.0]*20,"newton_iterations_total":40,
            "pcg_iterations_total_from_csv":140,"pcg_iterations_total_from_stats":70,
            "pcg_iterations_coverage":.5,"pcg_solves":40,"collision_counter_total":123}
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.root=Path(self.tmp.name)
        self.repo=ArchiveRepository(self.root/"runs")
    def tearDown(self): self.tmp.cleanup()
    def put(self,name,obj):
        path=self.repo.root/name;write(path,obj);return path
    def hashes(self):
        return {p.relative_to(self.root).as_posix():sha(p) for p in self.root.rglob("*") if p.is_file()}
    def test_empty_does_not_create_directory(self):
        self.assertEqual(self.repo.list_records()["total"],0)
        self.assertFalse(self.repo.root.exists())
    def test_single_verdict_and_numeric_units(self):
        self.put("one/report.json",report())
        v=self.repo.get_view("one")
        self.assertEqual(v["recorded_result_status"],"PASS")
        self.assertEqual(v["metrics"]["pcg_mean_per_frame"]["value"],7)
        self.assertEqual(v["metrics"]["pcg_mean_per_solve"]["value"],3.5)
        self.assertEqual(v["metrics"]["solver_time_mean_ms_per_frame"]["value"],10)
        self.assertEqual(len(metric_rows(v,kpis_only=True)),6)
        self.assertEqual(v["data_source"]["producer_kind"],"legacy_unknown")
    def test_corrupt_report_not_fake_fail(self):
        p=self.put("bad/report.json",{});p.write_text("{broken")
        v=self.repo.get_view("bad")
        self.assertEqual(v["archive_read_status"],"ERROR")
        self.assertIsNone(v["recorded_result_status"])
    def test_missing_evidence_preserves_pass(self):
        self.put("one/report.json",{**report(),"evidence_files":["stdout.log"]})
        r=self.repo.get_report("one")
        self.assertEqual(r["recorded_result_status"],"PASS")
        self.assertEqual(r["archive_read_status"],"PARTIAL")
    def test_json_null_array_nonfinite_rejected(self):
        for obj in ("null","[]",'{"status":"PASS","x":1e999}'):
            p=self.put("one/report.json",{});p.write_text(obj)
            self.assertEqual(self.repo.get_report("one")["archive_read_status"],"ERROR")
    def test_missing_metrics_and_zero_denominator_not_zero_fill(self):
        self.put("one/report.json",{"status":"FAIL","actual_frames":0,"pcg_solves":0})
        v=self.repo.get_view("one")
        self.assertIsNone(v["metrics"]["pcg_mean_per_frame"]["value"])
        self.assertEqual(v["metrics"]["pcg_mean_per_frame"]["reason"],"ZERO_DENOMINATOR")
        self.assertEqual(metric_rows(v,kpis_only=True)[0]["数值"],"未采集")
    def test_boolean_and_negative_metrics_are_invalid(self):
        self.put("one/report.json",{**report(),"newton_iterations_total":True,"collision_counter_total":-1})
        v=self.repo.get_view("one")
        self.assertIsNone(v["metrics"]["newton_iterations_total"]["value"])
        self.assertIsNone(v["metrics"]["collision_counter_total"]["value"])
        self.assertEqual(v["recorded_result_status"],"PASS")
    def test_recorded_mean_conflict_is_visible_not_overwrite(self):
        original={**report(),"solver_time_mean_ms_per_frame":999}
        self.put("one/report.json",original)
        v=self.repo.get_view("one")
        self.assertEqual(v["metrics"]["solver_time_mean_ms_per_frame"]["value"],10)
        self.assertEqual(v["raw_report"]["solver_time_mean_ms_per_frame"],999)
        self.assertIn("RECORDED_MEAN_MISMATCH",[i["code"] for i in v["issues"]])
    def test_csv_curves_and_missing_per_frame_collision(self):
        self.put("one/report.json",report());make_fixture(self.repo.root/"one/raw")
        v=self.repo.get_view("one")
        self.assertEqual(v["frames"][0]["pcg_iterations"],7)
        self.assertEqual(keyframe_rows(v)[0]["碰撞对计数"],"未记录")
        self.assertIn("raw/frames.csv",v["frame_source"])
    def test_invalid_csv_sequence_falls_back_with_issue(self):
        self.put("one/report.json",report())
        path=self.repo.root/"one/raw";path.mkdir()
        (path/"frames.csv").write_text("frame,step_ms\n2,10\n",encoding="utf-8")
        v=self.repo.get_view("one")
        self.assertEqual(len(v["frames"]),20)
        self.assertIn("INVALID_FRAMES",[i["code"] for i in v["issues"]])
    def test_short_csv_discloses_frame_mismatch(self):
        self.put("one/report.json",report());make_fixture(self.repo.root/"one/raw",19)
        v=self.repo.get_view("one")
        self.assertEqual(len(v["frames"]),19)
        self.assertIn("FRAME_COUNT_MISMATCH",[i["code"] for i in v["issues"]])
    def test_legacy_and_modern_suites(self):
        for name,prefix in (("old",""),("new","cases/")):
            self.put(name+"/"+prefix+"smoke-20/report.json",report())
            self.put(name+"/benchmark_summary.json",{"status":"PASS","tasks":[{"task_id":"smoke-20","status":"PASS"}]})
            v=self.repo.get_view(name)
            self.assertEqual(len(v["cases"]),1)
            self.assertEqual(v["selected_case_id"],"smoke-20")
            self.assertEqual(self.repo.get_view(name,"smoke-20")["metrics"]["pcg_mean_per_frame"]["value"],7)
    def test_missing_child_uses_explicit_summary_fallback(self):
        self.put("suite/benchmark_summary.json",{"status":"PASS","tasks":[{**report(),"task_id":"smoke-20"}]})
        v=self.repo.get_view("suite")
        self.assertEqual(v["recorded_result_status"],"PASS")
        self.assertEqual(v["archive_read_status"],"PARTIAL")
        self.assertEqual(v["cases"][0]["archive_read_status"],"PARTIAL")
    def test_new_cases_without_summary_preserve_available_children(self):
        self.put("job/cases/smoke-20/report.json",report())
        v=self.repo.get_view("job")
        self.assertEqual(len(v["cases"]),1)
        self.assertIsNone(v["recorded_result_status"])
        self.assertEqual(v["archive_read_status"],"ERROR")
    def test_conflicting_child_and_summary_not_reclassified(self):
        self.put("s/c/report.json",report())
        self.put("s/benchmark_summary.json",{"status":"FAIL","tasks":[{"task_id":"c","status":"FAIL"}]})
        v=self.repo.get_view("s")
        self.assertEqual(v["recorded_result_status"],"FAIL")
        self.assertEqual(v["cases"][0]["recorded_result_status"],"PASS")
        self.assertEqual(v["archive_read_status"],"PARTIAL")
    def test_reject_paths_and_wrong_case_membership(self):
        for name in ("../outside","C:/elsewhere","/absolute","a/../b","a\\b","."):
            with self.subTest(name=name),self.assertRaises(ValueError): self.repo.get_report(name)
        self.put("one/report.json",report())
        with self.assertRaises(ValueError): self.repo.get_view("one","other")
    def test_reject_child_traversal_and_recursive_reference(self):
        for reference in ("../outside/report.json","report.md","child/benchmark_summary.json"):
            self.put("s/benchmark_summary.json",{"status":"PASS","tasks":[{"task_id":"c","report_path":reference}]})
            v=self.repo.get_view("s")
            self.assertEqual(len(v["cases"]),0)
            self.assertEqual(v["archive_read_status"],"PARTIAL")
    def test_invalid_checks_do_not_crash_rendering(self):
        self.put("one/report.json",{**report(),"checks":[{"foo":"bar"}]})
        md=render_archive_markdown(self.repo.get_view("one"))
        self.assertIn("历史记录未包含检查清单",md)
    def test_orphan_and_pagination(self):
        (self.repo.root/"orphan").mkdir(parents=True)
        self.put("one/report.json",report());self.put("two/report.json",{"status":"FAIL"})
        self.assertEqual(self.repo.list_records()["total"],3)
        self.assertEqual(self.repo.list_records(recorded_status="PASS")["total"],1)
        self.assertEqual(len(self.repo.list_records(limit=1,offset=1)["records"]),1)
        with self.assertRaises(ValueError): self.repo.list_records(offset=-1)
    def test_reads_render_and_caller_mutations_leave_archive_untouched(self):
        self.put("one/report.json",report());make_fixture(self.repo.root/"one/raw")
        before=self.hashes()
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            self.repo.list_records()
            view=self.repo.get_view("one")
            md=render_archive_markdown(view)
            view["raw_report"]["status"]="FAIL"
        self.assertIn("平均 PCG 迭代（按帧）",md)
        self.assertIn("归档读取状态：完整可读",md)
        self.assertEqual(self.hashes(),before)
        self.assertEqual(self.repo.get_report("one")["recorded_result_status"],"PASS")
    def test_synthetic_provenance_is_explicit(self):
        self.put("one/report.json",{**report(),"synthetic":True})
        self.assertEqual(self.repo.get_view("one")["data_source"]["producer_kind"],"synthetic")

    def test_summary_fallback_metric_points_to_actual_summary(self):
        self.put("s/benchmark_summary.json",{"status":"PASS","tasks":[{**report(),"task_id":"c"}]})
        v=self.repo.get_view("s","c")
        self.assertIn("benchmark_summary.json#tasks/0/",v["metrics"]["solver_time_mean_ms_per_frame"]["source"][0])
    def test_missing_frame_counters_are_chinese_not_none(self):
        self.put("one/report.json",report())
        v=self.repo.get_view("one")
        self.assertEqual(keyframe_rows(v)[0]["牛顿迭代次数"],"未采集")
        self.assertNotIn("| None",render_archive_markdown(v))
    def test_malformed_check_types_do_not_crash_rendering(self):
        self.put("one/report.json",{**report(),"checks":[{"id":"V01","name":"检查","status":[],"required":True}]})
        self.assertIn("历史记录未包含检查清单",render_archive_markdown(self.repo.get_view("one")))

    def test_invalid_recorded_status_does_not_crash_or_reclassify(self):
        self.put("one/report.json",{**report(),"status":[]})
        v=self.repo.get_view("one")
        self.assertIsNone(v["recorded_result_status"])
        self.assertEqual(v["raw_report"]["status"],[])
        self.assertIn("未知状态",render_archive_markdown(v))
    def test_extreme_numeric_integer_is_reported_missing_instead_of_crashing(self):
        self.put("one/report.json",{**report(),"newton_iterations_total":10**1000})
        v=self.repo.get_view("one")
        self.assertIsNone(v["metrics"]["newton_iterations_total"]["value"])
        self.assertEqual(v["recorded_result_status"],"PASS")
