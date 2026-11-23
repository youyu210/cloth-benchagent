"""Run and verify BenchAgent cloth material cases on the sealed GPU build."""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchagent.app_service import AppService
from benchagent.common import read, sha, write
from benchagent.conversation_service import task_request
from benchagent.job_manager import TERMINAL
from benchagent.materials import MATERIAL_SCENES
from benchagent.planning_store import PlanningStore, new_id

EVIDENCE = ROOT / "docs/material_gpu_evidence"


def run_case(app, plan, expected):
    receipt = app.submit(plan["plan_id"], plan["plan_digest"], new_id("confirm"), new_id("execution"))
    job_id = receipt["job_id"]
    deadline = time.monotonic() + 640
    while time.monotonic() < deadline:
        status = app.job(job_id)
        if status["state"] in TERMINAL:
            break
        time.sleep(.5)
    else:
        app.cancel(job_id, new_id("cancel"))
        raise RuntimeError(f"Timed out waiting for {job_id}")
    case_id = status["cases"][0]["case_id"]
    folder = ROOT / "runs" / job_id / "cases" / case_id
    if not (folder / "report.json").is_file():
        raise RuntimeError(f"{job_id}: {status.get('failure_kind')}")
    report = read(folder / "report.json")
    material = report.get("material_evidence") or {}
    print(json.dumps({"job": job_id, "case": case_id, "status": report["status"],
                      "errors": report["errors"], "effective": material.get("effective")}),
          flush=True)
    assert report["status"] == "PASS" and report["actual_frames"] == 20
    assert report["visualization"]["frames"] == [0, 10, 20]
    assert material["effective"] == expected
    assert next(check for check in report["checks"] if check["id"] == "M01")["status"] == "PASS"
    return folder


def vectors(path):
    return list(struct.iter_unpack("<3d", path.read_bytes()))


def fixed_comparison(first, second):
    a, b = (read(folder / "report.json") for folder in (first, second))
    for relative in ("initial.f64", "initial_velocities.f64", "boundary.i32", "tetrahedra.u32"):
        assert sha(first / "raw" / relative) == sha(second / "raw" / relative), relative
    initial = vectors(first / "raw/initial.f64")
    final_a = vectors(first / "raw/final.f64")
    final_b = vectors(second / "raw/final.f64")
    assert len(initial) == len(final_a) == len(final_b) > 0
    assert a["execution_identity"]["build_sha256"] == b["execution_identity"]["build_sha256"]
    assert read(first / "run_meta.json")["environment"] == read(second / "run_meta.json")["environment"]
    assert read(first / "run_meta.json")["gpu"] == read(second / "run_meta.json")["gpu"]
    ea, eb = (r["material_evidence"]["derived"] for r in (a, b))
    for field in ("dt_s", "thickness_m", "bending_young_pa", "friction", "ground_friction"):
        assert ea[field] == eb[field], field
    delta = [math.dist(x, y) for x, y in zip(final_a, final_b)]
    assert all(math.isfinite(v) for v in delta)
    return {"left": first.relative_to(ROOT).as_posix(), "right": second.relative_to(ROOT).as_posix(),
            "vertices": len(delta), "max_final_distance_m": max(delta),
            "rms_final_distance_m": math.sqrt(math.fsum(d * d for d in delta) / len(delta)),
            "left_physics_time_ms": a["solver_time_mean_ms_per_frame"] * 20,
            "right_physics_time_ms": b["solver_time_mean_ms_per_frame"] * 20,
            "left_newton_iterations": a["newton_iterations_total"],
            "right_newton_iterations": b["newton_iterations_total"],
            "left_pcg_iterations": a["pcg_iterations_total_from_csv"],
            "right_pcg_iterations": b["pcg_iterations_total_from_csv"]}


