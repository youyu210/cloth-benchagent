"""Authorized infrastructure experiment: fixed C0, one smoke then one suite."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, now, sha, write
from benchagent.planning_store import PlanningStore
from benchagent.conversation_service import ConversationService
from benchagent.plan_service import PlanService
from benchagent.preflight import inspect
from benchagent.runner import execute
from benchagent.archive_repository import ArchiveRepository
from benchagent.view_models import render_archive_markdown, metric_rows
from benchagent.reporter import markdown_table
from benchagent.presentation import status_text

def main():
    experiment_id="bg02-"+time.strftime("%Y%m%d-%H%M%S")
    folder=ROOT/"docs/experiments"/experiment_id
    folder.mkdir(parents=True,exist_ok=False)
    historical={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    write(folder/"historical_hashes_before.json",historical)
    preflight=inspect()
    write(folder/"preflight.json",preflight)
    if not preflight["ready"]:
        write(folder/"result.json",{"status":"SKIPPED","errors":preflight["errors"]})
        print("PREFLIGHT_FAILED",flush=True)
        return 1
    protocol={
        "experiment_id":experiment_id,"created_at":now(),"producer_kind":"simulation",
        "authorization":"用户指示：继续，可以推进实验了",
        "purpose":"固定 C0 的真实运行及 BenchAgent 证据链集成验证",
        "comparison":None,"repeats_per_suite_case":1,
        "stages":[{"name":"smoke","frames":[20]},{"name":"standard","frames":[20,50,100]}],
        "scene":"default / source scene_no=1","backend_id":"c0-003-benchagent-v1",
        "timeout_sec_per_case":600,
        "success_criteria":"20帧先通过，再推进套件；每项沿用P00/V01-V12检查，不更改判定器",
        "scope":"不比较其他算法，不改求解参数；单次结果不构成稳定性能或物理精度认证",
        "gpu":preflight["gpu"],"execution_links":[]}
    write(folder/"protocol.json",protocol)
    store=PlanningStore()
    conv=ConversationService(store)
    plans=PlanService(store)
    conversation=conv.create_conversation("BG02 真实集成验证 "+experiment_id)
    write(folder/"conversation.json",conversation)
    results=[]
    for index,(name,frames) in enumerate((("smoke",[20]),("standard",[20,50,100]))):
        request={"schema_version":"task-request-v1","request_id":experiment_id+"-"+name,
                 "mode":"custom" if name=="smoke" else "standard","scene":"default",
                 "backend_id":"c0-003-benchagent-v1","task_frames":frames,"timeout_sec":600}
        draft=conv.update_draft(conversation["conversation_id"],request,index)
        plan=plans.preview_plan(draft["draft_id"],draft["revision"])
        receipt=plans.confirm_plan(plan["plan_id"],plan["plan_digest"],experiment_id+"-confirm-"+name)
        plans.verify_confirmation(receipt["confirmation_id"])
        write(folder/(name+"_plan.json"),plan)
        write(folder/(name+"_confirmation.json"),receipt)
        # Explicit authorized legacy entry; BG-02 confirmation itself never executes.
        legacy={"action":"run","scene":"default","frames":20} if name=="smoke" else {
            "action":"bench","scene":"default","suite_id":"default-mvp"}
        print("START "+name+" "+str(frames)+" "+now(),flush=True)
        report=execute(legacy,timeout_sec=request["timeout_sec"],
                       progress=lambda case,state: print("CASE "+case+" "+state,flush=True))
        report_id=report.get("suite_run_id",report.get("run_id"))
        protocol["execution_links"].append({
            "stage":name,"conversation_id":conversation["conversation_id"],"draft_id":draft["draft_id"],
            "draft_revision":draft["revision"],"plan_id":plan["plan_id"],"plan_digest":plan["plan_digest"],
            "confirmation_id":receipt["confirmation_id"],"execution_entry":"legacy runner.execute",
            "job_id":None,"report_id":report_id,"recorded_result_status":report["status"]})
        write(folder/"protocol.json",protocol)
        results.append({"stage":name,"report_id":report_id,"status":report["status"]})
        write(folder/(name+"_result.json"),report)
        repo=ArchiveRepository()
        view=repo.get_view(report_id)
        (folder/(name+"_只读报告.md")).write_text(render_archive_markdown(view),encoding="utf-8")
        if name=="standard":
            rows=[]
            for case in view["cases"]:
                metric={row["指标"]:str(row["数值"])+" "+row["单位"] for row in metric_rows(case,kpis_only=True)}
                rows.append({"用例":str(case["metrics"]["actual_frames"]["value"])+"帧","原记录结论":status_text(case["recorded_result_status"]),**metric})
            (folder/"中文指标表.md").write_text(
                "# 真实集成验证指标\n\n本次为固定 C0 单次标准套件，各项从相同初态独立启动。\n\n"+
                markdown_table(rows)+"\n物理精度未评估；不能据单次测量推断稳定性能或加速比。\n",encoding="utf-8")
        print("END "+name+" "+report["status"]+" "+str(report_id),flush=True)
        if report["status"]!="PASS":
            break
    unchanged=all((ROOT/name).is_file() and sha(ROOT/name)==digest for name,digest in historical.items())
    outcome={"experiment_id":experiment_id,"ended_at":now(),"results":results,
             "status":"PASS" if len(results)==2 and all(v["status"]=="PASS" for v in results) else "FAIL",
             "historical_files_checked":len(historical),"historical_hashes_unchanged":unchanged,
             "new_simulator_processes":sum(3 if v["stage"]=="standard" else 1 for v in results),
             "model_calls":0,"native_rebuilds":0,"quality_status":"NOT_ASSESSED"}
    write(folder/"result.json",outcome)
    write(ROOT/"docs/BG02_experiment_latest.json",{"folder":folder.relative_to(ROOT).as_posix(),**outcome})
    print(json.dumps(outcome,ensure_ascii=False),flush=True)
    return 0 if outcome["status"]=="PASS" and unchanged else 1

if __name__=="__main__":
    raise SystemExit(main())
