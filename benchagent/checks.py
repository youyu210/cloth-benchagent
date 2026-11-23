"""Stable evidence checks; SKIPPED means not evaluated, never a passed check."""

from copy import deepcopy
import math

SCHEMA = "benchagent-checks-v1"
CATALOG = (
    ("V01", "进程正常结束", True, {"returncode": 0, "timeout": False}, ["run_meta.json"]),
    ("V02", "唯一完成标记", True, "唯一 TEST_COMPLETE 标记且帧数与计划一致", ["stdout.log", "plan.json"]),
    ("V03", "实际帧数与连续帧序号", True, "帧数符合计划且帧序号从 1 连续递增", ["raw/frames.csv", "plan.json"]),
    ("V04", "程序完成状态", True, "完成标志、有限状态标志及完成帧数均符合计划", ["raw/status.json", "plan.json"]),
    ("V05", "文件新鲜度与运行身份", True, {"fresh_output": True, "identity_unchanged": True},
     ["run_meta.json", "identity_before.json", "identity_after.json"]),
    ("V06", "必需指标可读且数值合法", True, "必需文件可解析；时间与计数有限且非负，单帧耗时为正",
     ["raw/frames.csv", "raw/pcg_iterations.jsonl", "raw/metadata.json", "raw/status.json", "raw/collision_summary.json"]),
    ("V07", "PCG 聚合与明细一致", True, "帧号唯一且有效；完整明细与聚合计数相等，部分明细不超过聚合计数",
     ["raw/frames.csv", "raw/pcg_iterations.jsonl"]),
    ("V08", "PCG 求解明细覆盖率", False, "每次 PCG 调用均有明细；不完整时告警，不补零",
     ["raw/frames.csv", "raw/pcg_iterations.jsonl"]),
    ("V09", "默认场景与初始状态", True, {"scene_no": 1, "cloth_scene": "", "initial_physical_frame": 0},
     ["raw/metadata.json", "plan.json"]),
    ("V10", "固定 C0 配置", True, "固定基线、残差补偿 TOI、双段 Graph 与 indexed 配置全部匹配",
     ["raw/metadata.json", "profile.json"]),
    ("V11", "状态有限且保护预算未触顶", True, {"nonfinite_vertices": 0, "newton_cap_hits": 0, "diagnostic_budget_hits": 0},
     ["raw/frames.csv", "raw/status.json"]),
    ("V12", "Graph 实际执行且无回退", True, "方向图至少执行一次且所有帧均无 Graph 回退", ["raw/frames.csv"]),
)

def skipped_checks(reason):
    return [{
        "id": code, "name": name, "required": required, "status": "SKIPPED",
        "expected": deepcopy(expected), "actual": None,
        "evidence": list(paths), "message": reason, "details": [],
    } for code, name, required, expected, paths in CATALOG]

def preflight_check(ready, errors):
    return {
        "id": "P00", "name": "环境与固定文件前置检查", "required": True,
        "status": "PASS" if ready else "FAIL", "expected": {"ready": True},
        "actual": {"ready": ready, "errors": list(errors)},
        "evidence": ["preflight.json"], "message": "前置检查通过" if ready else "前置检查未通过，未启动仿真",
    }


def serializable_observation(value):
    """Preserve invalid observations as explicit text, never invalid JSON floats."""
    if isinstance(value, float) and not math.isfinite(value):
        return "非有限数值：" + ("非数值" if math.isnan(value) else "正无穷" if value > 0 else "负无穷")
    if isinstance(value, dict):
        return {key: serializable_observation(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serializable_observation(item) for item in value]
    return value
