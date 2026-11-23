"""Execute the thesis acceptance suite through the production job manager."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.app_service import AppService
from benchagent.common import ROOT, read, write
from benchagent.planning_store import new_id

QA = ROOT / "docs/qa/benchagent_v4_upgrade"
CASES = [
    ("T1", "fixed-cloth", 20), ("T2", "default", 20),
    ("T3", "wrecking-ball", 20), ("T4", "twisting-mat", 20),
    ("T5", "fixed-cloth", 100),
    ("A0", "box-pipe", 20), ("A5", "large-box-pipe-cloth", 20),
]


def main():
    service = AppService()
    results = {}
    try:
        for label, scene, count in CASES:
            req = {"schema_version": "task-request-v2", "request_id": new_id("thesis"),
                   "mode": "custom", "scene": scene, "backend_id": "c0-003-benchagent-v1",
                   "task_frames": [count], "timeout_sec": 120, "native_render": True}
            plan = service.preview(None, req, 0)["plan"]
            receipt = service.submit(plan["plan_id"], plan["plan_digest"],
                                     new_id("confirm"), new_id("execute"))
            job_id = receipt["job_id"]
            print(label, job_id, flush=True)
            deadline = time.monotonic() + 150
            while True:
                job = service.job(job_id)
                if job["state"] in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(job_id)
                time.sleep(.25)
            case = job["cases"][0]["case_id"]
            report_id = job_id + "/cases/" + case
            path = ROOT / "runs" / report_id
            report = read(path / "report.json") if (path / "report.json").is_file() else {}
            visual = report.get("visualization") or {}
            evidence = []
            if visual.get("kind") == "native_render_frame":
                QA.mkdir(parents=True, exist_ok=True)
                for number in visual["frames"]:
                    image_path = QA / f"{label}-frame-{number}.png"
                    image_path.write_bytes(service.native_frame(report_id, number))
                    evidence.append(image_path.relative_to(ROOT).as_posix())
            results[label] = {"job_id": job_id, "case_id": case, "scene": scene, "frames": count,
                              "state": job["state"], "result": job.get("result_status"),
                              "visualization": visual, "images": evidence,
                              "error": job.get("error")}
            print(label, job["state"], job.get("result_status"), visual.get("kind"), flush=True)
            write(QA / "real_runs.json", results)
    finally:
        service.close()


if __name__ == "__main__":
    main()
