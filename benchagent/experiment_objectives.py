"""Bounded experiment planning, evidence feedback and confirmed GPU supplements."""
import re

from .cloth_evidence import (
    CONTACT_SCOPE, TIMING_SCOPE, ClothEvidence, METRICS, associate_images, hot_statistics,
)
from .common import now, sha
from .conversation_service import (
    SCENES, SCENE_NUMBERS, case_name, explicitly_named_scene, numbered_scene, task_request,
)
from .job_manager import TERMINAL
from .model_gateway.contracts import ActionRequest
from .plan_service import plan_digest
from .planning_store import PlanningError, new_id, require

ACTIONS = {
    "query_archive": "核对任务、场景、初态及物理步计时口径。",
    "read_steps": "读取物理步耗时及 Newton、PCG 迭代。",
    "read_contact_trace": "寻找可按物理步对齐的装配前约束列表计数。",
    "read_contact_summary": "保留全程碰撞汇总并确认其时间分辨率。",
    "link_native_frames": "验证归档画面哈希并关联高耗时物理步。",
    "propose_diagnostic": "已确认存在能增加所需证据的原生采集选项。",
    "finish": "依据覆盖率、执行结果和当前能力结束本轮分析。",
    "assess_evidence": "跳过全程汇总，直接按已有逐帧覆盖判断证据缺口。",
}
UNAVAILABLE = {
    "stage_time": ("分阶段计时", "阶段耗时", "ccd", "线搜索", "瓶颈"),
    "convergence": ("残差曲线", "收敛质量"),
    "contact_event": ("接触次数", "首次接触", "球布接触数量", "球体与布料接触对"),
    "penetration": ("穿透",),
}
UNAVAILABLE_LABELS = {
    "stage_time": "受控配置未开放分阶段剖析，无法定位 CCD、线搜索等开销来源。",
    "convergence": "当前目标工具缺少完整残差序列，无法评价收敛质量。",
    "contact_event": "约束列表没有对象对和事件标签，无法识别球布接触事件或发生时刻。",
    "penetration": "缺少按物理步验证的几何间隙证据，无法判定穿透。",
}
LIMITS = {"tool_steps": 12, "model_calls": 8, "gpu_runs": 1, "frames": 100, "timeout_sec": 600}


