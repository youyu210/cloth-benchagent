"""Translate DeepSeek JSON to validated BenchAgent task requests."""
import json
import re

from .contracts import GatewayStatus, ModelResult
from .disabled import DisabledGateway
from ..conversation_service import (
    DIALOGUE_DEFAULT_FRAMES, OUTPUT_METRICS, SCENES, SCENE_NUMBERS,
    dialogue_intent, explicitly_named_scene, normalize_request, numbered_scene,
    scene_display, scene_from_label, task_request,
)
from ..planning_store import PlanningError

DEFAULT_MODEL = "deepseek-v4-pro"
PLAN_FIELDS = {"scene", "task_frames", "mode", "backend_id", "timeout_sec", "native_render", "output_metrics"}
FORMAT_ERRORS = {
    "MODEL_EMPTY", "MODEL_TRUNCATED", "MODEL_JSON", "MODEL_FIELDS", "MODEL_MISSING_FIELDS", "PLAN_SCHEMA",
}


def _api_failure(exc):
    """Expose SDK error type and HTTP status without provider text or credentials."""
    from openai import APIConnectionError, APITimeoutError

    status = getattr(exc, "status_code", None)
    messages = {
        400: "DeepSeek 请求格式不符合接口要求，请检查模型接口配置。",
        401: "DeepSeek 身份验证失败，请在「系统设置 → 模型接口」重新保存有效的 API 密钥。",
        402: "DeepSeek 账户余额不足，请检查 API 账户余额。",
        403: "DeepSeek 拒绝访问，请检查密钥及模型访问权限。",
        404: "DeepSeek 接口或模型不存在，请检查接口地址和模型名称。",
        408: "DeepSeek 请求超时，请稍后重试。",
        422: "DeepSeek 请求参数不合法，请检查模型接口配置。",
        429: "DeepSeek 请求速率或并发达到上限，请稍后重试。",
        500: "DeepSeek 服务内部错误，请稍后重试。",
        502: "DeepSeek 网关暂时不可用，请稍后重试。",
        503: "DeepSeek 服务繁忙，请稍后重试。",
        504: "DeepSeek 网关超时，请稍后重试。",
    }
    if isinstance(exc, APITimeoutError):
        message = "DeepSeek 请求超时，请检查网络后重试。"
    elif isinstance(exc, APIConnectionError):
        message = "无法连接 DeepSeek，请检查网络、代理和 TLS 连接。"
    else:
        message = messages.get(status, "DeepSeek API 调用失败，请检查账户、接口配置和网络。")
    diagnostic = "api:" + type(exc).__name__
    if type(status) is int:
        message += f"（HTTP {status}）"
        diagnostic += f":{status}"
    return message, diagnostic


def _payload(response, *, text_retry=False):
    if not response.choices:
        raise PlanningError("MODEL_EMPTY", "DeepSeek 未返回候选响应。")
    choice = response.choices[0]
    if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
        raise PlanningError("MODEL_DECLINED", "DeepSeek 拒绝生成计划。")
    if choice.finish_reason == "length":
        raise PlanningError("MODEL_TRUNCATED", "DeepSeek 响应超过输出长度。")
    if choice.finish_reason != "stop":
        raise PlanningError("MODEL_INCOMPLETE", "DeepSeek 未正常结束本次响应。")
    content = choice.message.content
    if not isinstance(content, str) or not content.strip():
        raise PlanningError("MODEL_EMPTY", "DeepSeek 返回了空的 JSON 内容。")
    if text_retry:
        fenced = re.fullmatch(r"\s*\x60{3}(?:json)?\s*\n(.*?)\n\s*\x60{3}\s*", content, re.I | re.S)
        if fenced:
            content = fenced[1]
    try:
        data = json.loads(content)
    except ValueError:
        raise PlanningError("MODEL_JSON", "DeepSeek 输出无法解析为 JSON。") from None
    if not isinstance(data, dict):
        raise PlanningError("MODEL_FIELDS", "DeepSeek 输出应为 JSON 对象。")
    return data


