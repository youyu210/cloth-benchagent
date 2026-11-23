"""Chinese display views. Machine-readable evidence keeps its original schema."""

import re

STATUS_LABELS = {
    "PASS": "通过",
    "FAIL": "失败",
    "WARNING": "告警",
    "PARTIAL": "部分完成",
    "SKIPPED": "已跳过",
    "RUNNING": "执行中",
    "NOT_ASSESSED": "未评估",
    "NOT_RUN": "未执行",
}
TASK_LABELS = {
    "smoke-20": "短程检查（20 帧）",
    "standard-50": "标准评测（50 帧）",
    "extended-100": "扩展检查（100 帧）",
    "default-mvp": "默认场景标准评测",
}
# Key, Chinese label, display unit, numeric format.
METRICS = (
    ("actual_frames", "实际完成帧数", "帧", "count"),
    ("solver_time_total_s", "物理步总耗时", "秒", "time"),
    ("solver_time_mean_ms_per_frame", "平均每帧物理步耗时", "毫秒/帧", "time"),
    ("solver_time_min_ms", "最短单帧物理步耗时", "毫秒", "time"),
    ("solver_time_max_ms", "最长单帧物理步耗时", "毫秒", "time"),
    ("newton_iterations_total", "牛顿迭代总次数", "次", "count"),
    ("newton_iterations_mean_per_frame", "平均每帧牛顿迭代次数", "次/帧", "decimal"),
    ("pcg_iterations_total_from_csv", "PCG 迭代总次数（逐帧聚合）", "次", "count"),
    ("pcg_iterations_total_from_stats", "PCG 迭代次数（已记录明细）", "次", "count"),
    ("pcg_solves", "PCG 求解调用次数", "次", "count"),
    ("pcg_iterations_coverage", "PCG 求解明细覆盖率", "%", "percent"),
    ("collision_counter_total", "累计碰撞对计数（源码口径）", "次", "count"),
    ("collision_pair_max", "最大碰撞对数", "对", "count"),
    ("wall_time_s", "进程墙钟耗时", "秒", "time"),
)

def status_text(value):
    return STATUS_LABELS.get(value, "未知状态")

def task_text(value):
    return TASK_LABELS.get(value, "单项评测")

def display_value(value, kind="decimal"):
    if value is None:
        return "未采集"
    if kind == "count":
        return str(int(value))
    if kind == "percent":
        return f"{value * 100:.2f}%"
    if kind == "time":
        return f"{value:.6f}"
    return f"{value:.3f}"

def metric_table(metrics, plan=None, wall_time_s=None):
    rows = []
    if plan is not None:
        rows.append({"指标": "目标帧数", "数值": display_value(plan.get("frames"), "count"), "单位": "帧"})
    for key, label, unit, kind in METRICS:
        value = wall_time_s if key == "wall_time_s" and wall_time_s is not None else metrics.get(key)
        rows.append({"指标": label, "数值": display_value(value, kind), "单位": unit})
    rows.append({
        "指标": "计时范围",
        "数值": "同步物理步墙钟计时；包含首次 Graph 准备；排除初始化、渲染和状态导出",
        "单位": "说明",
    })
    return rows

def summary_table(reports):
    rows = []
    for report in reports:
        rows.append({
            "任务": task_text(report.get("task_id")),
            "状态": status_text(report.get("status")),
            "目标帧数": display_value(report.get("plan", {}).get("frames"), "count"),
            "实际帧数": display_value(report.get("actual_frames"), "count"),
            "物理步总耗时（秒）": display_value(report.get("solver_time_total_s"), "time"),
            "平均每帧耗时（毫秒）": display_value(report.get("solver_time_mean_ms_per_frame"), "time"),
            "牛顿迭代总次数": display_value(report.get("newton_iterations_total"), "count"),
            "PCG 迭代总次数（逐帧聚合）": display_value(report.get("pcg_iterations_total_from_csv"), "count"),
            "PCG 明细覆盖率": display_value(report.get("pcg_iterations_coverage"), "percent"),
            "异常数": str(len(report["errors"])) if report.get("errors") is not None else "未采集",
            "告警数": str(len(report["warnings"])) if report.get("warnings") is not None else "未采集",
        })
    return rows

DIAGNOSTICS = {
    "Process failed or timed out": "进程异常退出或运行超时",
    "Runtime/source identity changed": "运行期间程序、源码或输入文件的身份发生变化",
    "Output does not belong to this run": "输出文件无法确认为本次运行产物",
    "Missing, duplicate or incorrect completion marker": "完成标记缺失、重复或帧数不符",
    "Incomplete or invalid status.json": "完成状态文件不完整或无效",
    "Frame sequence/count mismatch": "帧序号或实际帧数与计划不符",
    "Scene or initial state mismatch": "场景或初始状态与固定方案不符",
    "Base commit mismatch": "基线提交与固定版本不符",
    "Residual weight mismatch": "残差补偿权重与固定配置不符",
    "No actual Graph execution": "未记录到实际 Graph 执行",
    "Invalid PCG frame IDs": "PCG 明细包含重复或无效帧序号",
    "Too many PCG subrecords": "PCG 明细条目超过求解调用次数",
    "Partial PCG count exceeds aggregate": "已记录 PCG 明细之和超过逐帧聚合计数",
    "PCG aggregate/detail mismatch": "PCG 逐帧聚合与完整明细不一致",
    "No PCG solves; coverage is undefined": "未记录到 PCG 求解调用，明细覆盖率无法计算",
}
COUNTER_LABELS = {
    "newton_cap_hits": "牛顿迭代达到上限",
    "diagnostic_budget_hits": "诊断保护预算触顶",
    "fallbacks": "Graph 执行发生回退",
    "nonfinite_vertices": "顶点包含非有限数值",
}

