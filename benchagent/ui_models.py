"""Deterministic UI context and Chinese labels."""
from html import escape
from .planning_store import new_id
from .view_models import build_report_view

NAVIGATION=("智能测试","运行记录","测试报告","配置管理","系统设置")
JOB_LABELS={"QUEUED":"排队中","ACTIVE":"执行中","CANCELLING":"正在取消","COMPLETED":"已完成",
            "CANCELLED":"已取消","FAILED":"执行失败","INTERRUPTED":"已中断"}
PHASE_LABELS={"PENDING":"待启动","PREFLIGHT":"环境检查","PREPARING":"配置参数","RUNNING":"运行仿真",
              "PARSING":"解析数据","VALIDATING":"校验数据","REPORTING":"生成报告",
              "COMPLETED":"已结束","SKIPPED":"已跳过","CANCELLED":"已取消","FAILED":"执行失败"}
READ_LABELS={"OK":"完整可读","PARTIAL":"部分可读","ERROR":"读取失败","INCOMPLETE":"归档未完成"}
STEPS=("任务解析","环境检查","配置参数","运行仿真","数据分析","生成报告")

def initial_context():
    return {"mode":"empty","conversation_id":None,"draft_revision":0,"plan":None,
            "job_id":None,"archive_id":None,"input_signature":None,"intent_id":new_id("ui-intent"),
            "confirmation_request_id":None,"execution_request_id":None,"message":None}

def inputs_changed(context,signature):
    if context["input_signature"] is None:
        context["input_signature"]=signature
        return False
    if context["input_signature"]==signature:return False
    context.update(input_signature=signature,plan=None,mode="plan_preview",job_id=None,archive_id=None,
                   intent_id=new_id("ui-intent"),confirmation_request_id=None,execution_request_id=None,
                   message="输入已修改，请重新预览计划。")
    return True

def activate_plan(context,result):
    context.update(conversation_id=result["conversation_id"],draft_revision=result["draft"]["revision"],
        plan=result["plan"],mode="plan_preview",job_id=None,archive_id=None,
        confirmation_request_id=new_id("ui-confirm"),execution_request_id=new_id("ui-submit"),
        message="计划已冻结，尚未提交执行。")

def select_job(context,job_id):
    context.update(mode="live",job_id=job_id,archive_id=None,message=None)

def select_archive(context,report_id):
    context.update(mode="archive",archive_id=report_id,job_id=None,message=None)

def phase_markup(phase=None,*,preview=False,ended=False,interrupted=False):
    index={"PREFLIGHT":1,"PREPARING":2,"RUNNING":3,"PARSING":4,"VALIDATING":4,
           "REPORTING":5,"COMPLETED":5}.get(phase,-1)
    if preview:index=0
    items=[]
    for i,label in enumerate(STEPS):
        state="done" if ended and not interrupted else "done" if i<index else "current" if i==index else "pending"
        if preview and i==0:state="done"
        symbol="✓" if state=="done" else str(i+1)
        items.append('<div class="flow-step '+state+'"><span>'+symbol+'</span><b>'+escape(label)+'</b></div>')
    return '<div class="flow">'+"".join(items)+'</div>'

def case_summary_rows(view):
    from .presentation import status_text,task_text,display_value
    rows=[]
    for case in view.get("cases",[]):
        m=case["metrics"]
        def number(key):return display_value(m[key]["value"],m[key]["format"])
        rows.append({"任务":task_text(case["case_id"]),"状态":status_text(case["recorded_result_status"]),
            "实际帧数":number("actual_frames"),"物理步总耗时（秒）":number("solver_time_total_s"),
            "平均每帧耗时（毫秒）":number("solver_time_mean_ms_per_frame"),
            "平均牛顿迭代（次/帧）":number("newton_iterations_mean_per_frame"),
            "平均PCG迭代（次/帧）":number("pcg_mean_per_frame"),
            "PCG迭代总次数（逐帧聚合）":number("pcg_iterations_total_from_csv")})
    return rows

def live_case_view(job_id,case):
    rows=case["frames"]
    raw={"producer_kind":"simulation","actual_frames":len(rows),
         "solver_time_total_s":sum(v["solver_time_ms"] for v in rows)/1000 if rows else None,
         "newton_iterations_total":sum(v["newton_iterations"] for v in rows) if rows else None,
         "pcg_iterations_total_from_csv":sum(v["pcg_iterations"] for v in rows) if rows else None,
         "pcg_solves":sum(v["pcg_solves"] for v in rows) if rows and all(v["pcg_solves"] is not None for v in rows) else None}
    view=build_report_view({"report_id":job_id+"/cases/"+case["case_id"],"kind":"single","raw_report":raw,
        "frames":rows,"recorded_result_status":None,"archive_read_status":"PARTIAL" if case["issues"] else "OK",
        "issues":case["issues"],"cases":[]})
    view["data_source"]["view_mode"]="live"
    return view

def job_phase_markup(job,case_id):
    mapping={"PREFLIGHT":1,"PREPARING":2,"RUNNING":3,"PARSING":4,"VALIDATING":4,"REPORTING":5}
    events=[e for e in job.get("events",[]) if e.get("case_id")==case_id and e.get("type")=="PHASE"]
    phases=[mapping[e["phase"]] for e in events if e.get("phase") in mapping]
    case=next(c for c in job["cases"] if c["case_id"]==case_id)
    if not phases and case["phase"] in mapping:phases=[mapping[case["phase"]]]
    visited={0,*phases}
    index=phases[-1] if phases else 0
    terminal=job["state"] in ("COMPLETED","CANCELLED","FAILED","INTERRUPTED") or case["phase"] in ("COMPLETED","CANCELLED","SKIPPED","FAILED")
    items=[]
    for i,label in enumerate(STEPS):
        state="done" if i in visited and (terminal or i!=index or i==0) else "current" if i==index else "pending"
        symbol="✓" if state=="done" else str(i+1)
        items.append('<div class="flow-step '+state+'"><span>'+symbol+'</span><b>'+escape(label)+'</b></div>')
    return '<div class="flow">'+"".join(items)+'</div>'
