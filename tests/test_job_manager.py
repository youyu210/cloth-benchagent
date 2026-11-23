"""BG-03 lifecycle tests use only fixture processes inside tests/."""
import copy
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from benchagent.common import ROOT, read, write
from benchagent.planning_store import PlanningStore, PlanningError
from benchagent.conversation_service import ConversationService
from benchagent.plan_service import PlanService
from benchagent.job_manager import JobManager, TERMINAL
from benchagent.execution_lease import ExecutionLease, CleanupRequired, LeaseBusy
from support import make_fixture, FakeTracker

class FixtureProcess:
    calls=[]
    block=False
    exit_code=0
    started=threading.Event()
    def __init__(self,command,**kwargs):
        self.pid=999999
        self.killed=False
        self.frame=int(command[command.index("--benchmark-frames")+1])
        type(self).calls.append(self.frame)
        raw=Path(command[command.index("--benchmark-output-dir")+1])
        make_fixture(raw,self.frame)
        kwargs["stdout"].write(("TEST_COMPLETE frames="+str(self.frame)+"\n").encode())
        type(self).started.set()
    def wait(self,timeout=None):
        if self.killed:return -9
        if type(self).block:
            time.sleep(min(timeout or .01,.01))
            raise subprocess.TimeoutExpired("fixture",timeout)
        return type(self).exit_code
    def kill(self):self.killed=True

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.root=Path(self.tmp.name)
        write(self.root/"configs/c0.json",read(ROOT/"configs/c0.json"))
        self.store=PlanningStore(self.root/"data/planning")
        self.conversations=ConversationService(self.store)
        self.version=1
        self.plans=PlanService(self.store,snapshot_provider=lambda req:{"request":copy.deepcopy(req),"version":self.version})
        self.manager=JobManager(self.store,plans=self.plans,root=self.root).start(worker=False)
        self.patches=[
            patch("benchagent.runner.ROOT",self.root),
            patch("benchagent.runner.inspect",return_value={"ready":True,"gpu":"synthetic","errors":[]}),
            patch("benchagent.runner.identity",return_value={"synthetic":True}),
            patch("benchagent.runner.ProcessTracker",FakeTracker),
            patch("benchagent.runner.subprocess.Popen",FixtureProcess)]
        for context in self.patches:context.start()
        FixtureProcess.calls=[];FixtureProcess.block=False;FixtureProcess.exit_code=0
        FixtureProcess.started.clear();FakeTracker.terminated=0
    def tearDown(self):
        self.manager.close()
        for context in reversed(self.patches):context.stop()
        self.tmp.cleanup()

    def prepare(self,frames=None,timeout=60):
        c=self.conversations.create_conversation()
        req={"schema_version":"task-request-v1","request_id":"intent-test","mode":"custom","scene":"default",
             "backend_id":"c0-003-benchagent-v1","task_frames":frames or [20],"timeout_sec":timeout}
        draft=self.conversations.update_draft(c["conversation_id"],req,0)
        plan=self.plans.preview_plan(draft["draft_id"],1)
        confirmation=self.plans.confirm_plan(plan["plan_id"],plan["plan_digest"],"confirm-"+plan["plan_id"])
        return c,draft,plan,confirmation

    def submit(self,frames=None,timeout=60,request_id="execution-1"):
        c,d,p,confirmation=self.prepare(frames,timeout)
        receipt=self.manager.submit_plan(p["plan_id"],p["plan_digest"],request_id,confirmation["confirmation_id"])
        return receipt,c,d,p

    def code(self,wanted,method,*args):
        with self.assertRaises(PlanningError) as raised:method(*args)
        self.assertEqual(raised.exception.code,wanted)

    def test_atomic_idempotent_registration_and_duplicate_execution(self):
        c,d,p,confirmation=self.prepare()
        args=(p["plan_id"],p["plan_digest"],"same-request",confirmation["confirmation_id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts=list(pool.map(lambda _:self.manager.submit_plan(*args),range(2)))
        self.assertEqual(receipts[0]["job_id"],receipts[1]["job_id"])
        self.assertEqual(len(self.store.read()["jobs"]),1)
        self.manager.run_next();self.assertFalse(self.manager.run_next())
        self.assertEqual(FixtureProcess.calls,[20])

    def test_conflicting_submit_request(self):
        first,*_=self.submit()
        c,d,p,confirmation=self.prepare([50])
        self.code("REQUEST_CONFLICT",self.manager.submit_plan,p["plan_id"],p["plan_digest"],"execution-1",confirmation["confirmation_id"])

    def test_missing_confirmation_rejected(self):
        c,d,p,confirmation=self.prepare()
        self.code("NOT_FOUND",self.manager.submit_plan,p["plan_id"],p["plan_digest"],"execution-1","missing")
        self.assertFalse(self.store.read()["jobs"])

    def test_stale_submission_is_rejected(self):
        c,d,p,confirmation=self.prepare()
        req={**d["task_request"],"task_frames":[50]}
        self.conversations.update_draft(c["conversation_id"],req,1)
        self.code("PLAN_STALE",self.manager.submit_plan,p["plan_id"],p["plan_digest"],"execution-1",confirmation["confirmation_id"])

    def test_registration_disk_failure_does_not_accept_or_spawn(self):
        c,d,p,confirmation=self.prepare()
        before=self.store.path.read_bytes()
        with patch("benchagent.planning_store.write",side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.manager.submit_plan(p["plan_id"],p["plan_digest"],"execution-1",confirmation["confirmation_id"])
        self.assertEqual(before,self.store.path.read_bytes())
        self.assertEqual(FixtureProcess.calls,[])

    def test_subset_completes_using_selected_count_and_fixed_order(self):
        receipt,*_=self.submit([100,20])
        self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual((job["state"],job["result_status"]),("COMPLETED","PASS"))
        self.assertEqual(FixtureProcess.calls,[20,100])
        summary=read(self.root/"runs"/job["report_id"]/"benchmark_summary.json")
        self.assertEqual(summary["expected_count"],2)
        from benchagent.archive_repository import ArchiveRepository
        view=ArchiveRepository(self.root/"runs").get_view(job["report_id"])
        self.assertEqual(view["data_source"]["producer_kind"],"simulation")
        self.assertTrue(all(c["data_source"]["producer_kind"]=="simulation" for c in view["cases"]))
        self.assertTrue(all(t["report_path"].startswith("cases/") for t in summary["tasks"]))

    def test_submitted_snapshot_survives_later_draft_edits(self):
        receipt,c,d,p=self.submit()
        self.conversations.update_draft(c["conversation_id"],{**d["task_request"],"task_frames":[100]},1)
        self.manager.run_next()
        self.assertEqual(FixtureProcess.calls,[20])
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["result_status"],"PASS")

    def test_identity_change_after_queue_prevents_launch(self):
        receipt,*_=self.submit()
        self.version=2;self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual(job["failure_kind"],"IDENTITY_CHANGED")
        self.assertEqual(job["state"],"FAILED")
        self.assertEqual(FixtureProcess.calls,[])
        self.assertFalse((self.root/"runs"/receipt["job_id"]).exists())

    def test_queued_cancel_is_durable_and_never_launches(self):
        receipt,*_=self.submit([20,100])
        cancel=self.manager.cancel_job(receipt["job_id"],"cancel-1")
        self.assertEqual(cancel,self.manager.cancel_job(receipt["job_id"],"cancel-1"))
        self.assertFalse(self.manager.run_next())
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual((job["state"],job["result_status"]),("CANCELLED","SKIPPED"))
        self.assertTrue(all(c["phase"]=="SKIPPED" for c in job["cases"]))
        self.assertEqual(FixtureProcess.calls,[])

    def test_cancel_token_conflict(self):
        first,*_=self.submit()
        second,*_=self.submit([50],request_id="execution-2")
        self.manager.cancel_job(first["job_id"],"cancel-1")
        self.code("REQUEST_CONFLICT",self.manager.cancel_job,second["job_id"],"cancel-1")

    def test_preparing_cancel_skips_process(self):
        receipt,*_=self.submit()
        def ready():
            self.manager.cancel_job(receipt["job_id"],"cancel-preparing")
            return {"ready":True,"gpu":"synthetic","errors":[]}
        with patch("benchagent.runner.inspect",side_effect=ready):self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual(job["state"],"CANCELLED")
        self.assertEqual(job["cases"][0]["result_status"],"SKIPPED")
        self.assertEqual(FixtureProcess.calls,[])

    def test_running_cancel_kills_owned_tree_and_skips_later_case(self):
        receipt,*_=self.submit([20,50])
        FixtureProcess.block=True
        thread=threading.Thread(target=self.manager.run_next);thread.start()
        self.assertTrue(FixtureProcess.started.wait(2))
        self.manager.cancel_job(receipt["job_id"],"cancel-live")
        thread.join(3);self.assertFalse(thread.is_alive())
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual(job["state"],"CANCELLED")
        self.assertEqual([c["result_status"] for c in job["cases"]],["FAIL","SKIPPED"])
        self.assertEqual(FixtureProcess.calls,[20])
        self.assertGreater(FakeTracker.terminated,0)
        self.assertFalse((self.root/"runs/.execution.lock").exists())

    def test_final_analysis_returns_finalizing_and_preserves_pass(self):
        from benchagent.parser import parse_artifacts
        receipt,*_=self.submit()
        answers=[]
        def parse(path):
            answers.append(self.manager.cancel_job(receipt["job_id"],"cancel-final"))
            return parse_artifacts(path)
        with patch("benchagent.runner.parse_artifacts",side_effect=parse):self.manager.run_next()
        self.assertEqual(answers[0]["status"],"FINALIZING")
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["state"],"COMPLETED")
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["result_status"],"PASS")

    def test_cancel_analysis_preserves_finished_case_and_skips_next(self):
        from benchagent.parser import parse_artifacts
        receipt,*_=self.submit([20,50])
        def parse(path):
            self.manager.cancel_job(receipt["job_id"],"cancel-analysis")
            return parse_artifacts(path)
        with patch("benchagent.runner.parse_artifacts",side_effect=parse):self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual(job["state"],"CANCELLED")
        self.assertEqual([c["result_status"] for c in job["cases"]],["PASS","SKIPPED"])
        self.assertEqual(job["result_status"],"PARTIAL")

    def test_timeout_confirms_cleanup_and_marks_fail(self):
        receipt,*_=self.submit(timeout=1)
        FixtureProcess.block=True;self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        report=read(self.root/"runs"/job["job_id"]/"cases/smoke-20/report.json")
        self.assertEqual(report["failure_kind"],"TIMEOUT")
        self.assertEqual(job["result_status"],"FAIL")
        self.assertEqual(job["state"],"COMPLETED")
        self.assertFalse((self.root/"runs/.execution.lock").exists())

    def test_cleanup_failure_retains_lock_and_blocks_queue(self):
        receipt,*_=self.submit()
        later,*_=self.submit([50],request_id="execution-2")
        FixtureProcess.block=True
        class Broken(FakeTracker):
            def terminate(self):raise OSError("synthetic tree termination failure")
        with patch("benchagent.runner.ProcessTracker",Broken):
            thread=threading.Thread(target=self.manager.run_next);thread.start()
            self.assertTrue(FixtureProcess.started.wait(2))
            self.manager.cancel_job(receipt["job_id"],"cancel-unsafe")
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["failure_kind"],"CLEANUP_REQUIRED")
        self.assertTrue(read(self.root/"runs/.execution.lock")["cleanup_required"])
        self.assertTrue(self.manager.blocked)
        self.assertFalse(self.manager.run_next())
        self.assertEqual(FixtureProcess.calls,[20])
        self.assertEqual(self.manager.get_job_snapshot(later["job_id"])["state"],"QUEUED")

    def test_nonzero_exit_completed_is_not_pass(self):
        receipt,*_=self.submit()
        FixtureProcess.exit_code=5;self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual((job["state"],job["result_status"]),("COMPLETED","FAIL"))

    def test_reporting_error_does_not_publish_success(self):
        receipt,*_=self.submit()
        with patch("benchagent.runner.report_run",side_effect=OSError("report write failed")):
            self.manager.run_next()
        job=self.manager.get_job_snapshot(receipt["job_id"])
        self.assertEqual(job["state"],"FAILED")
        self.assertIsNone(job["report_id"])
        self.assertIsNone(job["result_status"])

    def test_old_pid_lock_blocks_without_stealing(self):
        receipt,*_=self.submit()
        path=self.root/"runs/.execution.lock"
        path.parent.mkdir(exist_ok=True);path.write_text("12345")
        self.assertFalse(self.manager.run_next())
        self.assertEqual(path.read_text(),"12345")
        self.assertTrue(self.manager.get_job_snapshot(receipt["job_id"])["waiting_for_lease"])
        self.assertEqual(FixtureProcess.calls,[])

    def test_second_manager_cannot_start(self):
        other=JobManager(self.store,plans=self.plans,root=self.root)
        self.code("MANAGER_BUSY",other.start)

    def test_restart_marks_queued_and_active_interrupted_without_replay(self):
        receipt,*_=self.submit()
        self.manager._update(receipt["job_id"],"ACTIVE",state="ACTIVE",child_pid=12345)
        self.manager.close()
        self.manager=JobManager(self.store,plans=self.plans,root=self.root).start(worker=False)
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["state"],"INTERRUPTED")
        self.assertFalse(self.manager.run_next())
        self.assertEqual(FixtureProcess.calls,[])

    def test_snapshot_cursor_does_not_mutate_or_start_job(self):
        receipt,*_=self.submit()
        before=self.store.path.read_bytes()
        view=self.manager.get_job_snapshot(receipt["job_id"],after_seq=1)
        self.assertEqual(view["events"],[])
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(FixtureProcess.calls,[])
        self.manager.run_next()
        events=self.manager.get_job_snapshot(receipt["job_id"])["events"]
        self.assertEqual([e["seq"] for e in events],list(range(1,len(events)+1)))

    def test_thread_worker_completes_asynchronously_and_closes(self):
        self.manager.close()
        self.manager=JobManager(self.store,plans=self.plans,root=self.root).start()
        receipt,*_=self.submit()
        deadline=time.monotonic()+4
        while self.manager.get_job_snapshot(receipt["job_id"])["state"] not in TERMINAL:
            self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        self.assertEqual(FixtureProcess.calls,[20])
        self.assertTrue(self.manager.close())
        self.assertFalse((self.store.root/".manager.lock").exists())

    def test_shutdown_cancels_running_work_before_releasing_owner(self):
        self.manager.close()
        self.manager=JobManager(self.store,plans=self.plans,root=self.root).start()
        FixtureProcess.block=True
        receipt,*_=self.submit()
        self.assertTrue(FixtureProcess.started.wait(2))
        self.assertTrue(self.manager.close(timeout=3))
        self.assertEqual(self.manager.get_job_snapshot(receipt["job_id"])["state"],"CANCELLED")
        self.assertFalse((self.root/"runs/.execution.lock").exists())

