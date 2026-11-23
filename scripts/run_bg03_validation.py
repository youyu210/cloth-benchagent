"""Authorized async integration: one completed run and one deliberate live cancellation."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, now, sha, write, read
from benchagent.planning_store import PlanningStore
from benchagent.conversation_service import ConversationService
from benchagent.plan_service import PlanService
from benchagent.job_manager import JobManager, TERMINAL
from benchagent.preflight import inspect
from benchagent.archive_repository import ArchiveRepository
from benchagent.view_models import render_archive_markdown

def main():
    experiment_id="bg03-"+time.strftime("%Y%m%d-%H%M%S")
    folder=ROOT/"docs/experiments"/experiment_id
    folder.mkdir(parents=True,exist_ok=False)
    historical={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    write(folder/"historical_hashes_before.json",historical)
    preflight=inspect();write(folder/"preflight.json",preflight)
    if not preflight["ready"]:raise RuntimeError("Preflight not ready")
    store=PlanningStore();conversations=ConversationService(store);plans=PlanService(store)
    manager=JobManager(store,plans=plans).start()
    protocol={"experiment_id":experiment_id,"created_at":now(),
              "authorization":"用户已允许推进实验，本轮继续BG-03链路验证",
              "purpose":"验证异步单工作器、幂等提交、运行中取消及退出确认",
              "scene":"default / scene_no=1","backend_id":"c0-003-benchagent-v1",
              "model_calls":0,"native_rebuilds":0,"jobs":[]}
    write(folder/"protocol.json",protocol)
    jobs=[]
    try:
        for stage,frames in (("complete",[20]),("cancel",[20,100])):
            conversation=conversations.create_conversation("BG03 "+stage+" "+experiment_id)
            request={"schema_version":"task-request-v1","request_id":experiment_id+"-"+stage,
                     "mode":"custom","scene":"default","backend_id":"c0-003-benchagent-v1",
                     "task_frames":frames,"timeout_sec":120}
            draft=conversations.update_draft(conversation["conversation_id"],request,0)
            plan=plans.preview_plan(draft["draft_id"],1)
            confirmation=plans.confirm_plan(plan["plan_id"],plan["plan_digest"],experiment_id+"-confirm-"+stage)
            args=(plan["plan_id"],plan["plan_digest"],experiment_id+"-submit-"+stage,confirmation["confirmation_id"])
            receipt=manager.submit_plan(*args)
            retry=manager.submit_plan(*args)
            if retry["job_id"]!=receipt["job_id"]:raise RuntimeError("Duplicate submission")
            job_id=receipt["job_id"]
            protocol["jobs"].append({"stage":stage,"job_id":job_id,"plan_id":plan["plan_id"],
                "plan_digest":plan["plan_digest"],"confirmation_id":confirmation["confirmation_id"],
                "idempotent_retry_job_id":retry["job_id"]})
            write(folder/"protocol.json",protocol)
            print("START "+stage+" "+job_id,flush=True)
            deadline=time.monotonic()+120
            cancellation=None
            while True:
                job=manager.get_job_snapshot(job_id)
                if job["state"] in TERMINAL:break
                if stage=="cancel" and cancellation is None:
                    csv=ROOT/"runs"/job_id/"cases/smoke-20/raw/frames.csv"
                    active=next((v for v in job["cases"] if v["case_id"]==job["active_case_id"]),None)
                    if job["active_case_id"]=="smoke-20" and active and active["phase"]=="RUNNING" and csv.exists():
                        content=csv.read_text(encoding="utf-8-sig")
                        if content.count("\n")>=2:
                            cancellation=manager.cancel_job(job_id,experiment_id+"-cancel")
                            write(folder/"cancel_receipt.json",cancellation)
                            print("CANCEL_REQUESTED "+job_id,flush=True)
                if time.monotonic()>deadline:raise TimeoutError("Async validation deadline")
                time.sleep(.05)
            write(folder/(stage+"_job.json"),job)
            summary=read(ROOT/"runs"/job_id/"benchmark_summary.json")
            write(folder/(stage+"_summary.json"),summary)
            archive=ArchiveRepository().get_view(job_id)
            (folder/(stage+"_只读报告.md")).write_text(render_archive_markdown(archive),encoding="utf-8")
            if stage=="complete":
                assert job["state"]=="COMPLETED" and job["result_status"]=="PASS",str((job["job_id"],job["state"],job["result_status"]))
                assert len(summary["tasks"])==1
            else:
                assert cancellation is not None,job
                assert job["state"]=="CANCELLED",job
                assert [v["status"] for v in summary["tasks"]]==["FAIL","SKIPPED"]
                assert summary["tasks"][0]["failure_kind"]=="CANCELLED"
                assert not (ROOT/"runs"/job_id/"cases/extended-100/run_meta.json").exists()
            meta=read(ROOT/"runs"/job_id/"cases/smoke-20/run_meta.json")
            assert meta["process_tree_empty"] is True
            assert meta["process_tracking"]=="windows-job-object-suspended-v1"
            assert job["child_pid"] is None and not (ROOT/"runs/.execution.lock").exists()
            jobs.append({"stage":stage,"job_id":job_id,"state":job["state"],
                         "result_status":job["result_status"],"pid":meta["pid"],
                         "process_tree_empty":meta["process_tree_empty"]})
            print("END "+stage+" "+job["state"]+" "+str(job["result_status"]),flush=True)
    finally:
        if not manager.close():raise RuntimeError("Worker did not finish cleanup")
    unchanged=all(sha(ROOT/name)==value for name,value in historical.items())
    assert unchanged
    result={"experiment_id":experiment_id,"ended_at":now(),"status":"PASS","jobs":jobs,
            "new_simulator_processes":2,"historical_files_checked":len(historical),
            "historical_hashes_unchanged":unchanged,"worker_closed":True,
            "execution_lock_released":not (ROOT/"runs/.execution.lock").exists(),
            "model_calls":0,"native_rebuilds":0,"quality_status":"NOT_ASSESSED"}
    write(folder/"result.json",result)
    write(ROOT/"docs/BG03_experiment_latest.json",{"folder":folder.relative_to(ROOT).as_posix(),**result})
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":main()
