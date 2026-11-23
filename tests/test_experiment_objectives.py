import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchagent.app_service import AppService
from benchagent.cloth_evidence import finite, hot_statistics, associate_images
from benchagent.common import ROOT
from benchagent.conversation_service import normalize_request, task_request
from benchagent.experiment_objectives import parse_goal, ExperimentObjectives
from benchagent.model_gateway.contracts import ModelResult
from benchagent.planning_store import PlanningError, PlanningStore

PARENT = "job-f0c29c3238ab4eddb961138407946393"
CASE = "frames-29"
GOAL = "分析球体与布料高耗时帧和接触相关计数，展示画面。"


class StatisticsTests(unittest.TestCase):
    def test_ties_invalid_samples_and_missing_values(self):
        self.assertIsNone(finite("inf"))
        self.assertIsNone(finite("nan"))
        self.assertIsNone(finite(0, positive=True))
        self.assertIsNone(finite(1.5, integer=True))
        rows = [
            {"frame": f, "step_ms": t, "newton_iterations": None, "pcg_iterations": 3,
             "contact_list_max": 0}
            for f, t in ((1, 4), (2, 4), (3, None), (4, 1), (5, 2))
        ]
        result = hot_statistics(rows, 1)
        self.assertEqual(result["hot_frames"][0]["frame"], 1)
        self.assertAlmostEqual(result["missing_ratio"], .2)
        self.assertEqual(result["groups"]["newton_iterations"]["high"]["n"], 0)
        self.assertEqual(result["groups"]["contact_list_max"]["high"]["median"], 0)
        self.assertEqual(result["groups"]["pcg_iterations"]["other"]["median"], 3)
        self.assertEqual(hot_statistics(rows[:2], 5)["hot_frames"], [])

    def test_keyframe_offset_and_tie(self):
        links = associate_images([{"frame": 4}, {"frame": 8}], [
            {"frame": 0, "file": "zero", "sha256": "zero"},
            {"frame": 8, "file": "eight", "sha256": "eight"},
        ])
        self.assertEqual([(r["image_frame"], r["offset"]) for r in links], [(0, -4), (8, 0)])

    def test_goal_revision_keeps_scene_and_evidence_scope(self):
        goal = parse_goal(GOAL)
        updated = parse_goal("改成前3帧", previous=goal)
        self.assertEqual(updated["scene"], "cloth-sphere7")
        self.assertEqual(updated["top"], 3)
        self.assertTrue(updated["need_contact"])
        with self.assertRaisesRegex(PlanningError, "OBJECTIVE_CONTEXT"):
            parse_goal("改成前3帧")
        with self.assertRaisesRegex(PlanningError, "UNSUPPORTED_SCENE"):
            parse_goal("分析场景999耗时")
        with self.assertRaisesRegex(PlanningError, "OBJECTIVE_SCENE"):
            parse_goal(GOAL, selected="fixed-cloth")

    def test_diagnostic_request_enforces_scene_frame_and_timeout(self):
        fields = {"scene": "cloth-sphere7", "task_frames": [29], "timeout_sec": 600,
                  "diagnostic_contact_trace": True}
        self.assertEqual(normalize_request(task_request("test", fields))["task_frames"], [29])
        for change in ({"scene": "fixed-cloth"}, {"task_frames": [101]}, {"timeout_sec": 601},
                       {"task_frames": [20, 50]}, {"mode": "standard"}):
            with self.assertRaises(PlanningError):
                normalize_request(task_request("test", {**fields, **change}))

    def test_invalid_model_action_cannot_choose_gpu_or_use_extra_fields(self):
        class BadGateway:
            def propose_action(self, request):
                return ModelResult(request.request_id, "ok", "", proposed_plan={
                    "action": "submit_diagnostic", "command": "arbitrary command",
                }, provider_calls=1)
        record = {"budget": {"model_calls": 8}, "model_requests": [], "model_used": False}
        action, actor, gateway = ExperimentObjectives._choose(
            record, {"text": "private input", "top": 5}, ["read_steps"], {"steps": 0}, BadGateway())
        self.assertEqual((action, actor, gateway), ("read_steps", "deterministic", None))
        self.assertEqual(record["model_requests"][0]["outcome"], "ACTION_REJECTED")

    def test_valid_model_suggestion_can_reorder_and_observations_are_structured(self):
        class Gateway:
            def propose_action(self, request):
                self.request = request
                return ModelResult(request.request_id, "ok", "", proposed_plan={
                    "action": "link_native_frames",
                }, provider_calls=1)
        gateway = Gateway()
        record = {"budget": {"model_calls": 1}, "model_requests": [], "model_used": False}
        action, actor, _ = ExperimentObjectives._choose(
            record, {"text": "private input", "top": 5}, ["read_steps", "link_native_frames"],
            {"trace_frames": 29}, gateway)
        self.assertEqual((action, actor), ("link_native_frames", "deepseek"))
        self.assertEqual(gateway.request.objective, {"top": 5})
        self.assertEqual(gateway.request.observation, {"trace_frames": 29})
        next_action = ExperimentObjectives._choose(record, {}, ["read_steps"], {}, gateway)
        self.assertEqual(next_action[:2], ("read_steps", "deterministic"))