def verify_cases(cases):
    original, changed, combined, reset, sphere = cases
    comparisons = [fixed_comparison(original, changed), fixed_comparison(changed, combined),
                   fixed_comparison(original, reset)]
    unchanged_delta = comparisons[2]["max_final_distance_m"]
    assert all(item["max_final_distance_m"] > max(1e-8, unchanged_delta) for item in comparisons[:2])
    def material(folder):
        return read(folder / "raw/material_effective.json")["derived"]
    assert material(original) == material(reset)
    assert math.isclose(material(changed)["stretch_stiff"] / material(original)["stretch_stiff"], 2)
    assert sha(original / "raw/masses.f64") == sha(changed / "raw/masses.f64")
    assert math.isclose(material(combined)["gpu_cloth_mass_kg"] / material(changed)["gpu_cloth_mass_kg"], 1.5)
    assert sha(original / "raw/masses.f64") == sha(reset / "raw/masses.f64")
    assert read(sphere / "report.json")["plan"]["material"]["previous"] == {}
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run five real 20-frame GPU tasks.")
    args = parser.parse_args()
    if not args.run:
        parser.error("Use --run to confirm GPU acceptance tasks.")
    acceptance = EVIDENCE / new_id("acceptance")
    acceptance.mkdir(parents=True)
    app = AppService(ROOT, store=PlanningStore(acceptance / "planning"))
    try:
        assert not (ROOT / "runs/.execution.lock").exists()
        assert not (ROOT / "data/planning/.manager.lock").exists()
        catalog = ROOT / "configs/material_catalog.json"
        preserved = [catalog, ROOT / "versions/source_manifest.json", ROOT / "versions/runtime_manifest.json",
                     ROOT / "versions/native_frames_manifest.json",
                     ROOT / "versions/native-frames-source/StiffGIPC/gl_main.cu",
                     ROOT / "simulator/Assets/scene/parameterSetting.txt"]
        source_hashes = {path: sha(path) for path in preserved}
        defaults = MATERIAL_SCENES["fixed-cloth"]["defaults"]
        base = task_request(new_id("material-baseline"), {
            "scene": "fixed-cloth", "task_frames": [20], "native_render": True, "timeout_sec": 600,
            "material": {"object": "cloth", "values": {}, "previous": {}},
        })
        first = app.preview(None, base, 0)
        original = run_case(app, first["plan"], defaults)

        def advance(result, message):
            return app.preview(result["conversation_id"], message, result["draft"]["revision"])

        second = advance(first, "把固定布料的杨氏模量改成原来的两倍，运行20帧。")
        edited = {**defaults, "young_pa": 100000}
        changed = run_case(app, second["plan"], edited)
        third = advance(second, "把密度改为300 kg/m³，其他参数保持不变。")
        combined = run_case(app, third["plan"], {**edited, "density_kg_m3": 300})
        assert third["draft"]["task_request"]["material"]["previous"] == {"young_pa": 100000}
        fourth = advance(third, "把泊松比改成0.3，其他参数保持不变。")
        assert fourth["draft"]["task_request"]["material"]["values"] == {
            "young_pa": 100000, "density_kg_m3": 300, "poisson_ratio": .3}
        restored = advance(fourth, "恢复这个场景的默认材料参数。")
        reset = run_case(app, restored["plan"], defaults)
        isolated = advance(restored, "将球体与布料场景中布料的密度修改为300，运行20帧。")
        assert isolated["draft"]["task_request"]["material"]["previous"] == {}
        sphere = run_case(app, isolated["plan"], {**MATERIAL_SCENES["cloth-sphere7"]["defaults"],
                                                  "density_kg_m3": 300})

        comparisons = verify_cases((original, changed, combined, reset, sphere))
        assert all(sha(path) == digest for path, digest in source_hashes.items())
        evidence = {
            "cases": {key: folder.relative_to(ROOT).as_posix() for key, folder in
                      zip(("T1", "T2", "T3", "T7", "T8"), (original, changed, combined, reset, sphere))},
            "comparisons": comparisons, "T5": fourth["draft"]["task_request"],
            "preserved_sha256": {p.relative_to(ROOT).as_posix(): h for p, h in source_hashes.items()},
        }
        write(acceptance / "result.json", evidence)
        print(acceptance / "result.json", flush=True)
    finally:
        app.close()


if __name__ == "__main__":
    main()

