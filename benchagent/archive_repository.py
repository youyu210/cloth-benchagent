"""Read-only archive access. Does not run simulations or reclassify verdicts."""
import copy
import csv
import json
import math
from pathlib import Path, PureWindowsPath
from .common import ROOT

def _finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    if isinstance(value, dict):
        for item in value.values(): _finite_tree(item)
    elif isinstance(value, list):
        for item in value: _finite_tree(item)

class ArchiveRepository:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else ROOT / "runs").resolve()

    def _path(self, value, base=None):
        if not isinstance(value, str) or not value or "\\" in value or ":" in value:
            raise ValueError("Invalid archive-relative path")
        if PureWindowsPath(value).is_absolute() or value.startswith("/"):
            raise ValueError("Absolute paths are not allowed")
        if any(part in ("", ".", "..") for part in value.split("/")):
            raise ValueError("Invalid archive-relative path")
        base = (base or self.root).resolve()
        target = (base / value).resolve()
        if not target.is_relative_to(base) or not target.is_relative_to(self.root):
            raise ValueError("Path escapes archive boundary")
        return target

    def _issue(self, issues, code, path, message):
        issues.append({"code": code, "path": str(path), "message": message})

    def _object(self, path, issues):
        try:
            text = path.read_text(encoding="utf-8-sig")
            obj = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            _finite_tree(obj)
            if not isinstance(obj, dict): raise ValueError("Expected JSON object")
            return obj
        except (OSError, ValueError, RecursionError) as exc:
            self._issue(issues, "MISSING_FILE" if not path.exists() else "INVALID_JSON",
                        path.relative_to(self.root).as_posix(), "归档文件不可读取：" + type(exc).__name__)
            return None

    def _frames(self, folder, issues):
        path = folder / "raw/frames.csv"
        try:
            resolved = self._path("raw/frames.csv", folder)
        except ValueError:
            self._issue(issues, "UNSAFE_PATH", "raw/frames.csv", "逐帧文件越界")
            return []
        if not resolved.is_file(): return []
        try:
            with resolved.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                if "frame" not in (reader.fieldnames or []):
                    raise ValueError("Missing frame column")
            result = []
            for i, row in enumerate(rows, 1):
                item = {"frame": i}
                if self._numeric(row.get("frame"), True) != i: raise ValueError("Frame sequence")
                for source, target, integer in (
                    ("step_ms", "solver_time_ms", False),
                    ("newton_iterations", "newton_iterations", True),
                    ("pcg_iterations", "pcg_iterations", True),
                    ("collision_pair_count", "collision_pair_count", True)):
                    value = row.get(source)
                    item[target] = None if value is None or value == "" else self._numeric(value, integer)
                result.append(item)
            return result
        except (OSError, ValueError, TypeError) as exc:
            self._issue(issues, "INVALID_FRAMES", path.relative_to(self.root).as_posix(),
                        "逐帧记录无效：" + type(exc).__name__)
            return []

    @staticmethod
    def _numeric(value, integer=False):
        if isinstance(value, bool): raise ValueError("Boolean metric")
        number = float(value)
        if not math.isfinite(number) or number < 0 or (integer and number != int(number)):
            raise ValueError("Invalid metric")
        return int(number) if integer else number

    def get_report(self, report_id):
        folder = self._path(report_id)
        issues = []
        # Explicit JSON entry points only; never choose an arbitrary file from user input.
        summary = folder / "benchmark_summary.json"
        report = folder / "report.json"
        kind = "suite" if summary.exists() or (folder / "cases").is_dir() else "single" if report.exists() else "incomplete"
        selected = summary if kind == "suite" else report
        try:
            selected = self._path(selected.name, folder)
        except ValueError:
            return self._record(report_id, kind, None, [], [
                {"code":"UNSAFE_PATH","path":report_id,"message":"报告文件越界"}], [])
        raw = self._object(selected, issues) if kind != "incomplete" else None
        if kind == "incomplete":
            self._issue(issues, "INCOMPLETE_ARCHIVE", report_id, "目录没有可读取的报告入口")
        cases = []
        if kind == "suite" and raw is not None:
            tasks = raw.get("tasks")
            if not isinstance(tasks, list):
                self._issue(issues, "INVALID_TASKS", report_id, "套件任务列表无效")
                tasks = []
            seen = set()
            for index, task in enumerate(tasks):
                if not isinstance(task, dict):
                    self._issue(issues, "INVALID_TASK", report_id, "子任务记录无效")
                    continue
                key = task.get("task_id")
                if not isinstance(key, str) or not key or "/" in key or "\\" in key or key in seen:
                    self._issue(issues, "INVALID_CASE_ID", report_id, "子任务标识缺失、重复或无效")
                    continue
                seen.add(key)
                reference = task.get("report_path")
                try:
                    if reference:
                        child_file = self._path(reference, folder)
                        if child_file.name not in ("report.md", "report.zh-CN.md", "report.json"):
                            raise ValueError("Unsupported report reference")
                        child_file = child_file.parent / "report.json"
                    else:
                        modern = self._path("cases/" + key + "/report.json", folder)
                        legacy = self._path(key + "/report.json", folder)
                        child_file = modern if modern.exists() else legacy
                    if child_file.parent == folder or (child_file.parent / "benchmark_summary.json").exists():
                        raise ValueError("Recursive or nested suite reference")
                    child_id = child_file.parent.relative_to(self.root).as_posix()
                    child = self.get_report(child_id)
                    if child["kind"] == "suite": raise ValueError("Nested suite not allowed")
                except (ValueError, OSError):
                    self._issue(issues, "UNSAFE_CASE_PATH", report_id, "子任务路径无效或越界")
                    continue
                # A missing child may use the embedded summary as an explicitly partial view.
                if child["raw_report"] is None:
                    child["raw_report"] = copy.deepcopy(task)
                    child["recorded_result_status"] = task.get("status") if isinstance(task.get("status"),str) else None
                    child["archive_read_status"] = "PARTIAL"
                    child["report_source"] = report_id + "/benchmark_summary.json#tasks/" + str(index)
                    child["issues"].append({"code":"SUMMARY_FALLBACK","path":report_id,
                                            "message":"子报告缺失，仅展示套件摘要中已记录的数据"})
                elif task.get("status") is not None and task.get("status") != child["recorded_result_status"]:
                    child["issues"].append({"code":"SUMMARY_CONFLICT","path":report_id,
                                            "message":"套件摘要与子报告状态不同，保留各自原记录"})
                    child["archive_read_status"] = "PARTIAL"
                child["case_id"] = key
                cases.append(child)
            if any(c["archive_read_status"] != "OK" for c in cases):
                self._issue(issues, "CASE_INCOMPLETE", report_id, "存在缺失、损坏或冲突的子报告")
        if kind == "suite" and raw is None:
            try:
                case_root = self._path("cases", folder)
            except ValueError:
                case_root = None
                self._issue(issues, "UNSAFE_CASE_PATH", report_id, "子任务容器目录越界")
            if case_root is not None and case_root.is_dir():
                for child_folder in sorted(case_root.iterdir()):
                    if not child_folder.is_dir(): continue
                    try:
                        safe = self._path("cases/" + child_folder.name, folder)
                        if (safe / "benchmark_summary.json").exists(): continue
                        child = self.get_report(safe.relative_to(self.root).as_posix())
                        child["case_id"] = child_folder.name
                        cases.append(child)
                    except (ValueError, OSError):
                        self._issue(issues, "UNSAFE_CASE_PATH", report_id, "子任务目录无效")
        if raw is not None and kind == "single":
            evidence = raw.get("evidence_files")
            if evidence is not None and not isinstance(evidence, list):
                self._issue(issues, "INVALID_EVIDENCE_INDEX", report_id, "证据索引无效")
            for relative in evidence if isinstance(evidence, list) else []:
                try:
                    evidence_path = self._path(relative, folder)
                    if not evidence_path.is_file():
                        self._issue(issues, "MISSING_EVIDENCE", str(relative), "索引中的证据不存在")
                except ValueError:
                    self._issue(issues, "UNSAFE_EVIDENCE", str(relative), "证据引用越界或无效")
        frames = self._frames(folder, issues) if kind == "single" else []
        return self._record(report_id, kind, raw, cases, issues, frames)

    @staticmethod
    def _record(report_id, kind, raw, cases, issues, frames):
        recorded = raw.get("status") if raw is not None else None
        if recorded is not None and not isinstance(recorded,str):
            issues = [*issues, {"code":"INVALID_RECORDED_STATUS","path":report_id,
                                "message":"原记录状态字段类型无效，保留原件且不生成结论"}]
            recorded = None
        return {"report_id":report_id,"kind":kind,
                "recorded_result_status":recorded,
                "archive_read_status":"ERROR" if raw is None and kind != "incomplete" else
                                      "INCOMPLETE" if kind == "incomplete" else "PARTIAL" if issues else "OK",
                "raw_report":copy.deepcopy(raw),"cases":cases,"issues":issues,"frames":frames}

    def list_records(self, *, recorded_status=None, offset=0, limit=100):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid pagination")
        records = []
        if self.root.exists():
            for folder in sorted(self.root.iterdir(), reverse=True):
                if not folder.is_dir(): continue
                try:
                    self._path(folder.name)
                    record = self.get_report(folder.name)
                except (ValueError, OSError):
                    continue
                if recorded_status is None or record["recorded_result_status"] == recorded_status:
                    records.append(record)
        return {"records":records[offset:offset+limit],"total":len(records),"offset":offset,"limit":limit}

    def get_view(self, report_id, case_id=None):
        from .view_models import build_report_view
        record = self.get_report(report_id)
        if case_id is not None:
            matches = [c for c in record["cases"] if c.get("case_id") == case_id]
            if len(matches) != 1: raise ValueError("Case does not belong to report")
            return build_report_view(matches[0])
        return build_report_view(record)
