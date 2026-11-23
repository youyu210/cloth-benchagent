"""Frozen execution plans and explicit confirmations. No execution submission."""
import copy
import hashlib
import json
from pathlib import Path
from .common import ROOT, BASE, IMPLEMENTATION, read, sha, now
from .conversation_service import SCENES, normalize_request, observation_args, scene_args, scene_selector
from .planning_store import PlanningError, PlanningStore, new_id, require, valid_id
from .materials import MATERIAL_RUNTIME, native_config

def plan_digest(snapshot):
    payload=json.dumps(snapshot,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def capture_execution_snapshot(request):
    """Hash and verify actual files without nvidia-smi or any subprocess."""
    request=normalize_request(request)
    def safe(base, name):
        if not isinstance(name,str) or "\\" in name or ":" in name or name.startswith("/") or any(
                p in ("",".","..") for p in name.split("/")):
            raise ValueError("Unsafe manifest path")
        result=(base/name).resolve()
        if not result.is_relative_to(base.resolve()):
            raise ValueError("Manifest path escapes root")
        return result
    def verify(base, manifest):
        files=manifest.get("files")
        if not isinstance(files,dict) or not files:
            raise ValueError("Empty manifest")
        for name,digest in files.items():
            if sha(safe(base,name))!=digest:
                raise ValueError("File identity changed: "+name)
        return copy.deepcopy(files)
    try:
        backend=read(ROOT/"versions/backend_manifest.json")
        if set(backend["files"])!={"configs/c0.json","configs/default_suite.json"}:
            raise ValueError("Backend manifest entries changed")
        verify(ROOT,backend)
        source=read(ROOT/"versions/source_manifest.json")
        runtime=read(ROOT/"versions/runtime_manifest.json")
        if source["base_commit"]!=BASE or source["implementation_commit"]!=IMPLEMENTATION or runtime["implementation_commit"]!=IMPLEMENTATION:
            raise ValueError("Wrong implementation")
        source_files=verify(ROOT/"simulator",source)
        runtime_files=verify(ROOT/"build/bin",runtime)
        source_sha=sha(ROOT/"versions/source_manifest.json")
        if runtime["source_manifest_sha256"]!=source_sha or "gipc.exe" not in runtime_files:
            raise ValueError("Runtime/source mismatch")
        profile=read(ROOT/"configs/c0.json")
        if profile["backend_id"]!=request["backend_id"] or profile["scene"]!=1 or profile["gpu"]!="0":
            raise ValueError("Wrong fixed profile")
        native = {}
        args = scene_args(profile["args"], request["scene"])
        variant = SCENES[request["scene"]].get("runtime_variant")
        if "material" in request:
            variant = MATERIAL_RUNTIME
            native["material_input"] = native_config(request["scene"], request["material"])
        if variant or request.get("native_render"):
            from .native_render import runtime_identity
            native["native_runtime"] = runtime_identity(variant=variant)
        args = observation_args(args, request)
        return {
            **native,
            "request":request,
            "source_identity":{"base_commit":BASE,"implementation_commit":IMPLEMENTATION,
                "adaptation":source["adaptation"],"source_manifest_sha256":source_sha,"files":source_files},
            "runtime_identity":{"runtime_manifest_sha256":sha(ROOT/"versions/runtime_manifest.json"),
                "executable":str(ROOT/"build/bin/gipc.exe"),"files":runtime_files},
            "profile_identity":{"backend_id":profile["backend_id"],"profile_sha256":sha(ROOT/"configs/c0.json"),
                "backend_manifest_sha256":sha(ROOT/"versions/backend_manifest.json"),
                "suite_sha256":sha(ROOT/"configs/default_suite.json"),"gpu":profile["gpu"],"args":args},
            "environment_policy":{"cwd":str(ROOT/"simulator"),"path_prefix":str(ROOT/"build/bin"),
                "clear_prefixes":["GIPC_","CIPC_"],"CUDA_VISIBLE_DEVICES":"0","CUDA_DEVICE_ORDER":"PCI_BUS_ID",
                **({"CIPC_SCENE":scene_selector(request["scene"])} if scene_selector(request["scene"]) else {})},
            "protection":{"timeout_sec":request["timeout_sec"],"diagnostic_max_newton":2000,"diagnostic_max_ms":300000}}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise PlanningError("IDENTITY_INVALID","固定源码、程序或配置身份校验失败："+str(exc)) from exc

class PlanService:
    def __init__(self, store=None, *, snapshot_provider=None):
        self.store=store or PlanningStore()
        self.snapshot_provider=snapshot_provider or capture_execution_snapshot

    def get_plan(self, plan_id):
        return self.store.get("plans",plan_id)

    def preview_plan(self, draft_id, expected_revision):
        if type(expected_revision) is not int:
            raise PlanningError("DRAFT_CONFLICT","草稿版本无效")
        draft=self.store.get("drafts",draft_id)
        if draft["revision"]!=expected_revision:
            raise PlanningError("DRAFT_CONFLICT","草稿已变化")
        snapshot=self.snapshot_provider(draft["task_request"])
        value={"schema_version":"execution-plan-v1","plan_id":new_id("plan"),
               "conversation_id":draft["conversation_id"],"draft_id":draft_id,
               "draft_revision":expected_revision,"execution_snapshot":snapshot,
               "plan_digest":plan_digest(snapshot),
               "provenance":{"origin":draft["origin"],"generated_at":now()},"confirmed_at":None}
        if draft.get("planning_audit"):
            value["provenance"]["planning_audit"] = draft["planning_audit"]
        with self.store.transaction() as state:
            current=require(state["drafts"],draft_id)
            if current!=draft:
                raise PlanningError("DRAFT_CONFLICT","生成计划期间草稿变化")
            state["plans"][value["plan_id"]]=value
        return copy.deepcopy(value)

    def _verify(self, state, plan_id, digest):
        plan=require(state["plans"],plan_id)
        if digest!=plan["plan_digest"] or plan_digest(plan["execution_snapshot"])!=digest:
            raise PlanningError("PLAN_MISMATCH","计划摘要不匹配")
        draft=require(state["drafts"],plan["draft_id"])
        if draft["revision"]!=plan["draft_revision"] or draft["task_request"]!=plan["execution_snapshot"]["request"]:
            raise PlanningError("PLAN_STALE","草稿已修改，请重新预览确认")
        try:
            current=self.snapshot_provider(draft["task_request"])
        except PlanningError as exc:
            raise PlanningError("PLAN_STALE","预览后的执行身份不可用") from exc
        if current!=plan["execution_snapshot"]:
            raise PlanningError("PLAN_STALE","预览后源码、配置或程序变化")
        return plan

    def confirm_plan(self, plan_id, digest, request_id):
        """Idempotently record explicit intent. BG-03 will create queued jobs."""
        valid_id(request_id)
        with self.store.transaction() as state:
            previous=state["confirmation_requests"].get(request_id)
            if previous:
                receipt=require(state["confirmations"],previous)
                if receipt["plan_id"]!=plan_id or receipt["plan_digest"]!=digest:
                    raise PlanningError("REQUEST_CONFLICT","同一确认请求绑定了不同计划")
                return copy.deepcopy(receipt)
            plan=self._verify(state,plan_id,digest)
            receipt={"schema_version":"plan-confirmation-v1","confirmation_id":new_id("confirm"),
                     "request_id":request_id,"namespace":"plan-confirmation",
                     "conversation_id":plan["conversation_id"],"draft_id":plan["draft_id"],
                     "draft_revision":plan["draft_revision"],"plan_id":plan_id,"plan_digest":digest,
                     "confirmed_at":now(),"status":"CONFIRMED_NOT_SUBMITTED","job_id":None}
            state["confirmations"][receipt["confirmation_id"]]=receipt
            state["confirmation_requests"][request_id]=receipt["confirmation_id"]
        return copy.deepcopy(receipt)

    def verify_confirmation(self, confirmation_id):
        """Recheck current revision/identity; does not enqueue or launch anything."""
        state=self.store.read()
        receipt=require(state["confirmations"],confirmation_id)
        self._verify(state,receipt["plan_id"],receipt["plan_digest"])
        return copy.deepcopy(receipt)
