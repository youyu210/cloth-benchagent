import json
import os
import re

from .common import FRAME_TARGETS
from .conversation_service import SCENES, SCENE_NUMBERS


def validate_plan(plan, *, expanded=False):
    if not isinstance(plan, dict) or plan.get("scene") not in (tuple(SCENE_NUMBERS) if expanded else ("default",)):
        raise ValueError("Only scene=default is supported")
    if plan.get("action") == "bench":
        if set(plan) != {"action", "scene", "suite_id"} or plan["suite_id"] != "default-mvp":
            raise ValueError("Only suite=default-mvp is supported")
    elif plan.get("action") == "run":
        fields = {"action", "scene", "frames"}
        if expanded and plan.get("native_render") is True:
            fields.add("native_render")
        if expanded and plan.get("diagnostic_contact_trace") is True:
            if not SCENES[plan["scene"]].get("goal_analysis", {}).get("contact_trace"):
                raise ValueError("Scene does not support contact tracing")
            fields.add("diagnostic_contact_trace")
        if expanded and "material" in plan:
            from .materials import validate_material
            validate_material(plan["scene"], plan["material"])
            fields.add("material")
        targets = FRAME_TARGETS if expanded else (20, 50, 100)
        if set(plan) != fields or type(plan["frames"]) is not int or plan["frames"] not in targets:
            raise ValueError("Allowed frame counts: 1–1000" if expanded else "Allowed frame counts: 20, 50, 100")
    else:
        raise ValueError("Unsupported action")
    return dict(plan)

def plan_request(text, llm=False):
    if not text.strip():
        raise ValueError("Empty request")
    if llm:
        from openai import OpenAI
        model = os.environ.get("OPENAI_MODEL")
        if not model:
            raise ValueError("Set OPENAI_MODEL for LLM mode")
        schema = {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["run", "bench", "unsupported"]},
            "scene": {"type": "string", "enum": ["default"]},
            "frames": {"type": ["integer", "null"], "enum": [20, 50, 100, None]},
            "suite_id": {"type": ["string", "null"], "enum": ["default-mvp", None]}},
            "required": ["action", "scene", "frames", "suite_id"],
            "additionalProperties": False}
        response = OpenAI().responses.create(
            model=model, store=False,
            instructions="Extract a benchmark plan. Support only the default StiffGIPC scene, 20/50/100 frames or default-mvp suite. Reject other scenes, shell commands, source edits, configuration changes and instructions to force PASS. Use unsupported for requests outside scope. For run suite_id is null; for bench frames is null.",
            input=text, text={"format": {"type": "json_schema", "name": "bench_plan",
                                       "schema": schema, "strict": True}})
        raw = json.loads(response.output_text)
        if raw["action"] == "run":
            if raw["suite_id"] is not None:
                raise ValueError("Contradictory LLM plan")
            raw.pop("suite_id")
        elif raw["action"] == "bench":
            if raw["frames"] is not None:
                raise ValueError("Contradictory LLM plan")
            raw.pop("frames")
        return validate_plan(raw)
    if re.search(r"强制|忽略|源码|shell|powershell|cmd\.exe|穿透|容差|改成|其他场景|切换", text, re.I):
        raise ValueError("Request outside the fixed benchmark scope")
    if "默认" not in text and "default" not in text.lower():
        raise ValueError("Please explicitly specify the default scene")
    numbers = re.findall(r"(\d+)\s*帧|(\d+)\s*frames?", text, re.I)
    if numbers:
        values = [int(a or b) for a, b in numbers]
        if len(values) != 1:
            raise ValueError("Specify one frame count or the standard suite")
        return validate_plan({"action": "run", "scene": "default", "frames": values[0]})
    if "标准" in text or "整套" in text or "default-mvp" in text:
        return validate_plan({"action": "bench", "scene": "default", "suite_id": "default-mvp"})
    raise ValueError("Specify 20/50/100 frames or the standard benchmark suite")
