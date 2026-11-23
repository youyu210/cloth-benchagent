"""Read-only evidence tools for StiffGIPC cloth experiment objectives."""
import csv
import hashlib
import io
import json
import math
from statistics import median

from .conversation_service import SCENE_NUMBERS, scene_selector
from .native_render import frame_name
from .planning_store import PlanningError, valid_id

METRICS = {
    "step_ms": "同步物理步耗时（ms）",
    "newton_iterations": "Newton 迭代（次/步）",
    "pcg_iterations": "PCG 累计迭代（次/步）",
    "contact_list_max": "装配前近距离约束列表峰值（项/步）",
}
CONTACT_SCOPE = (
    "每物理步取 Newton 装配前 contacts_before.contact_count 的最大值。"
    "计数对应 h_cpNum[0] 及 _collisonPairs，地面另计，包含该场景全部非地面近距离约束。"
    "没有对象对分类，无法识别球布独有约束、首次接触或穿透。"
)
TIMING_SCOPE = "step_ms 为同步物理步墙钟时间（毫秒），含图准备，排除初始化、画面采集和导出。"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024


def finite(value, *, integer=False, positive=False):
    try:
        if isinstance(value, bool):
            return None
        x = float(value)
        if not math.isfinite(x) or x < 0 or (positive and x == 0) or (integer and not x.is_integer()):
            return None
        return int(x) if integer else x
    except (TypeError, ValueError, OverflowError):
        return None


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * p
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def hot_statistics(rows, top):
    if type(top) is not int or not 1 <= top <= 20:
        raise PlanningError("OBJECTIVE_PARAMETER", "高耗时帧数量须为 1–20。")
    timed = [r for r in rows if r["step_ms"] is not None]
    ranked = sorted(timed, key=lambda r: (-r["step_ms"], r["frame"]))
    selected = ranked[:min(top, max(0, len(ranked) - 2))]
    ids = {r["frame"] for r in selected}
    other = [r for r in timed if r["frame"] not in ids]
    groups = {}
    for metric in METRICS:
        groups[metric] = {}
        for name, group in (("high", selected), ("other", other)):
            values = [row[metric] for row in group if row[metric] is not None]
            groups[metric][name] = {"n": len(values), "expected": len(group),
                                    "median": median(values) if values else None}
    times = [r["step_ms"] for r in timed]
    return {
        "ranking": "按 step_ms 降序取前 K，耗时相同优先取较早帧，至少保留两帧作为对照。"
                   "包含首个物理步，初态不入样。",
        "requested_top": top, "threshold_ms": selected[-1]["step_ms"] if selected else None,
        "physical_steps": len(rows), "valid_steps": len(times),
        "missing_ratio": 1 - len(times) / len(rows) if rows else None,
        "median_ms": median(times) if times else None, "p90_ms": percentile(times, .90),
        "max_ms": max(times, default=None), "hot_frames": selected, "groups": groups,
    }


