"""Authorized stream integration: fixed C0 20/50 frames, no performance comparison."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT,now,read,write,sha
from benchagent.app_service import AppService
from benchagent.preflight import inspect
from benchagent.job_manager import TERMINAL
from benchagent.ui_models import live_case_view
from benchagent.progress_reader import DISPLAY_CHARS

def main():
    experiment_id="bg05-"+time.strftime("%Y%m%d-%H%M%S")
    folder=ROOT/"docs/experiments"/experiment_id
    folder.mkdir(parents=True,exist_ok=False)
    before={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    write(folder/"historical_hashes_before.json",before)
    preflight=inspect();write(folder/"preflight.json",preflight)
    if not preflight["ready"]:raise RuntimeError("Preflight not ready")
    service=AppService()
    request={"schema_version":"task-request-v1","request_id":experiment_id+"-intent","mode":"custom",
             "scene":"default","backend_id":"c0-003-benchagent-v1","task_frames":[20,50],"timeout_sec":120}
    preview=service.preview(None,request,0)
    plan=preview["plan"]
    protocol={"experiment_id":experiment_id,"created_at":now(),"purpose":"连续帧、来源绑定、暂停续读及终态归档链路",
              "authorization":"既有用户授权推进实验，本轮继续BG-05",
              "backend":"固定C0 / 默认场景1","task_frames":[20,50],"sample_interval_seconds":.05,
              "performance_sample":False,"model_calls":0,"native_rebuilds":0,"plan_id":plan["plan_id"],
              "plan_digest":plan["plan_digest"]}
    write(folder/"protocol.json",protocol)
    samples=[];frame_cursors=None;log_cursors={};previous_counts=None
    paused_at=None;pause_cursor=None;pause_until=None;resume_done=False
    try:
        receipt=service.submit(plan["plan_id"],plan["plan_digest"],experiment_id+"-confirm",experiment_id+"-submit")
        job_id=receipt["job_id"];protocol["job_id"]=job_id
        write(folder/"protocol.json",protocol)
        print("START "+job_id,flush=True)
        deadline=time.monotonic()+180
        while True:
            telemetry=service.telemetry(job_id,frame_cursors)
            frame_cursors=telemetry["cursors"]
            counts=[c["confirmed_frames"] for c in telemetry["cases"]]
            if previous_counts is not None:
                assert all(new>=old for new,old in zip(counts,previous_counts))
            for case in telemetry["cases"]:
                assert not case["issues"],case["issues"]
                view=live_case_view(job_id,case)
                assert view["recorded_result_status"] is None
                if case["confirmed_frames"]:
                    assert view["metrics"]["pcg_mean_per_frame"]["denominator"]["value"]==case["confirmed_frames"]
                if case["case_id"]=="smoke-20" and 3<=case["confirmed_frames"]<20 and paused_at is None:
                    paused_at=case["confirmed_frames"];pause_until=time.monotonic()+.3
                    pause_cursor=log_cursors.get((case["case_id"],"stdout"))
                for stream in ("stdout","stderr"):
                    key=(case["case_id"],stream)
                    follow=not (paused_at is not None and not resume_done and case["case_id"]=="smoke-20" and stream=="stdout" and time.monotonic()<pause_until)
                    old=log_cursors.get(key)
                    result=service.log_slice(job_id,case["case_id"],stream,old,follow=follow)
                    if not follow and old is not None:assert result==old
                    if paused_at is not None and not resume_done and time.monotonic()>=pause_until:
                        resume_done=True
                    log_cursors[key]=result
                    assert not result["issues"],result["issues"]
            if counts!=previous_counts or not samples or samples[-1]["state"]!=telemetry["job"]["state"]:
                sample={"observed_at":telemetry["observed_at"],"seq":telemetry["observed_seq"],
                        "state":telemetry["job"]["state"],"counts":counts,
                        "confirmed_total":telemetry["confirmed_total"],"target_total":telemetry["target_total"]}
                samples.append(sample)
                print("PROGRESS "+str(counts)+" / [20, 50] "+sample["state"],flush=True)
            previous_counts=counts
            if telemetry["job"]["state"] in TERMINAL:break
            if time.monotonic()>deadline:raise TimeoutError("Stream validation deadline")
            time.sleep(.05)
        job=telemetry["job"]
        assert job["state"]=="COMPLETED" and job["result_status"]=="PASS"
        assert counts==[20,50] and telemetry["confirmed_total"]==70
        summary=read(ROOT/"runs"/job_id/"benchmark_summary.json")
        assert all(t["status"]=="PASS" and len(t["checks"])==13 and all(c["status"]=="PASS" for c in t["checks"]) for t in summary["tasks"])
        # Drain any final backlog, then compare the displayed suffix against source bytes.
        for key,cursor in log_cursors.items():
            case_id,stream=key
            for _ in range(300):
                cursor=service.log_slice(job_id,case_id,stream,cursor,follow=True)
                if cursor["at_eof"] or cursor["identity"] is None:break
            path=ROOT/"runs"/job_id/"cases"/case_id/(stream+".log")
            expected=path.read_bytes().decode("utf-8",errors="replace")[-DISPLAY_CHARS:] if path.exists() else ""
            assert cursor["text"]==expected
        write(folder/"samples.json",samples)
        write(folder/"final_job.json",job)
        write(folder/"summary.json",summary)
        result={"experiment_id":experiment_id,"ended_at":now(),"status":"PASS","job_id":job_id,
                "new_simulator_processes":2,"confirmed_frames":counts,"confirmed_total":70,
                "distinct_progress_samples":len(samples),"monotonic_progress":True,
                "case_bound_observations":True,"log_suffixes_match_sources":True,
                "pause_triggered_at_frame":paused_at,"resumed":resume_done,
                "model_calls":0,"native_rebuilds":0,"performance_sample":False}
    finally:
        if not service.close():raise RuntimeError("Worker not closed")
    assert all(sha(ROOT/name)==digest for name,digest in before.items())
    result.update(historical_files_checked=len(before),historical_hashes_unchanged=True,
                  execution_lock_released=not (ROOT/"runs/.execution.lock").exists())
    write(folder/"result.json",result)
    write(ROOT/"docs/BG05_experiment_latest.json",{"folder":folder.relative_to(ROOT).as_posix(),**result})
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":main()
