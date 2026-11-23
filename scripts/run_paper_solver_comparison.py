"""Run paired, fresh-process paper comparisons of the original and modified solver."""
import csv
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, read, sha, write
from benchagent.parser import number, parse_artifacts
from benchagent.runner import exclusive
from benchagent.validator import validate_run

ORIGINAL = ROOT / "build/paper-original/gipc.exe"
MODIFIED = ROOT / "build/bin/gipc.exe"
SOURCE_ORIGINAL = ROOT / "versions/paper-original-source"
SOURCE_MODIFIED = ROOT / "simulator"
PAPER = ROOT / "docs/BenchAgent_论文材料"
FRAMES = 100
ASSETS = (
    "scene/parameterSetting.txt", "scene/abd_system_config.json",
    "tetMesh/bunny2.msh", "triMesh/cloth_high.obj",
    "sorted_mesh/bunny2_sorted.16.msh", "sorted_mesh/bunny2_sorted.16.part",
    "sorted_mesh/cloth_high_sorted.16.obj", "sorted_mesh/cloth_high_sorted.16.part",
)
INITIAL = ("initial.f64", "initial_velocities.f64", "masses.f64", "boundary.i32", "tetrahedra.u32")
MATCH_METADATA = ("scene_no", "frames", "vertex_count", "tetrahedron_count", "dt",
                  "gpu_index", "pcg_tolerance", "newton_tolerance")


def source_manifest():
    original_before = read(ROOT / "versions/paper-original-inputs.json")
    original_after = {name: sha(SOURCE_ORIGINAL / name) for name in original_before}
    changed = sorted(name for name, digest in original_after.items() if digest != original_before[name])
    if changed != ["StiffGIPC/GIPC.cu", "StiffGIPC/gl_main.cu"]:
        raise ValueError(f"Unexpected original source changes: {changed}")
    modified = read(ROOT / "versions/source_manifest.json")["files"]
    if any(sha(SOURCE_MODIFIED / name) != digest for name, digest in modified.items()):
        raise ValueError("Modified source/assets do not match the sealed manifest")
    runtime = read(ROOT / "versions/runtime_manifest.json")["files"]
    if any(sha(ROOT / "build/bin" / name) != digest for name, digest in runtime.items()):
        raise ValueError("Modified runtime does not match the sealed manifest")
    assets = {}
    for name in ASSETS:
        a, b = SOURCE_ORIGINAL / "Assets" / name, SOURCE_MODIFIED / "Assets" / name
        if a.read_bytes().replace(b"\r\n", b"\n") != b.read_bytes().replace(b"\r\n", b"\n"):
            raise ValueError(f"Scene asset mismatch: {name}")
        assets[name] = {"original_sha256": sha(a), "modified_sha256": sha(b),
                        "identical_after_normalizing_line_endings": True}
    def build_settings(path):
        keys = {"CMAKE_CUDA_ARCHITECTURES", "CMAKE_CXX_COMPILER", "CMAKE_CUDA_COMPILER",
                "CMAKE_BUILD_TYPE", "CMAKE_CUDA_FLAGS_RELEASE", "CMAKE_CXX_FLAGS_RELEASE",
                "CMAKE_CXX_FLAGS"}
        values = {}
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name = line.split(":", 1)[0]
            if name in keys:
                values[name] = line.split("=", 1)[1]
        return values

    return {"build_settings": {
                "original": build_settings(ROOT / "build/paper-original/CMakeCache.txt"),
                "modified": build_settings(ROOT / "build/native/CMakeCache.txt"),
            }, "original_base_commit": "c49982fac7767225c7775683734c89eb74081142",
            "original_binary_sha256": sha(ORIGINAL), "original_source_files": original_after,
            "original_changed_files": changed, "modified_binary_sha256": sha(MODIFIED),
            "modified_source_manifest_sha256": sha(ROOT / "versions/source_manifest.json"),
            "modified_runtime_manifest_sha256": sha(ROOT / "versions/runtime_manifest.json"),
            "shared_runtime_dlls": {key: val for key, val in runtime.items() if key.endswith(".dll")},
            "profile_sha256": sha(ROOT / "configs/c0.json"), "scene_assets": assets}


def gpu_snapshot():
    query = "index,uuid,name,driver_version,temperature.gpu,utilization.gpu,clocks.sm"
    return subprocess.check_output(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"], text=True, timeout=10
    ).strip().splitlines()


