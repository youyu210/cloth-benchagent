"""BG-04 behavior tests: isolated metadata, deterministic results and no network."""
import copy
import json
import unittest
from unittest.mock import patch,MagicMock
from benchagent.common import ROOT,write
from benchagent.ui_models import NAVIGATION, initial_context, inputs_changed, activate_plan
from support import WorkbenchHarness

class WorkbenchTests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench()
    def tearDown(self):self.teardown_workbench()
    def no_exceptions(self,app):
        self.assertEqual([e.message for e in app.exception],[])
        return app
    def seed_archive(self):
        write(self.ui_root/"runs/history-20/report.json",{
            "status":"PASS","actual_frames":20,"solver_time_total_s":.2,
            "newton_iterations_total":40,"pcg_iterations_total_from_csv":140,
            "pcg_solves":40,"frame_solver_times_ms":[10]*20,"collision_counter_total":None,
            "errors":[],"warnings":[]})
        (self.ui_root/"runs/history-20/stdout.log").write_text("历史日志内容",encoding="utf-8")

    def test_six_routes_are_read_only_and_default_is_empty(self):
        app=self.no_exceptions(self.app())
        self.assertEqual(tuple(app.sidebar.radio(key="navigation").options),NAVIGATION)
        self.assertEqual(app.session_state["workspace"]["mode"],"empty")
        for route in NAVIGATION:
            app.sidebar.radio(key="navigation").set_value(route).run()
            self.no_exceptions(app)
        self.assertFalse((self.ui_root/"runs").exists())
        self.assertFalse(self.ui_service.store.path.exists())
        self.assertIsNone(self.ui_service.manager)
        self.no_process_mock.assert_not_called()

    def test_preview_and_rerun_do_not_create_jobs_or_runs(self):
        app=self.app();app.button(key="preview").click().run();self.no_exceptions(app)
        for _ in range(2):app.run();self.no_exceptions(app)
        self.assertFalse(self.ui_service.jobs())
        self.assertFalse((self.ui_root/"runs").exists())
        self.assertIsNone(self.ui_service.manager)

    def test_editing_form_clears_plan_and_submission_identity(self):
        app=self.app();app.button(key="preview").click().run()
        previous=copy.deepcopy(app.session_state["workspace"])
        app.selectbox(key="single_frames").set_value(50).run();self.no_exceptions(app)
        current=app.session_state["workspace"]
        self.assertIsNone(current["plan"])
        self.assertIsNone(current["execution_request_id"])
        self.assertNotEqual(current["intent_id"],previous["intent_id"])
        self.assertEqual(len([b for b in app.button if b.key=="submit"]),0)

    def test_fixed_and_custom_frames_are_single_case_plans(self):
        app=self.app()
        self.assertEqual(app.radio(key="task_kind").options,["固定帧数","自定义帧数"])
        self.assertFalse(app.multiselect)
        for frames in (20,50,100):
            app.selectbox(key="single_frames").set_value(frames).run()
            app.button(key="preview").click().run();self.no_exceptions(app)
            request=app.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
            self.assertEqual(request["task_frames"],[frames])
            self.assertEqual(request["mode"],"custom")
        app.radio(key="task_kind").set_value("自定义帧数").run()
        for frames in (37,137):
            next(w for w in app.number_input if w.label=="自定义仿真帧数").set_value(frames).run()
            app.button(key="preview").click().run();self.no_exceptions(app)
            request=app.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
            self.assertEqual(request["task_frames"],[frames])
        self.assertFalse(self.ui_service.jobs())

    def test_old_frame_type_resets_and_invalidates_preview(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(ROOT/"ui.py"),default_timeout=20)
        previous=initial_context()
        previous.update(mode="plan_preview",plan={"old_preview":True},execution_request_id="old-execution",
                        input_signature=("表单配置","default","标准套件",False,(20,50,100),3600))
        app.session_state["workspace"]=copy.deepcopy(previous)
        app.session_state["input_source"]="表单配置"
        app.session_state["task_kind"]="标准套件"
        app.run();self.no_exceptions(app)
        self.assertEqual(app.radio(key="task_kind").value,"固定帧数")
        self.assertIsNone(app.session_state["workspace"]["plan"])
        self.assertIsNone(app.session_state["workspace"]["execution_request_id"])
        self.assertNotEqual(app.session_state["workspace"]["intent_id"],previous["intent_id"])
        self.assertFalse(self.ui_service.jobs())

    def test_dialogue_requires_model_key_without_creating_plan(self):
        app=self.app()
        app.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        app.text_area(key="rule_prompt").set_value("默认场景运行50帧").run()
        app.button(key="preview").click().run()
        self.no_exceptions(app)
        self.assertIsNone(app.session_state["workspace"]["plan"])
        self.assertIn("请在系统设置中保存 DeepSeek API 密钥。", [e.value for e in app.error])
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_parameter_override_does_not_execute(self):
        from benchagent.planning_store import PlanningError
        with self.assertRaises(PlanningError):
            self.ui_service.preview(None, "默认场景运行20帧并修改容差", 0)
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_brand_rules_and_real_results_only(self):
        app=self.app();app.button(key="preview").click().run()
        self.no_exceptions(app)
        self.assertNotIn("show_demo", [b.key for b in app.button])
        self.assertNotIn("leave_demo", [b.key for b in app.button])
        text=" ".join(m.value for m in app.markdown)
        self.assertIn("布料仿真BenchAgent",text)
        self.assertNotIn("StiffGIPC BenchAgent",text)
        self.assertNotIn("合成演示",text)
        self.assertTrue(any("统一测试规则" in expander.label for expander in app.expander))
        self.assertFalse(self.ui_service.jobs())

    def test_archive_and_report_have_chinese_tables_missing_collision(self):
        self.seed_archive();app=self.app()
        app.sidebar.radio(key="navigation").set_value("测试报告").run();self.no_exceptions(app)
        tables=[v.value for v in app.dataframe]
        self.assertTrue(any("碰撞对计数" in t.columns and "未记录" in t["碰撞对计数"].tolist() for t in tables))
        self.assertTrue(any("指标" in t.columns and "数值" in t.columns for t in tables))
        self.assertIn("历史日志内容"," ".join(c.value for c in app.code))
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_history_selection_navigates_to_correct_report(self):
        self.seed_archive();app=self.app()
        app.sidebar.radio(key="navigation").set_value("运行记录").run()
        next(b for b in app.button if b.label=="查看归档报告").click().run();self.no_exceptions(app)
        self.assertEqual(app.sidebar.radio(key="navigation").value,"测试报告")
        self.assertEqual(app.selectbox(key="report_select").value,"history-20")

    def test_navigation_does_not_submit_confirmed_preview(self):
        app=self.app();app.button(key="preview").click().run()
        request_id=app.session_state["workspace"]["execution_request_id"]
        for route in ("配置管理","系统设置","智能测试"):
            app.sidebar.radio(key="navigation").set_value(route).run();self.no_exceptions(app)
        self.assertEqual(app.session_state["workspace"]["execution_request_id"],request_id)
        self.assertIsNone(self.ui_service.manager)
        self.assertFalse(self.ui_service.jobs())

    def test_submit_click_uses_frozen_ids_once_without_legacy_execute(self):
        app=self.app();app.button(key="preview").click().run()
        old=copy.deepcopy(app.session_state["workspace"])
        # UI wiring is exercised with a deterministic receipt. Process lifecycle
        # itself is covered by job-manager tests and the BG-03 real evidence.
        with patch.object(self.ui_service,"submit",return_value={"job_id":"synthetic-job"}) as submit,patch.object(
                self.ui_service,"job",return_value={"job_id":"synthetic-job","state":"QUEUED","active_case_id":None,
                "active_case_index":None,"cases":[{"case_id":"smoke-20","frames":20,"phase":"PENDING","result_status":None}],
                "result_status":None,"cancel_requested":False}),patch.object(self.ui_service,"job_log",return_value="尚未启动"):
            app.button(key="submit").click().run();self.no_exceptions(app)
            app.run();self.no_exceptions(app)
            submit.assert_called_once_with(old["plan"]["plan_id"],old["plan"]["plan_digest"],
                old["confirmation_request_id"],old["execution_request_id"])
            self.assertEqual(app.session_state["workspace"]["job_id"],"synthetic-job")
        self.no_process_mock.assert_not_called()

    def test_new_preview_clears_selected_history(self):
        self.seed_archive();app=self.app()
        ctx=app.session_state["workspace"];ctx.update(mode="archive",archive_id="history-20")
        app.button(key="preview").click().run();self.no_exceptions(app)
        self.assertEqual(app.session_state["workspace"]["mode"],"plan_preview")
        self.assertIsNone(app.session_state["workspace"]["archive_id"])
        self.assertIsNone(app.session_state["workspace"]["job_id"])

    def test_pdf_gate_csv_available_and_model_disabled(self):
        self.seed_archive();app=self.app()
        app.sidebar.radio(key="navigation").set_value("测试报告").run()
        self.assertTrue(app.button(key="generate-export").disabled)
        app.selectbox(key="export-format").set_value("CSV").run()
        self.assertFalse(app.button(key="generate-export").disabled)
        app.sidebar.radio(key="navigation").set_value("系统设置").run()
        next(w for w in app.text_input if w.label=="DeepSeek API 密钥").set_value("test-key")
        next(b for b in app.button if b.label=="保存密钥").click().run()
        self.no_exceptions(app)
        self.assertEqual(app.session_state["deepseek_api_key"],"test-key")
        app.sidebar.radio(key="navigation").set_value("智能测试").run()
        app.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        with patch.object(self.ui_service,"preview",side_effect=ValueError("offline")) as preview:
            app.button(key="preview").click().run()
        self.assertEqual(preview.call_args.kwargs,{"use_model":True,"api_key":"test-key","selected_scene":None})

    def test_configuration_values_come_from_current_profile(self):
        profile=self.ui_service.configuration()
        profile["args"][profile["args"].index("--residual-threshold")+1]="0.123"
        with patch.object(self.ui_service,"configuration",return_value=profile):
            app=self.app();app.sidebar.radio(key="navigation").set_value("配置管理").run();self.no_exceptions(app)
        data=app.dataframe[0].value
        self.assertEqual(data.loc[data["参数"]=="归一化残差阈值","固定值"].iloc[0],"0.123")

    def test_bounded_log_read_rejects_other_case_or_stream(self):
        self.seed_archive()
        with self.assertRaises(ValueError):self.ui_service.archive_log("history-20","other")
        with self.assertRaises(ValueError):self.ui_service.archive_log("history-20",stream="../secret")
        with self.assertRaises(ValueError):self.ui_service.archive_log("../outside")
        self.assertEqual(self.ui_service.archive_log("history-20"),"历史日志内容")

