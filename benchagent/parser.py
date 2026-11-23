import csv
import math
from .common import read

def number(value, integer=False):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a metric")
    n = float(value)
    if not math.isfinite(n) or n < 0 or (integer and n != int(n)):
        raise ValueError("Invalid metric: " + str(value))
    return int(n) if integer else n

def parse_artifacts(raw):
    with (raw / "frames.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError("Zero completed frames")
    required = ("frame", "newton_iterations", "pcg_solves", "pcg_iterations",
                "newton_cap_hits", "diagnostic_budget_hits", "fallbacks",
                "nonfinite_vertices", "direction_launches", "tail_launches")
    for row in rows:
        for key in required:
            row[key] = number(row[key], integer=True)
        for key in ("step_ms", "newton_ms", "pcg_ms"):
            row[key] = number(row[key])
        if row["step_ms"] <= 0:
            raise ValueError("Frame time must be positive")
    traces = []
    with (raw / "pcg_iterations.jsonl").open(encoding="utf-8") as f:
        import json
        for line in f:
            if not line.strip():
                raise ValueError("Empty PCG record")
            obj = json.loads(line)
            obj["frame"] = number(obj["frame"], integer=True)
            if not isinstance(obj["iterations"], list):
                raise ValueError("PCG iterations must be a list")
            obj["iterations"] = [number(v, integer=True) for v in obj["iterations"]]
            traces.append(obj)
    collisions = read(raw / "collision_summary.json")
    for key in ("collision_counter_total", "collision_pair_max"):
        collisions[key] = number(collisions[key], integer=True)
    times = [r["step_ms"] for r in rows]
    return {"rows": rows, "traces": traces, "metadata": read(raw / "metadata.json"),
            "status": read(raw / "status.json"), "collisions": collisions,
            "metrics": {
                "actual_frames": len(rows), "frame_solver_times_ms": times,
                "solver_time_total_s": sum(times) / 1000,
                "solver_time_mean_ms_per_frame": sum(times) / len(times),
                "solver_time_min_ms": min(times), "solver_time_max_ms": max(times),
                "newton_iterations_total": sum(r["newton_iterations"] for r in rows),
                "newton_iterations_mean_per_frame": sum(r["newton_iterations"] for r in rows) / len(rows),
                "pcg_iterations_total_from_csv": sum(r["pcg_iterations"] for r in rows),
                "pcg_iterations_total_from_stats": sum(sum(t["iterations"]) for t in traces),
                "pcg_solves": sum(r["pcg_solves"] for r in rows),
                "collision_counter_total": collisions["collision_counter_total"],
                "collision_pair_max": collisions["collision_pair_max"],
                "timing_scope": "synchronized physics step wall time; includes graph preparation; excludes initialization, rendering and exports"}}
