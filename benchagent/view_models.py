"""Canonical, JSON-safe archive views. No execution and no verdict recalculation."""
import copy
import math
from .presentation import METRICS, display_value, status_text

SCHEMA = "archive-view-v1"
ARCHIVE_STATUS_TEXT = {"OK":"完整可读","PARTIAL":"部分可读","ERROR":"读取失败","INCOMPLETE":"归档未完成"}
LABELS = {key:(label,unit,kind) for key,label,unit,kind in METRICS}
LABELS.update({
    "pcg_mean_per_frame":("平均 PCG 迭代（按帧）","次/帧","decimal"),
    "pcg_mean_per_solve":("平均 PCG 迭代（每次求解）","次/求解","decimal"),
})
COUNT_KEYS = {"actual_frames","newton_iterations_total","pcg_iterations_total_from_csv",
              "pcg_iterations_total_from_stats","pcg_solves","collision_counter_total","collision_pair_max"}
KPI_KEYS = ("solver_time_mean_ms_per_frame","newton_iterations_mean_per_frame","pcg_mean_per_frame",
            "newton_iterations_total","pcg_iterations_total_from_csv","collision_counter_total")

def _number(value, integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)): return None
    try:
        if not math.isfinite(value) or value < 0 or (integer and int(value)!=value): return None
    except (OverflowError,ValueError):
        return None
    return int(value) if integer else float(value)

def _producer(raw):
    raw=raw or {}
    if raw.get("synthetic") is True or raw.get("source_kind")=="synthetic":return "synthetic"
    return raw.get("producer_kind") if raw.get("producer_kind") in ("simulation","synthetic") else "legacy_unknown"

def build_report_view(record):
    if record["kind"] == "suite":
        cases = [build_report_view(case) for case in record["cases"]]
        usable = [c for c in cases if c["archive_read_status"] == "OK" and c["metrics"]["actual_frames"]["value"]]
        selected = max(usable, key=lambda c:c["metrics"]["actual_frames"]["value"]) if usable else None
        return {
            "view_schema":SCHEMA, "report_id":record["report_id"],"kind":"suite",
            "recorded_result_status":record["recorded_result_status"],
            "archive_read_status":record["archive_read_status"],
            "data_source":{"view_mode":"archive","producer_kind":_producer(record["raw_report"]),"source_id":record["report_id"]},
            "cases":cases,"selected_case_id":selected.get("case_id") if selected else None,
            "issues":copy.deepcopy(record["issues"]),"raw_report":copy.deepcopy(record["raw_report"]),
        }
    raw = record["raw_report"] or {}
    issues = copy.deepcopy(record["issues"])
    source = record.get("report_source", record["report_id"] + "/report.json")
    def field_ref(key):
        return source + ("/" if "#" in source else "#") + key
    metrics = {}
    for key,(label,unit,kind) in LABELS.items():
        value = _number(raw.get(key), key in COUNT_KEYS)
        reason = None if value is not None else "INVALID" if key in raw and raw[key] is not None else "MISSING"
        if reason == "INVALID":
            issues.append({"code":"INVALID_METRIC","field":key,"message":"指标为非法或非有限数值"})
        if key == "pcg_iterations_coverage" and value is not None and value > 1:
            value,reason = None,"INVALID"
            issues.append({"code":"INVALID_COVERAGE","field":key,"message":"覆盖率超出范围"})
        metrics[key]={"value":value,"unit":unit,"label":label,"format":kind,
                      "reason":reason,"source":field_ref(key),"derived":False}

    def derive(key,numerator,denominator,scale=1):
        top,bottom = metrics[numerator]["value"],metrics[denominator]["value"]
        computed = top*scale/bottom if top is not None and bottom is not None and bottom > 0 else None
        if computed is not None and not math.isfinite(computed): computed = None
        observed = metrics[key]["value"]
        if computed is not None and observed is not None and not math.isclose(computed,observed,rel_tol=1e-5,abs_tol=1e-6):
            issues.append({"code":"RECORDED_MEAN_MISMATCH","field":key,"message":"记录均值与总量/分母不一致，保留原值供核对"})
        metrics[key].update(value=computed,derived=True,recorded_value=observed,
                            denominator={"field":denominator,"value":bottom},
                            source=[field_ref(numerator),field_ref(denominator)],
                            reason=None if computed is not None else "ZERO_DENOMINATOR" if bottom == 0 else "MISSING_INPUT")
    derive("solver_time_mean_ms_per_frame","solver_time_total_s","actual_frames",1000)
    derive("newton_iterations_mean_per_frame","newton_iterations_total","actual_frames")
    derive("pcg_mean_per_frame","pcg_iterations_total_from_csv","actual_frames")
    derive("pcg_mean_per_solve","pcg_iterations_total_from_csv","pcg_solves")

    times=raw.get("frame_solver_times_ms")
    valid_times=isinstance(times,list) and all(_number(v) is not None for v in times)
    if times is not None and not valid_times:
        issues.append({"code":"INVALID_CURVE","message":"报告逐帧耗时无效，未绘制该曲线"})
    frames=copy.deepcopy(record.get("frames",[]))
    if not frames and valid_times:
        frames=[{"frame":i+1,"solver_time_ms":float(v),"newton_iterations":None,
                 "pcg_iterations":None,"collision_pair_count":None} for i,v in enumerate(times)]
    expected=metrics["actual_frames"]["value"]
    if frames and expected is not None and len(frames)!=expected:
        issues.append({"code":"FRAME_COUNT_MISMATCH","message":"可读取逐帧数据长度与报告实际帧数不同"})
    if frames and all(f["solver_time_ms"] is not None for f in frames):
        total=metrics["solver_time_total_s"]["value"]
        if total is not None and not math.isclose(sum(f["solver_time_ms"] for f in frames)/1000,total,rel_tol=1e-5,abs_tol=1e-6):
            issues.append({"code":"FRAME_TIME_MISMATCH","message":"逐帧耗时与报告总量不同"})
    if frames and valid_times and record.get("frames") and (
        len(frames)!=len(times) or any(f["solver_time_ms"] is not None and
        not math.isclose(f["solver_time_ms"],times[i],rel_tol=1e-5,abs_tol=1e-6)
        for i,f in enumerate(frames[:len(times)]))):
        issues.append({"code":"CURVE_SOURCE_MISMATCH","message":"CSV 与报告耗时序列不同；曲线标明使用 CSV"})
    source_kind=raw.get("source_kind")
    producer=_producer(raw)
    raw_checks=raw.get("checks")
    valid_checks=isinstance(raw_checks,list) and all(
        isinstance(c,dict) and all(k in c for k in ("id","name","status","required"))
        and isinstance(c["id"],str) and isinstance(c["name"],str)
        and isinstance(c["status"],str) and c["status"] in ("PASS","FAIL","WARNING","SKIPPED")
        and type(c["required"]) is bool
        and isinstance(c.get("message",""),str)
        and isinstance(c.get("evidence",[]),list)
        and all(isinstance(v,str) for v in c.get("evidence",[]))
        and isinstance(c.get("details",[]),list)
        and all(isinstance(v,str) for v in c.get("details",[])) for c in raw_checks)
    if raw_checks is not None and not valid_checks:
        issues.append({"code":"INVALID_CHECKS","message":"历史检查清单结构无效，未补判通过"})
    view={
        "view_schema":SCHEMA,"report_id":record["report_id"],"case_id":record.get("case_id"),
        "kind":"single","recorded_result_status":record["recorded_result_status"],
        "archive_read_status":record["archive_read_status"],
        "data_source":{"view_mode":"archive","producer_kind":producer,"source_id":record["report_id"],
                       "completeness":"partial" if issues or any(metrics[k]["value"] is None for k in KPI_KEYS) else "complete"},
        "metrics":metrics,"frames":frames,
        "frame_source":record["report_id"]+"/raw/frames.csv" if record.get("frames") else field_ref("frame_solver_times_ms"),
        "checks":copy.deepcopy(raw_checks) if valid_checks else None,
        "issues":issues,"raw_report":copy.deepcopy(record["raw_report"]),
    }
    return view

