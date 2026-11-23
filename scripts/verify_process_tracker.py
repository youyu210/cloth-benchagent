"""Real owned helper-tree verification; no GPU simulation."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, now, write
from benchagent.process_tracker import ProcessTracker
from benchagent.execution_lease import ExecutionLease

def main():
    evidence=ROOT/"docs/technical/BG03_进程树实测.json"
    lock=ROOT/"docs/technical/.bg03-helper.lock"
    signal=ROOT/"docs/technical"/("BG03_helper_child_"+uuid.uuid4().hex+".txt")
    child="import time; time.sleep(30)"
    parent=("import subprocess,sys,time;from pathlib import Path;time.sleep(.5);"
            "child=subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);"
            "Path("+repr(str(signal))+").write_text(str(child.pid));time.sleep(30)")
    with ExecutionLease(lock,"bg03-helper") as lease:
        proc=subprocess.Popen([sys.executable,"-c",parent],
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | 0x4) if os.name=="nt" else 0,
            start_new_session=os.name!="nt",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        lease.safe=False
        tracker=None
        try:
            tracker=ProcessTracker(proc);lease.attach(proc.pid)
            deadline=time.monotonic()+5
            while not signal.exists() and time.monotonic()<deadline:time.sleep(.05)
            before=tracker.active()
            if not signal.exists() or before<3:raise RuntimeError("Explicit descendant did not join owned job")
            tracker.terminate();tracker.finish()
            after=tracker.active()
            lease.cleared()
            result={"verified_at":now(),"platform":os.name,"owned_active_before":before,
                    "owned_active_after":after,"explicit_child_pid":int(signal.read_text()),
                    "root_returncode":proc.wait(timeout=1),"suspended_launch":True,
                    "parent_and_descendant_terminated":after==0}
        except BaseException as exc:
            if tracker:
                try:tracker.terminate();tracker.finish();lease.cleared()
                except Exception:lease.uncertain(exc)
            else:
                proc.kill();proc.wait(timeout=10);lease.uncertain(exc)
            raise
        finally:
            if tracker:tracker.close()
    result["lease_released"]=not lock.exists()
    write(evidence,result)
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":main()
