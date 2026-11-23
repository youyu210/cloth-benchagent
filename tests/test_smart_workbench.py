"""Prototype v2 wiring keeps plans, material inheritance and execution boundaries."""
import copy
import unittest

from streamlit.testing.v1 import AppTest

from benchagent.common import ROOT, write
from benchagent.ui_models import select_archive
from benchagent.conversation_service import task_request
from benchagent.model_gateway import ModelResult
from unittest.mock import patch
from support import WorkbenchHarness


class SmartWorkbenchTests(WorkbenchHarness, unittest.TestCase):
    def setUp(self):
        self.setup_workbench()

    def tearDown(self):
        self.teardown_workbench()

    def model_plan(self):
        page = self.app()
        page.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        page.session_state["deepseek_api_key"] = "fixture"
        page.text_area(key="rule_prompt").set_value(
            "将悬挂布料场景的布片泊松比从0.243修改为0.3，其他材料参数保持不变，运行20帧。").run()
        proposal = task_request("fixture-plan", {
            "scene": "cloth-hang", "task_frames": [20], "native_render": True,
            "material": {"object": "cloth", "values": {"poisson_ratio": .3}, "previous": {}}})
        response = ModelResult("fixture-plan", "ok", "已校验材料修改计划",
                               proposed_plan=proposal, provider_calls=1)
        with patch("benchagent.model_gateway.deepseek.DeepSeekGateway.propose_plan", return_value=response):
            page.button(key="preview").click().run()
        self.assertEqual([item.value for item in page.error], [])
        self.assertFalse(page.exception)
        return page

    def test_first_visit_opens_dialogue_and_four_workspace_tabs_without_work(self):
        page = AppTest.from_file(str(ROOT / "ui.py"), default_timeout=20).run()
        self.assertEqual(page.radio(key="input_source").value, "智能测试（DeepSeek V4）")
        self.assertEqual([tab.label for tab in page.tabs],
                         ["任务计划", "运行监控", "结果报告", "历史实验分析"])
        self.assertTrue(page.text_area(key="rule_prompt"))
        self.assertFalse(page.exception)
        self.assertFalse(self.ui_service.store.path.exists())
        self.no_process_mock.assert_not_called()

    def test_removed_rule_selection_migrates_to_deepseek_without_api_call(self):
        page = AppTest.from_file(str(ROOT / "ui.py"), default_timeout=20)
        page.session_state["input_source"] = "智能测试（规则对话）"
        page.run()
        self.assertEqual(page.radio(key="input_source").value, "智能测试（DeepSeek V4）")
        self.assertEqual(page.radio(key="input_source").options, ["手动表单", "DeepSeek V4"])
        self.assertFalse(self.ui_service.store.path.exists())
        self.assertFalse(page.exception)
        self.no_process_mock.assert_not_called()

    def test_successful_send_clears_composer_and_preserves_plan_history_and_confirmation(self):
        page = self.model_plan()
        self.assertEqual(page.text_area(key="rule_prompt").value, "")
        ctx = copy.deepcopy(page.session_state["workspace"])
        self.assertIsNotNone(ctx["plan"])
        self.assertFalse(page.button(key="submit").disabled)
        messages = self.ui_service.conversation(ctx["conversation_id"])["messages"]
        self.assertIn("泊松比从0.243修改为0.3", messages[0]["content"])
        page.run()
        current = page.session_state["workspace"]
        self.assertEqual(current["plan"], ctx["plan"])
        self.assertEqual(current["confirmation_request_id"], ctx["confirmation_request_id"])
        self.assertEqual(current["execution_request_id"], ctx["execution_request_id"])
        self.assertEqual(page.text_area(key="rule_prompt").value, "")
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_pending_clear_does_not_discard_a_newly_typed_message(self):
        page = self.model_plan()
        page.session_state["_clear_sent_prompt"] = "上一条已发送的内容"
        page.text_area(key="rule_prompt").set_value("改成29帧").run()
        self.assertEqual(page.text_area(key="rule_prompt").value, "改成29帧")
        self.assertFalse(page.exception)

    def test_successful_material_query_clears_composer_and_keeps_conversation(self):
        page = self.model_plan()
        old = page.session_state["workspace"]["conversation_id"]
        page.text_area(key="rule_prompt").set_value("查询当前材料参数").run()
        page.button(key="preview").click().run()
        self.assertEqual([e.value for e in page.error], [])
        self.assertFalse(page.exception)
        self.assertEqual(page.text_area(key="rule_prompt").value, "")
        self.assertEqual(page.session_state["workspace"]["conversation_id"], old)
        conversation = self.ui_service.conversation(old)
        self.assertEqual(conversation["messages"][-2]["content"], "查询当前材料参数")
        self.assertIsNotNone(conversation["active_draft_id"])
        self.assertFalse(self.ui_service.jobs())

    def test_pending_frame_edits_require_new_preview_and_keep_original_snapshot(self):
        page = self.model_plan()
        old = copy.deepcopy(page.session_state["workspace"]["plan"])
        key = "plan-edit-" + old["plan_id"]
        page.number_input(key=key + "-frames-cloth-hang").set_value(37).run()
        self.assertTrue(page.button(key="submit").disabled)
        page.button(key=key + "-apply").click().run()
        plan = page.session_state["workspace"]["plan"]
        self.assertNotEqual(plan["plan_id"], old["plan_id"])
        self.assertEqual(plan["execution_snapshot"]["request"]["task_frames"], [37])
        self.assertEqual(plan["execution_snapshot"]["request"]["material"]["values"], {"poisson_ratio": .3})
        self.assertEqual(self.ui_service.plans.get_plan(old["plan_id"]), old)
        self.assertFalse(page.button(key="submit").disabled)
        self.assertFalse(self.ui_service.jobs())

    def test_plan_editor_preserves_other_materials_and_scene_change_uses_target_defaults(self):
        page = self.model_plan()
        key = "plan-edit-" + page.session_state["workspace"]["plan"]["plan_id"]
        page.number_input(key=key + "-cloth-hang-young_pa").set_value(900000.).run()
        self.assertTrue(page.button(key="submit").disabled)
        page.button(key=key + "-apply").click().run()
        req = page.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        self.assertEqual(req["material"]["values"], {"poisson_ratio": .3, "young_pa": 900000})
        self.assertEqual(req["material"]["previous"], {"poisson_ratio": .3})
        key = "plan-edit-" + page.session_state["workspace"]["plan"]["plan_id"]
        page.selectbox(key=key + "-scene").set_value("cloth-sphere7").run()
        page.button(key=key + "-apply").click().run()
        req = page.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        self.assertEqual(req["scene"], "cloth-sphere7")
        self.assertNotIn("material", req)
        self.assertEqual(req["task_frames"], [20])
        self.assertFalse(page.exception)
        self.assertFalse(self.ui_service.jobs())

    def test_new_chat_preserves_saved_conversation_and_session_key(self):
        page = self.model_plan()
        old = page.session_state["workspace"]["conversation_id"]
        page.session_state["deepseek_api_key"] = "fixture"
        page.button(key="smart-new-chat").click().run()
        self.assertIsNone(page.session_state["workspace"]["conversation_id"])
        self.assertIsNone(page.session_state["workspace"]["plan"])
        self.assertEqual(page.text_area(key="rule_prompt").value, "")
        self.assertEqual(page.session_state["deepseek_api_key"], "fixture")
        self.assertTrue(self.ui_service.conversation(old)["messages"])
        self.assertFalse(self.ui_service.jobs())

    def test_suggestions_only_fill_input_and_objectives_keep_budget_limit(self):
        page = self.app()
        page.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        page.button(key="smart-suggest-悬挂布料").click().run()
        self.assertEqual(page.text_area(key="rule_prompt").value, "悬挂布料运行20帧")
        self.assertFalse(self.ui_service.store.path.exists())
        page.session_state["smart-tab"] = "历史实验分析"
        page.run()
        self.assertEqual(page.selectbox(key="goal-gpu").options, ["1", "0"])
        self.assertTrue(page.button(key="goal-review"))
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_target_inputs_persist_across_workspace_tabs(self):
        page = self.app()
        page.session_state["smart-tab"] = "历史实验分析"
        page.run()
        page.selectbox(key="goal-gpu").set_value(0).run()
        page.text_area(key="goal-text").set_value("分析球体与布料，只看耗时和迭代。").run()
        page.session_state["smart-tab"] = "任务计划"
        page.run()
        page.session_state["smart-tab"] = "历史实验分析"
        page.run()
        self.assertEqual(page.selectbox(key="goal-gpu").value, 0)
        self.assertIn("只看耗时和迭代", page.text_area(key="goal-text").value)
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_result_tab_reads_archive_and_exposes_existing_export_formats(self):
        write(self.ui_root / "runs/history-20/report.json", {
            "status": "PASS", "actual_frames": 20, "solver_time_total_s": .2,
            "newton_iterations_total": 40, "pcg_iterations_total_from_csv": 140,
            "pcg_solves": 40, "frame_solver_times_ms": [10]*20, "errors": [], "warnings": []})
        (self.ui_root / "runs/history-20/stdout.log").write_text("测试归档日志", encoding="utf-8")
        page = self.app()
        select_archive(page.session_state["workspace"], "history-20")
        page.session_state["smart-tab"] = "结果报告"
        page.run()
        self.assertFalse(page.exception)
        self.assertEqual([item.value for item in page.error], [])
        self.assertEqual(page.selectbox(key="export-format").options, ["PDF", "Markdown", "JSON", "CSV", "ZIP 证据包"])
        self.assertTrue(any("测试归档日志" in item.value for item in page.code))
        self.assertIsNone(self.ui_service.manager)
        self.no_process_mock.assert_not_called()