class ConnectedWorkbenchTests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench()
    def tearDown(self):self.teardown_workbench()
    def connected_patches(self):
        from test_job_manager import FixtureProcess
        from support import FakeTracker
        FixtureProcess.calls=[];FixtureProcess.block=False;FixtureProcess.exit_code=0;FixtureProcess.started.clear()
        return FixtureProcess,[
            patch("benchagent.runner.ROOT",self.ui_root),
            patch("benchagent.runner.inspect",return_value={"ready":True,"gpu":"synthetic","errors":[]}),
            patch("benchagent.runner.identity",return_value={"synthetic":True}),
            patch("benchagent.runner.ProcessTracker",FakeTracker),
            patch("benchagent.runner.subprocess.Popen",FixtureProcess)]

    def wait_terminal(self,job_id):
        import time
        from benchagent.job_manager import TERMINAL
        deadline=time.monotonic()+5
        while self.ui_service.job(job_id)["state"] not in TERMINAL:
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.02)

    def test_concrete_app_service_submission_returns_one_job_and_report(self):
        process,patches=self.connected_patches()
        for p in patches:p.start()
        try:
            app=self.app();app.selectbox(key="single_frames").set_value(20).run()
            app.button(key="preview").click().run()
            before=copy.deepcopy(app.session_state["workspace"])
            app.button(key="submit").click().run()
            self.assertEqual([e.message for e in app.exception],[])
            job_id=app.session_state["workspace"]["job_id"]
            self.wait_terminal(job_id);app.run()
            self.assertEqual([e.message for e in app.exception],[])
            retry=self.ui_service.submit(before["plan"]["plan_id"],before["plan"]["plan_digest"],
                before["confirmation_request_id"],before["execution_request_id"])
            self.assertEqual(retry["job_id"],job_id)
            self.assertEqual(process.calls,[20])
            self.assertEqual(self.ui_service.job(job_id)["result_status"],"PASS")
            self.assertTrue(any("PCG迭代总次数" in m.value for m in app.markdown))
        finally:
            self.ui_service.close()
            for p in reversed(patches):p.stop()

    def test_restored_multi_case_job_can_be_cancelled_and_skip_following_case(self):
        process,patches=self.connected_patches()
        for p in patches:p.start()
        process.block=True
        try:
            app=self.app()
            restored=self.ui_service.preview(None,"默认场景运行20、100帧",0)
            activate_plan(app.session_state["workspace"],restored)
            app.run()
            app.button(key="submit").click().run()
            self.assertEqual([e.message for e in app.exception],[])
            job_id=app.session_state["workspace"]["job_id"]
            self.assertTrue(process.started.wait(3))
            app.run()
            app.button(key="cancel-"+job_id).click().run()
            self.wait_terminal(job_id);app.run()
            self.assertEqual([e.message for e in app.exception],[])
            job=self.ui_service.job(job_id)
            self.assertEqual(job["state"],"CANCELLED")
            self.assertEqual([c["result_status"] for c in job["cases"]],["FAIL","SKIPPED"])
            self.assertEqual(process.calls,[20])
        finally:
            self.ui_service.close()
            for p in reversed(patches):p.stop()
