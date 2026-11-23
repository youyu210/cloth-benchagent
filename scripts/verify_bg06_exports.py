"""Offline export verification against existing native archives; no GPU runs."""
import base64
import csv
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT,read,write,sha,now
from benchagent.export_service import ExportService
from benchagent.pdf_report import fingerprint
from pypdf import PdfReader

def main():
    before={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/"runs").rglob("*") if p.is_file()}
    report_id=read(ROOT/"docs/BG05_experiment_latest.json")["job_id"]
    service=ExportService();snapshot=service.capture(report_id)
    outputs={}
    for format in ("markdown","json","csv","zip","pdf"):
        receipt=service.export(report_id,format)
        _,data=service.read(receipt["artifact_id"])
        assert service.export(report_id,format)["cached"]
        assert hashlib.sha256(data).hexdigest()==receipt["file"]["sha256"]
        outputs[format]=receipt
    _,data=service.read(outputs["json"]["artifact_id"])
    content=json.loads(data)
    assert content["view"]==snapshot["view"]
    for name,value in content["original_report_files"].items():
        assert base64.b64decode(value["content"])==snapshot["files"][name]
    _,data=service.read(outputs["csv"]["artifact_id"])
    rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    expected=[f for c in snapshot["view"]["cases"] for f in c["frames"]]
    assert len(rows)==len(expected)==70
    for row,frame in zip(rows,expected):
        assert int(row["帧号"])==frame["frame"]
        assert float(row["物理步耗时（毫秒）"])==frame["solver_time_ms"]
        assert row["碰撞对计数"]=="" and row["原校验结论"]=="通过"
    _,data=service.read(outputs["zip"]["artifact_id"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest=json.loads(z.read("导出清单.json"))
        for name,value in manifest["members"].items():
            assert hashlib.sha256(z.read(name)).hexdigest()==value["sha256"]
        assert manifest["csv_policy"]["column_mapping"]["帧号"]["field"]=="frames.frame"
    pdf=ROOT/outputs["pdf"]["file"]["relative_path"]
    reader=PdfReader(pdf);assert len(reader.pages)==9
    qa=ROOT/"docs/qa/BG06"
    subprocess.run(["C:/Users/hj0320/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe",
        "-png","-r","96",str(pdf),str(qa/"final")],check=True)
    pages=[]
    for n in range(1,10):
        final=qa/("final-"+str(n)+".png")
        assert sha(final)==sha(qa/("review-"+str(n)+".png"))
        pages.append({"page":n,"png":final.relative_to(ROOT).as_posix(),"sha256":sha(final)})
    acceptance=read(ROOT/"configs/pdf_export_acceptance.json")
    acceptance.update(sample_pdf=pdf.relative_to(ROOT).as_posix(),sample_pdf_sha256=sha(pdf),
                      verified_at_utc=now(),renderer=fingerprint())
    write(ROOT/"configs/pdf_export_acceptance.json",acceptance)
    write(ROOT/"docs/qa/BG06/final_render_verification.json",{"verified_at":now(),
        "pdf":pdf.relative_to(ROOT).as_posix(),"pdf_sha256":sha(pdf),"pages":pages,
        "latest_render_matches_all_visually_reviewed_pages":True})
    assert all(sha(ROOT/name)==value for name,value in before.items())
    write(ROOT/"docs/BG06_export_examples.json",{"created_at":now(),"report_id":report_id,"outputs":outputs,"new_simulations":0})
    result={"verified_at":now(),"report_id":report_id,"formats":list(outputs),"csv_rows":len(rows),
            "json_view_matches":True,"original_report_bytes_match":True,"zip_members_hashes_match":True,
            "pdf_pages":9,"pdf_latest_rasters_match_reviewed":True,"cached_exports_verified":True,
            "archive_files_checked":len(before),"archive_hashes_unchanged":True,
            "new_simulations":0,"model_calls":0,"native_rebuilds":0,
            "outputs":{f:v["file"] for f,v in outputs.items()}}
    write(ROOT/"docs/technical/BG06_导出验收.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
