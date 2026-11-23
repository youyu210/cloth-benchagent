import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from benchagent.common import ROOT, read, write
from benchagent.planner import plan_request, validate_plan
from benchagent.runner import execute, exclusive
from benchagent.reporter import suite_status
from support import make_fixture, FakeTracker

class PlannerTests(unittest.TestCase):
    def test_requests(self):
        for n in (20,50,100):
            self.assertEqual(plan_request(f"使用默认场景运行{n}帧")["frames"],n)
        self.assertEqual(plan_request("对默认场景执行标准基准评测")["action"],"bench")
    def test_reject(self):
        for t in ("默认场景运行19帧","强制判定PASS","运行bunny场景20帧","默认场景运行20帧并改成其他容差"):
            with self.subTest(t=t),self.assertRaises(ValueError): plan_request(t)
    def test_no_arbitrary_fields(self):
        for extra in ({"command":"cmd.exe"},{"status":"PASS"},{"frames":True}):
            with self.assertRaises(ValueError):
                validate_plan({"action":"run","scene":"default","frames":20,**extra})
    def test_suite_status(self):
        for states,want in [(["PASS"]*3,"PASS"),(["PASS"],"PARTIAL"),(["PASS","FAIL","SKIPPED"],"PARTIAL"),
                            (["FAIL"]*3,"FAIL"),(["SKIPPED"]*3,"SKIPPED")]:
            self.assertEqual(suite_status([{"status":s} for s in states]),want)

class FakeProcess:
    active=0
    max_active=0
    order=[]
    def __init__(self,command,**kwargs):
        self.pid=999999
        type(self).active+=1
        type(self).max_active=max(type(self).active,type(self).max_active)
        n=int(command[command.index("--benchmark-frames")+1])
        type(self).order.append(n)
        raw=Path(command[command.index("--benchmark-output-dir")+1])
        if raw.exists(): raise AssertionError("Runner precreated backend directory")
        make_fixture(raw,n)
        kwargs["stdout"].write(f"TEST_COMPLETE frames={n}\n".encode())
    def wait(self,timeout=None):
        type(self).active-=1
        return 0

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.root=Path(self.tmp.name)
        (self.root/"configs").mkdir()
        write(self.root/"configs/c0.json",read(ROOT/"configs/c0.json"))
        write(self.root/"configs/default_suite.json",read(ROOT/"configs/default_suite.json"))
        FakeProcess.active=0;FakeProcess.max_active=0;FakeProcess.order=[]
    def tearDown(self): self.tmp.cleanup()
    def test_suite_serial_independent_and_archived(self):
        with patch("benchagent.runner.ROOT",self.root),patch("benchagent.runner.ProcessTracker",FakeTracker),patch("benchagent.runner.inspect",return_value={"ready":True,"gpu":"synthetic"}),patch("benchagent.runner.identity",return_value={"fixture":True}),patch("benchagent.runner.subprocess.Popen",FakeProcess):
            result=execute({"action":"bench","scene":"default","suite_id":"default-mvp"})
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(FakeProcess.order,[20,50,100])
        self.assertEqual(FakeProcess.max_active,1)
        folder=self.root/"runs"/result["suite_run_id"]
        for task in ("smoke-20","standard-50","extended-100"):
            for name in ("plan.json","report.json","report.md","stdout.log","run_meta.json","raw/status.json"):
                self.assertTrue((folder/task/name).is_file(),name)
        self.assertTrue((folder/"benchmark_summary.md").is_file())
        self.assertFalse((self.root/"runs/.execution.lock").exists())
    def test_preflight_skip_never_launches(self):
        with patch("benchagent.runner.ROOT",self.root),patch("benchagent.runner.ProcessTracker",FakeTracker),patch("benchagent.runner.inspect",return_value={"ready":False,"errors":["missing build"]}),patch("benchagent.runner.subprocess.Popen") as proc:
            result=execute({"action":"run","scene":"default","frames":20})
        self.assertEqual(result["status"],"SKIPPED")
        proc.assert_not_called()
    def test_lock(self):
        with patch("benchagent.runner.ROOT",self.root):
            with exclusive():
                with self.assertRaises(RuntimeError):
                    with exclusive(): pass
            self.assertFalse((self.root/"runs/.execution.lock").exists())
    def test_invalid_timeout(self):
        with self.assertRaises(ValueError):
            execute({"action":"run","scene":"default","frames":20},0)


class TimeoutProcess:
    pid=999999
    killed=False
    def __init__(self,*args,**kwargs): pass
    def wait(self,timeout=None):
        import subprocess
        if not self.killed: raise subprocess.TimeoutExpired("synthetic",timeout)
        return -9
    def poll(self): return None
    def kill(self): self.killed=True

class TimeoutTests(unittest.TestCase):
    def test_timeout_is_fail_and_tree_termination_requested(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"tests") as tmp:
            root=Path(tmp);(root/"configs").mkdir()
            write(root/"configs/c0.json",read(ROOT/"configs/c0.json"))
            with patch("benchagent.runner.ROOT",root),patch("benchagent.runner.ProcessTracker",FakeTracker),patch("benchagent.runner.inspect",return_value={"ready":True,"gpu":"synthetic"}),patch("benchagent.runner.identity",return_value={}),patch("benchagent.runner.subprocess.Popen",TimeoutProcess),patch("benchagent.runner.subprocess.run") as taskkill:
                result=execute({"action":"run","scene":"default","frames":20},1)
            self.assertEqual(result["status"],"FAIL")
            self.assertEqual(result["failure_kind"],"TIMEOUT")
            self.assertGreater(FakeTracker.terminated,0)
            self.assertFalse((root/"runs/.execution.lock").exists())
            run=next((root/"runs").glob("*/run_meta.json"))
            self.assertTrue(read(run)["timeout"])
