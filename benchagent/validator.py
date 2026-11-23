import math
import re
from .checks import SCHEMA, serializable_observation, skipped_checks

from .conversation_service import SCENE_NUMBERS, scene_selector

def validate_run(plan, execution, data=None, *, parse_error=None):
    """Validate available evidence; parser failures leave dependent checks skipped."""
    errors, warnings = [], []
    checks = skipped_checks("缺少可解析的统计证据，未执行本项检查")
    by_id = {item["id"]: item for item in checks}

    def record(code, ok, actual, failures=(), *, expected=None, warning=False):
        item = by_id[code]
        item.update(status="PASS" if ok else "WARNING" if warning else "FAIL",
                    actual=serializable_observation(actual))
        if expected is not None:
            item["expected"] = expected
        item["message"] = "检查通过" if ok else "存在非关键缺项，保留告警" if warning else "检查未通过"
        item["details"] = list(failures) if not ok else []
        (warnings if warning else errors).extend(failures if not ok else [])

    n = plan["frames"]
    attempted = data is not None or execution.get("returncode") is not None or execution.get("timeout") is True
    if attempted:
        record("V01", execution.get("returncode") == 0 and not execution.get("timeout"),
               {"returncode": execution.get("returncode"), "timeout": execution.get("timeout")},
               ["Process failed or timed out"])
        markers = re.findall(r"^TEST_COMPLETE frames=(\d+)\s*$", execution.get("stdout", ""), re.M)
        record("V02", markers == [str(n)], {"completion_frames": markers},
               ["Missing, duplicate or incorrect completion marker"],
               expected={"completion_frames": [str(n)]})
        identity_errors = []
        if execution.get("identity_unchanged") is not True:
            identity_errors.append("Runtime/source identity changed")
        if execution.get("fresh_output") is not True:
            identity_errors.append("Output does not belong to this run")
        record("V05", not identity_errors,
               {key: execution.get(key) for key in ("fresh_output", "identity_unchanged")}, identity_errors)

    if data is None:
        if parse_error is not None:
            record("V06", False, {"parse_error": str(parse_error)}, [str(parse_error)])
        return {"status": "FAIL", "errors": errors, "warnings": warnings,
                "quality_status": "NOT_ASSESSED", "checks_schema": SCHEMA, "checks": checks}

    status, rows, traces, meta = data["status"], data["rows"], data["traces"], data["metadata"]
    # This entry point consumes the validated result of parse_artifacts().
    record("V06", True, {"parsed": True, "frame_records": len(rows), "pcg_records": len(traces)})

    frames = [r["frame"] for r in rows]
    record("V03", frames == list(range(1, n + 1)),
           {"actual_frames": len(rows), "frame_sequence": frames}, ["Frame sequence/count mismatch"],
           expected={"actual_frames": n, "frame_sequence": list(range(1, n + 1))})
    status_ok = (status.get("completed") is True and status.get("finite_state") is True
                 and type(status.get("frames_completed")) is int and status["frames_completed"] == n)
    record("V04", status_ok,
           {key: status.get(key) for key in ("completed", "finite_state", "frames_completed")},
           ["Incomplete or invalid status.json"],
           expected={"completed": True, "finite_state": True, "frames_completed": n})

    scene_no = SCENE_NUMBERS[plan.get("scene", "default")]
    cloth_scene = scene_selector(plan.get("scene", "default"))
    scene_keys = ("frames", "scene_no", "cloth_scene", "initial_physical_frame")
    scene_ok = (meta.get("frames") == n and meta.get("scene_no") == scene_no
                and meta.get("cloth_scene") == cloth_scene and meta.get("initial_physical_frame") == 0)
    record("V09", scene_ok, {key: meta.get(key) for key in scene_keys},
           ["Scene or initial state mismatch"],
           expected={"frames": n, "scene_no": scene_no, "cloth_scene": cloth_scene, "initial_physical_frame": 0})

    expected = {"termination": "compensated", "method": "pcg_dual_graph",
                "converter_indexed_reduce": True, "toi_min_updates": 6,
                "toi_threshold": .001, "residual_threshold": .03,
                "residual_floor": 1e-30, "newton_tolerance_scale": 1,
                "pcg_tolerance_scale": 1, "production_measurement": True,
                "profile_stages": False, "trace_newton": bool(plan.get("diagnostic_contact_trace")), "audit_frame": 0,
                "checkpoint_in": ""}
    config_errors = []
    for key, value in expected.items():
        ok = (type(meta.get(key)) is type(value) and meta.get(key) == value
              if isinstance(value, bool) else meta.get(key) == value)
        if not ok:
            config_errors.append("C0 configuration mismatch: " + key)
    expected["base_commit"] = "c49982fac7767225c7775683734c89eb74081142"
    if meta.get("base_commit") != expected["base_commit"]:
        config_errors.append("Base commit mismatch")
    weight = meta.get("residual_weight")
    if not (isinstance(weight, (int, float)) and not isinstance(weight, bool)
            and math.isclose(weight, 1/30, rel_tol=1e-12)):
        config_errors.append("Residual weight mismatch")
    expected["residual_weight"] = 1/30
    record("V10", not config_errors, {key: meta.get(key) for key in expected},
           config_errors, expected=expected)

    for code, keys in (
        ("V11", ("newton_cap_hits", "diagnostic_budget_hits", "nonfinite_vertices")),
        ("V12", ("fallbacks",)),
    ):
        failures, violations = [], []
        for row in rows:
            for key in keys:
                if row[key] != 0:
                    failures.append(f"Frame {row['frame']}: {key}")
                    violations.append({"frame": row["frame"], "field": key, "value": row[key]})
        actual = {key: sum(row[key] for row in rows) for key in keys}
        actual["violations"] = violations
        if code == "V12":
            launches = sum(r["direction_launches"] for r in rows)
            actual["direction_launches"] = launches
            if launches <= 0:
                failures.append("No actual Graph execution")
        record(code, not failures, actual, failures)

    pcg_errors, coverage_warnings = [], []
    ids = [t["frame"] for t in traces]
    if len(ids) != len(set(ids)) or not set(ids).issubset(set(frames)):
        pcg_errors.append("Invalid PCG frame IDs")
    trace_map = {t["frame"]: t["iterations"] for t in traces}
    covered, missing, partial, mismatches = 0, [], [], []
    expected_solves = sum(r["pcg_solves"] for r in rows)
    for row in rows:
        items = trace_map.get(row["frame"])
        if items is None:
            coverage_warnings.append(f"PCG detail missing in frame {row['frame']}")
            missing.append(row["frame"])
            continue
        if len(items) > row["pcg_solves"]:
            pcg_errors.append("Too many PCG subrecords")
            mismatches.append(row["frame"])
        covered += len(items)
        if len(items) < row["pcg_solves"]:
            coverage_warnings.append(f"Partial PCG detail in frame {row['frame']}")
            partial.append(row["frame"])
            if sum(items) > row["pcg_iterations"]:
                pcg_errors.append("Partial PCG count exceeds aggregate")
                mismatches.append(row["frame"])
        elif sum(items) != row["pcg_iterations"]:
            pcg_errors.append("PCG aggregate/detail mismatch")
            mismatches.append(row["frame"])
    coverage = covered / expected_solves if expected_solves else None
    data["metrics"]["pcg_iterations_coverage"] = coverage
    if not expected_solves:
        coverage_warnings.append("No PCG solves; coverage is undefined")
    record("V07", not pcg_errors, {
        "unique_valid_frames": len(ids) == len(set(ids)) and set(ids).issubset(set(frames)),
        "mismatch_frames": sorted(set(mismatches)),
        "aggregate_iterations": sum(r["pcg_iterations"] for r in rows),
        "recorded_iterations": sum(sum(t["iterations"]) for t in traces),
    }, pcg_errors)
    record("V08", not coverage_warnings, {
        "expected_solves": expected_solves, "covered_solves": covered,
        "coverage": coverage, "missing_frames": missing, "partial_frames": partial,
    }, coverage_warnings, warning=True)

    return {"status": "FAIL" if errors else "PASS", "errors": errors, "warnings": warnings,
            "quality_status": "NOT_ASSESSED", "checks_schema": SCHEMA, "checks": checks}