def _proposal(data, request_id, expected, patch_fields, prior):
    required = set(patch_fields) - {"mode"} if prior else {"scene", "task_frames"}
    if not required <= data.keys():
        raise PlanningError("MODEL_MISSING_FIELDS", "DeepSeek 计划缺少场景、帧数或本轮要求修改的字段。")
    if not data.keys() <= PLAN_FIELDS:
        raise PlanningError("MODEL_FIELDS", "DeepSeek 计划包含未允许的字段。")
    discarded = set(data) - required if prior else set()
    data = {key: value for key, value in data.items() if key not in discarded}
    if "scene" in data:
        scene = data["scene"]
        if type(scene) is int:
            scene = f"场景{scene}"
        resolved = scene_from_label(scene) if isinstance(scene, str) else None
        if resolved is None:
            raise PlanningError("UNSUPPORTED_SCENE", "DeepSeek 选择了未开放的场景。")
        data["scene"] = resolved
    if type(data.get("task_frames")) is int:
        data["task_frames"] = [data["task_frames"]]
    base = prior or {"output_metrics": list(OUTPUT_METRICS)}
    proposed = normalize_request(task_request(request_id, {
        **base, **({"mode": expected["mode"]} if expected else {}), **data,
        "schema_version": "task-request-v2", "request_id": request_id,
    }), detailed=True)
    if expected:
        keys = set(patch_fields) | {"scene", "task_frames", "mode"}
        mismatches = sorted(key for key in keys if proposed.get(key) != expected.get(key))
        if mismatches:
            raise PlanningError("MODEL_INTENT_MISMATCH", "DeepSeek 返回值与本轮请求不一致：" + "、".join(mismatches))
    return proposed, bool(discarded)


