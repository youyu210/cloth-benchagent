"""Reproduce the cloth objective branches with real archives and an optional GPU run."""
import argparse
import csv
import json
from pathlib import Path
from statistics import median
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchagent.app_service import AppService
from benchagent.common import ROOT, sha, write
from benchagent.model_gateway import get_default_gateway

PARENT = "job-f0c29c3238ab4eddb961138407946393"
CASE = "frames-29"
TARGET = "分析球体与布料的高耗时帧，对照接触相关计数和 Newton/PCG 迭代，展示关键帧。"


def verify_statistics(analysis):
    source = ROOT / "runs" / analysis["source"]["case_ref"] / "raw/frames.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        data = list(csv.DictReader(stream))
    ranked = sorted(data, key=lambda row: (-float(row["step_ms"]), int(row["frame"])))
    stats = analysis["statistics"]
    n = len(stats["hot_frames"])
    assert stats["valid_steps"] == len(data)
    assert stats["median_ms"] == median(float(row["step_ms"]) for row in data)
    assert [int(row["frame"]) for row in ranked[:n]] == [row["frame"] for row in stats["hot_frames"]]
    for row in analysis["keyframes"]:
        assert row["offset"] == row["image_frame"] - row["target_frame"]
        assert sha(ROOT / "runs" / row["file"]) == row["sha256"]
    if stats["groups"]["contact_list_max"]["high"]["n"]:
        path = source.with_name("newton_trace.jsonl")
        traces = {entry["frame"]: max(s["contacts_before"]["contact_count"] for s in entry["samples"])
                  for entry in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
        for name, group in (("high", ranked[:n]), ("other", ranked[n:])):
            assert stats["groups"]["contact_list_max"][name]["median"] == median(
                traces[int(row["frame"])] for row in group)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-diagnostic", action="store_true",
                        help="Explicitly authorize one 29-frame GPU diagnostic, at most 600 seconds.")
    parser.add_argument("--deepseek", action="store_true",
                        help="Use the existing process credential for bounded paid model suggestions.")
    args = parser.parse_args()
    app = AppService()
    try:
        gateway = get_default_gateway() if args.deepseek else None
        before_dirs = {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
        parent_folder = ROOT / "runs" / PARENT
        before = {p: sha(p) for p in parent_folder.rglob("*") if p.is_file()}
        objectives = app.objectives
        # Keep the input and adjudication fixed across the legacy path and the new path.
        old = app.archive.get_report(PARENT)["cases"]
        assert any(case["case_id"] == CASE for case in old)
        legacy = {"same_source": PARENT + "/cases/" + CASE, "archive_read": True,
                  "goal_feedback": "UNSUPPORTED", "gpu_runs": 0, "model_calls": 0}
        sufficient = objectives.create("分析球体与布料仿真耗时，只看耗时和迭代。",
                                       report_id=PARENT, case_id=CASE, gateway=gateway)
        assert sufficient["status"] == "COMPLETE" and sufficient["gpu_submissions"] == 0
        verify_statistics(sufficient["analysis"])
        limited = objectives.create("分析球体与布料仿真的高耗时帧，定位 CCD 的分阶段计时。",
                                    report_id=PARENT, case_id=CASE)
        assert limited["status"] == "LIMITED" and limited["diagnostic"] is None
        assert any("CCD" in s for s in limited["analysis"]["missing"])
        proposal = objectives.create(TARGET, report_id=PARENT, case_id=CASE, gateway=gateway)
        assert proposal["status"] == "AWAITING_CONFIRMATION" and proposal["gpu_submissions"] == 0
        assert {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()} == before_dirs
        verify_statistics(proposal["analysis"])
        gpu_status = "NOT_EXECUTED"
        diagnostic_archive_review = None
        if args.run_diagnostic:
            assert not any(app.health()[k] for k in ("blocked", "execution_lock", "manager_lock"))
            assert not any(j["state"] in {"ACTIVE", "QUEUED"} for j in app.jobs())
            request = proposal["diagnostic"]["request"]
            assert request["task_frames"] == [29] and request["timeout_sec"] == 600
            proposal = objectives.confirm_diagnostic(proposal["objective_id"], proposal["digest"])
            first_job = proposal["child_job_id"]
            assert objectives.confirm_diagnostic(proposal["objective_id"], proposal["digest"])["child_job_id"] == first_job
            end = time.monotonic() + 615
            while proposal["status"] == "DIAGNOSTIC_SUBMITTED" and time.monotonic() < end:
                time.sleep(2)
                proposal = objectives.refresh(proposal["objective_id"])
            if proposal["status"] == "DIAGNOSTIC_SUBMITTED":
                app.cancel(first_job, first_job + "-qa-cancel")
                end = time.monotonic() + 30
                while proposal["status"] == "DIAGNOSTIC_SUBMITTED" and time.monotonic() < end:
                    time.sleep(1)
                    proposal = objectives.refresh(proposal["objective_id"])
            gpu_status = "PASS" if proposal["status"] == "COMPLETE" else "FAIL"
            if gpu_status == "PASS":
                verify_statistics(proposal["diagnostic_analysis"])
        if any(case["trace"] and case["images"] and case["status"] == "PASS"
               for case in objectives.cases("cloth-sphere7")):
            diagnostic_archive_review = objectives.create(TARGET)
            assert diagnostic_archive_review["status"] == "COMPLETE"
            assert diagnostic_archive_review["gpu_submissions"] == 0
            verify_statistics(diagnostic_archive_review["analysis"])
        assert all(sha(p) == digest for p, digest in before.items())
        created = {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()} - before_dirs
        expected = {proposal["child_job_id"]} if args.run_diagnostic else set()
        assert created == expected, (created, expected)
        folder = ROOT / "docs/BenchAgent_论文材料/目标驱动案例" / proposal["objective_id"]
        folder.mkdir(parents=True, exist_ok=False)
        actions = sufficient["actions"]
        write(folder / "evaluation.json", {
            "old_workflow": legacy, "read_only_feedback": sufficient, "limited_feedback": limited,
            "proposal_or_diagnostic": proposal, "diagnostic_archive_reuse": diagnostic_archive_review,
            "gpu_status": gpu_status, "new_gpu_tasks": len(created),
            "read_only_model_status": "PASS" if sufficient["model_used"] else
                                      "BLOCKED" if gateway is not None else "NOT_EXECUTED",
            "fixed_acceptance": ["归档帧统计独立复算", "原生图片哈希一致", "原始输入哈希保持",
                                 "已有证据充分或不可采时无 GPU 新任务", "补测只新增一个授权任务"],
            "read_only_actions": len(actions),
            "original_archive_sha256": {p.relative_to(ROOT).as_posix(): h for p, h in before.items()},
        })
        report = proposal.get("diagnostic_analysis") or proposal["analysis"]
        with (folder / "高耗时帧.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["帧号", "物理步耗时（ms）", "Newton 迭代", "PCG 累计迭代", "近距离约束列表峰值"])
            writer.writerows(row.values() for row in report["statistics"]["hot_frames"])
        print(json.dumps({"evidence": str(folder), "gpu": gpu_status, "objective": proposal["objective_id"],
                          "child": proposal["child_job_id"], "steps": report["statistics"]["valid_steps"],
                          "model_used": sufficient["model_used"], "analysis": proposal["status"]}))
        return int(gpu_status == "FAIL")
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
