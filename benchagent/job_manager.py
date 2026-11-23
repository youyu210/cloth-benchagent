"""One serial worker, atomic submission, durable snapshots and cooperative cancellation."""
import copy
import os
import threading
import time
import uuid
from .common import ROOT, now, read
from .planning_store import PlanningStore, PlanningError, new_id, require, valid_id
from .plan_service import PlanService, capture_execution_snapshot
from .execution_control import ExecutionControl
from .execution_lease import LeaseBusy, CleanupRequired
from .runner import exclusive, execute_request

TERMINAL={"COMPLETED","CANCELLED","FAILED","INTERRUPTED"}
FINALIZING={"PARSING","VALIDATING","REPORTING","COMPLETED"}
from .conversation_service import case_name

class JobManager:
    def __init__(self,store=None,*,plans=None,root=None,executor=None):
        self.store=store or PlanningStore()
        self.plans=plans or PlanService(self.store)
        self.root=root or ROOT
        self.executor=executor or execute_request
        self.owner_token=uuid.uuid4().hex
        self.owner_path=self.store.root/".manager.lock"
        self._api=threading.RLock()
        self._wake=threading.Event()
        self._stop=threading.Event()
        self._thread=None
        self._started=False
        self.blocked=False
        self._active=None

    def _mutate(self,operation):
        with self._api:
            for attempt in range(100):
                try:
                    with self.store.transaction() as state:
                        value=operation(state)
                    return copy.deepcopy(value)
                except PlanningError as exc:
                    if exc.code!="SERVICE_BUSY":raise
                    time.sleep(.01)
            raise PlanningError("SERVICE_BUSY","元数据暂忙")

    @staticmethod
    def _event(job,kind,**payload):
        job["seq"]+=1
        job["updated_at"]=now()
        job["events"].append({"job_id":job["job_id"],"plan_id":job["plan_id"],
            "case_id":job.get("active_case_id"),"seq":job["seq"],"time":job["updated_at"],
            "type":kind,**payload})

    def start(self,*,worker=True):
        if self._started:return self
        self._stop.clear()
        self.store.root.mkdir(parents=True,exist_ok=True)
        try:stream=self.owner_path.open("x",encoding="utf-8")
        except FileExistsError as exc:
            raise PlanningError("MANAGER_BUSY","已有工作器或遗留所有权记录，不能自动接管") from exc
        import json
        with stream:
            json.dump({"owner_token":self.owner_token,"owner_pid":os.getpid(),"created_at":now()},stream)
            stream.flush();os.fsync(stream.fileno())
        try:
            def recover(state):
                for job in state["jobs"].values():
                    if job["state"] not in TERMINAL:
                        job.update(state="INTERRUPTED",failure_kind="INTERRUPTED",ended_at=now())
                        self._event(job,"INTERRUPTED",message="服务重启后只恢复记录，不自动重跑")
                    if job.get("failure_kind")=="CLEANUP_REQUIRED":self.blocked=True
            self._mutate(recover)
            self._started=True
            if worker:
                self._thread=threading.Thread(target=self._loop,name="benchagent-worker",daemon=True)
                self._thread.start()
        except BaseException:
            if read(self.owner_path).get("owner_token")==self.owner_token:self.owner_path.unlink()
            raise
        return self

    def _ensure(self):
        if not self._started or self._stop.is_set():
            raise PlanningError("SERVICE_CLOSED","工作器未启动或正在关闭")

    def submit_plan(self,plan_id,digest,request_id,confirmation_id):
        self._ensure();valid_id(request_id)
        def register(state):
            existing=state["submissions"].get(request_id)
            if existing:
                job=require(state["jobs"],existing)
                if job["plan_id"]!=plan_id or job["plan_digest"]!=digest:
                    raise PlanningError("REQUEST_CONFLICT","提交请求已绑定其他计划")
                return {"job_id":job["job_id"],"state":job["state"],"plan_id":plan_id,"request_id":request_id}
            if self.blocked:raise PlanningError("CLEANUP_REQUIRED","清理未确认，禁止新增执行")
            receipt=require(state["confirmations"],confirmation_id)
            if receipt["plan_id"]!=plan_id or receipt["plan_digest"]!=digest:
                raise PlanningError("PLAN_MISMATCH","确认与计划不匹配")
            plan=self.plans._verify(state,plan_id,digest)
            request=plan["execution_snapshot"]["request"]
            job_id=new_id("job")
            job={"schema_version":"job-v1","job_id":job_id,"plan_id":plan_id,"plan_digest":digest,
                 "conversation_id":plan["conversation_id"],"draft_id":plan["draft_id"],
                 "draft_revision":plan["draft_revision"],"confirmation_id":confirmation_id,
                 "request_id":request_id,"namespace":"execution-submit",
                 "execution_snapshot":copy.deepcopy(plan["execution_snapshot"]),
                 "state":"QUEUED","result_status":None,"created_at":now(),"updated_at":now(),
                 "owner":None,"child_pid":None,"active_case_id":None,"active_case_index":None,
                 "cancel_requested":False,"report_id":None,"failure_kind":None,
                 "cases":[{"case_id":case_name(n),"frames":n,"phase":"PENDING","result_status":None} for n in request["task_frames"]],
                 "seq":0,"events":[]}
            self._event(job,"QUEUED")
            state["jobs"][job_id]=job
            state["submissions"][request_id]=job_id
            return {"job_id":job_id,"state":"QUEUED","plan_id":plan_id,"request_id":request_id}
        receipt=self._mutate(register)
        self._wake.set()
        return receipt

    def get_job_snapshot(self,job_id,after_seq=None):
        if after_seq is not None and (type(after_seq) is not int or after_seq<0):
            raise PlanningError("INVALID_CURSOR","事件游标无效")
        with self._api:
            job=self.store.get("jobs",job_id)
        if after_seq is not None:job["events"]=[v for v in job["events"] if v["seq"]>after_seq]
        return job

    def cancel_job(self,job_id,cancel_request_id):
        valid_id(cancel_request_id)
        def cancel(state):
            previous=state["cancel_requests"].get(cancel_request_id)
            if previous:
                if previous["job_id"]!=job_id:raise PlanningError("REQUEST_CONFLICT","取消请求已用于其他任务")
                return previous
            job=require(state["jobs"],job_id)
            if job["state"] in TERMINAL:
                status="TERMINAL"
            elif job["active_case_index"]==len(job["cases"])-1 and any(
                    c["case_id"]==job["active_case_id"] and c["phase"] in FINALIZING for c in job["cases"]):
                status="FINALIZING"
            else:
                job["cancel_requested"]=True
                status="REQUESTED"
                if job["state"]=="QUEUED":
                    job.update(state="CANCELLED",result_status="SKIPPED",ended_at=now())
                    for case in job["cases"]:case.update(phase="SKIPPED",result_status="SKIPPED")
                    self._event(job,"CANCELLED",message="排队取消，未创建进程")
                else:
                    job["state"]="CANCELLING"
                    self._event(job,"CANCEL_REQUESTED")
            receipt={"job_id":job_id,"cancel_request_id":cancel_request_id,"status":status,"recorded_at":now()}
            state["cancel_requests"][cancel_request_id]=receipt
            return receipt
        value=self._mutate(cancel);self._wake.set();return value

    def _update(self,job_id,kind,**values):
        def update(state):
            job=require(state["jobs"],job_id)
            job.update(values)
            self._event(job,kind)
        self._mutate(update)

    def _case(self,job_id,case_id,index,total):
        self._update(job_id,"CASE_SELECTED",active_case_id=case_id,active_case_index=index)

    def _phase(self,job_id,phase):
        def update(state):
            job=require(state["jobs"],job_id)
            for case in job["cases"]:
                if case["case_id"]==job["active_case_id"]:case["phase"]=phase
            self._event(job,"PHASE",phase=phase)
        self._mutate(update)

    def run_next(self):
        self._ensure()
        if self.blocked:return False
        queued=[j for j in self.store.read()["jobs"].values() if j["state"]=="QUEUED"]
        if not queued:return False
        job=queued[0];job_id=job["job_id"]
        # ROOT is shared by both CLI and manager in production.
        lease=None
        try:
            lease=exclusive(job_id)
            with lease:
                current=self.get_job_snapshot(job_id)
                if current["state"]!="QUEUED":return True
                self._active=job_id
                self._update(job_id,"ACTIVE",state="ACTIVE",owner={
                    "owner_token":lease.token,"owner_pid":os.getpid()},started_at=now(),waiting_for_lease=False)
                snapshot=job["execution_snapshot"]
                def verify():
                    actual=self.plans.snapshot_provider(snapshot["request"])
                    if actual!=snapshot:raise PlanningError("PLAN_STALE","执行文件身份与冻结计划不同")
                verify()
                control=ExecutionControl(
                    cancelled=lambda:self._stop.is_set() or self.get_job_snapshot(job_id)["cancel_requested"],
                    phase=lambda phase:self._phase(job_id,phase),
                    process=lambda pid:self._update(job_id,"PROCESS",child_pid=pid),
                    verify=verify,case=lambda case,index,total:self._case(job_id,case,index,total))
                result=self.executor(snapshot["request"],self.root/"runs"/job_id,control,lease,
                    {"job_id":job_id,"plan_id":job["plan_id"],"plan_digest":job["plan_digest"],
                     "conversation_id":job["conversation_id"],"producer_kind":"simulation"})
            reports=result["tasks"]
            failures={v.get("failure_kind") for v in reports}
            if "CLEANUP_REQUIRED" in failures:
                self.blocked=True
                state="FAILED";failure="CLEANUP_REQUIRED"
            elif "IDENTITY_CHANGED" in failures:
                state="FAILED";failure="IDENTITY_CHANGED"
            elif "CANCELLED" in failures or (
                    self.get_job_snapshot(job_id)["cancel_requested"] and "NOT_STARTED" in failures):
                state="CANCELLED";failure="CANCELLED"
            else:
                state="COMPLETED";failure=None
            def finish(data):
                saved=require(data["jobs"],job_id)
                saved.update(state=state,result_status=result["status"],report_id=job_id,
                             failure_kind=failure,ended_at=now())
                for case,report in zip(saved["cases"],reports):
                    case["result_status"]=report["status"]
                    case["phase"]="SKIPPED" if report["status"]=="SKIPPED" else "CANCELLED" if report.get("failure_kind")=="CANCELLED" else "COMPLETED"
                self._event(saved,state)
            self._mutate(finish)
            return True
        except LeaseBusy:
            current=self.get_job_snapshot(job_id)
            if not current.get("waiting_for_lease"):
                self._update(job_id,"LEASE_WAIT",waiting_for_lease=True)
            return False
        except Exception as exc:
            unsafe=isinstance(exc,CleanupRequired) or (lease is not None and not lease.safe)
            self.blocked=self.blocked or unsafe
            self._update(job_id,"FAILED",state="FAILED",failure_kind="CLEANUP_REQUIRED" if unsafe else
                         "IDENTITY_CHANGED" if getattr(exc,"code",None) in ("PLAN_STALE","IDENTITY_INVALID") else "EXECUTION_ERROR",
                         error=str(exc),ended_at=now())
            return True
        finally:self._active=None

    def _loop(self):
        while not self._stop.is_set():
            try:
                worked=self.run_next()
            except Exception:
                # A publication failure cannot cause an implicit retry of a possibly launched job.
                self.blocked=True
                worked=False
            if not worked:
                self._wake.wait(.2);self._wake.clear()

    def close(self,timeout=15):
        if not self._started:return True
        if self._active is not None:
            self.cancel_job(self._active,new_id("shutdown"))
        self._stop.set();self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():return False
        if read(self.owner_path).get("owner_token")!=self.owner_token:
            raise PlanningError("MANAGER_OWNER_MISMATCH","工作器所有权异常，保留记录")
        self.owner_path.unlink()
        self._started=False
        return True