def parse_goal(text, *, previous=None, selected=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise PlanningError("OBJECTIVE_TEXT", "请输入不超过 2000 字的布料实验目标。")
    compact = re.sub(r"\s+", "", text.lower()).strip("。！!.")
    change = re.fullmatch(r"(?:改成|改为|调整为)(?:耗时)?前([0-9]{1,3})帧", compact)
    if change:
        if not previous:
            raise PlanningError("OBJECTIVE_CONTEXT", "请先保存一份目标分析，再修改高耗时帧数量。")
        top = int(change[1])
        if not 1 <= top <= 20:
            raise PlanningError("OBJECTIVE_PARAMETER", "高耗时帧数量须为 1–20。")
        return {**previous, "text": text.strip(), "top": top}
    if not any(word in compact for word in ("耗时", "慢帧", "慢的", "迭代", "瓶颈", "计时")):
        raise PlanningError("OBJECTIVE_UNSUPPORTED", "请描述布料仿真的高耗时帧或迭代分析目标。")
    number = numbered_scene(text)
    named = {s for s in SCENE_NUMBERS if explicitly_named_scene(text, s)}
    if number:
        named.add(number)
    if len(named) > 1 or (selected and named and selected not in named):
        raise PlanningError("OBJECTIVE_SCENE", "文字中的场景与所选归档不一致。")
    scene = next(iter(named), selected)
    if scene not in SCENE_NUMBERS:
        raise PlanningError("OBJECTIVE_SCENE", "请在目标中指定目录场景，或明确选择归档。")
    rank = re.search(r"(?:耗时)?前([0-9]{1,3})帧", compact)
    top = int(rank[1]) if rank else 5
    if not 1 <= top <= 20:
        raise PlanningError("OBJECTIVE_PARAMETER", "高耗时帧数量须为 1–20。")
    limited = "只看耗时和迭代" in compact
    return {
        "kind": "cloth_hot_steps", "text": text.strip(), "scene": scene, "top": top,
        "need_contact": not limited and "不要接触计数" not in compact,
        "need_images": not limited and all(t not in compact for t in ("不要画面", "不需要画面")),
        "unavailable_requests": [key for key, terms in UNAVAILABLE.items() if any(t in compact for t in terms)],
    }


class ExperimentObjectives:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.archive = service.archive

    def cases(self, scene=None):
        result = []
        for record in self.service.records():
            for case in record["cases"] if record["kind"] == "suite" else (record,):
                report = case.get("raw_report") or {}
                plan = report.get("plan") or {}
                if (report.get("producer_kind") != "simulation"
                        or plan.get("scene") not in SCENE_NUMBERS
                        or (scene and plan["scene"] != scene)):
                    continue
                result.append({
                    "report_id": record["report_id"], "case_id": case.get("case_id"),
                    "scene": plan["scene"], "frames": plan.get("frames"), "status": report.get("status"),
                    "images": (report.get("visualization") or {}).get("kind") == "native_render_frame",
                    "trace": plan.get("diagnostic_contact_trace") is True,
                    "native_ref": case["report_id"],
                })
        return result

    def get(self, objective_id):
        return self.store.get("objectives", objective_id)

    def list(self):
        return sorted(self.store.read()["objectives"].values(),
                      key=lambda record: record["created_at"], reverse=True)

    def create(self, text, *, report_id=None, case_id=None, previous_id=None, gpu_budget=1, gateway=None):
        if type(gpu_budget) is not int or not 0 <= gpu_budget <= LIMITS["gpu_runs"]:
            raise PlanningError("OBJECTIVE_BUDGET", "诊断补测预算为 0 或 1 个任务。")
        previous = self.get(previous_id) if previous_id else None
        if case_id is not None and report_id is None:
            raise PlanningError("EVIDENCE_MISSING", "选择用例时需要指定任务。")
        if report_id is None and previous:
            report_id, case_id = (previous["plan"]["source"][key] for key in ("report_id", "case_id"))
        evidence = ClothEvidence(self.archive, report_id, case_id) if report_id else None
        goal = parse_goal(text, previous=previous["plan"]["goal"] if previous else None,
                          selected=evidence.scene if evidence else None)
        if not evidence:
            candidates = self.cases(goal["scene"])
            if not candidates:
                raise PlanningError("EVIDENCE_MISSING", "未找到该场景的真实归档，请先用现有执行入口生成记录。")
            best = max(candidates, key=lambda c: (c["status"] == "PASS",
                       int(c["trace"] and goal["need_contact"]) + int(c["images"] and goal["need_images"])))
            evidence = ClothEvidence(self.archive, best["report_id"], best["case_id"])
        if goal["scene"] != evidence.scene:
            raise PlanningError("OBJECTIVE_SCENE", "更新后的目标与归档场景不一致。")
        record = {
            "schema_version": "experiment-objective-v1", "objective_id": new_id("objective"),
            "revision_of": previous_id, "created_at": now(),
            "budget": {**LIMITS, "gpu_runs": gpu_budget}, "actions": [], "model_requests": [],
            "model_used": False, "model_requested": gateway is not None,
            "gpu_submissions": 0, "child_job_id": None, "diagnostic": None,
        }
        result, proposed = self._review(record, goal, evidence, gateway, allow_proposal=True)
        plan = {
            "goal": goal, "source": evidence.source, "input_sha256": evidence.references,
            "budget": record["budget"], "allowed_actions": [*ACTIONS, "submit_diagnostic"] if proposed else list(ACTIONS),
            "required_metrics": ["step_ms", "newton_iterations", "pcg_iterations"]
                                + (["contact_list_max"] if goal["need_contact"] else []),
            "questions": ["哪些物理步耗时较高？", "高耗时帧的迭代和约束计数与其余帧有何差异？",
                          "关键画面距离分析帧多少步？"],
            "completion": "依据真实归档完成描述性对照，或明确记录缺失项及停止原因。",
            "recorded_verdict": evidence.report.get("status"),
            "historical_build_sha256": evidence.run_meta.get("build_sha256"),
            "missing_at_review": result["missing"], "diagnostic": proposed,
        }
        record.update(plan=plan, digest=plan_digest(plan), diagnostic=proposed, analysis=result,
                      status="AWAITING_CONFIRMATION" if proposed else result["status"])
        with self.store.transaction() as state:
            state["objectives"][record["objective_id"]] = record
        return record

    @staticmethod
    def _observe(record, action, observation, evidence, chosen_by="deterministic"):
        record["actions"].append({"step": len(record["actions"]) + 1, "at": now(),
                                  "action": action, "reason": ACTIONS.get(action, action),
                                  "chosen_by": chosen_by, "observation": observation,
                                  "case_ref": evidence.source["case_ref"]})

    @staticmethod
    def _choose(record, goal, options, observation, gateway):
        if gateway is None or len(record["model_requests"]) >= record["budget"]["model_calls"]:
            return options[0], "deterministic", gateway
        request = ActionRequest(new_id("tool"), {k: v for k, v in goal.items() if k != "text"},
                                observation, tuple(options))
        result = gateway.propose_action(request)
        proposal = result.proposed_plan
        action = proposal.get("action") if isinstance(proposal, dict) and set(proposal) == {"action"} else None
        accepted = result.status == "ok" and action in options and result.provider_calls == 1
        record["model_requests"].append({
            "request_id": request.request_id, "provider_calls": result.provider_calls,
            "offered": list(options), "observation": observation, "diagnostics": list(result.diagnostics),
            "selected": action if accepted else None, "outcome": "ACTION_VALIDATED" if accepted else
            result.error_code or "ACTION_REJECTED",
        })
        record["model_used"] |= accepted
        return (action, "deepseek", gateway) if accepted else (options[0], "deterministic", None)

    def _review(self, record, goal, evidence, gateway, *, allow_proposal):
        self._observe(record, "query_archive", {
            "binding_verified": evidence.binding_ok, "report_verdict": evidence.report.get("status"),
            "target_frames": evidence.target, "trace_enabled": evidence.metadata.get("trace_newton") is True,
            "native_render_recorded": evidence.report.get("plan", {}).get("native_render") is True,
        }, evidence)
        rows, contacts, images, contact_summary = [], {}, [], None
        done = []
        proposed = None
        while len(record["actions"]) < record["budget"]["tool_steps"]:
            remaining = []
            if "read_steps" not in done:
                remaining.append("read_steps")
            if goal["need_contact"] and "read_contact_trace" not in done:
                remaining.append("read_contact_trace")
            if goal["need_images"] and "link_native_frames" not in done:
                remaining.append("link_native_frames")
            if not remaining and "read_contact_trace" in done and not contacts and "read_contact_summary" not in done:
                remaining = ["read_contact_summary", "assess_evidence"]
            if not remaining:
                break
            obs = {"steps": sum(r["step_ms"] is not None for r in rows),
                   "trace_frames": len(contacts), "images": len(images), "done": list(done),
                   "trace_enabled": evidence.metadata.get("trace_newton") is True}
            action, actor, gateway = self._choose(record, goal, remaining, obs, gateway)
            if action == "assess_evidence":
                self._observe(record, action, {"trace_frames": 0, "summary_skipped": True}, evidence, actor)
                break
            if action == "read_steps":
                rows = evidence.read_steps()
                output = {"timed_frames": sum(r["step_ms"] is not None for r in rows), "source": "raw/frames.csv"}
            elif action == "read_contact_trace":
                contacts = evidence.read_contacts()
                output = {"valid_frames": len(contacts), "source": "raw/newton_trace.jsonl",
                          "trace_enabled": evidence.metadata.get("trace_newton") is True}
            elif action == "link_native_frames":
                images = evidence.read_images()
                output = {"frames": [image["frame"] for image in images]}
            else:
                contact_summary = evidence.read_contact_summary()
                output = contact_summary
            done.append(action)
            self._observe(record, action, output, evidence, actor)

        for row in rows:
            row["contact_list_max"] = contacts.get(row["frame"])
        stats = hot_statistics(rows, goal["top"])
        links = associate_images(stats["hot_frames"], images)
        missing = []
        if evidence.report.get("status") != "PASS" or not evidence.binding_ok:
            missing.append("归档未通过运行校验或场景、初态、计时绑定未证实。")
        if stats["valid_steps"] < 5 or stats["valid_steps"] != evidence.target:
            missing.append("逐帧耗时样本不足或不完整。")
        for key in ("newton_iterations", "pcg_iterations"):
            if any(v["n"] < v["expected"] for v in stats["groups"][key].values()):
                missing.append(METRICS[key] + "存在缺失。")
        lacks_contacts = goal["need_contact"] and len(contacts) != evidence.target
        lacks_images = goal["need_images"] and not links
        if lacks_contacts:
            missing.append("缺少完整逐帧接触相关约束计数。全程累计和峰值不能分配到各帧。")
        if lacks_images:
            missing.append("缺少同一归档的有效原生画面。")
        missing.extend(UNAVAILABLE_LABELS[key] for key in goal["unavailable_requests"])
        timing = TIMING_SCOPE
        if evidence.metadata.get("trace_newton") is True:
            timing += " 此归档启用 Newton 追踪，耗时包含诊断采集开销。"
        if missing and (lacks_contacts or lacks_images) and not goal["unavailable_requests"]:
            if allow_proposal and record["budget"]["gpu_runs"] and stats["valid_steps"] >= 5:
                try:
                    proposed = self._propose(record, evidence, lacks_contacts, lacks_images)
                except (PlanningError, ValueError, OSError, KeyError) as exc:
                    missing.append("补测前置条件未满足（" + getattr(exc, "code", "IDENTITY_INVALID") + "）。")
            elif not allow_proposal:
                missing.append("诊断预算已使用，本目标结束。")
            else:
                missing.append("当前补测预算或有效帧数不足。")
        if len(record["actions"]) < record["budget"]["tool_steps"]:
            self._observe(record, "propose_diagnostic" if proposed else "finish",
                          {"missing": missing, "new_evidence": proposed["new_evidence"] if proposed else [],
                           "hot_frame_ids": [row["frame"] for row in stats["hot_frames"]]}, evidence)
        facts = []
        for metric, groups in stats["groups"].items():
            high, other = groups["high"], groups["other"]
            if high["n"] and other["n"]:
                facts.append(f"{METRICS[metric]}的中位数在高耗时组为 {high['median']:.4g}"
                             f"（{high['n']} 帧），其余组为 {other['median']:.4g}（{other['n']} 帧）。")
        return {
            "facts": facts,
            "status": "LIMITED" if missing else "COMPLETE", "source": evidence.source,
            "statistics": stats, "rows": rows, "keyframes": links, "images": images,
            "contact_summary": contact_summary, "contact_scope": CONTACT_SCOPE, "timing_scope": timing,
            "missing": missing, "read_issues": evidence.issues, "input_sha256": evidence.references,
            "interpretation": "分组差异仅描述该归档中的同时变化。不能由迭代或约束计数推断耗时因果。",
        }, proposed

    def _propose(self, record, evidence, lacks_contacts, lacks_images):
        spec = SCENES[evidence.scene]
        can_trace = (lacks_contacts and evidence.metadata.get("trace_newton") is False
                     and spec.get("goal_analysis", {}).get("contact_trace") is True)
        original = evidence.report["plan"]
        can_capture = lacks_images and not original.get("native_render") and spec["native_render_frames"]
        if not (can_trace or can_capture):
            raise PlanningError("COLLECTION_UNAVAILABLE", "重复当前配置无法新增缺失证据。")
        if (not evidence.binding_ok or evidence.report.get("status") != "PASS"
                or evidence.target > record["budget"]["frames"]):
            raise PlanningError("DIAGNOSTIC_BUDGET", "仅可补测通过校验且未超过帧数预算的归档。")
        fields = {
            "scene": evidence.scene, "task_frames": [evidence.target], "timeout_sec": record["budget"]["timeout_sec"],
            "native_render": bool(original.get("native_render") or can_capture),
            "diagnostic_contact_trace": bool(original.get("diagnostic_contact_trace") or can_trace),
        }
        if "material" in original:
            fields["material"] = original["material"]
        request = task_request(new_id("diagnostic"), fields)
        # Reuse the existing sealed execution identity before creating a persistent draft.
        snapshot = self.service.plans.snapshot_provider(request)
        if (evidence.identity.get("source_manifest") != snapshot["source_identity"]["source_manifest_sha256"]
                or evidence.identity.get("profile") != snapshot["profile_identity"]["profile_sha256"]
                or evidence.run_meta.get("environment", {}).get("CUDA_VISIBLE_DEVICES") != snapshot["profile_identity"]["gpu"]
                or not evidence.run_meta.get("gpu")):
            raise PlanningError("DIAGNOSTIC_IDENTITY", "归档与当前源码、固定参数或 GPU 绑定不同。")
        build = snapshot.get("native_runtime", {}).get("sha256") or snapshot["runtime_identity"]["files"]["gipc.exe"]
        parent_build = evidence.run_meta.get("build_sha256")
        if not parent_build or (build != parent_build and not can_capture):
            raise PlanningError("DIAGNOSTIC_BUILD", "归档与当前原生构建不同。")
        preview = self.service.preview(None, request, 0)
        frozen = preview["plan"]
        if frozen["execution_snapshot"] != snapshot:
            raise PlanningError("DIAGNOSTIC_IDENTITY", "预览期间执行配置变化。")
        return {
            "plan_id": frozen["plan_id"], "plan_digest": frozen["plan_digest"],
            "request": request, "build_sha256": build, "parent_build_sha256": parent_build,
            "gpu_inventory": evidence.run_meta["gpu"].splitlines(),
            "new_evidence": (["逐物理步 Newton 装配前近距离约束列表峰值"] if can_trace else [])
                            + (["原生初态、中间及末帧画面"] if can_capture else []),
            "invariants": "同场景、相同初态、物理参数、帧数及 GPU 绑定。从初态重跑。",
            "cost": "最多一次原生执行。追踪开销包含在诊断物理步耗时中。"
                    "原生画面采集时间单列。子任务耗时与历史性能计时分别报告。",
        }

    def _check_sources(self, digests):
        for name, expected in digests.items():
            try:
                if sha(self.archive._path(name)) == expected:
                    continue
            except (ValueError, OSError):
                pass
            raise PlanningError("OBJECTIVE_STALE", "原始证据身份变化，请重新生成目标计划。")

    def confirm_diagnostic(self, objective_id, digest):
        from .preflight import inspect

        record = self.get(objective_id)
        if record["digest"] != digest or plan_digest(record["plan"]) != digest:
            raise PlanningError("OBJECTIVE_STALE", "目标计划摘要变化，请重新确认。")
        if record["child_job_id"]:
            return record
        if record["status"] != "AWAITING_CONFIRMATION" or not record["budget"]["gpu_runs"]:
            raise PlanningError("OBJECTIVE_AUTHORIZATION", "当前目标没有可确认的补测。")
        self._check_sources(record["plan"]["input_sha256"])
        diagnostic = record["plan"]["diagnostic"]
        execution_id = objective_id + "-gpu"
        existing = self.store.read()["submissions"].get(execution_id)
        if existing:
            job_id = existing
        else:
            current = inspect()
            if not current["ready"] or current["gpu"].splitlines() != diagnostic["gpu_inventory"]:
                raise PlanningError("DIAGNOSTIC_GPU", "当前 GPU 环境与冻结归档不同，补测未提交。")
            # Stable request IDs reuse the existing receipt on retries or concurrent confirmation.
            receipt = self.service.submit(diagnostic["plan_id"], diagnostic["plan_digest"],
                                          objective_id + "-confirm", execution_id)
            job_id = receipt["job_id"]
        with self.store.transaction() as state:
            saved = require(state["objectives"], objective_id)
            if not saved["child_job_id"]:
                saved.update(child_job_id=job_id, gpu_submissions=1, status="DIAGNOSTIC_SUBMITTED",
                             authorization={"at": now(), "digest": digest, "execution_request_id": execution_id})
                saved["actions"].append({"step": len(saved["actions"]) + 1, "at": now(),
                                         "action": "submit_diagnostic", "chosen_by": "user_confirmation",
                                         "observation": {"job_id": job_id}})
            return saved

    def refresh(self, objective_id):
        record = self.get(objective_id)
        if record["status"] != "DIAGNOSTIC_SUBMITTED":
            return record
        job = self.service.job(record["child_job_id"])
        if job["state"] not in TERMINAL:
            return record
        if job["state"] == "COMPLETED" and job["result_status"] == "PASS":
            source = record["plan"]["source"]
            child = ClothEvidence(self.archive, job["job_id"], case_name(source["requested_frames"]))
            diagnostic = record["plan"]["diagnostic"]
            if (child.scene != source["scene"] or child.target != source["requested_frames"]
                    or child.run_meta.get("build_sha256") != diagnostic["build_sha256"]
                    or child.run_meta.get("gpu", "").splitlines() != diagnostic["gpu_inventory"]):
                record.update(status="DIAGNOSTIC_FAILED", stop_reason="子任务身份与冻结计划不一致。")
            else:
                result, _ = self._review(record, record["plan"]["goal"], child, None, allow_proposal=False)
                record.update(status=result["status"], diagnostic_analysis=result)
        else:
            record.update(status="DIAGNOSTIC_FAILED", stop_reason=f"补测结束 {job['state']} / {job['result_status']}")
        record["child_outcome"] = {"job_id": job["job_id"], "state": job["state"], "result": job["result_status"]}
        with self.store.transaction() as state:
            current = require(state["objectives"], objective_id)
            if current["status"] == "DIAGNOSTIC_SUBMITTED":
                state["objectives"][objective_id] = record
                return record
            return current
