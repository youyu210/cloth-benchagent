"""Material edits are bounded, inheritable and frozen through the workbench."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchagent.conversation_service import normalize_request, task_request
from benchagent.material_dialogue import resolve_material
from benchagent.materials import native_config
from benchagent.model_gateway import PlanRequest
from benchagent.model_gateway.deepseek import DeepSeekGateway
from benchagent.planning_store import PlanningError
from support import WorkbenchHarness


class MaterialTests(unittest.TestCase):
    def test_units_and_sequential_relative_edits(self):
        first = resolve_material("把固定布料的杨氏模量改成原来的两倍，运行20帧。")
        second = resolve_material("把杨氏模量降低20%，并把体密度设为300 kg/m³", first)
        self.assertEqual(second["material"]["values"], {"young_pa": 80000, "density_kg_m3": 300})
        third = resolve_material("把泊松比改成0.3，其他参数保持不变", second)
        self.assertEqual(third["material"]["values"], {"young_pa": 80000, "density_kg_m3": 300, "poisson_ratio": .3})
        self.assertEqual(third["material"]["previous"], second["material"]["values"])

    def test_from_to_edit_matches_screenshot_and_preserves_other_values(self):
        from benchagent.materials import MATERIAL_SCENES, material_rows

        prompt = "将悬挂布料场景的布片泊松比从0.243修改为0.3，其他材料参数保持不变，运行20帧。"
        plan = resolve_material(prompt)
        self.assertEqual(plan["scene"], "cloth-hang")
        self.assertEqual(plan["task_frames"], [20])
        self.assertEqual(plan["material"], {
            "object": "cloth", "values": {"poisson_ratio": .3}, "previous": {},
        })
        rows = {row["参数"]: row for row in material_rows(plan["scene"], plan["material"])}
        self.assertEqual((rows["泊松比"]["修改前"], rows["泊松比"]["计划值"]), (.243, .3))
        self.assertEqual(rows["膜杨氏模量"]["计划值"], 821000)
        self.assertEqual(rows["体密度"]["计划值"], 472.641509)
        self.assertEqual(MATERIAL_SCENES["cloth-hang"]["defaults"]["poisson_ratio"], .243)

        prior = resolve_material("将悬挂布料的杨氏模量设为900 kPa，体密度设为500，运行37帧")
        previous_values = dict(prior["material"]["values"])
        updated = resolve_material(prompt, prior)
        self.assertEqual(updated["material"]["values"], {**previous_values, "poisson_ratio": .3})
        self.assertEqual(updated["material"]["previous"], previous_values)
        self.assertEqual(prior["material"]["values"], previous_values)
        repeated = resolve_material(prompt, updated)
        self.assertEqual(repeated["material"]["values"], updated["material"]["values"])
        self.assertEqual(repeated["material"]["previous"], updated["material"]["values"])

    def test_from_to_variants_units_and_validation(self):
        for prompt, scene, expected in (
            ("悬挂布料泊松比由0.243改成0.3，其余参数保持不变", "cloth-hang", {"poisson_ratio": .3}),
            ("把固定布料的杨氏模量从50 kPa调整到0.1 MPa，其余材料参数保持不变",
             "fixed-cloth", {"young_pa": 100000}),
            ("将悬挂布料的泊松比从0.243无量纲调节为0.3无量纲", "cloth-hang", {"poisson_ratio": .3}),
            ("把固定布料的体密度从200 kg/m³到300 kg/m³", "fixed-cloth", {"density_kg_m3": 300}),
            ("悬挂布料泊松比从0.243至0.3，杨氏模量从821 kPa修改为900 kPa",
             "cloth-hang", {"poisson_ratio": .3, "young_pa": 900000}),
        ):
            with self.subTest(prompt=prompt):
                plan = resolve_material(prompt)
                self.assertEqual(plan["scene"], scene)
                self.assertEqual(plan["material"]["values"], expected)
        for prompt, code in (
            ("悬挂布料泊松比从0.243修改为0.5", "MATERIAL_RANGE"),
            ("悬挂布料泊松比从0.243修改为-0.1", "MATERIAL_RANGE"),
            ("悬挂布料泊松比从0.243修改为", "MATERIAL_VALUE"),
            ("悬挂布料泊松比从0.243修改为0.3 Pa", "MATERIAL_UNIT"),
            ("悬挂布料泊松比从0.243 Pa修改为0.3", "MATERIAL_UNIT"),
            ("球体与布料泊松比从0.49修改为0.3", "MATERIAL_FIELD"),
            ("悬挂布料泊松比从0.243修改为0.3，其他材料参数保持不变并删除日志", "MATERIAL_FIELD"),
        ):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(PlanningError, code):
                resolve_material(prompt)

    def test_restore_preserves_scene_and_uses_empty_overrides(self):
        prior = resolve_material("将球体与布料场景中布料的密度修改为300。")
        reset = resolve_material("恢复这个场景的默认材料参数。", prior)
        self.assertEqual(reset["material"], {"object": "cloth", "previous": {"density_kg_m3": 300}, "values": {}})
        self.assertEqual(native_config(reset["scene"], reset["material"])["values"], {})

    def test_rejects_unsupported_fields_objects_and_invalid_values(self):
        rejected = [
            ("把球体与布料场景中球体的密度改成300", None, "MATERIAL_OBJECT"),
            ("把球体与布料的泊松比改成0.3", None, "MATERIAL_FIELD"),
            ("把悬挂布料的泊松比改成0.5", None, "MATERIAL_RANGE"),
            ("把固定布料的密度改为-300", None, "MATERIAL_RANGE"),
            ("把固定布料的密度改为300 g/m²", None, "MATERIAL_UNIT"),
            ("把固定布料的阻尼改成2", None, "MATERIAL_FIELD"),
            ("把固定布料的魔法参数改成2", None, "MATERIAL_FIELD"),
            ("把固定布料的杨氏模量改成100 kPa", "cloth-hang", "MATERIAL_SCENE"),
            ("把泊松比改成0.3", None, "MATERIAL_SCENE"),
        ]
        for prompt, selected, code in rejected:
            with self.subTest(prompt=prompt), self.assertRaisesRegex(PlanningError, code):
                resolve_material(prompt, selected=selected)

    def test_v2_schema_rejects_malformed_requests(self):
        for bad in ({"object": "obstacle", "values": {}, "previous": {}},
                    {"object": "cloth", "values": {"path": "../outside"}, "previous": {}},
                    {"object": "cloth", "values": {"young_pa": float("nan")}, "previous": {}}):
            with self.assertRaises(PlanningError):
                normalize_request(task_request("material-test", {"scene": "fixed-cloth",
                                  "task_frames": [20], "material": bad}))

    def test_qualitative_advice_cannot_be_submitted(self):
        prior = resolve_material("把固定布料的杨氏模量改成100 kPa")
        reply = resolve_material("让布料更柔软一些", prior)
        self.assertEqual(set(reply), {"reply"})
        self.assertIn("100000 Pa", reply["reply"])
        self.assertIn("弯曲", reply["reply"])

    def test_model_cannot_change_scene_values_or_add_execution_fields(self):
        request = PlanRequest("material-model", "把固定布料的杨氏模量改为100 kPa，运行20帧")
        exact = resolve_material(request.user_text)
        def reply(value):
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=json.dumps(value)))])
        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.side_effect = [reply({**exact, "command": "unexpected"}), reply(exact)]
            gateway = DeepSeekGateway("fixture")
            self.assertEqual(gateway.propose_plan(request).error_code, "MODEL_MATERIAL_SCHEMA")
            accepted = gateway.propose_plan(request)
            self.assertEqual(accepted.status, "ok")
            self.assertEqual(accepted.proposed_plan["material"]["values"], {"young_pa": 100000})


class MaterialFormTests(WorkbenchHarness, unittest.TestCase):
    def setUp(self):
        self.setup_workbench()

    def tearDown(self):
        self.teardown_workbench()

    def test_from_to_request_previews_through_model_entry(self):
        prompt = "将悬挂布料场景的布片泊松比从0.243修改为0.3，其他材料参数保持不变，运行20帧。"
        expected = {"scene": "cloth-hang", "mode": "custom", "task_frames": [20],
                    "material": {"object": "cloth", "values": {"poisson_ratio": .3}, "previous": {}},
                    "native_render": True}
        response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=json.dumps(expected)))])
        with patch("openai.OpenAI") as sdk:
            call = sdk.return_value.__enter__.return_value.chat.completions.create
            call.return_value = response
            for source in ("智能测试（DeepSeek V4）",):
                with self.subTest(source=source):
                    page = self.app()
                    page.session_state["deepseek_api_key"] = "fixture"
                    page.radio(key="input_source").set_value(source).run()
                    page.text_area(key="rule_prompt").set_value(prompt).run()
                    page.button(key="preview").click().run()
                    self.assertFalse(page.exception)
                    self.assertEqual([item.value for item in page.error], [])
                    plan = page.session_state["workspace"]["plan"]
                    req = plan["execution_snapshot"]["request"]
                    self.assertEqual(req["scene"], "cloth-hang")
                    self.assertEqual(req["task_frames"], [20])
                    self.assertEqual(req["material"], expected["material"])
                    self.assertIsNone(plan["confirmed_at"])
                    if source == "智能测试（DeepSeek V4）":
                        self.assertEqual(plan["provenance"]["planning_audit"]["provider_calls"], 1)
                        self.assertEqual(plan["provenance"]["planning_audit"]["outcome"], "PLAN_VALIDATED")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(json.loads(call.call_args.kwargs["messages"][1]["content"])["verified_plan"], expected)
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

    def test_form_previews_and_invalidates_material_edits(self):
        page = self.app()
        next(select for select in page.selectbox if select.label == "测试场景").set_value("fixed-cloth").run()
        page.checkbox(key="material-enable-fixed-cloth").check().run()
        field = page.number_input(key="material-fixed-cloth-young_pa")
        field.set_value(100000.).run()
        page.button(key="preview").click().run()
        self.assertEqual([item.value for item in page.error], [])
        req = page.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        self.assertEqual(req["material"]["values"], {"young_pa": 100000})
        page.checkbox(key="material-default-fixed-cloth").check().run()
        self.assertIsNone(page.session_state["workspace"]["plan"])
        page.button(key="preview").click().run()
        req = page.session_state["workspace"]["plan"]["execution_snapshot"]["request"]
        self.assertEqual(req["material"]["values"], {})
        self.assertEqual(req["material"]["previous"], {"young_pa": 100000})
        self.assertFalse(self.ui_service.jobs())
        self.no_process_mock.assert_not_called()