class ClothEvidence:
    def __init__(self, archive, report_id, case_id=None):
        self.archive = archive
        valid_id(report_id)
        if case_id is not None:
            valid_id(case_id)
        record = archive.get_report(report_id)
        if record["kind"] == "suite":
            record = next((c for c in record["cases"] if c.get("case_id") == case_id), None)
        elif case_id is not None:
            record = None
        if not record or not record.get("raw_report"):
            raise PlanningError("EVIDENCE_MISSING", "请选择具有原生报告的归档用例。")
        self.record = record
        self.folder = archive._path(record["report_id"])
        self.references = {}
        self.issues = []
        self.report = self.object("report.json")
        self.metadata = self.object("raw/metadata.json")
        if self.report.get("producer_kind") != "simulation":
            raise PlanningError("EVIDENCE_PRODUCER", "目标分析只接受标记为真实仿真的归档。")
        self.scene = (self.report.get("plan") or {}).get("scene")
        self.target = (self.report.get("plan") or {}).get("frames")
        if self.scene not in SCENE_NUMBERS or type(self.target) is not int or not 1 <= self.target <= 1000:
            raise PlanningError("EVIDENCE_IDENTITY", "归档缺少有效场景或请求帧数。")
        self.binding_ok = (
            self.metadata.get("scene_no") == SCENE_NUMBERS[self.scene]
            and self.metadata.get("cloth_scene", "") == scene_selector(self.scene)
            and self.metadata.get("frames") == self.target
            and self.metadata.get("initial_physical_frame") == 0
            and isinstance(self.metadata.get("timing"), str)
            and self.metadata["timing"].startswith("synchronized wall clock")
        )
        self.identity = self.object("identity_before.json")
        self.run_meta = self.object("run_meta.json")
        self.setup = self.object("raw/scene_setup.json") if scene_selector(self.scene) else {}
        self.source = {"report_id": report_id, "case_id": case_id, "case_ref": record["report_id"],
                       "scene": self.scene, "requested_frames": self.target}

    def payload(self, name, *, optional=False):
        try:
            path = self.archive._path(name, self.folder)
            with path.open("rb") as stream:
                data = stream.read(MAX_EVIDENCE_BYTES + 1)
            if len(data) > MAX_EVIDENCE_BYTES:
                raise ValueError("Evidence exceeds read limit")
            self.references[path.relative_to(self.archive.root).as_posix()] = hashlib.sha256(data).hexdigest()
            return data
        except (OSError, ValueError):
            if not optional:
                self.issues.append(name + " 不可用")
            return None

    def object(self, name, *, optional=False):
        data = self.payload(name, optional=optional)
        if data is not None:
            try:
                value = json.loads(data.decode("utf-8-sig"))
                if isinstance(value, dict):
                    return value
            except (UnicodeError, ValueError):
                pass
            self.issues.append(name + " 不是有效对象")
        return {}

    def read_steps(self):
        keys = ("step_ms", "newton_iterations", "pcg_iterations")
        data = self.payload("raw/frames.csv")
        indexed = {}
        duplicates = set()
        if data and self.binding_ok:
            try:
                reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
                fields = reader.fieldnames or []
                if len(fields) != len(set(fields)) or "frame" not in fields:
                    raise ValueError("Invalid columns")
                for row in reader:
                    frame = finite(row.get("frame"), integer=True, positive=True)
                    if frame is None or frame > self.target:
                        continue
                    if frame in indexed:
                        duplicates.add(frame)
                    indexed[frame] = {
                        key: finite(row.get(key), integer=key != "step_ms", positive=key == "step_ms")
                        for key in keys
                    }
            except (UnicodeError, ValueError, csv.Error):
                self.issues.append("raw/frames.csv 读取失败")
                indexed.clear()
        if duplicates:
            self.issues.append("重复帧的指标未纳入")
        return [
            {"frame": frame, **{key: None if frame in duplicates else indexed.get(frame, {}).get(key)
                                for key in keys}, "contact_list_max": None}
            for frame in range(1, self.target + 1)
        ]

    def read_contacts(self):
        if not self.binding_ok or self.metadata.get("trace_newton") is not True:
            return {}
        data = self.payload("raw/newton_trace.jsonl")
        counts = {}
        seen = set()
        for line in data.splitlines() if data else ():
            try:
                obj = json.loads(line)
                frame, samples = obj["frame"], obj["samples"]
                if type(frame) is not int or not 1 <= frame <= self.target:
                    continue
                if frame in seen:
                    counts.pop(frame, None)
                    continue
                seen.add(frame)
                if obj["physical_frame"] != frame or not isinstance(samples, list):
                    continue
                values = [s["contacts_before"]["contact_count"] for s in samples]
                if values and all(type(v) is int and v >= 0 for v in values):
                    counts[frame] = max(values)
            except (ValueError, KeyError, TypeError):
                continue
        return counts

    def read_contact_summary(self):
        summary = self.object("raw/collision_summary.json", optional=True)
        return {key: finite(summary.get(key), integer=True)
                for key in ("collision_counter_total", "collision_pair_max")}

    def read_images(self):
        visual = self.report.get("visualization") or {}
        identity = self.report.get("execution_identity") or {}
        if (not self.binding_ok or visual.get("kind") != "native_render_frame"
                or visual.get("scene_no") != SCENE_NUMBERS[self.scene]
                or visual.get("cloth_scene", "") != scene_selector(self.scene)
                or not visual.get("build_sha256")
                or visual["build_sha256"] != identity.get("build_sha256")
                or visual["build_sha256"] != self.run_meta.get("build_sha256")):
            return []
        capture = self.object("raw/native_render.json")
        if capture != visual:
            self.issues.append("原生画面索引与归档报告不一致")
            return []
        frames = visual.get("frames", [])
        if not isinstance(frames, list) or any(type(n) is not int for n in frames):
            return []
        valid = []
        for frame in sorted(set(frames)):
            if not 0 <= frame <= self.target:
                continue
            name = frame_name(frame)
            data = self.payload("raw/" + name)
            expected = (visual.get("files") or {}).get(name)
            if data is not None and expected and hashlib.sha256(data).hexdigest() == expected:
                valid.append({"frame": frame, "file": self.record["report_id"] + "/raw/" + name,
                              "sha256": expected})
        return valid


def associate_images(hot_frames, images):
    result = []
    for row in hot_frames:
        frame = row["frame"]
        if images:
            image = min(images, key=lambda entry: (abs(entry["frame"] - frame), entry["frame"]))
            result.append({"target_frame": frame, "image_frame": image["frame"],
                           "offset": image["frame"] - frame, "file": image["file"], "sha256": image["sha256"]})
    return result
