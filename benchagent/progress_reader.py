"""Read-only, source-bound CSV and UTF-8 byte cursors. No execution or validation."""
import codecs
import copy
import csv
import hashlib
import math
import os
import re
from pathlib import Path

from .common import FRAME_TARGETS

MAX_SOURCE=16*1024*1024
READ_BYTES=65536
DISPLAY_CHARS=32000

def _issue(state,code,message,offset=None):
    item={"code":code,"message":message,"offset":state["offset"] if offset is None else offset}
    if item not in state["issues"]:state["issues"].append(item)

def _initial(kind,binding,cursor):
    binding=copy.deepcopy(binding)
    if cursor is None:
        value={"schema_version":kind+"-cursor-v1","binding":binding,"offset":0,
               "identity":None,"prefix_sha256":hashlib.sha256(b"").hexdigest(),
               "issues":[],"blocked":False}
        if kind=="frames":value.update(header=None,frames=[])
        else:value.update(text="",display_truncated=False,at_eof=False)
        return value
    if (not isinstance(cursor,dict) or cursor.get("schema_version")!=kind+"-cursor-v1"
            or cursor.get("binding")!=binding or type(cursor.get("offset")) is not int
            or not 0<=cursor["offset"]<=MAX_SOURCE):
        raise ValueError("游标不属于当前任务、用例或输出流")
    return copy.deepcopy(cursor)

def _open(path,state):
    path=Path(path)
    try:stream=path.open("rb")
    except FileNotFoundError:
        if state["identity"] is not None:
            _issue(state,"SOURCE_MISSING","已读取的数据源消失，保留上次观测");state["blocked"]=True
        return None
    except OSError:
        _issue(state,"READ_ERROR","无法读取当前数据源");return None
    stat=os.fstat(stream.fileno())
    identity=[stat.st_dev,stat.st_ino]
    if state["identity"] is not None and state["identity"]!=identity:
        _issue(state,"SOURCE_REPLACED","数据源已替换，停止自动续读");state["blocked"]=True
    elif stat.st_size<state["offset"]:
        _issue(state,"SOURCE_TRUNCATED","数据源已截短，停止自动续读");state["blocked"]=True
    elif stat.st_size>MAX_SOURCE:
        _issue(state,"SOURCE_TOO_LARGE","数据源超出读取上限，停止自动续读");state["blocked"]=True
    else:
        state["identity"]=identity
        prefix=stream.read(state["offset"])
        if hashlib.sha256(prefix).hexdigest()!=state["prefix_sha256"]:
            _issue(state,"PREFIX_CHANGED","已消费的数据被改写，停止自动续读");state["blocked"]=True
    if state["blocked"]:stream.close();return None
    stream.seek(state["offset"])
    return stream

def _commit(stream,state,offset,suffix):
    stream.seek(0)
    prefix=stream.read(state["offset"])
    actual=stream.read(offset-state["offset"])
    if (hashlib.sha256(prefix).hexdigest()!=state["prefix_sha256"] or actual!=suffix):
        raise OSError("Source changed during read")
    state["prefix_sha256"]=hashlib.sha256(prefix+actual).hexdigest()
    state["offset"]=offset

def _integer(value):
    if not re.fullmatch(r"[0-9]+",value):raise ValueError("Invalid integer")
    number=int(value)
    if number>2**63-1:raise ValueError("Counter out of range")
    return number

