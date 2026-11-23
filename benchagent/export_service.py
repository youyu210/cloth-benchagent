"""Content-addressed exports outside immutable runs; atomic publish and provenance."""
import copy
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from .common import ROOT,read,write,sha,now
from .archive_repository import ArchiveRepository
from .view_models import build_report_view
from .export_document import SCHEMA,cases,provenance,markdown,json_bytes,csv_bytes
from . import pdf_report

EXT={"markdown":"md","json":"json","csv":"csv","pdf":"pdf","zip":"zip"}
MIME={"markdown":"text/markdown","json":"application/json","csv":"text/csv",
      "pdf":"application/pdf","zip":"application/zip"}
ROOT_FILES={"report.json","report.md","report.zh-CN.md","benchmark_summary.json","benchmark_summary.md",
 "benchmark_summary.zh-CN.md","metrics.json","plan.json","task_request.json","profile.json",
 "run_meta.json","preflight.json","identity_before.json","identity_after.json","stdout.log","stderr.log"}
RAW_FILES={"material_effective.json","scene_setup.json","native_render.json","metadata.json","status.json","collision_summary.json","frames.csv","pcg_iterations.jsonl",
 "audits.json","boundary.i32","initial.f64","final.f64","initial_velocities.f64","final_velocities.f64",
 "masses.f64","tetrahedra.u32"}
INPUT_FILES={"parameterSetting.txt","abd_system_config.json","material.json"}
MAX_FILE=64*1024*1024
MAX_TOTAL=128*1024*1024

