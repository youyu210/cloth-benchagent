"""BG-06 UI export tests: explicit creation and stale-artifact invalidation."""
import csv
import io
import json
import shutil
import unittest
from unittest.mock import patch
from pypdf import PdfReader
from benchagent.common import ROOT,write
from support import WorkbenchHarness
from test_exports import report

class ExportUITests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):
        self.setup_workbench()
        write(self.ui_root/"runs/history/report.json",report())
    def tearDown(self):self.teardown_workbench()
    def reports(self):
        app=self.app();app.sidebar.radio(key="navigation").set_value("测试报告").run()
        self.assertEqual([e.message for e in app.exception],[])
        return app
    def test_reading_and_switching_formats_does_not_create_exports(self):
        with patch("benchagent.pdf_report.render_pdf",side_effect=AssertionError("implicit PDF")):
            app=self.reports()
            for value in ("CSV","Markdown","JSON","ZIP 证据包"):
                app.selectbox(key="export-format").set_value(value).run()
                self.assertEqual([e.message for e in app.exception],[])
        self.assertFalse(self.ui_service.exporter.root.exists())
        self.assertIsNone(self.ui_service.manager)
        self.no_process_mock.assert_not_called()
    def test_csv_generation_exposes_chinese_data_and_manifest_download(self):
        app=self.reports();app.selectbox(key="export-format").set_value("CSV").run()
        app.button(key="generate-export").click().run()
        self.assertEqual([e.message for e in app.exception],[])
        artifact=app.session_state["_export_artifact"]
        manifest,data=self.ui_service.export_content(artifact)
        rows=list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertIn("碰撞对计数",rows[0]);self.assertEqual(rows[0]["碰撞对计数"],"")
        self.assertEqual(manifest["format"],"csv")
        self.assertGreaterEqual(len(app.get("download_button")),2)
        self.assertIsNone(self.ui_service.manager)
    def test_scope_change_uses_current_case_or_entire_suite(self):
        write(self.ui_root/"runs/suite/cases/smoke-20/report.json",report(2))
        write(self.ui_root/"runs/suite/cases/standard-50/report.json",report(3))
        write(self.ui_root/"runs/suite/benchmark_summary.json",{"status":"PASS","tasks":[
            {"task_id":"smoke-20","status":"PASS"},{"task_id":"standard-50","status":"PASS"}]})
        app=self.reports();app.selectbox(key="report_select").set_value("suite").run()
        app.selectbox(key="export-format").set_value("JSON").run()
        app.radio(key="export-scope").set_value("整个任务集").run()
        app.button(key="generate-export").click().run()
        self.assertEqual([e.message for e in app.exception],[])
        _,data=self.ui_service.export_content(app.session_state["_export_artifact"])
        self.assertEqual(len(json.loads(data)["view"]["cases"]),2)
        app.radio(key="export-scope").set_value("当前用例").run()
        self.assertIsNone(app.session_state["_export_artifact"])
        app.button(key="generate-export").click().run()
        manifest,data=self.ui_service.export_content(app.session_state["_export_artifact"])
        self.assertEqual(manifest["case_id"],"standard-50")
        self.assertEqual(json.loads(data)["view"]["case_id"],"standard-50")
    def test_changing_format_clears_previous_download_without_generating(self):
        app=self.reports();app.selectbox(key="export-format").set_value("CSV").run()
        app.button(key="generate-export").click().run()
        self.assertTrue(app.session_state["_export_artifact"])
        app.selectbox(key="export-format").set_value("Markdown").run()
        self.assertIsNone(app.session_state["_export_artifact"])
        self.assertEqual(len(list(self.ui_service.exporter.root.glob("*.manifest.json"))),1)
    def test_source_mutation_clears_stale_download(self):
        app=self.reports();app.selectbox(key="export-format").set_value("JSON").run()
        app.button(key="generate-export").click().run()
        write(self.ui_root/"runs/history/report.json",{**report(),"status":"FAIL"})
        app.run()
        self.assertEqual([e.message for e in app.exception],[])
        self.assertIsNone(app.session_state["_export_artifact"])
        self.assertTrue(any("来源" in e.value for e in app.error))
    def test_pdf_is_gated_until_review_record_exists(self):
        app=self.reports()
        self.assertEqual(app.selectbox(key="export-format").value,"PDF")
        self.assertTrue(app.button(key="generate-export").disabled)
        shutil.copy2(ROOT/"configs/pdf_export_acceptance.json",self.ui_root/"configs/pdf_export_acceptance.json")
        app.run()
        self.assertFalse(app.button(key="generate-export").disabled)
        app.button(key="generate-export").click().run()
        self.assertEqual([e.message for e in app.exception],[])
        manifest,data=self.ui_service.export_content(app.session_state["_export_artifact"])
        self.assertEqual(manifest["format"],"pdf")
        self.assertIn("基准测试报告","".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages))
    def test_zip_generation_contains_only_selected_archive(self):
        (self.ui_root/"runs/history/.env").write_text("DO_NOT_EXPORT")
        app=self.reports();app.selectbox(key="export-format").set_value("ZIP 证据包").run()
        app.button(key="generate-export").click().run()
        self.assertEqual([e.message for e in app.exception],[])
        manifest,data=self.ui_service.export_content(app.session_state["_export_artifact"])
        self.assertEqual(manifest["format"],"zip")
        self.assertTrue(data.startswith(b"PK"))
        self.no_process_mock.assert_not_called()