def read_frames(path,*,binding,target,cursor=None,finished=False):
    if type(target) is not int or target not in FRAME_TARGETS:
        raise ValueError("Invalid frame target")
    state=_initial("frames",{**binding,"target":target},cursor)
    if state["blocked"]:return state
    stream=_open(path,state)
    if stream is None:
        if finished and not state["blocked"]:_issue(state,"MISSING_CSV","用例已结束但未找到逐帧CSV")
        return state
    baseline=copy.deepcopy(state)
    try:
        start=state["offset"]
        chunk=stream.read(READ_BYTES)
        end=chunk.rfind(b"\n")+1
        consumed=0
        for raw in chunk[:end].splitlines(keepends=True):
            position=start+consumed
            try:
                text=raw.decode("utf-8-sig" if state["header"] is None else "utf-8").rstrip("\r\n")
                fields=next(csv.reader([text],strict=True))
                if state["header"] is None:
                    if len(fields)!=len(set(fields)) or not {"frame","step_ms","newton_iterations","pcg_iterations"}.issubset(fields):
                        raise ValueError("Invalid header")
                    state["header"]=fields
                else:
                    if len(fields)!=len(state["header"]):raise ValueError("Column mismatch")
                    row=dict(zip(state["header"],fields))
                    number=_integer(row["frame"])
                    if number!=len(state["frames"])+1 or number>target:
                        _issue(state,"FRAME_SEQUENCE","帧号重复、乱序、缺口或超过目标",position)
                        state["blocked"]=True;break
                    elapsed=float(row["step_ms"])
                    if not math.isfinite(elapsed) or elapsed<0:raise ValueError("Invalid elapsed")
                    item={"frame":number,"solver_time_ms":elapsed,
                          "newton_iterations":_integer(row["newton_iterations"]),
                          "pcg_iterations":_integer(row["pcg_iterations"]),
                          "pcg_solves":_integer(row["pcg_solves"]) if row.get("pcg_solves") else None,
                          "collision_pair_count":_integer(row["collision_pair_count"]) if row.get("collision_pair_count") else None}
                    state["frames"].append(item)
                consumed+=len(raw)
            except (ValueError,UnicodeError,csv.Error,StopIteration,OverflowError):
                _issue(state,"INVALID_CSV","CSV表头、字段或数值无效，仅保留此前连续帧",position)
                state["blocked"]=True;break
        _commit(stream,state,start+consumed,chunk[:consumed])
        if not state["blocked"] and len(chunk)-end:
            if end==0 and len(chunk)==READ_BYTES:
                _issue(state,"ROW_TOO_LONG","CSV单行过长，停止自动续读");state["blocked"]=True
            elif finished and stream.seek(0,2)<=start+len(chunk):
                _issue(state,"TRAILING_PARTIAL_ROW","终态文件仍有未结束的半行，不计入帧数")
    except OSError:
        for key in ("offset","prefix_sha256","frames","header"):state[key]=baseline[key]
        _issue(state,"SOURCE_RACE","读取期间数据源变化，保留已确认观测");state["blocked"]=True
    finally:stream.close()
    return state

def frame_snapshot(state,target,*,phase):
    rows=state["frames"]
    count=len(rows)
    eta=None
    if phase=="RUNNING" and len(rows)>=5 and count<target and not state["blocked"]:
        sample=rows[-20:]
        eta=sum(r["solver_time_ms"] for r in sample)/len(sample)*(target-count)/1000
    return {"confirmed_frames":count,"target_frames":target,"fraction":count/target,
            "stream_state":"BLOCKED" if state["blocked"] else "COLLECTED" if count==target else "PARTIAL" if count else "WAITING",
            "eta_seconds":eta,"eta_scope":"仅当前用例剩余物理步，不含初始化与校验",
            "frames":copy.deepcopy(rows),"issues":copy.deepcopy(state["issues"]),
            "source_cursor":state["offset"]}

def read_log(path,*,binding,cursor=None,finished=False,follow=True):
    state=_initial("log",binding,cursor)
    if not follow or state["blocked"]:return state
    stream=_open(path,state)
    if stream is None:return state
    baseline=copy.deepcopy(state)
    try:
        start=state["offset"]
        data=stream.read(READ_BYTES)
        at_end=stream.tell()>=os.fstat(stream.fileno()).st_size
        decoder=codecs.getincrementaldecoder("utf-8")(errors="replace")
        text=decoder.decode(data,final=finished and at_end)
        pending=decoder.getstate()[0]
        consumed=len(data)-len(pending)
        try:data[:consumed].decode("utf-8",errors="strict")
        except UnicodeError:_issue(state,"INVALID_UTF8","日志含无效或终态未完成的UTF-8字节，显示替代字符，原文件保留")
        joined=state["text"]+text
        if len(joined)>DISPLAY_CHARS:state["display_truncated"]=True
        state["text"]=joined[-DISPLAY_CHARS:]
        _commit(stream,state,start+consumed,data[:consumed])
        state["at_eof"]=at_end and not pending
    except OSError:
        for key in ("offset","prefix_sha256","text","display_truncated","at_eof"):state[key]=baseline[key]
        _issue(state,"SOURCE_RACE","日志读取期间数据源变化，停止自动续读");state["blocked"]=True
    finally:stream.close()
    return state
