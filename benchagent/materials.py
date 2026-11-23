"""Task-scoped cloth materials and checked native initialization evidence."""
import math
import struct

from .common import ROOT, read
from .planning_store import PlanningError

CATALOG_PATH = "configs/material_catalog.json"
CATALOG = read(ROOT / CATALOG_PATH)
MATERIAL_SCENES = CATALOG["scenes"]
MATERIAL_FIELDS = CATALOG["parameters"]
MATERIAL_RUNTIME = "native-material"
MATERIAL_INPUT = "inputs/material.json"
MATERIAL_EVIDENCE = "raw/material_effective.json"


def material_spec(scene):
    return MATERIAL_SCENES.get(scene)


def validate_material(scene, material):
    spec = material_spec(scene)
    if not spec:
        raise PlanningError("MATERIAL_SCENE", "此场景未开放按任务修改材料。")
    if (not isinstance(material, dict) or set(material) != {"object", "values", "previous"}
            or material["object"] != spec["object"]):
        raise PlanningError("MATERIAL_OBJECT", "请选择目录中可修改的布片对象。")
    for values in (material["values"], material["previous"]):
        if not isinstance(values, dict):
            raise PlanningError("MATERIAL_SCHEMA", "材料值必须按字段提交。")
        for name, value in values.items():
            field = MATERIAL_FIELDS.get(name)
            if name not in spec["editable"]:
                reason = spec.get("read_only", {}).get(name, "该参数未开放独立配置。")
                raise PlanningError("MATERIAL_FIELD", f"{field['label'] if field else name}。{reason}")
            if type(value) not in (float, int) or not field["min"] <= value <= field["max"]:
                raise PlanningError("MATERIAL_RANGE",
                                    f"{field['label']}须在 {field['min']}–{field['max']} {field['unit']} 内。")
    return material


def native_config(scene, material):
    validate_material(scene, material)
    return {"schema": CATALOG["schema"], "scene": scene, "object": material["object"],
            "values": material["values"]}


def material_rows(scene, material, applied=None):
    spec = MATERIAL_SCENES[scene]
    defaults = spec["defaults"]
    return [{
        "参数": field["label"], "单位": field["unit"], "默认值": default,
        "修改前": material["previous"].get(name, default),
        "计划值": material["values"].get(name, default),
        **({"实际值": applied.get(name)} if applied is not None else {}),
        "预期影响": spec.get("read_only", {}).get(name, field["effect"]),
    } for name, default in defaults.items() for field in (MATERIAL_FIELDS[name],)]


def verify_material(folder, scene, material):
    planned = native_config(scene, material)
    evidence = read(folder / MATERIAL_EVIDENCE)
    defaults = MATERIAL_SCENES[scene]["defaults"]
    expected = {**defaults, **material["values"]}
    if (read(folder / MATERIAL_INPUT) != planned or evidence["request"] != planned
            or evidence["defaults"] != defaults or evidence["effective"] != expected
            or any(type(v) not in (float, int) for v in evidence["effective"].values())):
        raise ValueError("原生默认值、输入配置或实际材料值与冻结计划不符")
    derived = evidence["derived"]
    mass_bytes = (folder / "raw/masses.f64").read_bytes()
    masses = [item[0] for item in struct.iter_unpack("<d", mass_bytes)]
    if not masses or not all(math.isfinite(mass) and mass >= 0 for mass in masses):
        raise ValueError("设备质量记录为空或包含非法数值")
    if scene != "fixed-cloth":
        scene_data = read(folder / "raw/scene_setup.json")
        roles = scene_data["roles"]
        if len(roles) != len(masses):
            raise ValueError("设备质量和场景对象索引无法对齐")
        masses = [mass for mass, role in zip(masses, roles) if role == 1]
    stretch = expected["young_pa"] / (2 * (1 + expected["poisson_ratio"]))
    nu = expected["poisson_ratio"]
    thickness = derived["thickness_m"]
    checks = {
        "stretch_stiff": stretch,
        "shear_stiff": .03 * stretch * derived["strain_rate"],
        "bend_stiff": derived["bending_young_pa"] * thickness ** 3 / (24 * (1 - nu * nu)),
        "areal_density_kg_m2": expected["density_kg_m3"] * thickness,
        "cloth_vertex_count": len(masses),
        "gpu_cloth_mass_kg": math.fsum(masses),
    }
    if checks["gpu_cloth_mass_kg"] <= 0 or not all(
        math.isclose(derived[key], value, rel_tol=1e-10, abs_tol=1e-12)
        for key, value in checks.items()
    ):
        raise ValueError("FEM 系数或 GPU 布片质量与初始化证据不一致")
    return evidence

