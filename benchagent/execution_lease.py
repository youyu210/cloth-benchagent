"""Token-owned cross-process lease; uncertain cleanup never releases it."""
import os
import uuid
from .common import now, read, write

class LeaseBusy(RuntimeError): pass
class CleanupRequired(RuntimeError): pass

class ExecutionLease:
    def __init__(self,path,job_id=None):
        self.path=path
        self.token=uuid.uuid4().hex
        self.record={"schema_version":"execution-lease-v1","owner_token":self.token,
                     "owner_pid":os.getpid(),"job_id":job_id,"acquired_at":now(),
                     "child_pid":None,"cleanup_required":False}
        self.safe=True

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:
            stream=self.path.open("x",encoding="utf-8")
        except FileExistsError as exc:
            raise LeaseBusy("执行锁占用；包括旧版PID锁，均不自动接管") from exc
        try:
            import json
            json.dump(self.record,stream,ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            stream.close()
        return self

    def _save(self):
        if read(self.path).get("owner_token")!=self.token:
            self.safe=False
            raise CleanupRequired("执行锁所有权不匹配")
        write(self.path,self.record)

    def attach(self,pid):
        self.safe=False
        self.record.update(child_pid=pid,child_started_at=now())
        self._save()

    def cleared(self):
        self.record.update(child_pid=None,child_ended_at=now())
        self._save()
        self.safe=True

    def uncertain(self,message):
        self.safe=False
        self.record.update(cleanup_required=True,cleanup_error=str(message))
        try:self._save()
        except Exception:pass

    def __exit__(self,kind,value,tb):
        if not self.safe:
            self.uncertain(value or "无法确认子进程结束")
            return
        try:
            owned=read(self.path).get("owner_token")==self.token
        except Exception:
            owned=False
        if not owned:
            raise CleanupRequired("释放时执行锁所有权异常，保留锁")
        self.path.unlink()
