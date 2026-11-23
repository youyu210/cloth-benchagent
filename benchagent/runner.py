import contextlib
import shutil
import os
import subprocess
import time
import uuid
from pathlib import Path
from .common import ROOT, IMPLEMENTATION, now, read, write
from .planner import validate_plan
from .preflight import inspect, identity
from .parser import parse_artifacts
from .validator import validate_run
from .checks import SCHEMA, preflight_check, skipped_checks
from .reporter import report_run, report_suite

from .execution_lease import ExecutionLease, CleanupRequired
from .execution_control import ExecutionControl, Cancelled
from .process_tracker import ProcessTracker

from .conversation_service import SCENES, SCENE_NUMBERS, case_name, observation_args, scene_args, scene_selector
from .native_render import runtime_identity, validate_capture
from .materials import MATERIAL_EVIDENCE, MATERIAL_INPUT, MATERIAL_RUNTIME, native_config, verify_material

def exclusive(job_id=None):
    return ExecutionLease(ROOT / "runs/.execution.lock", job_id)

def run_one(plan, folder, timeout_sec, control=None, lease=None, context=None):
    control = control or ExecutionControl()
    control.check()
    validate_plan(plan, expanded=True)
    if plan.get("diagnostic_contact_trace") and (plan["frames"] > 100 or not 1 <= timeout_sec <= 600):
        raise ValueError("Contact tracing exceeds the diagnostic budget")
    folder.mkdir(parents=True, exist_ok=False)
    write(folder / "plan.json", plan)
    for name in ("parameterSetting.txt", "abd_system_config.json"):
        source = ROOT / "simulator/Assets/scene" / name
        if source.is_file():
            target = folder / "inputs" / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
    shutil.copy2(ROOT / "configs/c0.json", folder / "profile.json")
    execution = {"implementation_commit": IMPLEMENTATION, "started_at": now(),
                 "returncode": None, "timeout": False, "identity_unchanged": False,
                 "fresh_output": False}
    data, metrics = None, {}
    preflight, parsing = None, False
    result = {"status": "FAIL", "errors": [], "warnings": [], "quality_status": "NOT_ASSESSED",
              "checks_schema": SCHEMA, "checks": skipped_checks("执行未完成，尚未取得本项证据")}
    try:
        control.phase("PREFLIGHT")
        control.check()
        preflight = inspect()
        write(folder / "preflight.json", preflight)
        if not preflight["ready"]:
            result.update(status="SKIPPED", errors=preflight["errors"],
                          checks=skipped_checks("前置检查未通过，未启动仿真"))
        else:
            control.phase("PREPARING")
            control.check()
            control.verify()
            profile = read(ROOT / "configs/c0.json")
            before = identity()
            write(folder / "identity_before.json", before)
            raw = folder / "raw"
            execution["fresh_output"] = not raw.exists()
            executable = ROOT / "build/bin/gipc.exe"
            args = scene_args(profile["args"], plan["scene"])
            native = None
            variant = SCENES[plan["scene"]].get("runtime_variant")
            material = plan.get("material")
            if material is not None:
                variant = MATERIAL_RUNTIME
                material_path = folder / MATERIAL_INPUT
                write(material_path, native_config(plan["scene"], material))
            if variant or plan.get("native_render"):
                native = runtime_identity(ROOT, variant=variant)
                executable = Path(native["executable"])
            args = observation_args(args, plan)
            command = [str(executable), *args,
                       "--benchmark-frames", str(plan["frames"]), "--benchmark-output-dir", str(raw)]
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("GIPC_", "CIPC_")) and k.upper() != "DEEPSEEK_API_KEY"}
            env.update(CUDA_VISIBLE_DEVICES=profile["gpu"], CUDA_DEVICE_ORDER="PCI_BUS_ID")
            selector = scene_selector(plan["scene"])
            if selector:
                env.update(CIPC_SCENE=selector, CIPC_META_OUT=str(raw / "scene_setup.json"))
            if material is not None:
                env["CIPC_MATERIAL_CONFIG"] = str(material_path.resolve())
                execution["material_input"] = MATERIAL_INPUT
            execution["build_sha256"] = native["sha256"] if native else before.get("runtime", {}).get("gipc.exe")
            env["PATH"] = str(ROOT / "build/bin") + os.pathsep + env.get("PATH", "")
            execution.update(command=command, cwd=str(ROOT / "simulator"),
                             gpu=preflight["gpu"],
                             environment={"CUDA_VISIBLE_DEVICES": profile["gpu"],
                                          "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                                          "CIPC_SCENE": selector or None})
            start = time.perf_counter()
            with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
                control.check()
                process = subprocess.Popen(command, cwd=ROOT / "simulator", env=env,
                                           stdout=out, stderr=err, shell=False,
                                           creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | 0x4) if os.name == "nt" else 0,
                                           start_new_session=os.name != "nt")
                if lease is not None:
                    lease.safe = False
                tracker = None
                try:
                    tracker = ProcessTracker(process)
                    execution["process_tracking"] = "windows-job-object-suspended-v1" if os.name == "nt" else "posix-process-group-v1"
                    if lease is not None:
                        lease.attach(process.pid)
                    execution["pid"] = process.pid
                    control.process(process.pid)
                    control.phase("RUNNING")
                    try:
                        execution["returncode"] = control.wait(process, timeout_sec)
                    except subprocess.TimeoutExpired:
                        execution["timeout"] = True
                    except Cancelled:
                        execution["cancelled"] = True
                finally:
                    try:
                        if tracker is None:
                            process.kill()
                            process.wait(timeout=10)
                            raise CleanupRequired("进程树追踪未建立，无法证明后代进程清理")
                        if execution["returncode"] is None:
                            tracker.terminate()
                        tracker.finish()
                        execution["process_tree_empty"] = True
                        execution["cleanup_confirmed_at"] = now()
                        if execution["returncode"] is None:
                            execution["returncode"] = process.wait(timeout=10)
                        if lease is not None:
                            lease.cleared()
                        control.process(None)
                    except BaseException as cleanup:
                        execution["cleanup_required"] = True
                        if lease is not None:
                            lease.uncertain(cleanup)
                        raise CleanupRequired(str(cleanup)) from cleanup
                    finally:
                        if tracker is not None:
                            tracker.close()
            execution["wall_seconds"] = time.perf_counter() - start
            after = identity()
            write(folder / "identity_after.json", after)
            execution["identity_unchanged"] = before == after and (
                native is None or runtime_identity(ROOT, variant=variant) == native)
            execution["stdout"] = (folder / "stdout.log").read_text(encoding="utf-8", errors="replace")
            if execution.get("cancelled"):
                raise Cancelled("任务取消，进程树已确认结束")
            if execution["timeout"]:
                raise RuntimeError("TIMEOUT: process tree terminated; partial evidence retained")
            if execution["returncode"] != 0:
                raise RuntimeError("PROCESS_EXIT: " + str(execution["returncode"]))
            control.phase("PARSING")
            parsing = True
            data = parse_artifacts(raw)
            parsing = False
            metrics = data["metrics"]
            control.phase("VALIDATING")
            result = validate_run(plan, execution, data)
            if material is not None:
                try:
                    result["material_evidence"] = verify_material(folder, plan["scene"], material)
                    material_error = None
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    material_error = str(exc)
                    result["status"] = "FAIL"
                    result["errors"].append("材料校验失败：" + material_error)
                result["checks"].append({
                    "id": "M01", "name": "材料初始化与设备质量", "required": True,
                    "status": "PASS" if material_error is None else "FAIL",
                    "expected": "计划、原生材料、FEM 系数与设备质量记录一致",
                    "actual": result.get("material_evidence"),
                    "evidence": [MATERIAL_INPUT, MATERIAL_EVIDENCE, "raw/masses.f64"],
                    "message": material_error or "检查通过",
                })
            if plan.get("native_render"):
                try:
                    if not execution["identity_unchanged"]:
                        raise ValueError("原生捕获构建在运行期间变化")
                    result["visualization"] = validate_capture(
                        raw, SCENE_NUMBERS[plan["scene"]], plan["frames"], native, selector)
                except (OSError, ValueError, KeyError) as exc:
                    result["visualization"] = {"kind": "no_visualization", "reason": str(exc)}
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
        execution["execution_error"] = error
        result = validate_run(plan, execution, data, parse_error=error if parsing else None)
        if error not in result["errors"]:
            result["errors"].append(error)
        if not parsing and not execution.get("timeout") and execution.get("returncode") in (None, 0):
            result["checks"].append({
                "id": "E00", "name": "工具链执行接口", "required": True, "status": "FAIL",
                "expected": "执行与证据归档接口正常完成", "actual": {"execution_error": error},
                "evidence": ["run_meta.json"], "message": "工具链执行异常，保留已有证据",
            })
        result["failure_kind"] = ("CLEANUP_REQUIRED" if execution.get("cleanup_required") else
                                  "CANCELLED" if isinstance(exc, Cancelled) else
                                  "IDENTITY_CHANGED" if getattr(exc,"code",None) in ("IDENTITY_INVALID","PLAN_STALE") else
                                  "TIMEOUT" if execution.get("timeout") else
                                  "PROCESS_EXIT" if execution.get("returncode") not in (None, 0) else
                                  "EVIDENCE_OR_EXECUTION_ERROR")
        if isinstance(exc, Cancelled) and execution.get("pid") is None:
            result.update(status="SKIPPED",errors=[],failure_kind="NOT_STARTED",
                          checks=skipped_checks("进程启动前取消"))
    if preflight is not None:
        result["checks"].insert(0, preflight_check(preflight["ready"], preflight.get("errors", [])))
    control.phase("REPORTING")
    if context:
        result.update(context)
    if execution.get("command") and execution.get("build_sha256"):
        result["execution_identity"] = {
            "executable": execution["command"][0], "build_sha256": execution["build_sha256"],
            "requested_frames": plan["frames"],
            "actual_scene_no": data["metadata"].get("scene_no") if data else None,
            "actual_cloth_scene": data["metadata"].get("cloth_scene") if data else None,
        }
    execution["ended_at"] = now()
    execution.pop("stdout", None)
    write(folder / "run_meta.json", execution)
    return report_run(folder, plan, execution, result, metrics)

def execute(plan, timeout_sec=3600, progress=None):
    plan = validate_plan(plan)
    if not isinstance(timeout_sec, (int, float)) or not 0 < timeout_sec <= 86400:
        raise ValueError("Timeout must be in (0, 86400] seconds")
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    with exclusive() as lease:
        if plan["action"] == "run":
            return run_one(plan, ROOT / "runs" / run_id, timeout_sec, lease=lease)
        suite = read(ROOT / "configs/default_suite.json")
        folder = ROOT / "runs" / run_id
        folder.mkdir()
        write(folder / "plan.json", plan)
        reports = []
        for task in suite["tasks"]:
            if progress:
                progress(task["id"], "RUNNING")
            report = run_one({"action": "run", "scene": "default", "frames": task["frames"]},
                             folder / task["id"], timeout_sec, lease=lease)
            report.update(task_id=task["id"], report_path=task["id"] + "/report.md")
            reports.append(report)
            report_suite(folder, reports)
            if progress:
                progress(task["id"], report["status"])
            if report.get("failure_kind") in ("CLEANUP_REQUIRED","IDENTITY_CHANGED"):
                break
        return report_suite(folder, reports)


def execute_request(request, folder, control, lease, context):
    """Already-leased subset execution; never nests the legacy execute lock."""
    from .conversation_service import normalize_request
    request = normalize_request(request)
    folder.mkdir(parents=True, exist_ok=False)
    write(folder / "task_request.json", request)
    reports = []
    stop_reason = None
    for index, frames in enumerate(request["task_frames"]):
        case_id = case_name(frames)
        control.case(case_id, index, len(request["task_frames"]))
        plan = {"action":"run","scene":request["scene"],"frames":frames}
        if request.get("native_render"):
            plan["native_render"] = True
        if request.get("diagnostic_contact_trace"):
            plan["diagnostic_contact_trace"] = True
        if "material" in request:
            plan["material"] = request["material"]
        child = folder / "cases" / case_id
        if stop_reason or control.cancelled():
            child.mkdir(parents=True, exist_ok=False)
            result = {"status":"SKIPPED","errors":[],"warnings":[],
                      "quality_status":"NOT_ASSESSED","checks_schema":SCHEMA,
                      "checks":skipped_checks(stop_reason or "后续用例已取消"),
                      "failure_kind":"NOT_STARTED",**context}
            report = report_run(child,plan,{},result,{})
            control.phase("SKIPPED")
        else:
            try:
                report = run_one(plan,child,request["timeout_sec"],control,lease,context)
            except Cancelled:
                child.mkdir(parents=True,exist_ok=True)
                report = report_run(child,plan,{},{
                    "status":"SKIPPED","errors":[],"warnings":[],"quality_status":"NOT_ASSESSED",
                    "checks_schema":SCHEMA,"checks":skipped_checks("启动前取消"),
                    "failure_kind":"NOT_STARTED",**context},{})
            control.phase("CANCELLED" if report.get("failure_kind")=="CANCELLED" else "COMPLETED")
            failure = report.get("failure_kind")
            if failure in ("CLEANUP_REQUIRED","IDENTITY_CHANGED"):
                stop_reason = failure
            elif report["status"]=="SKIPPED":
                stop_reason = "全局前置检查不满足"
            elif control.cancelled():
                stop_reason = "任务取消"
        report.update(task_id=case_id,report_path="cases/"+case_id+"/report.md")
        reports.append(report)
        report_suite(folder,reports,expected_count=len(request["task_frames"]),
                     suite_id="default-mvp" if request["mode"]=="standard" else "custom", context=context)
    return report_suite(folder,reports,expected_count=len(request["task_frames"]),
                        suite_id="default-mvp" if request["mode"]=="standard" else "custom", context=context)
