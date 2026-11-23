"""Prepare and review analysis scripts. Execution requires OS isolation and is disabled."""
import ast
import hashlib
import platform

from .cloth_evidence import METRICS
from .common import now
from .model_gateway.contracts import ScriptRequest
from .planning_store import PlanningError, new_id, require

TASK = {
    "analysis": "按 contact_list_max 的中位数分组。大于或等于中位数为 high，低于中位数为 low。"
                "仅使用接触约束、物理步耗时和 PCG 都有效的行，保留原始物理帧号。",
    "input_file": "input.csv", "output_file": "result.csv",
    "input_columns": {"frame": "物理步整数索引，初态不在输入中", **METRICS},
    "output_columns": ["group", "n", "median_step_ms", "median_pcg_iterations", "frames"],
    "sample_kind": "synthetic",
    "sample_rows": [
        {"frame": 1, "step_ms": 10.0, "newton_iterations": 2, "pcg_iterations": 10, "contact_list_max": 20},
        {"frame": 2, "step_ms": 20.0, "newton_iterations": 3, "pcg_iterations": 30, "contact_list_max": 40},
    ],
}
ALLOWED_IMPORTS = {"csv", "statistics", "math"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__", "getattr", "setattr", "globals",
                   "locals", "input", "breakpoint", "os", "sys", "subprocess", "socket", "pathlib"}


def review_script(script):
    if not isinstance(script, str) or not script.strip() or len(script) > 12000:
        return ["代码为空或超出大小限制。"]
    try:
        tree = ast.parse(script, mode="exec")
    except (SyntaxError, ValueError, RecursionError):
        return ["代码无法通过 Python 语法解析。"]
    issues = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name not in ALLOWED_IMPORTS for alias in node.names):
            issues.add("含目录以外的依赖。")
        if isinstance(node, ast.ImportFrom) and (node.level or node.module not in ALLOWED_IMPORTS):
            issues.add("含目录以外的依赖。")
        if ((isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES)
                or (isinstance(node, ast.Attribute) and node.attr.startswith("_"))):
            issues.add("含动态执行或系统访问相关符号。")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            path = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
            mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
            if (path, mode) not in {("input.csv", "r"), ("result.csv", "w")}:
                issues.add("文件打开方式不符合固定输入和输出契约。")
    return sorted(issues)


class AnalysisScripts:
    def __init__(self, service):
        self.service = service

    def generate(self, objective_id, gateway):
        objective = self.service.objectives.get(objective_id)
        analysis = objective.get("diagnostic_analysis", objective["analysis"])
        if objective["status"] in {"AWAITING_CONFIRMATION", "DIAGNOSTIC_SUBMITTED"}:
            raise PlanningError("SCRIPT_SOURCE_PENDING", "等待源目标取得证据或结束。")
        if not any(row["contact_list_max"] is not None for row in analysis["rows"]):
            raise PlanningError("SCRIPT_INPUT_MISSING", "该目标缺少脚本所需的逐步约束计数。")
        self.service.objectives._check_sources(analysis["input_sha256"])
        ready = gateway.get_status().ready
        script_id = new_id("analysis-script")
        request_id = new_id("script-request")
        record = {
            "schema_version": "analysis-script-v1", "script_id": script_id,
            "objective_id": objective_id, "goal_digest": objective["digest"], "created_at": now(),
            "request_id": request_id, "source": analysis["source"], "input_sha256": analysis["input_sha256"],
            "contract": TASK, "dependencies": {"python": platform.python_version(), "library": "stdlib"},
            "execution_status": "BLOCKED_NO_OS_ISOLATION", "execution_log": None,
            "generation_status": "REQUESTED" if ready else "BLOCKED_MODEL_NOT_CONFIGURED",
            "provider_calls": 1 if ready else 0, "script": None, "script_sha256": None,
            "static_review": None, "numerical_verification": "NOT_EXECUTED",
        }
        with self.service.store.transaction() as state:
            used = sum(x["provider_calls"] for x in objective["model_requests"])
            prior = [x for x in state["analysis_scripts"].values() if x["objective_id"] == objective_id]
            if ready and (used + sum(x["provider_calls"] for x in prior) >= objective["budget"]["model_calls"]
                          or any(x["provider_calls"] for x in prior)):
                raise PlanningError("SCRIPT_BUDGET", "本目标的脚本生成预算已用完。")
            state["analysis_scripts"][script_id] = record
        if not ready:
            return record
        response = gateway.propose_script(ScriptRequest(request_id, TASK))
        proposal = response.proposed_plan or {}
        script = proposal.get("script") if (response.status == "ok" and response.provider_calls == 1
                                             and set(proposal) == {"script"}) else None
        if isinstance(script, str) and 1 <= len(script) <= 12000:
            issues = review_script(script)
            record.update(generation_status="GENERATED", script=script,
                          script_sha256=hashlib.sha256(script.encode("utf-8")).hexdigest(),
                          static_review={"status": "REVIEW_REQUIRED" if issues else "STATIC_CHECKED",
                                         "issues": issues, "scope": "语法和固定契约检查。"})
        else:
            record.update(generation_status=response.error_code or "MODEL_SCRIPT_SCHEMA")
        with self.service.store.transaction() as state:
            require(state["analysis_scripts"], script_id)
            state["analysis_scripts"][script_id] = record
        return record