class DeepSeekGateway(DisabledGateway):
    def __init__(self, api_key):
        self.api_key = api_key

    def get_status(self):
        return GatewayStatus(True, True, "deepseek", ("plan", "objective_action", "analysis_script"), "已配置 DeepSeek V4 Pro 密钥")

    def propose_action(self, request):
        """Select one offered tool using a bounded, data-only observation."""
        schema = {
            "type": "object", "required": ["action"], "additionalProperties": False,
            "properties": {"action": {"enum": list(request.allowed_actions)}},
        }
        prompt = {
            "objective": request.objective, "observation": request.observation,
            "allowed_output_schema": schema,
        }
        return self._bounded_object(request.request_id, prompt, schema, 128, "MODEL_ACTION_SCHEMA",
                                    "Select one tool from the schema enum using the current observation. "
                                    "Return JSON only. Suggestions never authorize a GPU run.")

    def propose_script(self, request):
        schema = {"type": "object", "required": ["script"], "additionalProperties": False,
                  "properties": {"script": {"type": "string", "minLength": 1, "maxLength": 12000}}}
        return self._bounded_object(
            request.request_id, {"contract": request.contract, "allowed_output_schema": schema},
            schema, 2000, "MODEL_SCRIPT_SCHEMA",
            "Return a JSON object with one Python script string. Use only csv, statistics and math. "
            "Open the fixed input.csv in read mode and result.csv in write mode, using literal names "
            "and explicit modes. No other input or output, execution environment access, or dependencies. "
            "The sample rows are synthetic. The script will undergo review only.",
        )

    def _bounded_object(self, request_id, prompt, schema, max_tokens, error_code, instruction):
        from jsonschema import Draft202012Validator
        from openai import OpenAI, OpenAIError

        try:
            with OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com",
                        timeout=25, max_retries=0) as client:
                response = client.chat.completions.create(
                    model=DEFAULT_MODEL, response_format={"type": "json_object"},
                    extra_body={"thinking": {"type": "disabled"}}, temperature=0, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": instruction},
                              {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                )
            proposed = _payload(response)
            if not Draft202012Validator(schema).is_valid(proposed):
                raise PlanningError(error_code, "模型输出超出当前约定。")
            if "script" in proposed and self.api_key in proposed["script"]:
                raise PlanningError(error_code, "生成文本未通过凭据过滤。")
            return ModelResult(request_id, "ok", "模型结果已通过 Schema 校验", proposed_plan=proposed,
                               provider_calls=1, diagnostics=("provider:SCHEMA_VALIDATED",))
        except PlanningError as exc:
            return ModelResult(request_id, "error", str(exc).split(": ", 1)[-1], error_code=exc.code,
                               provider_calls=1, diagnostics=("provider:" + exc.code,))
        except OpenAIError as exc:
            message, diagnostic = _api_failure(exc)
            return ModelResult(request_id, "error", message, error_code="MODEL_API_ERROR",
                               provider_calls=1, diagnostics=(diagnostic,))

    def propose_plan(self, request):
        from ..material_dialogue import resolve_material
        try:
            material = resolve_material(request.user_text, request.previous_plan, request.selected_scene)
        except PlanningError as exc:
            return ModelResult(request.request_id, "error", str(exc), error_code=exc.code)
        if material:
            if "reply" in material:
                return ModelResult(request.request_id, "ok", material["reply"], error_code="MATERIAL_ADVICE")
            try:
                expected = normalize_request(task_request(request.request_id, material), detailed=True)
            except PlanningError as exc:
                return ModelResult(request.request_id, "error", str(exc), error_code=exc.code)
            # Only accept a proposal that agrees with the user's explicit, locally checked edit.
            shape = {key: expected[key] for key in material}
            schema = {
                "type": "object", "required": list(shape), "additionalProperties": False,
                "properties": {key: {"const": value} for key, value in shape.items()},
            }
            response = self._bounded_object(
                request.request_id, {"user_request": request.user_text, "verified_plan": shape},
                schema, 512, "MODEL_MATERIAL_SCHEMA",
                "Convert the request into a structured BenchAgent material task. "
                "Return exactly the verified_plan fields and values as JSON. Use canonical units. "
                "The material values are task-local and previous contains the preceding draft overrides.",
            )
            if response.status != "ok":
                return response
            return ModelResult(request.request_id, "ok", "已校验材料修改计划", proposed_plan=expected,
                               provider_calls=response.provider_calls,
                               diagnostics=(*response.diagnostics, "provider:MATERIAL_PLAN_VALIDATED"))

        diagnostics = []
        calls = 0

        def result(status, message, *, code=None, plan=None):
            return ModelResult(request.request_id, status, message, error_code=code,
                               proposed_plan=plan, provider_calls=calls, diagnostics=tuple(diagnostics))

        if not self.api_key:
            return result("disabled", "请在系统设置中保存 DeepSeek API 密钥。", code="MODEL_NOT_CONFIGURED")
        if not isinstance(request.user_text, str) or not request.user_text.strip() or len(request.user_text) > 4000:
            return result("error", "请输入 1 至 4000 字的任务需求。", code="INVALID_MESSAGE")

        try:
            fields, revision = dialogue_intent(request.user_text, previous=request.previous_plan,
                                               selected=request.selected_scene)
            prior = request.previous_plan if revision else None
            expected = None
            if revision and prior and "material" in prior and "scene" in fields and fields["scene"] != prior["scene"]:
                prior = {key: value for key, value in prior.items() if key != "material"}
            if fields:
                expected = normalize_request(task_request(request.request_id, {
                    "output_metrics": list(OUTPUT_METRICS), **(prior or {}), **fields,
                    "schema_version": "task-request-v2", "request_id": request.request_id,
                }), detailed=True)
            elif re.fullmatch(r"\s*(?:运行|执行)\s*[^，,。]+[，,]\s*\d+\s*帧[。！!]?\s*", request.user_text):
                raise PlanningError("UNSUPPORTED_SCENE", "未找到该场景，请使用能力目录中的场景名或编号。")
        except PlanningError as exc:
            diagnostics.append("local:" + exc.code)
            status = "ok" if exc.code in {"MISSING_CONTEXT", "UNSUPPORTED_SCENE"} else "error"
            return result(status, str(exc).split(": ", 1)[-1], code=exc.code)

        scenes = [
            {"scene": key, "name": scene_display(key), "aliases": SCENES[key].get("aliases", []),
             "frames": SCENES[key]["frames"], "native_render": SCENES[key]["native_render_frames"]}
            for key in SCENE_NUMBERS
        ]
        system = (
            'You operate the BenchAgent plan preview. Return only a JSON object. '
            'For a new plan return scene and task_frames, for example '
            '{"scene":"cloth-hang","task_frames":[20]}. Use a catalog scene ID. '
            'For a question, ambiguity or unsupported request return {"reply":"中文说明"}; never substitute a scene. '
            'Optional plan fields: mode ("custom" or "standard"), timeout_sec (integer 1–86400), '
            'native_render (boolean), output_metrics (array), backend_id ("c0-003-benchagent-v1"). '
            'Standard mode means [20,50,100]. Each plan uses one scene. '
            'Never claim a simulation ran. Use the prior validated plan for references. '
            f'Default frames for a known scene: {DIALOGUE_DEFAULT_FRAMES}. '
            f'Allowed metrics: {json.dumps(OUTPUT_METRICS)}. '
            f'Verified scene catalog: {json.dumps(scenes, ensure_ascii=False)}. '
            f'Explicitly selected scene (or null): {json.dumps(request.selected_scene)}. '
            f'Prior validated plan: {json.dumps(request.previous_plan, ensure_ascii=False)}. '
        )
        if expected:
            shape = {key: expected[key] for key in fields if key != "mode" or not prior}
            # Short commands are already resolved against the catalog and prior plan.
            # Send only this turn's allowed output so earlier scene names cannot compete.
            system = (
                "Return a JSON object for the explicitly resolved BenchAgent request. "
                "Return exactly these keys and values: " + json.dumps(shape, ensure_ascii=False)
            )
        history = () if expected else request.history
        messages = [{"role": "system", "content": system},
                    *({"role": role, "content": content} for role, content in history),
                    {"role": "user", "content": request.user_text}]
        from openai import OpenAI, OpenAIError

        try:
            with OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com",
                        timeout=25, max_retries=0) as client:
                for attempt in range(2):
                    calls += 1
                    text_retry = bool(attempt)
                    response = client.chat.completions.create(
                        model=DEFAULT_MODEL, messages=messages,
                        response_format={"type": "text" if text_retry else "json_object"},
                        extra_body={"thinking": {"type": "disabled"}},
                        temperature=0, max_tokens=1024,
                    )
                    try:
                        data = _payload(response, text_retry=text_retry)
                        if set(data) == {"reply"}:
                            reply = data["reply"]
                            if not isinstance(reply, str) or not reply.strip() or len(reply) > 2000:
                                raise PlanningError("MODEL_FIELDS", "DeepSeek 对话回复内容不符合约定。")
                            diagnostics.append("provider:MODEL_REPLY")
                            return result("ok", reply.strip().replace(self.api_key, "[已隐藏]"),
                                          code="MODEL_REPLY")
                        plan, discarded = _proposal(data, request.request_id, expected, fields, prior)
                        if not expected:
                            numbered = numbered_scene(request.user_text)
                            if numbered and plan["scene"] != numbered:
                                raise PlanningError("MODEL_INTENT_MISMATCH", "DeepSeek 改变了请求中的原生场景编号。")
                            context_scene = request.selected_scene or (request.previous_plan or {}).get("scene")
                            if (plan["scene"] == "default" and context_scene != "default"
                                    and not explicitly_named_scene(request.user_text, "default")):
                                raise PlanningError("MISSING_CONTEXT", "请确认要运行的场景。")
                    except PlanningError as exc:
                        diagnostics.append("provider:" + exc.code)
                        can_retry = exc.code in FORMAT_ERRORS or (
                            prior and exc.code in {"MODEL_INTENT_MISMATCH", "UNSUPPORTED_SCENE"})
                        if attempt == 0 and can_retry:
                            messages = [{"role": "system", "content": system +
                                         f" Previous response failed {exc.code}. Return the expected JSON only, "
                                         "with no explanation or Markdown."},
                                        *messages[1:]]
                            continue
                        if prior and (exc.code in FORMAT_ERRORS or exc.code == "MODEL_INTENT_MISMATCH"):
                            # Only an unambiguous, already validated edit can be applied locally.
                            diagnostics.append("local:PATCH_VALIDATED")
                            return result("ok", "模型补丁未通过，已按本轮明确修改生成本地预览。",
                                          code="LOCAL_PATCH_VALIDATED", plan=expected)
                        return result("error", str(exc).split(": ", 1)[-1], code=exc.code)
                    if discarded:
                        diagnostics.append("provider:PATCH_EXTRAS_IGNORED")
                    diagnostics.append("provider:PLAN_VALIDATED")
                    return result("ok", "已生成模型草稿", plan=plan)
        except OpenAIError as exc:
            message, diagnostic = _api_failure(exc)
            diagnostics.append(diagnostic)
            return result("error", message, code="MODEL_API_ERROR")
