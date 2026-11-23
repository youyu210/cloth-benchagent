"""Read-only native archive replay; never reruns or revalidates physics."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT,read,write,sha,now
from benchagent.progress_reader import read_frames

def main():
    before={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    records=[]
    for path in sorted((ROOT/"runs").rglob("frames.csv")):
        if not path.resolve().is_relative_to((ROOT/"runs").resolve()):raise ValueError("Unsafe archive")
        case=path.parent.parent
        plan=read(case/"plan.json")
        binding={"job_id":case.relative_to(ROOT/"runs").as_posix(),"case_id":case.name}
        state=read_frames(path,binding=binding,target=plan["frames"],finished=True)
        original=read(case/"report.json")
        if original["status"]=="PASS":
            assert len(state["frames"])==plan["frames"] and not state["issues"]
        records.append({"source":path.relative_to(ROOT).as_posix(),"target":plan["frames"],
                        "confirmed":len(state["frames"]),"original_status":original["status"],
                        "issues":state["issues"]})
    after={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    assert before==after
    result={"verified_at":now(),"mode":"historical_readonly","new_simulations":0,"records":records,
            "streams":len(records),"files_checked":len(before),"archive_hashes_unchanged":True}
    write(ROOT/"docs/technical/BG05_历史回放.json",result)
    print(json.dumps({"streams":len(records),"files_checked":len(before),"unchanged":True},ensure_ascii=False))
if __name__=="__main__":main()