class ExportError(ValueError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(code+": "+message)

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()

class ExportService:
    def __init__(self,repository=None,root=None):
        self.repository=repository or ArchiveRepository()
        self.workspace=self.repository.root.parent
        self.root=Path(root or self.workspace/"exports").resolve()
        if not self.root.is_relative_to(self.workspace) or self.root.is_relative_to(self.repository.root):
            raise ValueError("导出目录必须在项目内且位于runs之外")
        self.acceptance=self.workspace/"configs/pdf_export_acceptance.json"

    def renderer(self):
        try:pdf=pdf_report.fingerprint()
        except (OSError,ImportError,ValueError):pdf={"available":False}
        return {"export_schema":SCHEMA,"export_code":sha(__file__),
                "document_code":sha(Path(__file__).with_name("export_document.py")),
                "pdf":pdf}

    def capabilities(self):
        ready=False;reason="PDF尚未完成本版本渲染验收"
        try:
            acceptance=read(self.acceptance)
            ready=acceptance.get("accepted") is True and acceptance.get("renderer")==pdf_report.fingerprint()
            if not ready:reason="PDF渲染器或字体已变更，等待重新验收"
        except (OSError,ValueError,KeyError,ImportError):pass
        return {"formats":list(EXT),"pdf_ready":ready,"pdf_reason":None if ready else reason}

    def _plain(self,path):
        path=Path(path)
        if not path.is_relative_to(self.repository.root):raise ExportError("UNSAFE_SOURCE","来源越过归档边界")
        current=self.repository.root
        for part in path.relative_to(self.repository.root).parts:
            current=current/part
            if current.is_symlink() or getattr(current,"is_junction",lambda:False)():
                raise ExportError("UNSAFE_SOURCE","导出不跟随归档中的链接或联接")
        if not path.resolve().is_relative_to(self.repository.root):
            raise ExportError("UNSAFE_SOURCE","来源越过归档边界")

    def _guard_active(self,report_id):
        state_path=self.workspace/"data/planning/state.json"
        if state_path.exists():
            state=read(state_path)
            root_id=report_id.split("/")[0]
            job=state.get("jobs",{}).get(root_id)
            if job and job["state"] not in ("COMPLETED","FAILED","CANCELLED","INTERRUPTED"):
                raise ExportError("JOB_ACTIVE","任务尚未归档，暂不生成最终导出")

    def capture(self,report_id,case_id=None):
        self._guard_active(report_id)
        self._plain(self.repository.root/report_id)
        record=self.repository.get_report(report_id)
        if record["kind"]=="incomplete":raise ExportError("NO_REPORT","归档尚未形成报告")
        if case_id is not None:
            selected=[c for c in record["cases"] if c.get("case_id")==case_id]
            if len(selected)!=1:raise ExportError("INVALID_CASE","用例不属于此报告")
            view=build_report_view(selected[0]);records=selected
        else:
            view=build_report_view(record)
            records=record["cases"] if record["kind"]=="suite" else []
        files={};total=0
        def include(folder_id,relative):
            nonlocal total
            candidate=self.repository.root/folder_id/relative
            self._plain(candidate)
            if not candidate.is_file():return
            if candidate.stat().st_size>MAX_FILE:raise ExportError("SOURCE_TOO_LARGE","单个来源文件超过64MiB")
            data=candidate.read_bytes()
            total+=len(data)
            if total>MAX_TOTAL or len(files)>=500:raise ExportError("SOURCE_TOO_LARGE","所选证据超出128MiB或500文件上限")
            key=folder_id+"/"+relative
            files[key]=data
        def collect(folder_id):
            for name in sorted(ROOT_FILES):include(folder_id,name)
            for name in sorted(RAW_FILES):include(folder_id,"raw/"+name)
            evidence = self.repository.root / folder_id / "raw/native_frames"
            self._plain(evidence)
            if evidence.is_dir():
                for candidate in sorted(evidence.iterdir()):
                    if re.fullmatch(r"frame-[0-9]{1,4}\.bmp",candidate.name):
                        include(folder_id,"raw/native_frames/"+candidate.name)
            for name in sorted(INPUT_FILES):include(folder_id,"inputs/"+name)
        collect(report_id)
        for child in records:collect(child["report_id"])
        index={name:{"sha256":hashlib.sha256(data).hexdigest(),"size_bytes":len(data)} for name,data in sorted(files.items())}
        source_digest=digest({"report_id":report_id,"case_id":case_id,"view":view,"source_index":index})
        return {"report_id":report_id,"case_id":case_id,"view":view,"files":files,
                "source_index":index,"source_digest":source_digest}

    def _key(self,snapshot,format):
        return digest({"source":snapshot["source_digest"],"format":format,"locale":"zh-CN",
                       "renderer":self.renderer()})

    def _path(self,name):
        candidate=self.root/name
        if candidate.is_symlink() or getattr(candidate,"is_junction",lambda:False)():
            raise ExportError("UNSAFE_OUTPUT","产物路径不能是链接")
        path=candidate.resolve()
        if not path.is_relative_to(self.root) or path.parent!=self.root:raise ExportError("UNSAFE_OUTPUT","导出路径越界")
        return path

    def _manifest(self,artifact_id):
        if re.fullmatch(r"[a-f0-9]{64}",artifact_id or "") is None:raise ExportError("INVALID_ARTIFACT","产物标识无效")
        return self._path(artifact_id+".manifest.json")

    def _cached(self,key,format,snapshot):
        manifest_path=self._manifest(key)
        if not manifest_path.is_file():return None
        try:
            manifest=read(manifest_path)
            path=self._path(key+"."+EXT[format])
            if (manifest["artifact_id"]!=key or manifest["format"]!=format or
                manifest["source_digest"]!=snapshot["source_digest"] or manifest["renderer"]!=self.renderer() or
                not path.is_file() or path.stat().st_size!=manifest["file"]["size_bytes"] or
                sha(path)!=manifest["file"]["sha256"]):return None
            return manifest
        except (OSError,ValueError,KeyError,TypeError):return None

    def export(self,report_id,format,case_id=None):
        if format not in EXT:raise ExportError("INVALID_FORMAT","不支持的导出格式")
        if format=="pdf" and not self.capabilities()["pdf_ready"]:
            raise ExportError("PDF_NOT_ACCEPTED","PDF尚未完成当前渲染验收")
        snapshot=self.capture(report_id,case_id)
        key=self._key(snapshot,format)
        cached=self._cached(key,format,snapshot)
        if cached:return {**cached,"cached":True}
        self.root.mkdir(parents=True,exist_ok=True)
        lock=self._path(key+".lock")
        try:stream=lock.open("x",encoding="utf-8")
        except FileExistsError as exc:raise ExportError("EXPORT_BUSY","同一导出正在生成或有遗留登记") from exc
        temp=self._path(key+"."+uuid.uuid4().hex+".tmp")
        try:
            with stream:
                stream.write(str(os.getpid()));stream.flush()
                extra={}
                if format=="markdown":temp.write_text(markdown(snapshot),encoding="utf-8")
                elif format=="json":temp.write_bytes(json_bytes(snapshot))
                elif format=="csv":temp.write_bytes(csv_bytes(snapshot))
                elif format=="pdf":extra["pdf"]=pdf_report.render_pdf(snapshot,temp)
                else:
                    members={"统一报告.md":markdown(snapshot).encode("utf-8"),
                             "统一视图.json":json_bytes(snapshot),"逐帧数据.csv":csv_bytes(snapshot)}
                    for name,data in snapshot["files"].items():
                        relative=Path(name).relative_to(Path(report_id)).as_posix()
                        members["原始归档/"+relative]=data
                    index={name:{"sha256":hashlib.sha256(data).hexdigest(),"size_bytes":len(data)} for name,data in sorted(members.items())}
                    embedded={**provenance(snapshot),"members":index,"pdf_included":False}
                    members["导出清单.json"]=(json.dumps(embedded,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
                    with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED) as archive:
                        for name,data in sorted(members.items()):archive.writestr(name,data)
                    extra["zip_members"]=index
                if temp.stat().st_size>MAX_TOTAL:
                    raise ExportError("OUTPUT_TOO_LARGE","导出产物超过128MiB")
                if self.capture(report_id,case_id)["source_digest"]!=snapshot["source_digest"]:
                    raise ExportError("SOURCE_CHANGED","生成期间来源变化，请重试")
                output=self._path(key+"."+EXT[format])
                manifest={**provenance(snapshot),"artifact_id":key,"format":format,"created_at":now(),
                    "download_name":"BenchAgent_"+key[:12]+"."+EXT[format],"mime":MIME[format],
                    "renderer":self.renderer(),"file":{"relative_path":output.relative_to(self.workspace).as_posix(),
                    "sha256":sha(temp),"size_bytes":temp.stat().st_size},**extra}
                os.replace(temp,output)
                write(self._manifest(key),manifest)
                return {**manifest,"cached":False}
        finally:
            temp.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)

    def read(self,artifact_id):
        manifest=read(self._manifest(artifact_id))
        format=manifest.get("format")
        if format not in EXT:raise ExportError("INVALID_ARTIFACT","产物格式无效")
        snapshot=self.capture(manifest["report_id"],manifest.get("case_id"))
        if self._key(snapshot,format)!=artifact_id:
            raise ExportError("STALE_EXPORT","来源或渲染版本已变化，请重新生成")
        checked=self._cached(artifact_id,format,snapshot)
        if checked is None:raise ExportError("CORRUPT_EXPORT","产物或清单校验失败，请重新生成")
        if format=="pdf" and not self.capabilities()["pdf_ready"]:
            raise ExportError("PDF_NOT_ACCEPTED","当前PDF渲染尚未验收")
        data=self._path(artifact_id+"."+EXT[format]).read_bytes()
        if hashlib.sha256(data).hexdigest()!=checked["file"]["sha256"]:
            raise ExportError("CORRUPT_EXPORT","下载读取期间文件变化")
        return checked,data

    def manifest_bytes(self,artifact_id):
        checked,_=self.read(artifact_id)
        return (json.dumps(checked,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
