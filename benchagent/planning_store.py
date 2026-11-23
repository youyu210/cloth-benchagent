"""Atomic product metadata; never creates simulation tasks or starts processes."""
import copy
import re
import uuid
from contextlib import contextmanager
from .common import ROOT, now, read, write

class PlanningError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(code + ": " + message)

def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex

def valid_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
        raise PlanningError("INVALID_ID", "标识格式无效")
    return value

def require(items, key):
    valid_id(key)
    if key not in items:
        raise PlanningError("NOT_FOUND", "未找到对应记录")
    return items[key]

class PlanningStore:
    def __init__(self, root=None):
        from pathlib import Path
        self.root = Path(root if root is not None else ROOT / "data/planning").resolve()
        self.path = self.root / "state.json"

    def read(self):
        if not self.path.exists():
            return {"schema_version":"planning-store-v1", "conversations":{}, "drafts":{},
                    "plans":{}, "confirmations":{}, "confirmation_requests":{},
                    "jobs":{},"submissions":{},"cancel_requests":{},"objectives":{},"comparisons":{},"analysis_scripts":{}}
        try:
            state = read(self.path)
            if state["schema_version"] != "planning-store-v1" or any(
                    not isinstance(state[k], dict) for k in
                    ("conversations","drafts","plans","confirmations","confirmation_requests")):
                raise ValueError("Invalid planning state")
            for key in ("jobs","submissions","cancel_requests","objectives","comparisons","analysis_scripts"):
                state.setdefault(key,{})
                if not isinstance(state[key],dict):raise ValueError("Invalid job collection")
            return state
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PlanningError("STORE_INVALID", "元数据损坏，停止写入") from exc

    @contextmanager
    def transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".write.lock"
        try:
            stream = lock.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise PlanningError("SERVICE_BUSY", "元数据写锁已占用，请重试") from exc
        try:
            import os
            stream.write(str(os.getpid()))
            stream.flush()
            state = self.read()
            yield state
            write(self.path, state)
        finally:
            stream.close()
            lock.unlink()

    def get(self, collection, key):
        return copy.deepcopy(require(self.read()[collection], key))