def diagnostic_text(message):
    if message in DIAGNOSTICS:
        return DIAGNOSTICS[message]
    patterns = (
        (r"PCG detail missing in frame (\d+)", "第 {} 帧缺少 PCG 求解明细"),
        (r"Partial PCG detail in frame (\d+)", "第 {} 帧的 PCG 求解明细仅部分覆盖"),
    )
    for pattern, template in patterns:
        match = re.fullmatch(pattern, message)
        if match:
            return template.format(match[1])
    match = re.fullmatch(r"Frame (\d+): (\w+)", message)
    if match:
        return f"第 {match[1]} 帧：" + COUNTER_LABELS.get(match[2], "统计异常")
    if message.startswith("C0 configuration mismatch: "):
        return "C0 配置与固定方案不符（字段：" + message.split(": ", 1)[1] + "）"
    if "TIMEOUT:" in message:
        return "运行超时；已终止进程树并保留部分证据"
    if "PROCESS_EXIT:" in message:
        return "仿真进程异常退出（返回码：" + message.rsplit(": ", 1)[-1] + "）"
    return "执行或证据异常（原始提示：" + message + "）"


CHECK_FIELD_LABELS = {
    "returncode": "退出码", "timeout": "是否超时", "completion_frames": "完成标记帧数",
    "actual_frames": "实际帧数", "frame_sequence": "帧序号", "completed": "完成标志",
    "finite_state": "状态有限标志", "frames_completed": "完成帧数",
    "fresh_output": "本次新产物", "identity_unchanged": "运行身份一致",
    "parsed": "必需指标可解析", "frame_records": "帧记录数", "pcg_records": "PCG 明细帧数",
    "parse_error": "解析异常", "frames": "计划帧数", "scene_no": "源码场景编号",
    "cloth_scene": "具名场景", "initial_physical_frame": "初始物理帧",
    "termination": "终止策略", "method": "PCG 执行方式",
    "converter_indexed_reduce": "indexed 汇总", "toi_min_updates": "TOI 最少更新次数",
    "toi_threshold": "TOI 阈值", "residual_threshold": "残差阈值",
    "residual_floor": "残差分母下限", "newton_tolerance_scale": "牛顿容差倍率",
    "pcg_tolerance_scale": "PCG 容差倍率", "production_measurement": "生产计时模式",
    "profile_stages": "阶段剖析", "trace_newton": "牛顿追踪", "audit_frame": "审计帧",
    "checkpoint_in": "恢复检查点", "base_commit": "基线提交", "residual_weight": "残差补偿权重",
    "nonfinite_vertices": "非有限顶点数", "newton_cap_hits": "牛顿上限触顶次数",
    "diagnostic_budget_hits": "保护预算触顶次数", "fallbacks": "Graph 回退次数",
    "direction_launches": "方向图执行次数", "violations": "异常明细", "frame": "帧号",
    "field": "字段", "value": "观测值", "unique_valid_frames": "明细帧号唯一且有效",
    "mismatch_frames": "计数不一致帧", "aggregate_iterations": "聚合迭代次数",
    "recorded_iterations": "已记录明细迭代次数", "expected_solves": "求解调用总数",
    "covered_solves": "有明细的调用数", "coverage": "明细覆盖率",
    "missing_frames": "完全缺少明细的帧", "partial_frames": "部分缺少明细的帧",
    "ready": "前置条件满足", "errors": "异常", "execution_error": "执行接口异常",
}

def observation_text(value, key=None):
    if value is None:
        return "未取得"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        if not value:
            return "无"
        return "；".join(
            CHECK_FIELD_LABELS.get(k, k) + "：" + observation_text(v, k)
            for k, v in value.items())
    if isinstance(value, list):
        if not value:
            return "无"
        if len(value) > 8 and all(type(v) is int for v in value):
            if value == list(range(value[0], value[0] + len(value))):
                return f"{value[0]} 至 {value[-1]}（共 {len(value)} 项）"
        return "、".join(observation_text(v, key) for v in value)
    if key == "coverage" and isinstance(value, (int, float)):
        return f"{value * 100:.2f}%"
    if value == "":
        return "未设置"
    if value == "compensated":
        return "残差补偿 TOI"
    if value == "pcg_dual_graph":
        return "双段 Graph"
    if key == "field":
        return CHECK_FIELD_LABELS.get(value, value)
    return str(value)

def check_table(checks):
    return [{
        "编号": item["id"],
        "检查项": item["name"],
        "结论": status_text(item["status"]),
        "必需": "是" if item["required"] else "否",
        "期望值": observation_text(item.get("expected")),
        "实际值": observation_text(item.get("actual")),
        "证据路径": "\n".join(item.get("evidence", [])) or "未记录",
        "说明": item.get("message", "") + (
            "；" + "；".join(diagnostic_text(m) for m in item["details"])
            if item.get("details") else ""),
    } for item in checks]