def run_one(folder, version, env, identity):
    folder.mkdir()
    raw = folder / "raw"
    if version == "original":
        cmd = [str(ORIGINAL), "--paper-benchmark", str(FRAMES), str(raw)]
        cwd = SOURCE_ORIGINAL
    else:
        cmd = [str(MODIFIED), *read(ROOT / "configs/c0.json")["args"],
               "--benchmark-frames", str(FRAMES), "--benchmark-output-dir", str(raw)]
        cwd = SOURCE_MODIFIED
    gpu_before = gpu_snapshot()
    write(folder / "command.json", {"argv": cmd, "cwd": str(cwd), "gpu_before": gpu_before,
                                    "CUDA_VISIBLE_DEVICES": env["CUDA_VISIBLE_DEVICES"],
                                    "CUDA_DEVICE_ORDER": env["CUDA_DEVICE_ORDER"]})
    started = perf_counter()
    with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
        process = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=err, timeout=180)
    wall = perf_counter() - started
    gpu_after = gpu_snapshot()
    write(folder / "execution.json", {"returncode": process.returncode, "wall_s": wall, "gpu_after": gpu_after})
    stdout = (folder / "stdout.log").read_text(encoding="utf-8", errors="replace")
    if process.returncode or stdout.splitlines().count(f"TEST_COMPLETE frames={FRAMES}") != 1:
        raise RuntimeError(f"Run did not finish: {folder}")
    status, meta = read(raw / "status.json"), read(raw / "metadata.json")
    if (status.get("completed") is not True or status.get("finite_state") is not True
            or status.get("frames_completed") != FRAMES or meta.get("scene_no") != 1
            or meta.get("gpu_index") != 0):
        raise ValueError(f"Unexpected run identity or invalid state: {folder}")
    if version == "modified":
        data = parse_artifacts(raw)
        verdict = validate_run({"scene": "default", "frames": FRAMES},
                               {"returncode": 0, "stdout": stdout, "fresh_output": True,
                                "identity_unchanged": sha(MODIFIED) == identity["modified_binary_sha256"]}, data)
        write(folder / "checks.json", verdict)
        if verdict["status"] != "PASS":
            raise ValueError(f"Modified validation failed: {verdict['errors']}")
        rows = data["rows"]
        newton = [r["newton_iterations"] for r in rows]
        pcg = [r["pcg_iterations"] for r in rows]
        collision = data["collisions"]["collision_counter_total"]
    else:
        with (raw / "frames.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        if any(number(r["newton_cap_hits"], True) for r in rows):
            raise ValueError("Original Newton iteration cap reached")
        newton = [number(r["newton_iters"], True) for r in rows]
        pcg = [number(r["pcg_iters"], True) for r in rows]
        counters = [number(r["collision_counter_cumulative"], True) for r in rows]
        if counters != sorted(counters):
            raise ValueError("Original cumulative collision counter went backwards")
        collision = counters[-1]
    steps = [number(r["step_ms"]) for r in rows]
    if [number(r["frame"], True) for r in rows] != list(range(1, FRAMES + 1)) or min(steps) <= 0:
        raise ValueError("Unexpected frame sequence or timer")
    result = {"version": version, "frames": FRAMES, "step_total_s": math.fsum(steps) / 1000,
              "step_mean_ms": statistics.mean(steps), "newton_total": sum(newton), "pcg_total": sum(pcg),
              "collision_counter_total": collision, "wall_s": wall, "metadata": meta,
              "initial_sha256": {name: sha(raw / name) for name in INITIAL},
              "final_sha256": sha(raw / "final.f64"), "gpu_before": gpu_before, "gpu_after": gpu_after}
    write(folder / "metrics.json", result)
    return result


def main():
    root = PAPER / ("原版与改进版对照-" + uuid4().hex[:8])
    root.mkdir()
    print(root, flush=True)
    identity = source_manifest()
    write(root / "build_and_inputs.json", identity)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("GIPC_", "CIPC_"))}
    env.update(CUDA_VISIBLE_DEVICES="0", CUDA_DEVICE_ORDER="PCI_BUS_ID")
    env["PATH"] = str(ROOT / "build/bin") + os.pathsep + env["PATH"]
    all_runs = []
    with exclusive():
        reference = None
        for pair, order in enumerate((("original", "modified"), ("modified", "original"),
                                      ("original", "modified")), 1):
            for version in order:
                name = f"pair-{pair}-{version}"
                result = run_one(root / name, version, env, identity)
                group = (result["initial_sha256"],
                         {key: result["metadata"][key] for key in MATCH_METADATA},
                         [line.split(",")[1].strip() for line in result["gpu_before"][:1]])
                if reference is not None and group != reference:
                    write(root / "mismatch.json", {"expected": reference, "observed": group, "run": name})
                    raise ValueError(f"Initial state or configuration differs: {name}")
                reference = group
                all_runs.append({"directory": name, **result})
                write(root / "runs.json", all_runs)
                print(name, f"{result['step_mean_ms']:.3f} ms/frame", flush=True)
        if source_manifest() != identity:
            raise ValueError("Source or binaries changed during the comparison")
    stats = {}
    for version in ("original", "modified"):
        values = [r["step_mean_ms"] for r in all_runs if r["version"] == version]
        stats[version] = {"mean_ms": statistics.mean(values), "sd_ms": statistics.stdev(values), "n": len(values)}
    write(root / "comparison.json", {"status": "PASS", "statistics": stats,
                                    "speedup": stats["original"]["mean_ms"] / stats["modified"]["mean_ms"],
                                    "initial_state_and_configuration_identical": True})
    print(stats, flush=True)


if __name__ == "__main__":
    main()
