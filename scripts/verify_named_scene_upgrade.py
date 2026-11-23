"""Real GPU acceptance for named-scene routing and custom frame counts."""
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.app_service import AppService
from benchagent.common import ROOT, read, write
from benchagent.conversation_service import request_from_text
from benchagent.planning_store import PlanningStore, new_id


def main():
    qa = ROOT / "docs/qa/benchagent_v4_named"
    qa.mkdir(parents=True, exist_ok=True)
    store = PlanningStore(qa / ("planning-" + uuid4().hex[:8]))
    service = AppService(store=store)
    evidence = []
    cases = [("hang-37", "运行悬挂布料，37帧", "hang", 37),
             ("sphere7-137", "运行球体与布料，137帧", "sphere7", 137)]
    try:
        for label, prompt, selector, count in cases:
            req = {**request_from_text(prompt), "native_render": True}
            plan = service.preview(None, req, 0)["plan"]
            assert plan["execution_snapshot"]["environment_policy"]["CIPC_SCENE"] == selector
            jid = service.submit(plan["plan_id"], plan["plan_digest"], new_id("confirm"),
                                 new_id("execute"))["job_id"]
            print(label, jid, flush=True)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = service.job(jid)
                if job["state"] in ("COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"):
                    break
                time.sleep(.25)
            else:
                raise TimeoutError(jid)
            case = job["cases"][0]["case_id"]
            rid = f"{jid}/cases/{case}"
            path = ROOT / "runs" / rid
            report = read(path / "report.json")
            visual = report.get("visualization") or {}
            row = dict(job_id=jid, case=case, scene=req["scene"], selector=selector, frames=count,
                       result=job.get("result_status"), identity=report.get("execution_identity"),
                       images=[], errors=report.get("errors"), visualization=visual)
            evidence.append(row)
            write(qa / "real_runs.json", evidence)
            print(job["state"], job.get("result_status"), visual.get("kind"), flush=True)
            assert report["status"] == "PASS" and report["actual_frames"] == count, row
            assert read(path / "raw/metadata.json")["cloth_scene"] == selector
            assert visual["cloth_scene"] == selector
            for frame in visual["frames"]:
                image = qa / f"{label}-{frame}.png"
                image.write_bytes(service.native_frame(rid, frame))
                row["images"].append(image.relative_to(ROOT).as_posix())
            write(qa / "real_runs.json", evidence)
    finally:
        service.close()


if __name__ == "__main__":
    main()
