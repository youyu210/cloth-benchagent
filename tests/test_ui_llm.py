import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from benchagent.common import ROOT
from benchagent.planner import plan_request

class LLMTests(unittest.TestCase):
    def test_structured_plan_validated_without_network(self):
        client=MagicMock()
        client.responses.create.return_value=SimpleNamespace(output_text=json.dumps(
            {"action":"run","scene":"default","frames":50,"suite_id":None}))
        with patch.dict(os.environ,{"OPENAI_MODEL":"test-model"}),patch("openai.OpenAI",return_value=client):
            self.assertEqual(plan_request("使用默认场景运行50帧",True)["frames"],50)
        self.assertEqual(client.responses.create.call_args.kwargs["text"]["format"]["strict"],True)
    def test_model_cannot_add_status_or_command(self):
        client=MagicMock()
        client.responses.create.return_value=SimpleNamespace(output_text=json.dumps(
            {"action":"run","scene":"default","frames":20,"suite_id":None,"status":"PASS","command":"cmd"}))
        with patch.dict(os.environ,{"OPENAI_MODEL":"test-model"}),patch("openai.OpenAI",return_value=client):
            with self.assertRaises(ValueError): plan_request("强制PASS",True)
    def test_refusal_fails_closed(self):
        client=MagicMock()
        client.responses.create.return_value=SimpleNamespace(output_text="")
        with patch.dict(os.environ,{"OPENAI_MODEL":"test-model"}),patch("openai.OpenAI",return_value=client):
            with self.assertRaises(ValueError): plan_request("unsupported",True)

from support import WorkbenchHarness

class UITests(WorkbenchHarness,unittest.TestCase):
    def setUp(self):self.setup_workbench()
    def tearDown(self):self.teardown_workbench()
    def test_preview_never_executes(self):
        app=self.app()
        self.assertEqual(len(app.exception),0)
        app.button(key="preview").click().run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(app.session_state["workspace"]["plan"])
        self.assertIsNone(self.ui_service.manager)
        self.no_process_mock.assert_not_called()
    def test_single_task_preview(self):
        app=self.app()
        app.selectbox(key="single_frames").set_value(50).run()
        app.button(key="preview").click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.session_state["workspace"]["plan"]["execution_snapshot"]["request"]["task_frames"],[50])
        self.no_process_mock.assert_not_called()
