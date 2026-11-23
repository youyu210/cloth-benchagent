"""Exercise live DeepSeek tool feedback on the completed 29-frame cloth diagnostic."""
from getpass import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchagent.app_service import AppService
from benchagent.common import ROOT, sha, write
from benchagent.model_gateway import get_default_gateway
from verify_objective_feedback import TARGET, verify_statistics

SOURCE = "job-58d6d5f1265f4269a363e98be24344f1"
CASE = "frames-29"


def main():
    app = AppService()
    try:
        case = next((item for item in app.objectives.cases("cloth-sphere7")
                     if item["report_id"] == SOURCE and item["case_id"] == CASE), None)
        if not case or not (case["status"] == "PASS" and case["trace"] and case["images"]):
            raise SystemExit("The verified diagnostic archive is unavailable.")
        folders_before = {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
        jobs_before = {job["job_id"] for job in app.jobs()}
        source_folder = ROOT / "runs" / case["native_ref"]
        source_hashes = {p: sha(p) for p in source_folder.rglob("*") if p.is_file()}

        key = os.environ.get("DEEPSEEK_API_KEY") or getpass("DeepSeek API key (hidden): ")
        gateway = get_default_gateway(key)
        del key
        if not gateway.get_status().ready:
            raise SystemExit("DeepSeek credential unavailable.")
        result = app.objectives.create(TARGET, report_id=SOURCE, case_id=CASE, gpu_budget=0, gateway=gateway)
        verify_statistics(result["analysis"])
        trials = result["model_requests"]
        accepted = [item for item in trials if item["outcome"] == "ACTION_VALIDATED"]
        followup = any(item["observation"]["done"] for item in accepted)
        original_unchanged = all(sha(path) == digest for path, digest in source_hashes.items())
        no_gpu = (
            result["gpu_submissions"] == 0 and result["child_job_id"] is None
            and folders_before == {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
            and jobs_before == {job["job_id"] for job in app.jobs()}
        )
        passed = (result["status"] == "COMPLETE" and result["model_used"] and len(accepted) >= 2
                  and len(accepted) == len(trials) and followup and original_unchanged and no_gpu)
        destination = ROOT / "docs/BenchAgent_论文材料/目标驱动案例" / result["objective_id"]
        destination.mkdir(parents=True, exist_ok=False)
        summary = {
            "test_status": "PASS" if passed else "FAIL",
            "provider": gateway.get_status().provider,
            "provider_calls": sum(item["provider_calls"] for item in trials),
            "accepted_actions": [item["selected"] for item in accepted],
            "followup_observation_accepted": followup,
            "no_gpu_submissions": no_gpu,
            "verified_input_files": len(source_hashes),
            "source_unchanged": original_unchanged,
            "outcomes": [item["outcome"] for item in trials],
            "case_ref": case["native_ref"],
            "valid_physical_steps": result["analysis"]["statistics"]["valid_steps"],
            "objective_id": result["objective_id"],
        }
        write(destination / "deepseek_objective.json", {"summary": summary, "objective": result})
        print(json.dumps(summary, ensure_ascii=True))
        return 0 if passed else 1
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
