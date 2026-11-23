import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from support import WorkbenchHarness

from benchagent.common import ROOT, read
from benchagent.conversation_service import SCENES, normalize_request, request_from_text, scene_args
from benchagent.planner import validate_plan
from benchagent.planning_store import PlanningError
from benchagent.model_gateway import PlanRequest
from benchagent.model_gateway.deepseek import DeepSeekGateway


class SceneUpgradeTests(unittest.TestCase):
    def test_rule_plan_and_native_dispatch_agree(self):
        args = read(ROOT / "configs/c0.json")["args"]
        for spec in SCENES.values():
            with self.subTest(scene=spec["id"]):
                request = request_from_text(spec["display_name"] + "运行20帧")
                self.assertEqual(request["scene"], spec["id"])
                native_args = scene_args(args, request["scene"])
                self.assertEqual(int(native_args[native_args.index("--scene") + 1]), spec["native_id"])
                validate_plan({"action": "run", "scene": request["scene"], "frames": 20},
                              expanded=True)

    def test_reject_unregistered_and_untrusted_fields(self):
        valid = request_from_text("摆锤运行20帧")
        for change in ({"scene": "unregistered"}, {"task_frames": [1001]}, {"path": "../outside"},
                       {"command": "shell"}, {"native_render": 1}):
            with self.subTest(change=change), self.assertRaises(PlanningError):
                normalize_request({**valid, **change})

    def test_catalog_covers_native_switch(self):
        source = (ROOT / "simulator/StiffGIPC/gl_main.cu").read_text(encoding="utf-8")
        dispatch = source.split("else switch(scene_no)", 1)[1].split("setMAS_partition", 1)[0]
        cases = dict(re.findall(r"case (\d+):[^\n]*\n\s*(set_case\d+)\(\);", dispatch))
        self.assertEqual(cases, {str(s["native_id"]): s["source"].split(":")[1] for s in SCENES.values() if not s.get("cloth_scene") and not s.get("runtime_variant")})
        box = SCENES["wrecking-box"]
        box_source = (ROOT / "versions" / (box["runtime_variant"] + "-source")
                      / "StiffGIPC/gl_main.cu").read_text(encoding="utf-8")
        self.assertRegex(box_source, r"case 6:\s+set_wrecking_box_scene\(\);")
        named_source = (ROOT / "simulator/StiffGIPC/benchmark_cloth_v1.inc").read_text(encoding="utf-8")
        native_named = set(re.findall(r'cipc_case!="([a-z0-9]+)"', named_source.split("throw", 1)[0]))
        self.assertEqual(native_named, {s["cloth_scene"] for s in SCENES.values() if s.get("cloth_scene")})
        for spec in SCENES.values():
            self.assertTrue(all((ROOT / "simulator" / name).is_file() for name in spec["required_assets"]))


    def test_rule_accepts_arbitrary_frames_without_changing_scene(self):
        for text, scene, frames in (
            ("运行场景5，37帧", "large-box-pipe-cloth", [37]),
            ("运行固定布料，137帧", "fixed-cloth", [137]),
            ("运行场景3，200帧", "fixed-cloth", [200]),
            ("运行悬挂布料，37帧", "cloth-hang", [37]),
        ):
            request = request_from_text(text)
            self.assertEqual((request["scene"], request["task_frames"]), (scene, frames))
        with self.assertRaises(PlanningError):
            request_from_text("运行场景999，37帧")

    def test_model_revision_preserves_scene_and_settings(self):
        prior = {**request_from_text("运行场景5，100帧"), "native_render": True, "timeout_sec": 120}
        def reply(content):
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                                   message=SimpleNamespace(content=content))])
        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.side_effect = [
                reply('{"scene":"large-box-pipe-cloth","mode":"custom","task_frames":[200]}'),
                reply('{"scene":"default","mode":"custom","task_frames":[37]}'),
            ]
            gateway = DeepSeekGateway("fixture")
            result = gateway.propose_plan(PlanRequest("rev", "改成200帧", previous_plan=prior))
            self.assertEqual(result.proposed_plan["task_frames"], [200])
            self.assertEqual(result.proposed_plan["scene"], prior["scene"])
            self.assertTrue(result.proposed_plan["native_render"])
            self.assertEqual(result.proposed_plan["timeout_sec"], 120)
            result = gateway.propose_plan(PlanRequest("mismatch", "运行场景5，37帧"))
            self.assertEqual(result.error_code, "MODEL_INTENT_MISMATCH")
            result = gateway.propose_plan(PlanRequest("unsupported", "运行场景999，37帧"))
            self.assertIsNone(result.proposed_plan)
            self.assertEqual(call.call_count, 2)


    def test_short_dialogue_retry_and_revision_patch(self):
        def reply(content):
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=content))])

        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.side_effect = [
                reply(" "),
                reply(chr(96) * 3 + 'json\n{"scene":"cloth-hang","task_frames":20}\n' + chr(96) * 3),
                reply('{"task_frames":[29]}'),
                reply('{"scene":"球体与布料","task_frames":[20]}'),
                reply('{"scene":"cloth-sphere7","task_frames":[37]}'),
            ]
            gateway = DeepSeekGateway("fixture")
            initial = gateway.propose_plan(PlanRequest("init", "悬挂布料运行20帧"))
            self.assertEqual(initial.provider_calls, 2)
            self.assertEqual(initial.diagnostics, ("provider:MODEL_EMPTY", "provider:PLAN_VALIDATED"))
            change = gateway.propose_plan(PlanRequest(
                "change", "把帧数改成29", previous_plan=initial.proposed_plan))
            self.assertEqual(change.proposed_plan["scene"], "cloth-hang")
            self.assertEqual(change.proposed_plan["task_frames"], [29])
            switch = gateway.propose_plan(PlanRequest(
                "switch", "换成球体与布料", previous_plan=change.proposed_plan))
            self.assertEqual(switch.proposed_plan["scene"], "cloth-sphere7")
            self.assertEqual(switch.proposed_plan["task_frames"], [29])
            self.assertIn("provider:PATCH_EXTRAS_IGNORED", switch.diagnostics)
            custom = gateway.propose_plan(PlanRequest("custom", "球体与布料跑37帧"))
            self.assertEqual(custom.proposed_plan["task_frames"], [37])
            for text, code in (("改成29帧", "MISSING_CONTEXT"), ("场景999运行20帧", "UNSUPPORTED_SCENE"),
                               ("运行固定布料，1001帧", "PARAMETER_RANGE"), ("运行20帧", "MISSING_CONTEXT")):
                result = gateway.propose_plan(PlanRequest("blocked", text))
                self.assertEqual(result.error_code, code)
                self.assertEqual(result.provider_calls, 0)
            self.assertEqual(call.call_count, 5)
            self.assertEqual(call.call_args_list[0].kwargs["response_format"], {"type": "json_object"})
            self.assertEqual(call.call_args_list[1].kwargs["response_format"], {"type": "text"})


    def test_repeated_empty_reply_applies_only_explicit_validated_patch(self):
        previous = {**request_from_text("悬挂布料运行29帧"), "native_render": True}
        blank = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=" "))])
        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.return_value = blank
            result = DeepSeekGateway("fixture").propose_plan(PlanRequest(
                "patch", "换成球体与布料", previous_plan=previous))
        self.assertEqual(result.error_code, "LOCAL_PATCH_VALIDATED")
        self.assertEqual(result.diagnostics, (
            "provider:MODEL_EMPTY", "provider:MODEL_EMPTY", "local:PATCH_VALIDATED"))
        self.assertEqual(result.proposed_plan["scene"], "cloth-sphere7")
        self.assertEqual(result.proposed_plan["task_frames"], [29])
        self.assertTrue(result.proposed_plan["native_render"])
        self.assertEqual(call.call_count, 2)


    def test_free_form_does_not_fall_back_to_default_or_change_numbered_scene(self):
        reply = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content='{"scene":"default","task_frames":[37]}'))])
        with patch("openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.chat.completions.create.return_value = reply
            gateway = DeepSeekGateway("fixture")
            for text, code in (
                ("麻烦在场景3跑37帧并统计耗时", "MODEL_INTENT_MISMATCH"),
                ("请帮我测试一个尚未登记的场景，计划采集37帧", "MISSING_CONTEXT"),
            ):
                result = gateway.propose_plan(PlanRequest("unexpected", text))
                self.assertEqual(result.error_code, code)
                self.assertIsNone(result.proposed_plan)


class SceneWorkbenchTests(WorkbenchHarness, unittest.TestCase):
    def setUp(self):
        self.setup_workbench()

    def tearDown(self):
        self.teardown_workbench()

    def test_form_and_model_choose_the_same_added_scene(self):
        app = self.app()
        scene = next(box for box in app.selectbox if box.label == "测试场景")
        scene.set_value("wrecking-ball").run()
        app.selectbox(key="single_frames").set_value(20).run()
        app.button(key="preview").click().run()
        self.assertFalse(app.exception)
        form = app.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        app.radio(key="input_source").set_value("智能测试（DeepSeek V4）").run()
        app.session_state["deepseek_api_key"] = "fixture"
        app.text_area(key="rule_prompt").set_value("摆锤运行20帧").run()
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content='{"scene":"wrecking-ball","task_frames":[20],"mode":"custom"}'))])
        with patch("openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value.chat.completions.create.return_value = response
            app.button(key="preview").click().run()
        self.assertFalse(app.exception)
        model = app.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        self.assertEqual((form["scene"], form["task_frames"]), ("wrecking-ball", [20]))
        self.assertEqual((form["scene"], form["task_frames"]), (model["scene"], model["task_frames"]))
        self.assertIsNone(self.ui_service.manager)
