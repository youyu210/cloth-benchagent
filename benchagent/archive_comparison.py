"""Guarded, read-only comparison of real run archives."""
from statistics import median

from .cloth_evidence import ClothEvidence, finite
from .common import now
from .conversation_service import SCENES
from .plan_service import plan_digest
from .planning_store import PlanningError, new_id

VARIANTS = {"repeat": "同配置重复运行", "solver_version": "仅求解器构建版本",
            "cuda_graph": "仅 PCG CUDA Graph 模式", "material": "单项布片材料参数"}
GRAPH_METHODS = {"pcg_dual_graph", "pcg_eager"}
GRAPH_FLAGS = {"--pcg-dual-graph", "--pcg-eager"}
METADATA_KEYS = (
    "dt", "scene_no", "cloth_scene", "vertex_count", "tetrahedron_count", "initial_physical_frame",
    "pcg_tolerance", "newton_tolerance", "pcg_tolerance_scale", "newton_tolerance_scale",
    "termination", "toi_min_updates", "toi_threshold", "residual_floor", "residual_threshold",
    "residual_weight", "converter_indexed_reduce", "cloth_offset_y", "cloth_initial_vy",
    "production_measurement", "trace_newton", "profile_stages", "timing", "gpu_name",
    "cuda_runtime", "cuda_driver", "checkpoint_in", "diagnostic_max_ms", "diagnostic_max_newton",
)


def _args(run, variable):
    command = run.get("command")
    if not isinstance(command, list) or not all(isinstance(token, str) for token in command):
        return None
    args = iter(command[1:])
    result = []
    for arg in args:
        if arg in {"--benchmark-frames", "--benchmark-output-dir"}:
            if next(args, None) is None:
                return None
        elif variable != "cuda_graph" or arg not in GRAPH_FLAGS:
            result.append(arg)
    return result or None


def _state(evidence, *, varying_mass=False):
    names = ["initial.f64", "initial_velocities.f64", "boundary.i32", "tetrahedra.u32"]
    if not varying_mass:
        names.append("masses.f64")
    for name in names:
        if evidence.payload("raw/" + name) is None:
            return None
    return {name: evidence.references[f"{evidence.source['case_ref']}/raw/{name}"] for name in names}


def _assets(evidence):
    files = evidence.identity.get("source") or {}
    required = set(SCENES[evidence.scene]["required_assets"])
    required.add("Assets/scene/parameterSetting.txt")
    if not required.issubset(files):
        return None
    return {name: digest for name, digest in files.items() if name.startswith("Assets/")}


