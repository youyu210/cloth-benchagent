"""One report snapshot drives all formats; no metric recomputation."""
import base64
import csv
import io
import json
from .presentation import status_text,check_table
from .view_models import metric_rows,keyframe_rows,render_archive_markdown,ARCHIVE_STATUS_TEXT
from .reporter import markdown_table

SCHEMA="benchagent-export-v1"
CSV_COLUMNS=("来源报告","用例","原校验结论","归档读取状态","帧号","物理步耗时（毫秒）","牛顿迭代次数","PCG迭代次数（逐帧聚合）","碰撞对计数","缺项说明")

def cases(view):
    return view["cases"] if view["kind"]=="suite" else [view]

def provenance(snapshot):
    return {"export_schema":SCHEMA,"view_schema":snapshot["view"]["view_schema"],
            "report_id":snapshot["report_id"],"case_id":snapshot["case_id"],
            "source_digest":snapshot["source_digest"],"source_files":snapshot["source_index"],
            "recorded_result_status":snapshot["view"]["recorded_result_status"],
            "archive_read_status":snapshot["view"]["archive_read_status"],
            "case_statuses":[{"case_id":c.get("case_id"),"recorded_result_status":c["recorded_result_status"],
                              "archive_read_status":c["archive_read_status"]} for c in cases(snapshot["view"])],
            "view_mode":"archive","original_verdict_preserved":True,
            "simulation_rerun":False,"quality_status":"NOT_ASSESSED",
            "csv_policy":{"columns":list(CSV_COLUMNS),
                "column_mapping":{
                    "来源报告":{"field":"report_id","unit":None},
                    "用例":{"field":"case_id","unit":None},
                    "原校验结论":{"field":"recorded_result_status","labels":"zh-CN"},
                    "归档读取状态":{"field":"archive_read_status","labels":"zh-CN"},
                    "帧号":{"field":"frames.frame","unit":"帧"},
                    "物理步耗时（毫秒）":{"field":"frames.solver_time_ms","unit":"毫秒"},
                    "牛顿迭代次数":{"field":"frames.newton_iterations","unit":"次"},
                    "PCG迭代次数（逐帧聚合）":{"field":"frames.pcg_iterations","unit":"次"},
                    "碰撞对计数":{"field":"frames.collision_pair_count","unit":"对"},
                    "缺项说明":{"field":"derived_missing_reasons","unit":None}},
                "missing_numbers":"empty",
                "missing_reason_column":"缺项说明","formula_strings":"leading apostrophe",
                "metric_rounding":"view presentation policy; CSV numeric values retain full source precision"}}

def markdown(snapshot):
    view=snapshot["view"]
    text="# StiffGIPC 基准测试报告\n\n"
    text+="来源归档："+snapshot["report_id"]+"\n\n"
    text+="导出范围："+(snapshot["case_id"] or "整个报告")+"\n\n"
    text+="来源摘要："+snapshot["source_digest"]+"\n\n"
    text+="只读归档导出，不重跑仿真或重判历史结论；物理精度未评估。\n\n"
    if view["kind"]=="suite":
        text+=render_archive_markdown(view).replace("# 历史归档只读视图","## 任务集概览",1)+"\n"
    for case in cases(view):
        title="## 用例 "+str(case.get("case_id") or "单项")
        text+=render_archive_markdown(case).replace("# 历史归档只读视图",title,1)+"\n"
        raw=case.get("raw_report") or {}
        text+="### 原始运行配置\n\n~~~json\n"+json.dumps(raw.get("plan",{}),ensure_ascii=False,indent=2)+"\n~~~\n\n"
        for key,label in (("errors","异常"),("warnings","告警")):
            text+="### "+label+"\n\n"
            values=raw.get(key,[])
            text+=("\n".join("- "+str(v) for v in values) if values else "无记录。")+"\n\n"
    return text

def json_bytes(snapshot):
    originals={name:{"sha256":item["sha256"],"encoding":"base64",
                    "content":base64.b64encode(snapshot["files"][name]).decode("ascii")}
               for name,item in snapshot["source_index"].items()
               if name.endswith(("/report.json","/benchmark_summary.json"))}
    return (json.dumps({"provenance":provenance(snapshot),"view":snapshot["view"],
                       "original_report_files":originals},
                       ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")

def _cell(value):
    if value is None:return ""
    if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")):return "'"+value
    return value

def csv_bytes(snapshot):
    output=io.StringIO(newline="")
    writer=csv.writer(output);writer.writerow(CSV_COLUMNS)
    for case in cases(snapshot["view"]):
        for frame in case["frames"]:
            missing=[]
            fields=(("solver_time_ms","物理步耗时未采集"),("newton_iterations","牛顿迭代未采集"),
                    ("pcg_iterations","PCG迭代未采集"),("collision_pair_count","逐帧碰撞未记录"))
            values=[]
            for key,reason in fields:
                value=frame.get(key)
                values.append(value)
                if value is None:missing.append(reason)
            writer.writerow([_cell(v) for v in [snapshot["report_id"],case.get("case_id") or snapshot["case_id"] or "单项",
                status_text(case["recorded_result_status"]),
                ARCHIVE_STATUS_TEXT.get(case["archive_read_status"],"未知"),
                frame["frame"],*values,"；".join(missing)]])
    return b"\xef\xbb\xbf"+output.getvalue().encode("utf-8")
