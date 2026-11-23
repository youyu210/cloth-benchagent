"""V3 contracts and default-off model boundary; no online provider is used."""

import copy
import os
import unittest
from dataclasses import asdict
from unittest.mock import patch

from benchagent.common import ROOT, read
from benchagent.model_gateway import (
    SCHEMA_VERSION, ExplainRequest, PlanRequest, get_default_gateway,
)

class DisabledGatewayTests(unittest.TestCase):
    def test_status_is_explicitly_not_ready(self):
        status = get_default_gateway().get_status()
        self.assertFalse(status.enabled)
        self.assertFalse(status.ready)
        self.assertEqual(status.provider, "disabled")
        self.assertEqual(status.capabilities, ())
        self.assertEqual(status.schema_version, SCHEMA_VERSION)

    def test_plan_never_reads_credentials_or_connects(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-do-not-use"}):
            with patch("os.getenv", side_effect=AssertionError("credential read")), \
                 patch("socket.create_connection", side_effect=AssertionError("network")):
                result = get_default_gateway().propose_plan(PlanRequest("plan-1", "默认场景20帧"))
        self.assertEqual(result.request_id, "plan-1")
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.error_code, "MODEL_NOT_CONFIGURED")
        self.assertIsNone(result.proposed_plan)

    def test_report_result_is_not_modified(self):
        report = {"status": "PASS", "actual_frames": 20, "errors": []}
        original = copy.deepcopy(report)
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            result = get_default_gateway().explain_report(ExplainRequest("explain-1", "report-1", report))
        self.assertEqual(report, original)
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.request_id, "explain-1")
        self.assertIsNone(result.explanation)
        self.assertIsNone(result.proposed_plan)
        self.assertNotIn("status_override", asdict(result))

class TaskRequestContractTests(unittest.TestCase):
    def setUp(self):
        from jsonschema import Draft202012Validator
        self.validator = Draft202012Validator(read(ROOT / "docs/contracts/task_request_v1.schema.json"))
        self.valid = {
            "schema_version": "task-request-v1", "request_id": "synthetic-v3-request",
            "mode": "standard", "scene": "default", "backend_id": "c0-003-benchagent-v1",
            "task_frames": [20, 50, 100], "timeout_sec": 3600,
        }

    def test_standard_and_custom_whitelist(self):
        self.assertTrue(self.validator.is_valid(self.valid))
        self.assertTrue(self.validator.is_valid({**self.valid, "mode": "custom", "task_frames": [20, 100]}))

    def test_reject_empty_duplicates_arbitrary_frames_and_overrides(self):
        bad = [
            {**self.valid, "mode": "custom", "task_frames": []},
            {**self.valid, "task_frames": [20, 20, 100]},
            {**self.valid, "mode": "custom", "task_frames": [19]},
            {**self.valid, "task_frames": [20]},
            {**self.valid, "pcg_tolerance": .1},
            {**self.valid, "command": "arbitrary"},
            {**self.valid, "timeout_sec": 0},
            {**self.valid, "scene": "bunny"},
        ]
        for request in bad:
            with self.subTest(request=request):
                self.assertFalse(self.validator.is_valid(request))