class ArchiveComparison:
    def __init__(self, service):
        self.service = service

    def create(self, left, right, variable="repeat"):
        if variable not in VARIANTS:
            raise PlanningError("COMPARISON_VARIABLE", "请选择已定义的对照变量。")
        a = ClothEvidence(self.service.archive, *left)
        b = ClothEvidence(self.service.archive, *right)
        reasons = []
        checks = []

        def check(label, x, y, *, may_differ=False):
            known = x is not None and y is not None
            ok = known and (x == y or may_differ)
            checks.append({"item": label, "status": "PASS" if ok else "DIFFERENT" if known else "UNVERIFIED",
                           "changed": known and x != y, "allowed_to_differ": may_differ})
            if not ok:
                reasons.append(label + ("不同。" if known else "未证实。"))
            return ok

        if a.source["case_ref"] == b.source["case_ref"]:
            reasons.append("两侧引用同一个运行。")
        if not all(e.binding_ok and e.report.get("status") == "PASS" for e in (a, b)):
            reasons.append("至少一侧的运行校验、场景或计时绑定未通过。")
        check("场景", a.scene, b.scene)
        check("资产、网格及物理配置哈希", _assets(a), _assets(b))
        material_plans = [e.report.get("plan", {}).get("material") for e in (a, b)]
        observed = []
        for e, material in zip((a, b), material_plans):
            value = e.object("raw/material_effective.json") if material else None
            if material:
                e.payload("inputs/material.json")
                e.payload("raw/masses.f64")
            if value and (value != e.report.get("material_evidence") or e.issues):
                reasons.append("材料报告与初始化原始证据不符。")
                value = None
            observed.append(value)
        changes = []
        if all(observed):
            aa_material, bb_material = (item["effective"] for item in observed)
            changes = [name for name in aa_material if aa_material[name] != bb_material.get(name)]
            check("实际材料参数", aa_material, bb_material, may_differ=variable == "material" and len(changes) == 1)
            invariant_keys = ("thickness_m", "bending_young_pa", "strain_rate", "friction", "ground_friction", "dt_s")
            derived_invariants = lambda item: {key: item["derived"][key] for key in invariant_keys}
            check("厚度、弯曲模量、应变率、摩擦和时间步", *(derived_invariants(v) for v in observed))
        elif any(material_plans) or variable == "material":
            reasons.append("两侧均须具有可核对的原生材料初始化证据。")
        if variable == "material" and len(changes) != 1:
            reasons.append("材料对照要求只改变一项已生效的布片材料参数。")
        varying_mass = variable == "material" and changes == ["density_kg_m3"]
        label = ("初始顶点、速度、边界和四面体哈希" if varying_mass
                 else "初始顶点、速度、质量、边界和四面体哈希")
        check(label, _state(a, varying_mass=varying_mass), _state(b, varying_mass=varying_mass))
        if a.setup or b.setup or a.metadata.get("cloth_scene") or b.metadata.get("cloth_scene"):
            # Native scene descriptions record material and component roles. Camera is visual only.
            setup = lambda e: {k: v for k, v in e.setup.items() if k != "camera"} if e.setup else None
            check("具名场景材料、组件和边界", setup(a), setup(b))
        check("原生命令选项", _args(a.run_meta, variable), _args(b.run_meta, variable))
        check("GPU 绑定与场景选择器", a.run_meta.get("environment"), b.run_meta.get("environment"))
        check("GPU 设备清单", a.run_meta.get("gpu"), b.run_meta.get("gpu"))
        check("源码版本", a.report.get("implementation_commit"), b.report.get("implementation_commit"),
              may_differ=variable == "solver_version")
        left_build, right_build = (e.run_meta.get("build_sha256") for e in (a, b))
        check("原生构建", left_build, right_build, may_differ=variable == "solver_version")
        if variable == "solver_version" and left_build and left_build == right_build:
            reasons.append("声明了构建变量，但两侧运行同一构建。")
        ma, mb = (e.metadata.get("method") for e in (a, b))
        graph_change = variable == "cuda_graph" and {ma, mb} == GRAPH_METHODS
        check("求解运行模式", ma, mb, may_differ=graph_change)
        if variable == "cuda_graph" and not graph_change:
            reasons.append("归档未证实一侧双段 Graph、另一侧 eager。")
        for key in METADATA_KEYS:
            check(key, a.metadata.get(key), b.metadata.get(key))
        if any(e.metadata.get("trace_newton") or e.metadata.get("profile_stages") for e in (a, b)):
            reasons.append("包含诊断插桩，排除在性能基准对照之外。")

        steps_a, steps_b = a.read_steps(), b.read_steps()
        common = min(a.target, b.target)
        dt = finite(a.metadata.get("dt"), positive=True)
        aa, bb = [row["step_ms"] for row in steps_a[:common]], [row["step_ms"] for row in steps_b[:common]]
        if dt is None or len(aa) != common or len(bb) != common or any(v is None for v in aa + bb):
            reasons.append("共同物理步缺少有效毫秒计时或时间步。")
        metrics = None
        if not reasons:
            metrics = {
                "aligned_frames": [1, common], "aligned_physical_time_s": [dt, common * dt],
                "sample_count_per_side": common, "left_median_ms": median(aa), "right_median_ms": median(bb),
                "left_total_ms": sum(aa), "right_total_ms": sum(bb), "left_over_right": sum(aa) / sum(bb),
            }
        plan = {"left": a.source, "right": b.source, "declared_variable": variable,
                "input_sha256": {**a.references, **b.references}}
        result = {
            "schema_version": "archive-comparison-v1", "comparison_id": new_id("comparison"),
            "created_at": now(), "plan": plan, "digest": plan_digest(plan),
            "status": "COMPARABLE" if metrics else "NOT_COMPARABLE", "checks": checks,
            "reasons": reasons, "metrics": metrics,
            **({"material": {"left": observed[0]["effective"], "right": observed[1]["effective"],
                             "changed": changes}} if all(observed) else {}),
            "limits": ["每侧只有一份运行，未进行统计显著性检验。",
                       "本入口未验证同拓扑逐帧坐标，未计算相对参考运行的顶点误差。"],
        }
        with self.service.store.transaction() as state:
            state["comparisons"][result["comparison_id"]] = result
        return result
