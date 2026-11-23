"""BG-05 application/UI identity and progress tests with isolated stream files."""
import copy
import unittest
from pathlib import Path
from benchagent.common import write,sha
from benchagent.ui_models import live_case_view,job_phase_markup
from support import WorkbenchHarness
from test_progress_reader import HEADER,row

class TelemetryHarness(WorkbenchHarness):
    def seed(self,active="smoke-20",state_name="ACTIVE"):
        cases=[{"case_id":"smoke-20","frames":20,"phase":"RUNNING","result_status":None},
               {"case_id":"standard-50","frames":50,"phase":"PENDING","result_status":None}]
        if active=="standard-50":
            cases[0]["phase"]="COMPLETED";cases[1]["phase"]="RUNNING"
        job={"job_id":"job-a","plan_id":"plan-a","state":state_name,"result_status":None,
             "active_case_id":active,"active_case_index":0 if active=="smoke-20" else 1,
             "cases":cases,"seq":3,"events":[],"cancel_requested":False,"report_id":None}
        with self.ui_service.store.transaction() as state:state["jobs"]["job-a"]=job
        return job
    def put_frames(self,case_id,count,partial=False):
        path=self.ui_root/"runs/job-a/cases"/case_id/"raw/frames.csv"
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(HEADER+b"".join(row(i) for i in range(1,count+1))+(str(count+1)+",10,2").encode() if partial else HEADER+b"".join(row(i) for i in range(1,count+1)))
        return path
    def put_log(self,cid,text,stream="stdout"):
        path=self.ui_root/"runs/job-a/cases"/cid/(stream+".log")
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8")
        return path
    def live_app(self):
        app=self.app()
        app.session_state["workspace"].update(mode="live",job_id="job-a")
        app.run()
        self.assertEqual([e.message for e in app.exception],[])
        return app

