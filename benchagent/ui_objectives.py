"""Goal planning and evidence panels in the existing intelligent test view."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from .cloth_evidence import METRICS
from .conversation_service import scene_display
from .model_gateway import get_default_gateway
from .planning_store import PlanningError

STATUS = {
    "COMPLETE": "证据充分", "LIMITED": "有限结论",
    "AWAITING_CONFIRMATION": "补测待确认", "DIAGNOSTIC_SUBMITTED": "补测已提交",
    "DIAGNOSTIC_FAILED": "补测未通过",
}
EXAMPLE = "分析球体与布料仿真的高耗时帧，对照接触相关计数和 Newton/PCG 迭代，展示关键帧。"


def _case_label(case):
    extra = (" · 有原生图" if case["images"] else "") + (" · 有接触追踪" if case["trace"] else "")
    return f"{scene_display(case['scene'])} · {case['frames']} 帧 · {case['status']} · {case['report_id']}{extra}"


def _fill_goal_example():
    st.session_state["goal-text"] = EXAMPLE


def objective_panel(service):
    objectives = service.objectives
    st.markdown("### 历史实验分析")
    st.caption("根据实验目标检索已有仿真记录，分析性能表现、定位关键帧，并在证据不足时提出补测建议。")
    cases = objectives.cases()
    by_ref = {(c["report_id"], c["case_id"]): c for c in cases}
    choice = st.selectbox("输入归档", [None, *by_ref], format_func=lambda ref:
                          "按目标场景查询" if ref is None else _case_label(by_ref[ref]), key="goal-case")
    records = objectives.list()
    choices = [None, *(r["objective_id"] for r in records)]
    previous = st.selectbox("沿用上一份目标（可选）", choices, key="goal-previous",
                            format_func=lambda key: key or "新目标")
    st.button("填入球体与布料示例", key="goal-example", on_click=_fill_goal_example)
    text = st.text_area("分析目标", placeholder=EXAMPLE, max_chars=2000, key="goal-text")
    with st.expander("预算与工具选择"):
        gpu = st.selectbox("最多诊断补测次数", [1, 0], key="goal-gpu")
        st.caption("每次最多 100 帧、600 秒。启用 DeepSeek 时最多 8 次模型调用。")
        use_model = st.checkbox("让 DeepSeek 依据工具观测选择下一动作", key="goal-model")
        if use_model:
            st.caption("发送场景标识、所需证据类型、帧数覆盖、可用画面数量和已用工具。原文、日志和文件内容留在本地。")
    if st.button("查询归档并分析", type="primary", key="goal-review"):
        if not text.strip():
            st.warning("请先输入分析目标，或点击「填入球体与布料示例」。")
        else:
            case = by_ref.get(choice, {})
            gateway = get_default_gateway(st.session_state.get("deepseek_api_key")) if use_model else None
            try:
                record = objectives.create(text, report_id=case.get("report_id"), case_id=case.get("case_id"),
                                           previous_id=previous, gpu_budget=gpu, gateway=gateway)
                st.session_state["goal-current"] = record["objective_id"]
            except (PlanningError, OSError, ValueError) as exc:
                st.error(str(exc))
    if records:
        saved = st.selectbox("已保存的目标", choices, key="goal-history",
                             format_func=lambda key: key or "选择记录")
        if st.button("查看目标记录", disabled=saved is None, key="goal-load"):
            st.session_state["goal-current"] = saved
    if st.session_state.get("goal-current"):
        record = objectives.get(st.session_state["goal-current"])
        show_objective(service, record)
        script_panel(service, record)
    comparison_panel(service, by_ref)


def show_objective(service, record):
    st.subheader(STATUS.get(record["status"], record["status"]))
    st.write(record["plan"]["goal"]["text"])
    used = sum(item["provider_calls"] for item in record["model_requests"])
    label = "DeepSeek 建议与确定性校验" if record["model_used"] else "本地确定性分析"
    st.caption(f"{label} · 工具记录 {len(record['actions'])} 项 · 模型调用 {used} 次 · GPU 提交 {record['gpu_submissions']} 次")
    if record["model_requested"] and not record["model_used"]:
        st.info("本轮未采纳模型动作，已保存接口或校验结果。")
    if record["status"] == "AWAITING_CONFIRMATION":
        d = record["diagnostic"]
        st.markdown("#### 待确认的诊断补测")
        st.write("拟新增证据", "、".join(d["new_evidence"]))
        st.write(d["invariants"])
        st.write(d["cost"])
        st.caption(f"帧数 {d['request']['task_frames'][0]} · 超时 {d['request']['timeout_sec']} 秒 · 构建 {d['build_sha256']}")
        if d["build_sha256"] != d["parent_build_sha256"]:
            st.caption("启用原生抓帧需要切换到已封存的抓帧构建。")
        if st.button("确认配置与预算并提交补测", key="goal-confirm"):
            try:
                service.objectives.confirm_diagnostic(record["objective_id"], record["digest"])
                st.rerun()
            except (PlanningError, OSError, ValueError) as exc:
                st.error(str(exc))
    elif record["status"] == "DIAGNOSTIC_SUBMITTED":
        job = service.job(record["child_job_id"])
        st.write(f"子任务 {job['job_id']} · {job['state']}")
        st.caption("可在运行记录查看进度、跟随日志或取消任务。")
        if st.button("刷新补测并读取结果", key="goal-refresh"):
            try:
                service.objectives.refresh(record["objective_id"])
                st.rerun()
            except (PlanningError, OSError, ValueError) as exc:
                st.error(str(exc))
    elif record.get("stop_reason"):
        st.warning(record["stop_reason"])

    if record.get("diagnostic_analysis"):
        st.markdown("#### 诊断子任务证据")
        show_analysis(service, record["diagnostic_analysis"], "diagnostic")
        with st.expander("查看补测前的历史归档分析"):
            show_analysis(service, record["analysis"], "parent")
    else:
        show_analysis(service, record["analysis"], "parent")
    with st.expander("冻结计划、工具动作及证据来源"):
        st.json({key: record.get(key) for key in
                 ("plan", "digest", "actions", "model_requests", "authorization", "child_outcome")}, expanded=False)
    st.download_button("下载目标证据 JSON", json.dumps(record, ensure_ascii=False, indent=2),
                       file_name=record["objective_id"] + ".json", mime="application/json", key="goal-download")


def show_analysis(service, analysis, suffix):
    stats = analysis["statistics"]
    st.markdown("#### 已观测事实")
    st.caption(analysis["source"]["case_ref"])
    st.write(f"有效耗时帧 {stats['valid_steps']}/{stats['physical_steps']}")
    st.caption(stats["ranking"] + " " + analysis["timing_scope"])
    if stats["threshold_ms"] is not None:
        st.write(f"高耗时选取阈值 {stats['threshold_ms']:.3f} ms · "
                 f"中位数 {stats['median_ms']:.3f} ms · P90 {stats['p90_ms']:.3f} ms · "
                 f"最大 {stats['max_ms']:.3f} ms")
    if stats["hot_frames"]:
        columns = {"frame": "帧号", **METRICS}
        st.dataframe(pd.DataFrame(stats["hot_frames"]).rename(columns=columns), hide_index=True)
        groups = stats["groups"]
        st.dataframe([{
            "指标": label, "高耗时组样本": groups[key]["high"]["n"],
            "高耗时组中位数": groups[key]["high"]["median"],
            "其余组样本": groups[key]["other"]["n"], "其余组中位数": groups[key]["other"]["median"],
        } for key, label in METRICS.items()], hide_index=True)
    chosen = None
    if analysis["keyframes"]:
        chosen = st.selectbox("关联高耗时帧", analysis["keyframes"], key="goal-frame-" + suffix,
                             format_func=lambda item: f"第 {item['target_frame']} 帧")
        try:
            image = service.native_frame(analysis["source"]["case_ref"], chosen["image_frame"])
            st.image(image, caption=f"原生第 {chosen['image_frame']} 帧 · 偏移 {chosen['offset']:+d} 帧")
        except (OSError, ValueError, KeyError):
            st.warning("归档原生画面不可读取。")
    if stats["hot_frames"]:
        plot = pd.DataFrame(analysis["rows"]).rename(columns=columns)
        groups = [("物理步耗时（ms）", ["step_ms"]),
                  ("迭代次数（次/步）", ["newton_iterations", "pcg_iterations"])]
        if plot[METRICS["contact_list_max"]].notna().any():
            groups.append(("装配前近距离约束列表峰值", ["contact_list_max"]))
        for title, metrics in groups:
            series = plot.melt("帧号", [METRICS[m] for m in metrics], "指标", "数值")
            chart = alt.Chart(series).mark_line().encode(
                x="帧号:Q", y=alt.Y("数值:Q", title=title), color="指标:N",
                tooltip=["帧号:Q", "指标:N", "数值:Q"])
            if chosen:
                marker = alt.Chart(pd.DataFrame([{"帧号": chosen["target_frame"]}])).mark_rule(
                    color="#7b3cff", strokeDash=[4, 4]).encode(x="帧号:Q")
                chart += marker
            st.altair_chart(chart.properties(height=155), width="stretch", key=f"goal-chart-{suffix}-{metrics[0]}")
    st.markdown("#### 解释与证据限制")
    for fact in analysis.get("facts", []):
        st.write(fact)
    st.write(analysis["interpretation"])
    st.caption(analysis["contact_scope"])
    if analysis["contact_summary"]:
        st.caption("归档全程碰撞汇总，仅作全程上下文")
        st.json(analysis["contact_summary"])
    for missing in analysis["missing"]:
        st.warning(missing)


def comparison_panel(service, by_ref):
    from .archive_comparison import VARIANTS

    with st.expander("对照两组真实归档"):
        if len(by_ref) < 2:
            st.info("当前可用归档不足两组。")
            return

        def label(ref):
            return _case_label(by_ref[ref])

        left = st.selectbox("参考运行", list(by_ref), format_func=label, key="comparison-left")
        right = st.selectbox("待对照运行", list(by_ref), index=1, format_func=label, key="comparison-right")
        variable = st.selectbox("预先声明的对照变量", list(VARIANTS), format_func=VARIANTS.get, key="comparison-var")
        if st.button("检查可比性并计算对照", key="comparison-run"):
            try:
                record = service.comparisons.create(left, right, variable)
                st.session_state["comparison-current"] = record["comparison_id"]
            except (PlanningError, OSError, ValueError) as exc:
                st.error(str(exc))
        records = service.store.read()["comparisons"]
        selected = st.selectbox("对照记录", [None, *reversed(records)], key="comparison-history",
                                format_func=lambda key: key or "查看本次结果")
        current = selected or st.session_state.get("comparison-current")
        if not current:
            return
        result = records[current]
        st.write("可比" if result["metrics"] else "不可比或可比性未证实")
        for item in result["reasons"] + result["limits"]:
            st.write(item)
        if result["metrics"]:
            metrics = result["metrics"]
            st.dataframe([{"共同物理步数": metrics["sample_count_per_side"],
                           "参考运行总耗时（ms）": metrics["left_total_ms"],
                           "待对照运行总耗时（ms）": metrics["right_total_ms"],
                           "总耗时比（参考/待对照）": metrics["left_over_right"]}], hide_index=True)
        if result.get("material"):
            from .materials import MATERIAL_FIELDS
            change = result["material"]
            st.dataframe([{"材料参数": MATERIAL_FIELDS[key]["label"], "单位": MATERIAL_FIELDS[key]["unit"],
                           "参考值": value, "待对照值": change["right"][key]}
                          for key, value in change["left"].items()], hide_index=True)
        labels = {"PASS": "通过", "DIFFERENT": "不同", "UNVERIFIED": "未证实"}
        st.dataframe([{"核查项": item["item"], "结论": labels[item["status"]]}
                      for item in result["checks"]], hide_index=True)
        st.download_button("下载对照证据 JSON", json.dumps(result, ensure_ascii=False, indent=2),
                           file_name=current + ".json", mime="application/json", key="comparison-download")


def script_panel(service, objective):
    with st.expander("临时分析脚本"):
        st.caption("按约束计数分组比较耗时和 PCG。请求仅包含字段定义与人工样例。")
        st.info("执行状态 BLOCKED。当前环境缺少已验证的操作系统级隔离。")
        if st.button("用 DeepSeek 生成待审核脚本", key="script-generate"):
            try:
                gateway = get_default_gateway(st.session_state.get("deepseek_api_key"))
                service.analysis_scripts.generate(objective["objective_id"], gateway)
            except (PlanningError, OSError, ValueError) as exc:
                st.error(str(exc))
        records = [r for r in service.store.read()["analysis_scripts"].values()
                   if r["objective_id"] == objective["objective_id"]]
        if not records:
            return
        record = records[-1]
        st.write(record["generation_status"])
        if record["script"]:
            st.code(record["script"], language="python")
            st.write(record["static_review"])
        st.download_button("下载脚本审核记录", json.dumps(record, ensure_ascii=False, indent=2),
                           file_name=record["script_id"] + ".json", mime="application/json", key="script-download")