class LeaseTests(unittest.TestCase):
    def test_token_mismatch_preserves_foreign_lock(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as temp:
            path=Path(temp)/"lock"
            with self.assertRaises(CleanupRequired):
                with ExecutionLease(path,"test"):
                    write(path,{"owner_token":"foreign"})
            self.assertEqual(read(path)["owner_token"],"foreign")

    def test_uncertain_tree_keeps_lease(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as temp:
            path=Path(temp)/"lock"
            with ExecutionLease(path,"test") as lease:lease.attach(12345)
            self.assertTrue(read(path)["cleanup_required"])
            with self.assertRaises(LeaseBusy):
                with ExecutionLease(path,"later"):pass


class AtomicPublishTests(unittest.TestCase):
    def test_windows_transient_sharing_retries_only_replace(self):
        import os
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as temp:
            path=Path(temp)/"state.json";write(path,{"value":"old"})
            original=os.replace
            calls=[]
            def replace(source,target):
                calls.append((source,target))
                if len(calls)<3:
                    error=PermissionError("synthetic sharing");error.winerror=32
                    raise error
                return original(source,target)
            with patch("benchagent.common.os.replace",side_effect=replace),patch("benchagent.common.time.sleep"):
                write(path,{"value":"new"})
            self.assertEqual(read(path),{"value":"new"})
            self.assertEqual(len(calls),3)

    def test_permanent_publish_error_preserves_previous_state(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as temp:
            path=Path(temp)/"state.json";write(path,{"value":"old"})
            error=PermissionError("synthetic ACL");error.winerror=5
            with patch("benchagent.common.os.replace",side_effect=error),patch("benchagent.common.time.sleep"):
                with self.assertRaises(PermissionError):write(path,{"value":"new"})
            self.assertEqual(read(path),{"value":"old"})