@unittest.skipUnless((ROOT / "runs" / PARENT).is_dir(), "real archive not installed")
class ArchiveObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = AppService(ROOT, store=PlanningStore(Path(self.tmp.name)))
        self.obj = self.app.objectives

    def create(self, text=GOAL, **kw):
        return self.obj.create(text, report_id=PARENT, case_id=CASE, **kw)

    def test_sufficient_and_unavailable_finish_without_worker(self):
        result = self.create("分析球体与布料，只看耗时和迭代。")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["analysis"]["statistics"]["valid_steps"], 29)
        blocked = self.create("分析球体与布料的高耗时阶段，定位 CCD 耗时。")
        self.assertEqual(blocked["status"], "LIMITED")
        self.assertIsNone(blocked["diagnostic"])
        self.assertEqual(self.app.jobs(), [])
        self.assertIsNone(self.app.manager)

    def test_confirmation_and_duplicate_submission(self):
        result = self.create()
        self.assertEqual(result["status"], "AWAITING_CONFIRMATION")
        self.assertIsNone(self.app.manager)
        self.assertIn("read_contact_summary", [a["action"] for a in result["actions"]])
        self.assertTrue(result["analysis"]["keyframes"])
        self.assertEqual(result["diagnostic"]["build_sha256"], result["diagnostic"]["parent_build_sha256"])
        with self.assertRaisesRegex(PlanningError, "OBJECTIVE_STALE"):
            self.obj.confirm_diagnostic(result["objective_id"], "wrong")
        inventory = "\n".join(result["diagnostic"]["gpu_inventory"])
        with patch("benchagent.preflight.inspect", return_value={"ready": True, "gpu": inventory}), \
                patch.object(self.app, "submit", return_value={"job_id": "job-fixture"}) as submit:
            first = self.obj.confirm_diagnostic(result["objective_id"], result["digest"])
            self.obj.confirm_diagnostic(result["objective_id"], result["digest"])
            submit.assert_called_once()
            self.assertEqual(first["gpu_submissions"], 1)

    def test_zero_budget_never_builds_diagnostic_plan(self):
        result = self.create(gpu_budget=0)
        self.assertEqual(result["status"], "LIMITED")
        self.assertIsNone(result["diagnostic"])
        self.assertFalse(self.obj.store.read()["plans"])
        with self.assertRaisesRegex(PlanningError, "OBJECTIVE_AUTHORIZATION"):
            self.obj.confirm_diagnostic(result["objective_id"], result["digest"])

    def test_source_hash_changes_rejected(self):
        with self.assertRaisesRegex(PlanningError, "OBJECTIVE_STALE"):
            self.obj._check_sources({PARENT + "/cases/" + CASE + "/report.json": "incorrect"})




@unittest.skipUnless((ROOT / "runs" / PARENT).is_dir(), "real archive not installed")
class ObjectiveUiTests(unittest.TestCase):
    def test_smart_page_links_real_rows_and_native_images(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as folder:
            app = AppService(ROOT, store=PlanningStore(Path(folder)))
            with patch("benchagent.ui_app.get_app_service", return_value=app):
                page = AppTest.from_file(str(ROOT / "ui.py"), default_timeout=25).run()
                page.session_state["smart-tab"] = "历史实验分析"
                page.run()
                self.assertEqual([item.value for item in page.error], [])
                self.assertTrue(page.button(key="comparison-run"))
                page.button(key="goal-review").click().run()
                self.assertEqual([item.value for item in page.error], [])
                self.assertIn("请先输入分析目标，或点击「填入球体与布料示例」。",
                              [item.value for item in page.warning])
                self.assertEqual(app.objectives.list(), [])
                page.button(key="goal-example").click().run()
                self.assertIn("Newton/PCG", page.text_area(key="goal-text").value)
                page.text_area(key="goal-text").set_value(
                    "分析球体与布料的高耗时帧，不要接触计数。").run()
                page.button(key="goal-review").click().run()
                self.assertEqual([error.message for error in page.exception], [])
                self.assertEqual([item.value for item in page.error], [])
                self.assertTrue(page.button(key="script-generate"))
                self.assertIn("证据充分", [v.value for v in page.subheader])
                self.assertTrue(page.selectbox(key="goal-frame-parent").options)
                self.assertIsNone(app.manager)


class ScriptReviewTests(unittest.TestCase):
    def test_review_accepts_fixed_schema_script_and_flags_external_access(self):
        from benchagent.analysis_scripts import review_script

        self.assertEqual(review_script('import csv\nwith open("input.csv", "r") as stream:\n    pass'), [])
        issues = review_script('import subprocess\nopen("../file", "w")\nexec("arbitrary")')
        self.assertEqual(len(issues), 3)
        self.assertTrue(review_script("if syntax error:"))
