"""API errors preserve actionable causes and never expose response credentials."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError

from benchagent.model_gateway import PlanRequest
from benchagent.model_gateway.deepseek import DeepSeekGateway
from support import WorkbenchHarness


SECRET = "sk-fixture-private-never-display"
PROMPTS = ("悬挂布料运行20帧",
           "将悬挂布料场景的布片泊松比从0.243修改为0.3，其他材料参数保持不变，运行20帧。")


def api_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
                              json={"error": {"message": SECRET}})
    error = AuthenticationError if status == 401 else APIStatusError
    return error("provider text includes " + SECRET, response=response, body={"secret": SECRET})


class GatewayErrorTests(unittest.TestCase):
    def test_http_failure_causes_survive_both_plan_paths(self):
        causes = {400: "请求格式", 401: "身份验证失败", 402: "余额不足", 403: "访问权限",
                  404: "模型不存在", 408: "超时", 422: "请求参数", 429: "达到上限",
                  500: "服务内部错误", 502: "网关暂时不可用", 503: "服务繁忙", 504: "网关超时"}
        for prompt in PROMPTS:
            for status, reason in causes.items():
                with self.subTest(prompt=prompt, status=status), patch("openai.OpenAI") as sdk:
                    call = sdk.return_value.__enter__.return_value.chat.completions.create
                    call.side_effect = api_error(status)
                    result = DeepSeekGateway(SECRET).propose_plan(PlanRequest("api-test", prompt))
                    self.assertEqual(result.status, "error")
                    self.assertEqual(result.error_code, "MODEL_API_ERROR")
                    self.assertEqual(result.provider_calls, 1)
                    self.assertIsNone(result.proposed_plan)
                    self.assertIn(f"HTTP {status}", result.message)
                    self.assertIn(reason, result.message)
                    self.assertIn(str(status), result.diagnostics[0])
                    self.assertNotIn(SECRET, result.message + repr(result.diagnostics))
                    self.assertEqual(call.call_count, 1)
                    self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)

    def test_transport_failures_are_distinct(self):
        request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
        for error, reason in ((APITimeoutError(request=request), "请求超时"),
                              (APIConnectionError(message=SECRET, request=request), "无法连接")):
            for prompt in PROMPTS:
                with self.subTest(prompt=prompt, error=type(error).__name__), patch("openai.OpenAI") as sdk:
                    call = sdk.return_value.__enter__.return_value.chat.completions.create
                    call.side_effect = error
                    result = DeepSeekGateway(SECRET).propose_plan(PlanRequest("connection-test", prompt))
                    self.assertEqual(result.status, "error")
                    self.assertIn(reason, result.message)
                    self.assertEqual(result.provider_calls, 1)
                    self.assertIsNone(result.proposed_plan)
                    self.assertNotIn(SECRET, result.message + repr(result.diagnostics))

    def test_material_schema_failure_is_reported_as_schema_error(self):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=json.dumps({"scene": "unexpected"})))])
        with patch("openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.chat.completions.create.return_value = response
            result = DeepSeekGateway(SECRET).propose_plan(PlanRequest("schema-test", PROMPTS[1]))
        self.assertEqual(result.error_code, "MODEL_MATERIAL_SCHEMA")
        self.assertIn("模型输出超出当前约定", result.message)
        self.assertNotIn("HTTP", result.message)
        self.assertNotIn(SECRET, result.message + repr(result.diagnostics))


class GatewayErrorUITests(WorkbenchHarness, unittest.TestCase):
    def setUp(self):
        self.setup_workbench()

    def tearDown(self):
        self.teardown_workbench()

    def test_auth_failure_is_visible_without_creating_plan_or_job(self):
        page = self.app()
        page.session_state["deepseek_api_key"] = SECRET
        page.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        page.text_area(key="rule_prompt").set_value(PROMPTS[1]).run()
        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.side_effect = api_error(401)
            page.button(key="preview").click().run()
            self.assertEqual(call.call_count, 1)
        self.assertFalse(page.exception)
        self.assertEqual(len(page.error), 1)
        self.assertIn("身份验证失败", page.error[0].value)
        self.assertIn("HTTP 401", page.error[0].value)
        self.assertIn("重新保存有效的 API 密钥", page.error[0].value)
        self.assertNotIn(SECRET, page.error[0].value)
        self.assertIsNone(page.session_state["workspace"]["plan"])
        self.assertEqual(page.text_area(key="rule_prompt").value, PROMPTS[1])
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()
