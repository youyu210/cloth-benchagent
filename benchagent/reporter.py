from .conversation_service import SCENE_LABELS
from .common import write
from .materials import material_rows
from .presentation import (
    check_table, diagnostic_text, metric_table, status_text, summary_table,
)

def markdown_table(rows):
    if not rows:
        return "暂无数据。\n"
    headers = list(rows[0])
    def cell(value):
        return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(cell(row.get(h, "未采集")) for h in headers) + " |" for row in rows)
    return "\n".join(lines) + "\n"

def report_run(folder, plan, execution, result, metrics):
    report = {**result, **metrics, "run_id": folder.name, "plan": plan,
              "implementation_commit": execution.get("implementation_commit"),
              "wall_time_s": execution.get("wall_seconds"),
              "evidence_files": sorted(p.relative_to(folder).as_posix()
                                      for p in folder.rglob("*") if p.is_file())}
    if plan.get("diagnostic_contact_trace"):
        report["measurement_kind"] = "contact_trace_diagnostic"
        report["timing_scope"] = report.get("timing_scope", "") + "; includes Newton trace instrumentation"
    write(folder / "metrics.json", metrics)
    write(folder / "report.json", report)
    (folder / "report.md").write_text(render_run_markdown(report), encoding="utf-8")
    return report

def render_run_markdown(report):
    text = f"# 单项评测报告\n\n运行标识：{report.get('run_id', '未记录')}\n\n"
    text += f"执行状态：{status_text(report.get('status'))}；物理精度：未评估。\n\n"
    plan = report.get("plan") or {}
    text += "场景：" + SCENE_LABELS.get(plan.get("scene"), "未记录") + "\n\n"
    visual = report.get("visualization") or {}
    if visual.get("kind") == "native_render_frame":
        frames = "、".join(map(str, visual["frames"]))
        text += (f"原生 OpenGL 画面，第 {frames} 帧。采集耗时 {visual['capture_ms']:.1f} 毫秒。\n\n"
                 f"捕获构建 SHA256：{visual['build_sha256']}\n\n")
    if "material" in plan:
        text += "## 布片材料配置\n\n"
        evidence = report.get("material_evidence") or {}
        text += markdown_table(material_rows(plan["scene"], plan["material"], evidence.get("effective", {})))
        text += "\n计划来源见 inputs/material.json。实际值与系数见 raw/material_effective.json，设备回读质量见 raw/masses.f64。\n\n"
    text += markdown_table(metric_table(report, report.get("plan"), report.get("wall_time_s")))
    text += "\n## 逐项校验\n\n"
    if report.get("checks"):
        text += markdown_table(check_table(report["checks"]))
    else:
        text += "本报告未记录逐项校验清单；未据此补判检查通过。\n"
    for key, label in (("errors", "异常"), ("warnings", "告警")):
        messages = report.get(key, [])
        text += f"\n## {label}\n\n"
        text += ("\n".join("- " + diagnostic_text(e) for e in messages) + "\n"
                 if messages else "无。\n")
    text += "\n执行通过仅表示固定配置的运行与证据校验通过，不代表物理精度认证。\n"
    return text

def suite_status(reports, expected_count=3):
    statuses = [r["status"] for r in reports]
    if len(statuses) < expected_count:
        return "PARTIAL"
    if statuses and all(s == "PASS" for s in statuses):
        return "PASS"
    if any(s == "PASS" for s in statuses):
        return "PARTIAL"
    return "SKIPPED" if statuses and all(s == "SKIPPED" for s in statuses) else "FAIL"

def report_suite(folder, reports, expected_count=3, suite_id="default-mvp", context=None):
    summary = {"suite_id": suite_id, "expected_count": expected_count, "suite_run_id": folder.name,
               "status": suite_status(reports, expected_count), "tasks": reports,
               "quality_status": "NOT_ASSESSED"}
    if context:
        summary.update(context)
    write(folder / "benchmark_summary.json", summary)
    (folder / "benchmark_summary.md").write_text(render_suite_markdown(summary), encoding="utf-8")
    return summary

def render_suite_markdown(summary):
    text = "# 基准评测汇总\n\n运行标识：" + summary.get("suite_run_id", "未记录")
    text += "\n\n执行状态：" + status_text(summary.get("status")) + "\n\n"
    reports = summary.get("tasks", [])
    rows = summary_table(reports)
    for row, report in zip(rows, reports):
        row["场景"] = SCENE_LABELS.get((report.get("plan") or {}).get("scene"), "未记录")
        row["报告路径"] = report.get("report_path", "未生成")
    text += markdown_table(rows)
    text += "\n配置：CUDA、双容差残差补偿 TOI、PCG 双段 CUDA Graph、indexed 归约。各任务从初态独立启动。执行通过仅表示运行与证据校验通过，不代表物理精度认证。\n"
    text += "PCG 总次数取逐帧聚合计数；明细部分缺失时覆盖率单列，缺失值不补零。\n"
    return text