class ServiceTelemetryTests(TelemetryHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench();self.seed()
    def tearDown(self):self.teardown_workbench()
    def test_total_is_sum_of_real_rows_and_selected_targets(self):
        self.put_frames("smoke-20",3);self.put_frames("standard-50",2)
        value=self.ui_service.telemetry("job-a")
        self.assertEqual(value["confirmed_total"],5);self.assertEqual(value["target_total"],70)
        self.assertAlmostEqual(value["fraction"],5/70)
        self.assertIsNone(self.ui_service.manager)
    def test_cancelled_and_skipped_do_not_inflate_progress(self):
        self.put_frames("smoke-20",3)
        with self.ui_service.store.transaction() as state:
            job=state["jobs"]["job-a"];job.update(state="CANCELLED",result_status="FAIL")
            job["cases"][0].update(phase="CANCELLED",result_status="FAIL")
            job["cases"][1].update(phase="SKIPPED",result_status="SKIPPED")
        value=self.ui_service.telemetry("job-a")
        self.assertEqual(value["confirmed_total"],3);self.assertLess(value["fraction"],1)
        self.assertFalse(value["cases"][1]["issues"])
    def test_cursor_binding_and_case_membership_are_enforced(self):
        self.put_frames("smoke-20",1)
        value=self.ui_service.telemetry("job-a")
        with self.assertRaises(ValueError):self.ui_service.telemetry("job-a",{"other":{}})
        with self.assertRaises(ValueError):self.ui_service.telemetry("job-a",[])
        with self.assertRaises(ValueError):
            self.ui_service.telemetry("job-a",{"standard-50":value["cursors"]["smoke-20"]})
        with self.assertRaises(ValueError):self.ui_service.log_slice("job-a","other")
    def test_stdout_stderr_and_cases_use_independent_cursors(self):
        self.put_log("smoke-20","甲");self.put_log("smoke-20","错误","stderr")
        self.put_log("standard-50","乙")
        first=self.ui_service.log_slice("job-a","smoke-20")
        self.assertEqual(first["text"],"甲")
        self.assertEqual(self.ui_service.log_slice("job-a","smoke-20","stderr")["text"],"错误")
        self.assertEqual(self.ui_service.log_slice("job-a","standard-50")["text"],"乙")
        with self.assertRaises(ValueError):
            self.ui_service.log_slice("job-a","standard-50",cursor=first)
    def test_observation_view_uses_collected_denominator_and_no_verdict(self):
        self.put_frames("smoke-20",3)
        value=self.ui_service.telemetry("job-a")["cases"][0]
        view=live_case_view("job-a",value)
        self.assertEqual(view["data_source"]["view_mode"],"live")
        self.assertIsNone(view["recorded_result_status"])
        self.assertEqual(view["metrics"]["pcg_mean_per_frame"]["value"],7)
        self.assertEqual(view["metrics"]["pcg_mean_per_frame"]["denominator"]["value"],3)
        self.assertIsNone(view["metrics"]["collision_counter_total"]["value"])
    def test_reads_leave_metadata_and_stream_hashes_unchanged(self):
        csv=self.put_frames("smoke-20",2);log=self.put_log("smoke-20","运行")
        before=[sha(p) for p in (self.ui_service.store.path,csv,log)]
        value=self.ui_service.telemetry("job-a")
        self.ui_service.telemetry("job-a",value["cursors"])
        self.ui_service.log_slice("job-a","smoke-20")
        self.assertEqual(before,[sha(p) for p in (self.ui_service.store.path,csv,log)])
        self.no_process_mock.assert_not_called()
    def test_stage_markup_does_not_invent_analysis_after_cancel(self):
        job=self.ui_service.job("job-a")
        job["state"]="CANCELLED";job["cases"][0]["phase"]="CANCELLED"
        job["events"]=[{"case_id":"smoke-20","type":"PHASE","phase":p}
                       for p in ("PREFLIGHT","PREPARING","RUNNING","REPORTING")]
        html=job_phase_markup(job,"smoke-20")
        self.assertIn('flow-step pending"><span>5</span><b>数据分析',html)

class TelemetryUITests(TelemetryHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench();self.seed()
    def tearDown(self):self.teardown_workbench()
    def test_partial_line_and_rerun_do_not_double_count(self):
        path=self.put_frames("smoke-20",4,partial=True)
        app=self.live_app()
        cursors=app.session_state["_frame_cursors_job-a"]
        self.assertEqual(len(cursors["smoke-20"]["frames"]),4)
        with path.open("ab") as f:f.write(b",7,2\n")
        app.run();app.run()
        self.assertEqual([e.message for e in app.exception],[])
        self.assertEqual(len(app.session_state["_frame_cursors_job-a"]["smoke-20"]["frames"]),5)
        table=next(t.value for t in app.dataframe if "已采集帧数" in t.value.columns)
        self.assertEqual(table["已采集帧数"].tolist(),[5,0])
        self.assertEqual(table["校验结论"].tolist(),["尚未产生","尚未产生"])
    def test_case_selection_binds_metrics_keyframes_and_log(self):
        self.seed(active="standard-50")
        self.put_frames("smoke-20",5);self.put_frames("standard-50",2)
        self.put_log("smoke-20","20帧日志");self.put_log("standard-50","50帧日志")
        app=self.live_app()
        self.assertEqual(app.selectbox(key="_observed_case_job-a").value,"standard-50")
        app.checkbox(key="follow-case-job-a").set_value(False).run()
        app.selectbox(key="_observed_case_job-a").set_value("smoke-20").run()
        self.assertEqual([e.message for e in app.exception],[])
        self.assertIn("20帧日志"," ".join(c.value for c in app.code))
        self.assertNotIn("50帧日志"," ".join(c.value for c in app.code))
        keys=next(t.value for t in app.dataframe if "帧号" in t.value.columns)
        self.assertEqual(keys["帧号"].max(),5)
        self.assertIn("实时观测 · 5帧"," ".join(m.value for m in app.markdown))
    def test_pausing_logs_does_not_pause_progress_and_resume_keeps_text(self):
        csv=self.put_frames("smoke-20",1);log=self.put_log("smoke-20","甲")
        app=self.live_app()
        key="_log_cursor_job-a_smoke-20_stdout"
        first=copy.deepcopy(app.session_state[key])
        app.checkbox(key="follow-log-job-a").set_value(False).run()
        with log.open("ab") as f:f.write("乙".encode())
        with csv.open("ab") as f:f.write(row(2))
        app.run()
        self.assertEqual(app.session_state[key]["offset"],first["offset"])
        self.assertEqual(len(app.session_state["_frame_cursors_job-a"]["smoke-20"]["frames"]),2)
        app.checkbox(key="follow-log-job-a").set_value(True).run()
        self.assertEqual(app.session_state[key]["text"],"甲乙")
        self.assertEqual([e.message for e in app.exception],[])
    def test_stream_switch_never_reuses_stdout_cursor(self):
        self.put_frames("smoke-20",1);self.put_log("smoke-20","正常");self.put_log("smoke-20","错误","stderr")
        app=self.live_app()
        app.radio(key="live-stream-job-a").set_value("stderr").run()
        self.assertEqual([e.message for e in app.exception],[])
        self.assertIn("错误"," ".join(c.value for c in app.code))
        self.assertNotIn("正常"," ".join(c.value for c in app.code))
    def test_input_edit_hides_previous_task_telemetry(self):
        self.put_frames("smoke-20",2)
        app=self.live_app()
        app.selectbox(key="single_frames").set_value(50).run()
        self.assertEqual([e.message for e in app.exception],[])
        self.assertIsNone(app.session_state["workspace"]["job_id"])
        self.assertEqual(app.session_state["workspace"]["mode"],"plan_preview")
        self.assertEqual(len([t for t in app.dataframe if "已采集帧数" in t.value.columns]),0)
