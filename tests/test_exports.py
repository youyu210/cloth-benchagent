"""BG-06 exports use isolated archives. No simulation or model/network calls."""
import base64
import copy
import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
from benchagent.common import ROOT,write,read,sha
from benchagent.archive_repository import ArchiveRepository
from benchagent.export_service import ExportService,ExportError
from benchagent import pdf_report

def report(n=2):
    return {"status":"PASS","actual_frames":n,"solver_time_total_s":n*.01,
            "newton_iterations_total":n*2,"pcg_iterations_total_from_csv":n*7,"pcg_solves":n*2,
            "frame_solver_times_ms":[10]*n,"plan":{"action":"run","scene":"default","frames":20},
            "errors":[],"warnings":[],"checks":None}

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/"tests");self.root=Path(self.tmp.name)
        self.repo=ArchiveRepository(self.root/"runs")
        self.service=ExportService(self.repo)
        write(self.repo.root/"single/report.json",report())
    def tearDown(self):self.tmp.cleanup()
    def code(self,wanted,method,*args):
        with self.assertRaises(ExportError) as raised:method(*args)
        self.assertEqual(raised.exception.code,wanted)
    def approve_pdf(self):
        self.root.joinpath("configs").mkdir(exist_ok=True)
        shutil.copy2(ROOT/"configs/pdf_export_acceptance.json",self.root/"configs/pdf_export_acceptance.json")
    def make_suite(self,modern=True,missing=False):
        prefix="cases/" if modern else ""
        tasks=[]
        for cid,frames in (("smoke-20",20),("standard-50",50)):
            if not (missing and frames==50):write(self.repo.root/"suite"/prefix/cid/"report.json",report())
            tasks.append({"task_id":cid,"status":"PASS",**report()})
        write(self.repo.root/"suite/benchmark_summary.json",{"status":"PASS","tasks":tasks})
    def test_constructor_and_capabilities_do_not_create_exports(self):
        self.assertFalse(self.service.root.exists())
        self.assertFalse(self.service.capabilities()["pdf_ready"])
        self.assertFalse(self.service.root.exists())
    def test_markdown_chinese_and_original_json_bytes_preserved(self):
        original=(self.repo.root/"single/report.json").read_bytes()
        receipt=self.service.export("single","markdown")
        manifest,data=self.service.read(receipt["artifact_id"])
        text=data.decode("utf-8")
        self.assertIn("平均 PCG 迭代（按帧）",text);self.assertIn("未记录",text)
        self.assertIn("不重跑",text)
        raw=self.service.export("single","json")
        _,encoded=self.service.read(raw["artifact_id"])
        value=json.loads(encoded)
        self.assertEqual(value["view"]["raw_report"]["status"],"PASS")
        preserved=value["original_report_files"]["single/report.json"]
        self.assertEqual(base64.b64decode(preserved["content"]),original)
        self.assertEqual(manifest["file"]["sha256"],hashlib.sha256(data).hexdigest())
    def test_csv_chinese_header_missing_numeric_empty_with_reason(self):
        receipt=self.service.export("single","csv")
        _,data=self.service.read(receipt["artifact_id"])
        rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]["原校验结论"],"通过")
        self.assertEqual(rows[0]["归档读取状态"],"完整可读")
        self.assertEqual(rows[0]["物理步耗时（毫秒）"],"10.0")
        self.assertEqual(rows[0]["牛顿迭代次数"],"")
        self.assertEqual(rows[0]["碰撞对计数"],"")
        self.assertIn("逐帧碰撞未记录",rows[0]["缺项说明"])
        self.assertEqual(receipt["csv_policy"]["column_mapping"]["物理步耗时（毫秒）"]["field"],"frames.solver_time_ms")
    def test_formula_like_identifier_is_escaped(self):
        write(self.repo.root/"=source/report.json",report())
        receipt=self.service.export("=source","csv")
        _,data=self.service.read(receipt["artifact_id"])
        rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["来源报告"],"'=source")
    def test_cache_same_source_reused_and_formats_are_distinct(self):
        first=self.service.export("single","json");second=self.service.export("single","json")
        other=self.service.export("single","csv")
        self.assertTrue(second["cached"]);self.assertEqual(first["artifact_id"],second["artifact_id"])
        self.assertNotEqual(first["artifact_id"],other["artifact_id"])
    def test_source_change_invalidates_old_download_and_creates_new_id(self):
        old=self.service.export("single","json")
        write(self.repo.root/"single/report.json",{**report(),"status":"FAIL"})
        self.code("STALE_EXPORT",self.service.read,old["artifact_id"])
        new=self.service.export("single","json")
        self.assertNotEqual(old["artifact_id"],new["artifact_id"])
    def test_corrupt_output_is_detected_then_repaired(self):
        receipt=self.service.export("single","csv")
        path=self.root/receipt["file"]["relative_path"]
        path.write_bytes(b"broken")
        self.code("CORRUPT_EXPORT",self.service.read,receipt["artifact_id"])
        repaired=self.service.export("single","csv")
        self.assertFalse(repaired["cached"])
        self.assertEqual(repaired["artifact_id"],receipt["artifact_id"])
        self.assertTrue(self.service.read(repaired["artifact_id"])[1].startswith(b"\xef\xbb\xbf"))
    def test_zip_whitelist_and_member_hashes(self):
        root=self.repo.root/"single"
        (root/".env").write_text("SECRET_NOT_TO_EXPORT")
        (root/"private.pem").write_text("PRIVATE_NOT_TO_EXPORT")
        (root/"stdout.log").write_text("allowed log")
        (root/"raw").mkdir()
        (root/"raw/token.txt").write_text("TOKEN_NOT_TO_EXPORT")
        receipt=self.service.export("single","zip")
        _,data=self.service.read(receipt["artifact_id"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("原始归档/stdout.log",z.namelist())
            self.assertFalse(any("token" in n or ".env" in n or "private" in n for n in z.namelist()))
            manifest=json.loads(z.read("导出清单.json"))
            for name,item in manifest["members"].items():
                self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),item["sha256"])
            self.assertEqual(z.read("原始归档/report.json"),(root/"report.json").read_bytes())
    def test_legacy_modern_and_selected_case_scopes(self):
        for modern in (False,True):
            self.make_suite(modern)
            full=self.service.export("suite","json")
            one=self.service.export("suite","json","smoke-20")
            _,full_data=self.service.read(full["artifact_id"])
            _,one_data=self.service.read(one["artifact_id"])
            self.assertEqual(len(json.loads(full_data)["view"]["cases"]),2)
            self.assertEqual(json.loads(one_data)["view"]["case_id"],"smoke-20")
            self.assertNotEqual(full["artifact_id"],one["artifact_id"])
    def test_selected_case_zip_preserves_cases_layout_excludes_other_raw(self):
        self.make_suite(True)
        for cid in ("smoke-20","standard-50"):
            (self.repo.root/"suite/cases"/cid/"stdout.log").write_text(cid)
        receipt=self.service.export("suite","zip","smoke-20")
        _,data=self.service.read(receipt["artifact_id"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("原始归档/cases/smoke-20/stdout.log",z.namelist())
            self.assertNotIn("原始归档/cases/standard-50/stdout.log",z.namelist())
    def test_missing_child_preserves_partial_read_and_original_pass(self):
        self.make_suite(True,missing=True)
        receipt=self.service.export("suite","json")
        _,data=self.service.read(receipt["artifact_id"])
        view=json.loads(data)["view"]
        self.assertEqual(view["recorded_result_status"],"PASS")
        self.assertEqual(view["archive_read_status"],"PARTIAL")
    def test_invalid_original_json_survives_as_exact_base64(self):
        path=self.repo.root/"single/report.json";path.write_bytes(b"{broken\xff")
        receipt=self.service.export("single","json")
        _,data=self.service.read(receipt["artifact_id"])
        value=json.loads(data)
        self.assertIsNone(value["view"]["recorded_result_status"])
        self.assertEqual(base64.b64decode(value["original_report_files"]["single/report.json"]["content"]),path.read_bytes())
    def test_reject_formats_paths_foreign_cases_and_output_inside_runs(self):
        self.code("INVALID_FORMAT",self.service.export,"single","exe")
        self.code("INVALID_CASE",self.service.export,"single","json","other")
        with self.assertRaises(ValueError):self.service.export("../outside","json")
        with self.assertRaises(ValueError):ExportService(self.repo,self.repo.root/"exports")
        self.code("INVALID_ARTIFACT",self.service.read,"../x")
    def test_active_job_is_not_exported(self):
        write(self.root/"data/planning/state.json",{"jobs":{"single":{"state":"ACTIVE"}}})
        self.code("JOB_ACTIVE",self.service.export,"single","json")
        self.assertFalse(self.service.root.exists())
    def test_size_limit_is_enforced_without_silent_truncation(self):
        with patch("benchagent.export_service.MAX_FILE",1):
            self.code("SOURCE_TOO_LARGE",self.service.export,"single","zip")
    def test_changed_source_before_publication_returns_no_manifest(self):
        first=self.service.capture("single");changed=copy.deepcopy(first);changed["source_digest"]="changed"
        with patch.object(self.service,"capture",side_effect=[first,changed]):
            self.code("SOURCE_CHANGED",self.service.export,"single","csv")
        self.assertEqual(list(self.service.root.glob("*.manifest.json")),[])
        self.assertEqual(list(self.service.root.glob("*.lock")),[])
    def test_pdf_gate_and_embedded_chinese_text(self):
        self.code("PDF_NOT_ACCEPTED",self.service.export,"single","pdf")
        self.approve_pdf()
        receipt=self.service.export("single","pdf")
        _,data=self.service.read(receipt["artifact_id"])
        self.assertTrue(data.startswith(b"%PDF-"))
        reader=PdfReader(io.BytesIO(data));text="".join(p.extract_text() for p in reader.pages)
        self.assertIn("基准测试报告",text);self.assertIn("物理精度未评估",text)
        self.assertIn("平均 PCG 迭代",text)
        self.assertTrue(receipt["pdf"]["font_embedded"])
    def test_font_or_renderer_acceptance_change_disables_pdf(self):
        self.approve_pdf()
        self.assertTrue(self.service.capabilities()["pdf_ready"])
        acceptance=read(self.service.acceptance);acceptance["renderer"]["renderer_sha256"]="old"
        write(self.service.acceptance,acceptance)
        self.assertFalse(self.service.capabilities()["pdf_ready"])
    def test_nonpdf_exports_still_work_if_font_missing(self):
        with patch.object(pdf_report,"FONT_PATH",self.root/"missing-font.ttf"):
            self.assertFalse(self.service.capabilities()["pdf_ready"])
            receipt=self.service.export("single","markdown")
            self.assertTrue(self.service.read(receipt["artifact_id"])[1])
    def test_pdf_long_values_and_unsupported_glyphs_do_not_break_layout(self):
        self.approve_pdf()
        value={**report(),"checks":[{"id":"V01","name":"长说明","status":"FAIL","required":True,
               "expected":"说明"*4000,"actual":"emoji\U0001f642","evidence":["raw/very_long_path_"+("x"*200)+".json"],
               "message":"保留原始字段"}]}
        write(self.repo.root/"single/report.json",value)
        receipt=self.service.export("single","pdf")
        _,data=self.service.read(receipt["artifact_id"])
        self.assertGreater(len(PdfReader(io.BytesIO(data)).pages),1)
        self.assertIn("U+1F642",receipt["pdf"]["unsupported_codepoints"])
        self.assertGreater(receipt["pdf"]["truncated_fields"],0)
    def test_exports_do_not_change_archives_or_start_processes(self):
        before={p.relative_to(self.repo.root).as_posix():sha(p) for p in self.repo.root.rglob("*") if p.is_file()}
        with patch("subprocess.Popen",side_effect=AssertionError("process")),patch("socket.create_connection",side_effect=AssertionError("network")):
            for format in ("markdown","json","csv","zip"):self.service.export("single",format)
        self.assertEqual(before,{p.relative_to(self.repo.root).as_posix():sha(p) for p in self.repo.root.rglob("*") if p.is_file()})