def metric_rows(view, *, kpis_only=False):
    keys=KPI_KEYS if kpis_only else tuple(LABELS)
    return [{"指标":view["metrics"][key]["label"],
             "数值":display_value(view["metrics"][key]["value"],view["metrics"][key]["format"]),
             "单位":view["metrics"][key]["unit"],
             "来源":str(view["metrics"][key]["source"])} for key in keys]

def keyframe_rows(view):
    frames=view["frames"]
    indices=sorted({0,len(frames)-1,*[round((len(frames)-1)*p/5) for p in range(1,5)]}) if frames else []
    return [{"帧号":frames[i]["frame"],
             "物理步耗时（毫秒）":frames[i]["solver_time_ms"] if frames[i]["solver_time_ms"] is not None else "未采集",
             "牛顿迭代次数":frames[i]["newton_iterations"] if frames[i]["newton_iterations"] is not None else "未采集",
             "PCG 迭代次数（聚合）":frames[i]["pcg_iterations"] if frames[i]["pcg_iterations"] is not None else "未采集",
             "碰撞对计数":"未记录" if frames[i]["collision_pair_count"] is None else frames[i]["collision_pair_count"]}
            for i in indices]

def render_archive_markdown(view):
    from .reporter import markdown_table
    text="# 历史归档只读视图\n\n来源："+view["report_id"]+"\n\n"
    text+="归档读取状态："+ARCHIVE_STATUS_TEXT.get(view["archive_read_status"],"未知状态")+"；原记录结论："+status_text(view["recorded_result_status"])+"。\n\n"
    text+="本视图未重跑仿真，也未按新版规则重判原记录。\n\n"
    if view["data_source"]["producer_kind"]=="synthetic":
        text+="数据来源：合成样例，不是实际性能成绩。\n\n"
    if view["kind"]=="suite":
        text+=markdown_table([{"用例":c["case_id"],"原记录结论":status_text(c["recorded_result_status"]),
                               "归档读取状态":ARCHIVE_STATUS_TEXT.get(c["archive_read_status"],"未知状态")} for c in view["cases"]])
    else:
        text+=markdown_table(metric_rows(view))
        text+="\n## 关键帧\n\n"+markdown_table(keyframe_rows(view))
        text+="\n## 逐项检查\n\n"
        if view["checks"] is None: text+="历史记录未包含检查清单，不补判通过。\n"
        else:
            from .presentation import check_table
            text+=markdown_table(check_table(view["checks"]))
    if view["issues"]:
        text+="\n## 读取与数据说明\n\n"+"\n".join("- "+i["message"] for i in view["issues"])+"\n"
    return text
