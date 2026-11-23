import csv
import json
from pathlib import Path

def make_fixture(raw, n=20):
    raw.mkdir(parents=True)
    rows = []
    for i in range(1, n+1):
        rows.append(dict(frame=i, newton_iterations=2, pcg_solves=2, pcg_iterations=7,
            newton_cap_hits=0, diagnostic_budget_hits=0, fallbacks=0, nonfinite_vertices=0,
            direction_launches=7, tail_launches=5, step_ms=10.0, newton_ms=8.0, pcg_ms=3.0))
    with (raw/"frames.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["frame"])
        w.writeheader();w.writerows(rows)
    (raw/"pcg_iterations.jsonl").write_text("".join(json.dumps({"frame":i,"iterations":[3,4]})+"\n" for i in range(1,n+1)),encoding="utf-8")
    meta=dict(frames=n, scene_no=1, cloth_scene="", initial_physical_frame=0,
        base_commit="c49982fac7767225c7775683734c89eb74081142",
        termination="compensated", method="pcg_dual_graph", converter_indexed_reduce=True,
        toi_min_updates=6, toi_threshold=.001, residual_threshold=.03, residual_floor=1e-30,
        residual_weight=1/30, newton_tolerance_scale=1, pcg_tolerance_scale=1,
        production_measurement=True, profile_stages=False, trace_newton=False,
        audit_frame=0, checkpoint_in="")
    for name,obj in {"metadata.json":meta,
        "status.json":{"completed":True,"frames_completed":n,"finite_state":True},
        "collision_summary.json":{"collision_counter_total":123,"collision_pair_max":12}}.items():
        (raw/name).write_text(json.dumps(obj),encoding="utf-8")

def execution(n=20):
    return dict(returncode=0, timeout=False, identity_unchanged=True,
                fresh_output=True, stdout=f"TEST_COMPLETE frames={n}\n")


class FakeTracker:
    """Injected test tracker; never queries or terminates a host process."""
    terminated=0
    def __init__(self,process):self.process=process
    def terminate(self):
        type(self).terminated+=1
        self.process.kill()
    def finish(self,timeout=10):pass
    def close(self):pass


class WorkbenchHarness:
    def setup_workbench(self):
        import copy
        import tempfile
        from unittest.mock import patch
        from benchagent.common import ROOT,read,write
        from benchagent.planning_store import PlanningStore
        from benchagent.plan_service import PlanService
        from benchagent.app_service import AppService
        self.ui_tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests")
        self.ui_root=Path(self.ui_tmp.name)
        write(self.ui_root/"configs/c0.json",read(ROOT/"configs/c0.json"))
        store=PlanningStore(self.ui_root/"data/planning")
        plans=PlanService(store,snapshot_provider=lambda req:{"request":copy.deepcopy(req)})
        self.ui_service=AppService(self.ui_root,store=store,plans=plans)
        self.ui_patch=patch("benchagent.ui_app.get_app_service",return_value=self.ui_service)
        self.ui_patch.start()
        self.no_process=patch("benchagent.runner.subprocess.Popen",side_effect=AssertionError("UI action unexpectedly spawned a process"))
        self.no_process_mock=self.no_process.start()
        self.no_network=patch("socket.create_connection",side_effect=AssertionError("network"))
        self.no_network.start()

    def teardown_workbench(self):
        self.ui_service.close()
        self.no_network.stop()
        self.no_process.stop()
        self.ui_patch.stop()
        self.ui_tmp.cleanup()

    def app(self):
        from streamlit.testing.v1 import AppTest
        from benchagent.common import ROOT
        page = AppTest.from_file(str(ROOT/"ui.py"),default_timeout=20)
        page.session_state["input_source"] = "表单配置"
        return page.run()
