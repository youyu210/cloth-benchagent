"""UI application boundary. Read/preview never starts the worker."""
import atexit
import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from .common import ROOT, now, read, write
from .planning_store import PlanningStore, PlanningError, new_id
from .conversation_service import ConversationService
from .plan_service import PlanService
from .job_manager import JobManager
from .archive_repository import ArchiveRepository
from .model_gateway import PlanRequest, get_default_gateway

LOGGER = logging.getLogger(__name__)


class AppService:
    def __init__(self,root=None,*,store=None,plans=None,manager_factory=None):
        self.root=Path(root or ROOT)
        self.store=store or PlanningStore(self.root/"data/planning")
        self.conversations=ConversationService(self.store)
        self.plans=plans or PlanService(self.store)
        self.archive=ArchiveRepository(self.root/"runs")
        self.manager_factory=manager_factory or JobManager
        self.manager=None
        from .export_service import ExportService
        self.exporter=ExportService(self.archive,self.root/"exports")
        from .experiment_objectives import ExperimentObjectives
        self.objectives = ExperimentObjectives(self)
        from .archive_comparison import ArchiveComparison
        self.comparisons = ArchiveComparison(self)
        from .analysis_scripts import AnalysisScripts
        self.analysis_scripts = AnalysisScripts(self)

    def preview(self,conversation_id,request,expected_revision,*,use_model=False,api_key=None,selected_scene=None):
        text = None
        if isinstance(request, str) and not use_model:
            from .material_dialogue import resolve_material
            from .conversation_service import dialogue_intent, task_request

            conversation = self.conversation(conversation_id)
            active = (conversation or {}).get("active_draft_id")
            prior = self.conversations.get_draft(active)["task_request"] if active else None
            material = resolve_material(request, prior, selected_scene)
            if material:
                if conversation_id is None:
                    conversation_id = self.conversations.create_conversation()["conversation_id"]
                if "reply" in material:
                    return {"conversation_id": conversation_id, "reply": material["reply"]}
                text, request = request, task_request(new_id("intent"), material)
            elif prior and "material" in prior:
                fields, revision = dialogue_intent(request, previous=prior, selected=selected_scene)
                if revision and fields.get("scene", prior["scene"]) == prior["scene"]:
                    text, request = request, task_request(new_id("intent"), {
                        **prior, **fields, "request_id": new_id("intent"),
                    })
        if use_model:
            conversation = self.conversation(conversation_id)
            turns = [m for m in (conversation or {}).get("messages", [])
                     if m.get("source_kind") == "model"][-6:]
            history = tuple(("user" if m["actor"] == "user" else "assistant", m["content"])
                            for m in turns)
            prior = None
            if conversation and conversation["active_draft_id"]:
                draft = self.conversations.get_draft(conversation["active_draft_id"])
                if draft:
                    prior = draft["task_request"]
            result = get_default_gateway(api_key).propose_plan(
                PlanRequest(new_id("intent"), request, history=history, previous_plan=prior,
                            selected_scene=selected_scene))
            audit = {"request_id": result.request_id, "provider_calls": result.provider_calls,
                     "stages": list(result.diagnostics), "outcome": result.error_code or "PLAN_VALIDATED"}
            LOGGER.log(logging.INFO if result.status == "ok" else logging.WARNING,
                       "DeepSeek planning request=%s calls=%d stages=%s outcome=%s",
                       audit["request_id"], audit["provider_calls"], audit["stages"], audit["outcome"])
            if result.status != "ok":
                message = result.message
                if result.error_code == "MODEL_NOT_CONFIGURED":
                    message = "请在系统设置中保存 DeepSeek API 密钥。"
                if result.provider_calls:
                    message += f"（{result.error_code}，调用 {result.provider_calls} 次，编号 {result.request_id}）"
                raise PlanningError(result.error_code, message)
            text, request = request, result.proposed_plan
        if conversation_id is None:
            conversation=self.conversations.create_conversation()
            conversation_id=conversation["conversation_id"]
        if use_model and request is None:
            self.conversations.record_model_reply(conversation_id, text, result.message, result.request_id, audit=audit)
            return {"conversation_id": conversation_id, "reply": result.message}
        draft=self.conversations.update_draft(conversation_id,request,expected_revision,text=text,model=use_model,
                                              audit=audit if use_model else None)
        plan=self.plans.preview_plan(draft["draft_id"],draft["revision"])
        return {"conversation_id":conversation_id,"draft":draft,"plan":plan}

    def submit(self,plan_id,digest,confirmation_request_id,execution_request_id):
        # Only this explicitly invoked operation creates a worker or simulation.
        if self.manager is None:
            manager=self.manager_factory(self.store,plans=self.plans,root=self.root)
            manager.start()
            self.manager=manager
            atexit.register(self.close)
        confirmation=self.plans.confirm_plan(plan_id,digest,confirmation_request_id)
        return self.manager.submit_plan(plan_id,digest,execution_request_id,confirmation["confirmation_id"])

    def close(self):
        if self.manager is not None:
            closed=self.manager.close()
            if closed:self.manager=None
            return closed
        return True

    def conversation(self,conversation_id):
        return self.conversations.get_conversation(conversation_id) if conversation_id else None

    def jobs(self):
        return sorted(copy.deepcopy(list(self.store.read()["jobs"].values())),
                      key=lambda j:j["created_at"],reverse=True)

    def job(self,job_id):
        return self.manager.get_job_snapshot(job_id) if self.manager else self.store.get("jobs",job_id)

    def cancel(self,job_id,request_id):
        # Reconnecting read-only clients may register a cancellation without starting a worker.
        manager=self.manager or self.manager_factory(self.store,plans=self.plans,root=self.root)
        return manager.cancel_job(job_id,request_id)

    def records(self,recorded_status=None):
        return self.archive.list_records(recorded_status=recorded_status)["records"]

    def report(self,report_id,case_id=None):
        return self.archive.get_view(report_id,case_id)

    @staticmethod
    def _tail(path,limit=16000):
        if not path.is_file():return "未记录日志。"
        with path.open("rb") as stream:
            stream.seek(0,2)
            stream.seek(max(0,stream.tell()-limit))
            return stream.read(limit).decode("utf-8",errors="replace")

    def archive_log(self,report_id,case_id=None,stream="stdout"):
        if stream not in ("stdout","stderr"):raise ValueError("Invalid log stream")
        record=self.archive.get_report(report_id)
        if case_id is not None:
            matches=[c for c in record["cases"] if c.get("case_id")==case_id]
            if len(matches)!=1:raise ValueError("Case does not belong to report")
            record=matches[0]
        folder=self.archive._path(record["report_id"])
        return self._tail(self.archive._path(stream+".log",folder))

    def native_frame(self, report_id, frame):
        from .native_render import frame_name, frame_png
        record = self.archive.get_report(report_id)
        visual = (record["raw_report"] or {}).get("visualization") or {}
        if (visual.get("kind") != "native_render_frame" or type(frame) is not int
                or frame not in visual["frames"]):
            raise ValueError("该任务没有所选原生画面")
        folder = self.archive._path(record["report_id"])
        name = frame_name(frame)
        return frame_png(self.archive._path("raw/" + name, folder), visual["files"][name])

    def cloth_replay(self, report_id):
        from .cloth_replay import ClothReplay
        record = self.archive.get_report(report_id)
        plan = (record["raw_report"] or {}).get("plan") or {}
        if plan.get("scene") != "fixed-cloth":
            return None
        folder = self.archive._path(record["report_id"])
        mesh_path = "Assets/triMesh/cloth_high.obj"
        identity = read(self.archive._path("identity_before.json", folder))
        source = identity["source"]
        return ClothReplay(self.archive._path("raw", folder), self.root / "simulator" / mesh_path,
                           source[mesh_path])

    def job_log(self,job_id,stream="stdout"):
        if stream not in ("stdout","stderr"):raise ValueError("Invalid log stream")
        job=self.job(job_id)
        case=job.get("active_case_id")
        if case is None:return "任务尚未启动用例。"
        if case not in [v["case_id"] for v in job["cases"]]:raise ValueError("Invalid active case")
        folder=self.archive._path(job_id)
        return self._tail(self.archive._path("cases/"+case+"/"+stream+".log",folder))

    def original_markdown(self,report_id,case_id=None):
        record=self.archive.get_report(report_id)
        if case_id is not None:
            matches=[c for c in record["cases"] if c.get("case_id")==case_id]
            if len(matches)!=1:raise ValueError("Case does not belong to report")
            record=matches[0]
        folder=self.archive._path(record["report_id"])
        name="benchmark_summary.md" if record["kind"]=="suite" else "report.md"
        path=self.archive._path(name,folder)
        return path.read_bytes() if path.is_file() else None

    def configuration(self):
        return read(self.root/"configs/c0.json")

    def environment(self):
        for name in ("docs/ui_environment.json","docs/preflight_latest.json"):
            path=self.root/name
            if path.is_file():
                value=read(path)
                value.setdefault("observed_at",datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat())
                value["source"]=name
                return value
        return None

    def inspect_environment(self):
        from .preflight import inspect
        value={**inspect(),"observed_at":now(),"source":"docs/ui_environment.json"}
        write(self.root/"docs/ui_environment.json",value)
        return value

    def model_status(self, api_key=None):
        from dataclasses import asdict
        return asdict(get_default_gateway(api_key).get_status())

    def health(self):
        return {"worker_started":self.manager is not None,
                "blocked":bool(self.manager and self.manager.blocked),
                "execution_lock":(self.root/"runs/.execution.lock").exists(),
                "manager_lock":(self.store.root/".manager.lock").exists()}

    def _job_case_path(self,job,case_id,relative):
        if case_id not in [case["case_id"] for case in job["cases"]]:
            raise ValueError("用例不属于当前任务")
        folder=self.archive._path(job["job_id"])
        return self.archive._path("cases/"+case_id+"/"+relative,folder)

    def telemetry(self,job_id,cursors=None):
        from .progress_reader import read_frames,frame_snapshot
        from .job_manager import TERMINAL
        job=self.job(job_id)
        if cursors is None:cursors={}
        valid={c["case_id"] for c in job["cases"]}
        if not isinstance(cursors,dict) or not set(cursors).issubset(valid):
            raise ValueError("帧游标含其他用例")
        result=[];updated={}
        for case in job["cases"]:
            cid=case["case_id"]
            path=self._job_case_path(job,cid,"raw/frames.csv")
            finished=(job["state"] in TERMINAL or case["phase"] in ("COMPLETED","CANCELLED","FAILED")) and case["phase"] not in ("PENDING","SKIPPED")
            cursor=read_frames(path,binding={"job_id":job_id,"case_id":cid},
                               target=case["frames"],cursor=cursors.get(cid),finished=finished)
            updated[cid]=cursor
            result.append({**case,**frame_snapshot(cursor,case["frames"],phase=case["phase"])})
        confirmed=sum(c["confirmed_frames"] for c in result)
        target=sum(c["target_frames"] for c in result)
        return {"schema_version":"job-telemetry-v1","job":job,"job_id":job_id,
                "plan_id":job.get("plan_id"),"observed_at":now(),"observed_seq":job.get("seq"),
                "cases":result,"confirmed_total":confirmed,"target_total":target,
                "fraction":confirmed/target,"cursors":updated}

    def log_slice(self,job_id,case_id,stream="stdout",cursor=None,*,follow=True):
        from .progress_reader import read_log
        from .job_manager import TERMINAL
        if stream not in ("stdout","stderr"):raise ValueError("日志流无效")
        job=self.job(job_id)
        path=self._job_case_path(job,case_id,stream+".log")
        case=next(c for c in job["cases"] if c["case_id"]==case_id)
        finished=job["state"] in TERMINAL or case["phase"] in ("COMPLETED","CANCELLED","FAILED","SKIPPED")
        return read_log(path,binding={"job_id":job_id,"case_id":case_id,"stream":stream},
                        cursor=cursor,finished=finished,follow=follow)

    def export_capabilities(self):
        return self.exporter.capabilities()

    def export_report(self,report_id,format,case_id=None):
        return self.exporter.export(report_id,format,case_id)

    def export_content(self,artifact_id):
        return self.exporter.read(artifact_id)
